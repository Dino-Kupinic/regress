import { QueryClient } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import type { ModelList, Provider } from "./api";
import { refreshModels } from "./models";

const clients: QueryClient[] = [];
function client() {
  const value = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  clients.push(value);
  return value;
}

function catalog(provider: Provider, id = "selected"): ModelList {
  return {
    provider,
    models: [{ id, created_date: "", newest: true, default: false }],
    source: "api",
    description: "Models from the provider",
    note: null,
    verified: true,
    default_model: null,
    model_source: "default",
    hidden: 0,
  };
}

function respond(data: ModelList) {
  return new Response(JSON.stringify(data), {
    headers: { "Content-Type": "application/json" },
  });
}

afterEach(() => {
  for (const value of clients.splice(0)) value.clear();
  vi.unstubAllGlobals();
});

it("requests a fresh short list and updates only the requested provider", async () => {
  const cache = client();
  const openai = catalog("openai");
  const anthropic = catalog("anthropic", "claude");
  cache.setQueryData(["models", "openai"], openai);
  cache.setQueryData(["models", "anthropic"], anthropic);
  const fresh = catalog("openai", "new-model");
  const fetch = vi.fn().mockResolvedValue(respond(fresh));
  vi.stubGlobal("fetch", fetch);

  await refreshModels(cache, "openai");

  expect(fetch.mock.calls[0][0]).toBe(
    "/api/models?all=false&refresh=true&provider=openai",
  );
  expect(cache.getQueryData(["models", "openai"])).toEqual(fresh);
  expect(cache.getQueryData(["models", "anthropic"])).toEqual(anthropic);
});

it("keeps the old list when refresh returns a fallback and exposes its note", async () => {
  const cache = client();
  const previous = catalog("anthropic", "selected");
  cache.setQueryData(["models", "anthropic"], previous);
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(
      respond({
        ...catalog("anthropic", "offline-model"),
        source: "sdk",
        note: "could not reach the Anthropic API",
      }),
    ),
  );

  await refreshModels(cache, "anthropic");

  expect(cache.getQueryData(["models", "anthropic"])).toEqual({
    ...previous,
    note: "could not reach the Anthropic API",
  });
});

it("keeps a returned fallback when no useful list exists", async () => {
  const cache = client();
  const fallback = { ...catalog("openai"), note: "OPENAI_API_KEY is not set" };
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(respond(fallback)));

  await refreshModels(cache, "openai");

  expect(cache.getQueryData(["models", "openai"])).toEqual(fallback);
});

it("preserves the cached list when the request fails", async () => {
  const cache = client();
  const previous = catalog("openai");
  cache.setQueryData(["models", "openai"], previous);
  vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")));

  await expect(refreshModels(cache, "openai")).rejects.toThrow("offline");

  expect(cache.getQueryData(["models", "openai"])).toEqual(previous);
});

it("cannot overwrite another provider after the selection changes", async () => {
  const cache = client();
  const anthropic = catalog("anthropic", "claude");
  cache.setQueryData(["models", "anthropic"], anthropic);
  let finish!: (response: Response) => void;
  vi.stubGlobal(
    "fetch",
    vi.fn(
      () =>
        new Promise<Response>((resolve) => {
          finish = resolve;
        }),
    ),
  );
  const refresh = refreshModels(cache, "openai");
  await vi.waitFor(() => expect(finish).toBeTypeOf("function"));
  const fresh = catalog("openai", "new-model");

  finish(respond(fresh));
  await refresh;

  expect(cache.getQueryData(["models", "anthropic"])).toEqual(anthropic);
  expect(cache.getQueryData(["models", "openai"])).toEqual(fresh);
});

it("discards a cached request that finishes after the forced refresh", async () => {
  const cache = client();
  let finish!: (value: ModelList) => void;
  const ordinary = cache
    .fetchQuery({
      queryKey: ["models", "openai"],
      queryFn: () =>
        new Promise<ModelList>((resolve) => {
          finish = resolve;
        }),
    })
    .catch(() => undefined);
  const fresh = catalog("openai", "new-model");
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(respond(fresh)));

  await refreshModels(cache, "openai");
  finish(catalog("openai", "stale-model"));
  await ordinary;

  expect(cache.getQueryData(["models", "openai"])).toEqual(fresh);
});
