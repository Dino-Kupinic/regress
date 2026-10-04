from __future__ import annotations

import dataclasses
import sys
from pathlib import Path
from typing import Annotated, Literal, NoReturn

import typer
from dotenv import find_dotenv, load_dotenv
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from regress.batch import Target, resolve_targets, run_batch
from regress.catalog import load_catalog
from regress.changes import code_ranges, format_ranges
from regress.check import render_markdown, run_check
from regress.config import (
    CONFIG_FILE,
    CONFIG_TEMPLATE,
    Settings,
    load_settings,
    save_user_settings,
    user_config_path,
)
from regress.errors import ProjectError, RegressError, ToolError
from regress.evaluation import COUNTING_NOTE, EvalResult, evaluate_oracles, evaluate_regress, load_suites
from regress.files import atomic_write_text
from regress.llm import create_llm, require_api_key
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
from regress.providers import PROVIDERS, missing_key, qualified_model, split_model
from regress.store import RunStore, regress_dir
from regress.ui import (
    ConsoleReporter,
    choose_model,
    format_score,
    render_check,
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


def _catalog(settings: Settings, refresh: bool = False):
    return load_catalog(
        refresh, provider=settings.provider, base_url=settings.base_url, api_key_env=settings.api_key_env
    )


def _pick_model(settings: Settings, yes: bool) -> str:
    """The model for this run: asked interactively unless it was given, remembered, or we can't ask.

    A typed `provider:model` switches the provider of `settings` for this run.
    """
    if yes or settings.model_is_explicit or not settings.ask_model or not _interactive():
        if settings.model is None:
            raise ProjectError(
                f"No model chosen for provider {settings.provider}. Pass --model or set model in regress.toml."
            )
        return settings.model
    with console.status("[dim]Fetching available models...[/]"):
        catalog = _catalog(settings)
    answer, remember = choose_model(console, catalog, settings.model)
    prefix, model = split_model(answer)
    if prefix is not None:
        settings.provider = prefix
    if remember:
        path = save_user_settings(model=qualified_model(settings.provider, model), ask_model=False)
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
    sources: Annotated[
        list[Path] | None,
        typer.Argument(
            help="Source files or directories to test, e.g. src/cart.ts or src/. "
            "With --changed, defaults to the current directory.",
            metavar="SOURCE",
            show_default=False,
        ),
    ] = None,
    changed: Annotated[
        str | None,
        typer.Option(
            "--changed",
            metavar="REF",
            help="Only source files changed since this git ref (e.g. main), mutating just the changed lines.",
        ),
    ] = None,
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
    """Generate tests for each source file, then improve them from surviving mutants."""
    if not sources and changed is None:
        _fail(ProjectError("Give a source file or directory to test, or --changed <ref> for changed files."))
    paths = sources or [Path.cwd()]
    try:
        root, targets = resolve_targets(paths, changed)
        single = len(paths) == 1 and not paths[0].is_dir()
        if test is not None and not (single and len(targets) == 1):
            raise ProjectError("--test works only with a single source file.")
        if not targets:
            where = ", ".join(escape(str(p)) for p in paths)
            if changed is not None:
                console.print(f"No source files changed since {escape(changed)} in {where}.")
                return
            raise ProjectError(f"No source files to test in {where}.")
        settings = load_settings(root, rounds=rounds, model=model, runner=runner)
        if single and len(targets) == 1:
            target = targets[0]
            lines = code_ranges(target.source.read_text(encoding="utf-8"), target.lines) if target.lines else []
            if target.lines is not None and not lines:
                console.print("Only comments, imports or blank lines changed; nothing to test.")
                return
            project = dataclasses.replace(load_project(target.source, test, settings.runner), mutate_lines=tuple(lines))
        elif not yes and _interactive():
            _confirm_targets(root, targets, changed)
        chosen = _pick_model(settings, yes)
        require_api_key(settings.provider, settings.api_key_env)
        options = RunOptions(
            rounds=settings.rounds,
            max_repairs=settings.max_repairs,
            max_mutants=settings.max_mutants,
            measure_baseline=baseline,
            generate=generate,
            vitest_timeout=settings.vitest_timeout,
            stryker_timeout=settings.stryker_timeout,
        )
        with create_llm(settings, chosen) as llm:
            if single and len(targets) == 1:
                Pipeline(project, llm, options, ConsoleReporter(console, verbose)).run()
                return
            console.print("[bold]regress[/]\n")
            reporter = ConsoleReporter(console, verbose, banner=False)
            result = run_batch(root, targets, llm, options, settings.runner, reporter, changed_since=changed)
        if result.failed:
            raise typer.Exit(1)
    except RegressError as error:
        _fail(error)
    except KeyboardInterrupt:
        err_console.print("\n[yellow]Interrupted.[/] The test file was restored.")
        raise typer.Exit(130) from None


def _confirm_targets(root: Path, targets: list[Target], changed: str | None) -> None:
    """List the files a multi-file run will cover, since each one costs model calls, and ask to go on."""
    since = f" changed since {escape(changed)}" if changed else ""
    console.print(f"[bold]{len(targets)} source {'file' if len(targets) == 1 else 'files'}[/]{since}:")
    for target in targets[:20]:
        lines = f" [dim]lines {format_ranges(target.lines)}[/]" if target.lines else ""
        console.print(f"  {escape(target.source.relative_to(root).as_posix())}{lines}")
    if len(targets) > 20:
        console.print(f"  [dim]...and {len(targets) - 20} more[/]")
    if not typer.confirm("Run Regress on each of them?", default=True):
        raise typer.Exit(0)
    console.print()


@app.command()
def check(
    sources: Annotated[
        list[Path] | None,
        typer.Argument(
            help="Source files or directories to check. With --changed, defaults to the current directory.",
            metavar="SOURCE",
            show_default=False,
        ),
    ] = None,
    changed: Annotated[
        str | None,
        typer.Option("--changed", metavar="REF", help="Only source files changed since this git ref, and their lines."),
    ] = None,
    fail_under: Annotated[
        float | None,
        typer.Option(
            min=0, max=100, help="Exit with code 1 when the combined mutation score is below this percentage."
        ),
    ] = None,
    as_json: Annotated[bool, typer.Option("--json", help="Print the check report as JSON, and nothing else.")] = False,
    markdown: Annotated[
        Path | None, typer.Option(help="Also write a pull request comment in Markdown to this file.")
    ] = None,
    mutants: Annotated[int, typer.Option(min=0, help="Surviving mutants to list per file.")] = 5,
    runner: RunnerOption = None,
) -> None:
    """Mutation-test the existing tests, without the model. A quality gate for CI."""
    if not sources and changed is None:
        _fail(ProjectError("Give a source file or directory to check, or --changed <ref> for changed files."))
    paths = sources or [Path.cwd()]
    try:
        root, targets = resolve_targets(paths, changed)
        settings = load_settings(root, runner=runner, fail_under=fail_under)
        if not targets and changed is None:
            raise ProjectError(f"No source files to check in {', '.join(escape(str(p)) for p in paths)}.")
        result, directory = run_check(
            root,
            targets,
            settings.runner,
            None if as_json else ConsoleReporter(console),
            changed_since=changed,
            fail_under=settings.fail_under,
            max_survivors=max(mutants, 10),
            vitest_timeout=settings.vitest_timeout,
            stryker_timeout=settings.stryker_timeout,
        )
        if markdown is not None:
            atomic_write_text(markdown.expanduser().resolve(), render_markdown(result, mutants))
        if as_json:
            typer.echo(result.model_dump_json(indent=2))
        else:
            render_check(result, directory, console, mutants)
        if not result.passed:
            raise typer.Exit(1)
    except RegressError as error:
        _fail(error)
    except KeyboardInterrupt:
        err_console.print("\n[yellow]Interrupted.[/]")
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
            atomic_write_text(config, CONFIG_TEMPLATE)
            console.print(f"[green]✓[/] Wrote {CONFIG_FILE}")
        console.print("[green]✓[/] Created .regress/ for run artifacts (git-ignored)")
        settings = load_settings(root)
        missing = missing_key(settings.provider, settings.api_key_env)
        if missing:
            console.print(f"[yellow]![/] {escape(missing)}")
        console.print("\nNext: [bold]regress run src/<file>.ts[/]")
    except RegressError as error:
        _fail(error)


@app.command()
def models(
    set_model: Annotated[
        str | None,
        typer.Option(
            "--set", help="Make this your default model (saved in your user config). provider:model switches provider."
        ),
    ] = None,
    provider_name: Annotated[
        str | None,
        typer.Option("--provider", help="List this provider's models: openai, anthropic or openai-compatible."),
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
        root = _project_root_or_none(Path.cwd())
        prefix, model_id = split_model(set_model) if set_model is not None else (None, None)
        if provider_name is not None and provider_name not in PROVIDERS:
            raise RegressError(f"Unknown provider {provider_name}. Expected one of: {', '.join(PROVIDERS)}.")
        settings = load_settings(root, provider=prefix or provider_name)
        with console.status("[dim]Fetching available models...[/]"):
            catalog = _catalog(settings, refresh)
        changes: dict[str, object] = {}
        if model_id is not None:
            if catalog.verified and not catalog.has(model_id):
                hint = catalog.suggestions(model_id)
                suffix = f" Did you mean {', '.join(hint)}?" if hint else " See `regress models --all`."
                raise RegressError(f"{model_id} is not available to your API key.{suffix}")
            changes["model"] = qualified_model(settings.provider, model_id)
        if ask is not None:
            changes["ask_model"] = ask
        if changes:
            path = save_user_settings(**changes)
            console.print(f"[green]✓[/] Saved to {escape(str(path))}\n")
        shown = load_settings(root, provider=provider_name) if provider_name else load_settings(root)
        render_models(console, catalog, shown, user_config_path(), show_all)
        if model_id is not None and (shown.provider, shown.model) != (settings.provider, model_id):
            console.print(f"[yellow]! {shown.model_source} overrides your default here.[/]")
    except RegressError as error:
        _fail(error)


@app.command()
def serve(
    path: Annotated[Path, typer.Argument(help="Project directory (must contain package.json).")] = Path("."),
    host: Annotated[
        str, typer.Option(help="Address to listen on. The API has no authentication, so keep it local.")
    ] = "127.0.0.1",
    port: Annotated[int, typer.Option(min=1, max=65535, help="Port to listen on.")] = 8765,
    allow_remote: Annotated[
        bool, typer.Option(help="Allow binding the unauthenticated API beyond loopback. Requires a trusted network.")
    ] = False,
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
    if url_host not in LOCAL_HOSTS and not allow_remote:
        _fail(ProjectError("Remote binding requires --allow-remote. For hosting, use the authenticated Nginx proxy."))
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
    uvicorn.run(api, host=host, port=port, workers=1, timeout_graceful_shutdown=15, server_header=False)


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
            chosen = _pick_model(settings, yes)
            require_api_key(settings.provider, settings.api_key_env)
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
                lambda: create_llm(settings, chosen),
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
        marker = " [red](error)[/]" if module.error else " [yellow]*[/]" if module.note else ""
        table.add_row(escape(module.module) + marker, *cells)
    totals: list[str] = []
    for total in result.totals:
        totals += [f"[bold]{format_score(total.score)}[/]", f"[bold]{total.caught}/{total.bugs_total}[/]"]
    table.add_row(f"[bold]{result.totals_label}[/]", *totals, end_section=True)
    console.print()
    console.print(table)
    for module in result.modules:
        if module.error:
            console.print(f"[red]{escape(module.module)}:[/] {escape(module.error)}")
        if module.note:
            console.print(f"[yellow]* {escape(module.module)}:[/] {escape(module.note)}")
        for stage in module.stages:
            if result.mode == "oracle" and stage.label == "Oracle" and stage.missed:
                missed = ", ".join(stage.missed)
                console.print(f"[yellow]{escape(module.module)}: oracle misses hidden bug(s) {escape(missed)}[/]")
    if len(result.counted_modules) < len(result.modules):
        console.print(f"[dim]{COUNTING_NOTE}[/]")
    if result.output_dir:
        console.print(f"\n[dim]Results saved to {escape(result.output_dir)} (eval.md, eval.json)[/]")
