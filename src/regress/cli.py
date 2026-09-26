from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Annotated, Literal, NoReturn

import typer
from dotenv import find_dotenv, load_dotenv
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from regress.catalog import load_catalog
from regress.config import (
    CONFIG_FILE,
    CONFIG_TEMPLATE,
    Settings,
    load_settings,
    save_user_settings,
    user_config_path,
)
from regress.errors import ProjectError, RegressError, ToolError
from regress.evaluation import EvalResult, evaluate_oracles, evaluate_regress, load_suites
from regress.llm import OpenAILLM, require_api_key
from regress.pipeline import Pipeline, RunOptions
from regress.process import run_command
from regress.project import (
    REQUIRED_PACKAGES,
    VITEST_INSTALL_SPEC,
    find_project_root,
    install_command,
    load_project,
    major,
    package_version,
    planned_installs,
)
from regress.store import RunStore, regress_dir
from regress.ui import (
    ConsoleReporter,
    choose_model,
    format_score,
    render_models,
    render_report,
    render_run_list,
)

app = typer.Typer(
    name="regress",
    help="Improve AI-generated tests using mutation testing as an objective feedback loop.",
    no_args_is_help=True,
    add_completion=False,
    pretty_exceptions_enable=False,
)
console = Console(highlight=False)
err_console = Console(stderr=True, highlight=False)

RunnerOption = Annotated[
    Literal["auto", "bun", "npx"] | None,
    typer.Option(help="How to run Vitest and Stryker. [default: auto, from regress.toml]"),
]
ModelOption = Annotated[
    str | None,
    typer.Option("--model", "-m", help="OpenAI model for this run. Skips the model question."),
]
YesOption = Annotated[
    bool,
    typer.Option("--yes", "-y", help="Don't ask anything; use the default model."),
]


def main() -> None:
    load_dotenv(find_dotenv(usecwd=True))
    app()


def _interactive() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def _pick_model(settings: Settings, yes: bool) -> str:
    """The model for this run: asked interactively unless it was given, remembered, or we can't ask."""
    if yes or settings.model_is_explicit or not settings.ask_model or not _interactive():
        return settings.model
    with console.status("[dim]Fetching available models...[/]"):
        catalog = load_catalog()
    model, remember = choose_model(console, catalog, settings.model)
    if remember:
        path = save_user_settings(model=model, ask_model=False)
        console.print(f"[dim]Saved to {escape(str(path))}. Change it any time with `regress models`.[/]")
        if settings.model_source == "regress.toml" and model != settings.model:
            console.print(
                f"[yellow]! This project's regress.toml pins model = {escape(settings.model)}, "
                "which wins over your default when nobody is asked.[/]"
            )
    console.print()
    return model


def _project_root_or_none(path: Path) -> Path | None:
    try:
        return find_project_root(path.expanduser().resolve())
    except ProjectError:
        return None


def _fail(error: RegressError) -> NoReturn:
    err_console.print(f"[red bold]error:[/] {escape(str(error))}")
    if isinstance(error, ToolError) and error.output:
        err_console.print(f"[dim]{escape(error.output)}[/]")
    raise typer.Exit(1)


@app.command()
def run(
    source: Annotated[Path, typer.Argument(help="Source file to test, e.g. src/cart.ts.")],
    test: Annotated[
        Path | None, typer.Option("--test", "-t", help="Test file to extend. Auto-detected by default.")
    ] = None,
    rounds: Annotated[
        int | None, typer.Option(min=0, max=5, help="Improvement rounds after the first mutation run. [default: 1]")
    ] = None,
    model: ModelOption = None,
    baseline: Annotated[
        bool, typer.Option("--baseline", help="Also mutation-test the existing tests before generating.")
    ] = False,
    generate: Annotated[
        bool,
        typer.Option(
            "--generate/--no-generate",
            help="Start with a one-shot generation round, or improve the existing tests directly.",
        ),
    ] = True,
    runner: RunnerOption = None,
    verbose: Annotated[
        bool, typer.Option("--verbose", "-v", help="Show validation details and model summaries.")
    ] = False,
    yes: YesOption = False,
) -> None:
    """Generate tests for SOURCE, then improve them from surviving mutants."""
    try:
        root = find_project_root(source.expanduser().resolve().parent)
        settings = load_settings(root, rounds=rounds, model=model, runner=runner)
        project = load_project(source, test, settings.runner)
        require_api_key()
        llm = OpenAILLM(_pick_model(settings, yes), settings.reasoning_effort, idle_timeout=settings.llm_timeout)
        options = RunOptions(
            rounds=settings.rounds,
            max_repairs=settings.max_repairs,
            max_mutants=settings.max_mutants,
            measure_baseline=baseline,
            generate=generate,
            vitest_timeout=settings.vitest_timeout,
            stryker_timeout=settings.stryker_timeout,
        )
        Pipeline(project, llm, options, ConsoleReporter(console, verbose)).run()
    except RegressError as error:
        _fail(error)
    except KeyboardInterrupt:
        err_console.print("\n[yellow]Interrupted.[/] The test file was restored.")
        raise typer.Exit(130) from None


@app.command()
def report(
    run_id: Annotated[str | None, typer.Argument(help="Run ID (or part of it). Defaults to the latest run.")] = None,
    list_runs: Annotated[bool, typer.Option("--list", "-l", help="List all runs.")] = False,
    as_json: Annotated[bool, typer.Option("--json", help="Print the raw JSON report.")] = False,
    mutants: Annotated[int, typer.Option(help="How many undetected mutants to show.")] = 10,
    project: Annotated[Path, typer.Option("--project", "-p", help="Project directory.")] = Path("."),
) -> None:
    """Show the report of the latest (or a given) run."""
    try:
        store = RunStore(find_project_root(project.expanduser().resolve()))
        if list_runs:
            runs = [store.load(p.name) for p in store.run_dirs()]
            if not runs:
                raise RegressError("No runs yet. Start one with `regress run <file>`.")
            render_run_list(runs, console)
            return
        loaded, run_dir = store.load(run_id)
        if as_json:
            typer.echo(loaded.model_dump_json(indent=2))
            return
        render_report(loaded, run_dir, console, show_mutants=mutants)
    except RegressError as error:
        _fail(error)


@app.command()
def init(
    path: Annotated[Path, typer.Argument(help="Project directory (must contain package.json).")] = Path("."),
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Install missing dependencies without asking.")] = False,
) -> None:
    """Check a project and install what Regress needs (Vitest, StrykerJS)."""
    try:
        root = find_project_root(path.expanduser().resolve())
        console.print(f"[bold]regress init[/] [dim]{escape(str(root))}[/]\n")
        installs = planned_installs(root)
        for name in REQUIRED_PACKAGES:
            version = package_version(root, name)
            mark = "[green]✓[/]" if version else "[yellow]✗[/]"
            console.print(f"  {mark} {name} [dim]{version or 'missing'}[/]")
        vitest = package_version(root, "vitest")
        if vitest and major(vitest) >= 5:
            console.print(
                f"[yellow]  vitest {vitest} breaks Stryker's Vitest runner; Regress needs {VITEST_INSTALL_SPEC}.[/]"
            )
        if installs:
            command = install_command(root, installs)
            console.print(f"\nMissing: {' '.join(installs)}")
            if not yes and not typer.confirm(f"Run `{' '.join(command)}`?", default=True):
                raise RegressError("Dependencies not installed. Install them yourself, then run `regress init` again.")
            with console.status("[dim]Installing...[/]"):
                result = run_command(command, cwd=root, timeout=600)
            if result.returncode != 0:
                raise ToolError("Installing dependencies failed.", result.output[-3000:])
            console.print("[green]✓[/] Installed")

        regress_dir(root)
        config = root / CONFIG_FILE
        if not config.exists():
            config.write_text(CONFIG_TEMPLATE)
            console.print(f"[green]✓[/] Wrote {CONFIG_FILE}")
        console.print("[green]✓[/] Created .regress/ for run artifacts (git-ignored)")
        if not os.environ.get("OPENAI_API_KEY"):
            console.print("[yellow]![/] OPENAI_API_KEY is not set. Export it or add it to a .env file.")
        console.print("\nNext: [bold]regress run src/<file>.ts[/]")
    except RegressError as error:
        _fail(error)


@app.command()
def models(
    set_model: Annotated[
        str | None, typer.Option("--set", help="Make this your default model (saved in your user config).")
    ] = None,
    ask: Annotated[
        bool | None, typer.Option("--ask/--no-ask", help="Whether `regress run` asks which model to use.")
    ] = None,
    show_all: Annotated[bool, typer.Option("--all", help="Include dated snapshots and every older model.")] = False,
    refresh: Annotated[
        bool, typer.Option("--refresh", help="Fetch the list again instead of using the cache.")
    ] = False,
) -> None:
    """List the latest models and choose your default."""
    try:
        with console.status("[dim]Fetching available models...[/]"):
            catalog = load_catalog(refresh=refresh)
        changes: dict[str, object] = {}
        if set_model is not None:
            if catalog.verified and not catalog.has(set_model):
                hint = catalog.suggestions(set_model)
                suffix = f" Did you mean {', '.join(hint)}?" if hint else " See `regress models --all`."
                raise RegressError(f"{set_model} is not available to your API key.{suffix}")
            changes["model"] = set_model
        if ask is not None:
            changes["ask_model"] = ask
        if changes:
            path = save_user_settings(**changes)
            console.print(f"[green]✓[/] Saved to {escape(str(path))}\n")
        settings = load_settings(_project_root_or_none(Path.cwd()))
        render_models(console, catalog, settings, user_config_path(), show_all)
        if set_model is not None and settings.model != set_model:
            console.print(f"[yellow]! {settings.model_source} overrides your default here.[/]")
    except RegressError as error:
        _fail(error)


@app.command()
def serve(
    path: Annotated[Path, typer.Argument(help="Project directory (must contain package.json).")] = Path("."),
    host: Annotated[
        str, typer.Option(help="Address to listen on. The API has no authentication, so keep it local.")
    ] = "127.0.0.1",
    port: Annotated[int, typer.Option(help="Port to listen on.")] = 8765,
    origins: Annotated[
        list[str] | None,
        typer.Option(
            "--origin",
            help="Browser origin allowed to call the API (repeatable). Defaults to Vite's dev server on port 5173.",
        ),
    ] = None,
) -> None:
    """Serve the HTTP API for the web app."""
    import uvicorn

    from regress.api import DEV_ORIGINS, LOCAL_HOSTS, create_app

    url_host = f"[{host}]" if ":" in host else host  # an IPv6 address
    try:
        root = find_project_root(path.expanduser().resolve())
        api = create_app(root, origins=origins or DEV_ORIGINS, hosts=[*LOCAL_HOSTS, url_host])
    except RegressError as error:
        _fail(error)
    base = f"http://{url_host}:{port}/api"
    console.print(f"[bold]regress serve[/] [dim]{escape(str(root))}[/]\n")
    console.print(f"  API   {base}")
    console.print(f"  Docs  {base}/docs")
    console.print(f"[dim]  Allowed browser origins: {', '.join(origins or DEV_ORIGINS)}[/]\n")
    if url_host not in LOCAL_HOSTS:
        console.print(
            "[yellow]! The API has no authentication: anyone who can reach it can read your code "
            "and spend your OpenAI credits.[/]\n"
        )
    uvicorn.run(api, host=host, port=port, timeout_graceful_shutdown=3)


@app.command("eval")
def eval_command(
    examples: Annotated[Path, typer.Argument(help="Examples project with a hidden-bugs/ directory.")] = Path(
        "examples"
    ),
    modules: Annotated[
        list[str] | None, typer.Option("--module", "-m", help="Only evaluate these modules (repeatable).")
    ] = None,
    oracle: Annotated[
        bool,
        typer.Option("--oracle", help="Validate hidden bugs with the reference tests instead of running the model."),
    ] = False,
    rounds: Annotated[int | None, typer.Option(min=0, max=5, help="Improvement rounds per module.")] = None,
    model: Annotated[str | None, typer.Option(help="OpenAI model. Skips the model question.")] = None,
    runner: RunnerOption = None,
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False,
    yes: YesOption = False,
) -> None:
    """Compare existing tests, one-shot AI tests, and Regress on example modules with hidden bugs."""
    try:
        examples = examples.expanduser().resolve()
        settings = load_settings(examples, rounds=rounds, model=model, runner=runner)
        suites = load_suites(examples, modules)

        def announce(suite) -> None:
            console.rule(f"[bold]{suite.name}[/]", align="left")

        if oracle:
            with console.status("[dim]Checking hidden bugs against the oracle tests...[/]"):
                result = evaluate_oracles(examples, suites, settings.runner, on_module=None)
        else:
            require_api_key()
            chosen = _pick_model(settings, yes)
            options = RunOptions(
                rounds=settings.rounds,
                max_repairs=settings.max_repairs,
                max_mutants=settings.max_mutants,
                vitest_timeout=settings.vitest_timeout,
                stryker_timeout=settings.stryker_timeout,
            )
            result = evaluate_regress(
                examples,
                suites,
                lambda: OpenAILLM(chosen, settings.reasoning_effort, idle_timeout=settings.llm_timeout),
                options,
                ConsoleReporter(console, verbose),
                settings.runner,
                on_module=announce,
            )
        _render_eval(result)
    except RegressError as error:
        _fail(error)
    except KeyboardInterrupt:
        err_console.print(
            "\n[yellow]Interrupted.[/] Results so far, including the interrupted module's prompts and "
            "responses, are saved under examples/.regress/eval/."
        )
        raise typer.Exit(130) from None


def _render_eval(result: EvalResult) -> None:
    labels = result.stage_labels
    table = Table(title=f"Regress evaluation ({result.mode})", header_style="dim", title_style="bold")
    table.add_column("Module")
    for label in labels:
        table.add_column(f"{label}\nscore", justify="right")
        table.add_column("bugs", justify="right")
    for module in result.modules:
        cells: list[str] = []
        for label in labels:
            stage = next((s for s in module.stages if s.label == label), None)
            cells += (
                ["—", "—"] if stage is None else [format_score(stage.score), f"{len(stage.caught)}/{stage.bugs_total}"]
            )
        name = escape(module.module) + (" [red](error)[/]" if module.error else "")
        table.add_row(name, *cells)
    totals: list[str] = []
    for label in labels:
        mean, caught, total = result.totals(label)
        totals += [f"[bold]{format_score(mean)}[/]", f"[bold]{caught}/{total}[/]"]
    table.add_row("[bold]All modules[/]", *totals, end_section=True)
    console.print()
    console.print(table)
    for module in result.modules:
        if module.error:
            console.print(f"[red]{escape(module.module)}:[/] {escape(module.error)}")
        for stage in module.stages:
            if result.mode == "oracle" and stage.label == "Oracle" and stage.missed:
                missed = ", ".join(stage.missed)
                console.print(f"[yellow]{escape(module.module)}: oracle misses hidden bug(s) {escape(missed)}[/]")
    if result.output_dir:
        console.print(f"\n[dim]Results saved to {escape(result.output_dir)} (eval.md, eval.json)[/]")
