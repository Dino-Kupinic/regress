"""The HTTP API for the web app. Start it with `regress serve`, or `uvicorn --factory regress.api:create_app`."""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

import anyio.to_thread
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from starlette.datastructures import Headers
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.types import ASGIApp, Receive, Scope, Send

from regress import __version__
from regress.api.evaluations import EvaluationManager
from regress.api.jobs import LLMFactory, RunManager
from regress.api.routes import router
from regress.api.schemas import ErrorBody
from regress.errors import LLMError, RegressError, ToolError
from regress.project import find_project_root

LOCAL_HOSTS = ("localhost", "127.0.0.1", "[::1]")  # as in a Host header
DEV_ORIGINS = ("http://localhost:5173", "http://127.0.0.1:5173")  # Vite's dev server
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
MAX_REQUEST_BYTES = 1_048_576
logger = logging.getLogger(__name__)


def create_app(
    root: Path | None = None,
    *,
    llm_factory: LLMFactory | None = None,
    origins: Sequence[str] = DEV_ORIGINS,
    hosts: Sequence[str] = LOCAL_HOSTS,
) -> FastAPI:
    """An API for the project containing `root` (default: the current directory).

    `origins` are the browser origins allowed to call it, `hosts` the names it may be reached by.
    `llm_factory` replaces the OpenAI model, e.g. with a scripted one in tests.
    """
    project_root = find_project_root((root or Path.cwd()).expanduser().resolve())
    manager = RunManager(project_root, llm_factory)
    evaluations = EvaluationManager(project_root, manager=manager)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        # A rolling deploy starts this container before stopping the previous one. That previous
        # process still holds the project lock, so refuse to block startup on it: answer health
        # checks first, which is what lets the platform stop the previous container.
        takeover: asyncio.Task[None] | None = None
        try:
            manager.startup(lock_timeout=0)
        except RuntimeError as error:
            if "already owns" not in str(error):
                raise
            manager.replacing = True

            async def take_over() -> None:
                try:
                    await anyio.to_thread.run_sync(manager.startup)
                except Exception:
                    logger.exception("Regress could not take over %s", manager.root)
                    if not manager.closed:
                        os._exit(1)

            takeover = asyncio.create_task(take_over())
        try:
            yield
        finally:
            try:
                await anyio.to_thread.run_sync(evaluations.shutdown)
            finally:
                await anyio.to_thread.run_sync(manager.shutdown)
            if takeover is not None:
                await takeover

    app = FastAPI(
        title="Regress",
        summary="Improve AI-generated tests using mutation testing as an objective feedback loop.",
        version=__version__,
        lifespan=lifespan,
        openapi_url="/api/openapi.json",
        docs_url="/api/docs",
        redoc_url=None,
        generate_unique_id_function=_operation_id,
    )
    app.state.manager = manager
    app.state.evaluations = evaluations
    app.include_router(router)
    app.add_exception_handler(RegressError, _regress_error)
    app.add_exception_handler(Exception, _unexpected_error)
    app.add_middleware(RequestLimits)
    # The last one added runs first: check the Host header, answer CORS preflights, then refuse cross-site writes.
    app.add_middleware(SameOriginWrites, origins=origins)
    app.add_middleware(CORSMiddleware, allow_origins=list(origins), allow_methods=["*"], allow_headers=["*"])
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(hosts))
    return app


class RequestLimits:
    """Bound request bodies before JSON parsing and attach a server-generated diagnostic ID."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id = uuid4().hex
        scope.setdefault("state", {})["request_id"] = request_id

        async def send_response(message):
            if message["type"] == "http.response.start":
                message.setdefault("headers", []).extend(
                    [(b"x-request-id", request_id.encode()), (b"x-content-type-options", b"nosniff")]
                )
            await send(message)

        async def reject(status: int, detail: str) -> None:
            await JSONResponse({"detail": detail}, status_code=status)(scope, receive, send_response)

        length = Headers(scope=scope).get("content-length")
        if length is not None:
            try:
                size = int(length)
                if size < 0:
                    raise ValueError
            except ValueError:
                await reject(400, "Invalid Content-Length header.")
                return
            if size > MAX_REQUEST_BYTES:
                await reject(413, "Request body exceeds the 1 MiB limit.")
                return
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body.extend(message.get("body", b""))
            if len(body) > MAX_REQUEST_BYTES:
                await reject(413, "Request body exceeds the 1 MiB limit.")
                return
            if not message.get("more_body", False):
                break
        delivered = False

        async def replay():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        await self.app(scope, replay, send_response)


class SameOriginWrites:
    """Refuses requests that change something when they come from a page on another site.

    A page on any website can send a simple POST to localhost without a CORS preflight, so CORS
    alone would let it start or cancel runs. Browsers always send an Origin header with such
    requests; clients like curl send none and are let through. (The Host check, in turn, stops
    DNS rebinding from making another site look like the same origin.)
    """

    def __init__(self, app: ASGIApp, origins: Sequence[str]) -> None:
        self.app = app
        self.origins = frozenset(origins)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["method"] not in SAFE_METHODS:
            headers = Headers(scope=scope)
            origin = headers.get("origin")
            same_origin = f"{scope['scheme']}://{headers.get('host')}"
            if origin is not None and origin not in self.origins and origin != same_origin:
                body = ErrorBody(
                    detail=f"Requests from {origin} are not allowed. Start the server with --origin {origin}."
                )
                await JSONResponse(body.model_dump(exclude_none=True), status_code=403)(scope, receive, send)
                return
        await self.app(scope, receive, send)


async def _regress_error(request: Request, error: Exception) -> JSONResponse:
    assert isinstance(error, RegressError)
    status = 500 if isinstance(error, ToolError) else 503 if isinstance(error, LLMError) else 400
    output = error.output if isinstance(error, ToolError) and error.output else None
    return JSONResponse(ErrorBody(detail=str(error), output=output).model_dump(exclude_none=True), status_code=status)


async def _unexpected_error(request: Request, error: Exception) -> JSONResponse:
    request_id = getattr(request.state, "request_id", uuid4().hex)
    logger.error("Unhandled API error (request_id=%s)", request_id, exc_info=error)
    return JSONResponse(
        {"detail": "An internal error occurred. Check the server log using the X-Request-ID header."},
        status_code=500,
        headers={"X-Request-ID": request_id, "X-Content-Type-Options": "nosniff"},
    )


def _operation_id(route: APIRoute) -> str:
    """Operation IDs are the route functions' names (e.g. `start_run`), so generated clients read well."""
    return route.name
