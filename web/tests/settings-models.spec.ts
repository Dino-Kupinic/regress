import { expect, type Page, test } from "@playwright/test";
import type { ModelList, Provider, SettingsInfo } from "../app/lib/api";

const settings: SettingsInfo = {
  effective: {
    provider: "openai",
    model: "gpt-selected",
    base_url: null,
    api_key_env: null,
    ask_model: true,
    reasoning_effort: null,
    rounds: 1,
    max_repairs: 2,
    max_mutants: 20,
    runner: "auto",
    llm_timeout: 120,
    vitest_timeout: 120,
    stryker_timeout: 300,
    max_cost: null,
  },
  model_source: "default",
  user_config_path: "/tmp/regress-ui-test-config.toml",
  user_config: {},
  project_config_path: null,
};

function catalog(provider: Provider, ids: string[]): ModelList {
  return {
    provider,
    models: ids.map((id) => ({
      id,
      created_date: "",
      newest: false,
      default: id === settings.effective.model,
    })),
    source: "api",
    description: "Test catalog",
    note: null,
    verified: true,
    default_model: null,
    model_source: "default",
    hidden: 0,
  };
}

async function selectProvider(page: Page, name: string) {
  await page.getByRole("combobox", { name: "Provider", exact: true }).click();
  await page.getByRole("option", { name, exact: true }).click();
}

test.beforeEach(async ({ page }) => {
  await page.route("**/api/settings", (route) =>
    route.fulfill({ json: settings }),
  );
  await page.route("**/api/project", (route) =>
    route.fulfill({
      json: {
        root: "/tmp/regress-ui-test",
        name: "Settings test",
        config_file: null,
        packages: [],
        toolchain: null,
        problems: [],
        ready: true,
        api_key_set: false,
        provider: "openai",
        api_key_name: "OPENAI_API_KEY",
        active_run: null,
        active_batch: null,
      },
    }),
  );
  await page.route("**/api/runs", (route) => route.fulfill({ json: [] }));
});

test("refresh requests a fresh provider list and keeps the selection and draft", async ({
  page,
}) => {
  const requests: URL[] = [];
  await page.route("**/api/models?**", (route) => {
    const url = new URL(route.request().url());
    requests.push(url);
    return route.fulfill({
      json: catalog(
        "openai",
        url.searchParams.get("refresh") === "true"
          ? ["gpt-new"]
          : ["gpt-selected", "gpt-draft"],
      ),
    });
  });
  await page.goto("/settings");
  await page.getByRole("radio", { name: "gpt-draft", exact: true }).click();
  await page.getByRole("spinbutton", { name: "Improvement rounds" }).fill("3");

  await page.getByRole("button", { name: "Refresh models" }).click();

  await expect(
    page.getByRole("radio", { name: "gpt-new", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("radio", { name: "gpt-draft", exact: true }),
  ).toBeChecked();
  await expect(
    page.getByRole("spinbutton", { name: "Improvement rounds" }),
  ).toHaveValue("3");
  expect(requests.map((url) => url.search)).toEqual([
    "?all=false&refresh=false&provider=openai",
    "?all=false&refresh=true&provider=openai",
  ]);
});

test("refresh keeps a useful list and draft on transport and fallback failures", async ({
  page,
}) => {
  let mode: "normal" | "error" | "fallback" = "normal";
  await page.route("**/api/models?**", (route) => {
    if (mode === "error") {
      return route.fulfill({
        status: 503,
        json: { detail: "Provider unavailable" },
      });
    }
    return route.fulfill({
      json:
        mode === "fallback"
          ? {
              ...catalog("openai", ["offline-model"]),
              source: "sdk",
              note: "could not reach the OpenAI API",
            }
          : catalog("openai", ["gpt-selected", "gpt-draft"]),
    });
  });
  await page.goto("/settings");
  await page.getByRole("radio", { name: "gpt-draft", exact: true }).click();
  await page.getByRole("spinbutton", { name: "Improvement rounds" }).fill("3");

  mode = "error";
  await page.getByRole("button", { name: "Refresh models" }).click();
  await expect(
    page.getByText("Could not refresh models", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("radio", { name: "gpt-draft", exact: true }),
  ).toBeChecked();
  await expect(
    page.getByRole("spinbutton", { name: "Improvement rounds" }),
  ).toHaveValue("3");

  mode = "fallback";
  await page.getByRole("button", { name: "Refresh models" }).click();
  await expect(
    page.getByText("could not reach the OpenAI API; Test catalog."),
  ).toBeVisible();
  await expect(
    page.getByRole("radio", { name: "gpt-draft", exact: true }),
  ).toBeChecked();
  await expect(
    page.getByRole("radio", { name: "offline-model", exact: true }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("spinbutton", { name: "Improvement rounds" }),
  ).toHaveValue("3");
});

test("overlapping providers keep separate pending state and cache entries", async ({
  page,
}) => {
  let resolveOpenai!: (value: ModelList) => void;
  let resolveAnthropic!: (value: ModelList) => void;
  const openai = new Promise<ModelList>((resolve) => {
    resolveOpenai = resolve;
  });
  const anthropic = new Promise<ModelList>((resolve) => {
    resolveAnthropic = resolve;
  });
  const refreshes: string[] = [];
  await page.route("**/api/models?**", async (route) => {
    const url = new URL(route.request().url());
    const provider = url.searchParams.get("provider") as Provider;
    if (url.searchParams.get("refresh") === "true") {
      refreshes.push(provider);
      await route.fulfill({
        json: await (provider === "openai" ? openai : anthropic),
      });
    } else {
      await route.fulfill({
        json: catalog(provider, [
          provider === "openai" ? "gpt-selected" : "claude-selected",
        ]),
      });
    }
  });
  await page.goto("/settings");
  const refresh = page.getByRole("button", { name: "Refresh models" });
  await expect(refresh).toBeEnabled();
  await refresh.click();
  await expect.poll(() => refreshes).toEqual(["openai"]);
  await selectProvider(page, "Anthropic");
  await expect(
    page.getByRole("radio", { name: "claude-selected", exact: true }),
  ).toBeVisible();
  await refresh.click();
  await expect.poll(() => refreshes).toEqual(["openai", "anthropic"]);

  await selectProvider(page, "OpenAI");
  await expect(refresh).toBeDisabled();
  resolveOpenai(catalog("openai", ["gpt-new"]));
  await expect(
    page.getByRole("radio", { name: "gpt-new", exact: true }),
  ).toBeVisible();
  await expect(refresh).toBeEnabled();

  await selectProvider(page, "Anthropic");
  await expect(refresh).toBeDisabled();
  await expect(
    page.getByRole("radio", { name: "gpt-new", exact: true }),
  ).toHaveCount(0);
  resolveAnthropic(catalog("anthropic", ["claude-new"]));
  await expect(
    page.getByRole("radio", { name: "claude-new", exact: true }),
  ).toBeVisible();
  await expect(refresh).toBeEnabled();
  await selectProvider(page, "OpenAI");
  await expect(
    page.getByRole("radio", { name: "gpt-new", exact: true }),
  ).toBeVisible();
  expect(refreshes).toEqual(["openai", "anthropic"]);
});

test("overlapping refresh errors stay with their provider until retry succeeds", async ({
  page,
}) => {
  let failOpenai!: () => void;
  let finishAnthropic!: () => void;
  const openai = new Promise<void>((resolve) => {
    failOpenai = resolve;
  });
  const anthropic = new Promise<void>((resolve) => {
    finishAnthropic = resolve;
  });
  const refreshes: string[] = [];
  await page.route("**/api/models?**", async (route) => {
    const url = new URL(route.request().url());
    const provider = url.searchParams.get("provider") as Provider;
    const refresh = url.searchParams.get("refresh") === "true";
    if (refresh) {
      refreshes.push(provider);
      if (
        provider === "openai" &&
        refreshes.filter((item) => item === "openai").length === 1
      ) {
        await openai;
        await route.fulfill({
          status: 503,
          json: { detail: "OpenAI unavailable" },
        });
        return;
      }
      if (provider === "anthropic") await anthropic;
    }
    await route.fulfill({
      json: catalog(provider, [
        provider === "openai"
          ? refresh
            ? "gpt-new"
            : "gpt-selected"
          : "claude-selected",
      ]),
    });
  });
  await page.goto("/settings");
  const refresh = page.getByRole("button", { name: "Refresh models" });
  await expect(refresh).toBeEnabled();
  await refresh.click();
  await expect.poll(() => refreshes).toEqual(["openai"]);
  await selectProvider(page, "Anthropic");
  await expect(refresh).toBeEnabled();
  await refresh.click();
  await expect.poll(() => refreshes).toEqual(["openai", "anthropic"]);

  failOpenai();
  await selectProvider(page, "OpenAI");
  await expect(
    page.getByText("Could not refresh models", { exact: true }),
  ).toBeVisible();
  await expect(page.getByText(/OpenAI unavailable/)).toBeVisible();
  await expect(
    page.getByRole("radio", { name: "gpt-selected", exact: true }),
  ).toBeChecked();
  await selectProvider(page, "Anthropic");
  await expect(
    page.getByText("Could not refresh models", { exact: true }),
  ).toHaveCount(0);
  finishAnthropic();
  await expect(refresh).toBeEnabled();
  await selectProvider(page, "OpenAI");
  await expect(
    page.getByText("Could not refresh models", { exact: true }),
  ).toBeVisible();

  await refresh.click();

  await expect(
    page.getByRole("radio", { name: "gpt-new", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("Could not refresh models", { exact: true }),
  ).toHaveCount(0);
});
