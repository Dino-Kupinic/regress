"""Model catalog, user config, the model picker, and `regress models`."""

from __future__ import annotations

import json
import time
from datetime import date
from types import SimpleNamespace

import openai
import pytest
from rich.console import Console
from typer.testing import CliRunner

from regress import cli
from regress.catalog import (
    CACHE_TTL_SECONDS,
    Catalog,
    ModelInfo,
    is_snapshot,
    is_text_model,
    load_catalog,
    sdk_models,
    usable,
)
from regress.config import DEFAULT_MODEL, load_settings, save_user_settings, user_cache_dir, user_config_path
from regress.errors import ProjectError
from regress.ui import choose_model, format_duration

API_MODELS = [
    ("gpt-6-sol", 300, None),
    ("gpt-6-sol-2026-09-01", 290, None),
    ("gpt-5.5", 200, None),
    ("gpt-4o-mini", 100, "2026-12-01"),
    ("gpt-3.5-turbo", 50, None),
    ("o3", 150, "2020-01-01"),
    ("text-embedding-3-large", 400, None),
    ("gpt-realtime", 350, None),
    ("gpt-live-1", 360, None),
    ("ft:gpt-5.5:acme::abc", 250, None),
]


class FakeModels:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.calls = 0

    def list(self):
        self.calls += 1
        if self.fail:
            raise openai.APIConnectionError(request=None)  # type: ignore[arg-type]
        return [SimpleNamespace(id=i, created=c, shutdown_date=d) for i, c, d in API_MODELS]


def fake_client(fail: bool = False) -> SimpleNamespace:
    return SimpleNamespace(models=FakeModels(fail))


# --- catalog ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("model_id", "expected"),
    [
        ("gpt-6-sol", True),
        ("gpt-5.3-codex", True),
        ("o3", True),
        ("o4-mini", True),
        ("gpt-4o", True),
        ("gpt-4.1-mini", True),
        ("ft:gpt-5.5:acme::abc", True),
        ("gpt-4", False),
        ("gpt-4-turbo", False),
        ("gpt-3.5-turbo", False),
        ("gpt-realtime-2", False),
        ("gpt-live-1", False),
        ("gpt-4o-mini-tts", False),
        ("gpt-image-2", False),
        ("text-embedding-3-small", False),
        ("omni-moderation-latest", False),
        ("whisper-1", False),
    ],
)
def test_only_text_models_are_listed(model_id, expected):
    assert is_text_model(model_id) is expected


def test_snapshots_are_recognized():
    assert is_snapshot("gpt-5.5-2026-04-23")
    assert is_snapshot("ft:gpt-5.5:acme::abc")
    assert not is_snapshot("gpt-5.5")


def test_usable_sorts_newest_first_and_drops_shut_down_models():
    models = [ModelInfo(i, c, d) for i, c, d in API_MODELS]
    ids = [m.id for m in usable(models, today=date(2026, 9, 26))]
    assert ids == ["gpt-6-sol", "gpt-6-sol-2026-09-01", "ft:gpt-5.5:acme::abc", "gpt-5.5", "gpt-4o-mini"]


def test_catalog_lists_aliases_but_accepts_any_model_the_key_has():
    catalog = load_catalog(client=fake_client())
    assert catalog.source == "api"
    assert [m.id for m in catalog.latest()] == ["gpt-6-sol", "gpt-5.5", "gpt-4o-mini"]
    assert catalog.has("gpt-live-1")  # hidden from the list, but a real model
    assert not catalog.has("gpt-9")
    assert "gpt-6-sol" in catalog.suggestions("gpt-6-soll")


def test_catalog_is_cached_for_a_day():
    client = fake_client()
    load_catalog(client=client)
    cached = load_catalog(client=client)
    assert cached.source == "cache"
    assert cached.has("gpt-live-1")
    assert client.models.calls == 1

    stale = json.loads((user_cache_dir() / "models.json").read_text())
    stale["fetched_at"] = time.time() - CACHE_TTL_SECONDS - 1
    (user_cache_dir() / "models.json").write_text(json.dumps(stale))
    assert load_catalog(client=client).source == "api"
    assert load_catalog(refresh=True, client=client).source == "api"
    assert client.models.calls == 3


def test_falls_back_to_stale_cache_then_sdk_when_the_api_fails():
    fallback = load_catalog(client=fake_client(fail=True))
    assert fallback.source == "sdk"
    assert "could not reach" in (fallback.note or "")
    assert fallback.latest()

    load_catalog(client=fake_client())
    stale = json.loads((user_cache_dir() / "models.json").read_text())
    stale["fetched_at"] = 0
    (user_cache_dir() / "models.json").write_text(json.dumps(stale))
    catalog = load_catalog(client=fake_client(fail=True))
    assert catalog.source == "cache"
    assert catalog.note


def test_without_an_api_key_the_sdk_list_is_used():
    catalog = load_catalog()
    assert catalog.source == "sdk"
    assert not catalog.verified
    assert catalog.note == "OPENAI_API_KEY is not set"
    assert all(is_text_model(m.id) for m in sdk_models())


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("fetched_at", float("nan")),
        ("fetched_at", float("inf")),
        ("fetched_at", "tomorrow"),
        ("fetched_at", True),
        ("fetched_at", 10**1000),
        ("fetched_at", time.time() + 86400),
        ("models", [{"id": 123}]),
        ("models", [{"id": "gpt-test", "created": "bad"}]),
        ("models", [{"id": "gpt-test", "created": 10**20}]),
        ("models", [{"id": "gpt-test", "shutdown_date": 123}]),
        ("models", [{"id": "gpt-test", "shutdown_date": "bad"}]),
        ("all_ids", "gpt-test"),
        ("all_ids", [123]),
    ],
)
def test_malformed_model_cache_falls_back_safely(key, value):
    load_catalog(client=fake_client())
    path = user_cache_dir() / "models.json"
    payload = json.loads(path.read_text())
    payload[key] = value
    path.write_text(json.dumps(payload))
    assert load_catalog(client=fake_client(fail=True)).source == "sdk"


def test_non_object_model_cache_falls_back_safely():
    path = user_cache_dir() / "models.json"
    path.parent.mkdir(parents=True)
    path.write_text("[]")
    assert load_catalog(client=fake_client(fail=True)).source == "sdk"


def test_cached_models_are_filtered_again_for_retirement():
    client = fake_client()
    load_catalog(client=client)
    path = user_cache_dir() / "models.json"
    payload = json.loads(path.read_text())
    payload["models"][0]["shutdown_date"] = date.today().isoformat()
    path.write_text(json.dumps(payload))
    cached = load_catalog(client=client)
    assert cached.source == "cache"
    assert "gpt-6-sol" not in [model.id for model in cached.models]
    assert client.models.calls == 1


@pytest.mark.parametrize("attribute", ["api_key", "base_url", "organization", "project"])
def test_model_cache_is_scoped_to_current_credentials(attribute):
    client = fake_client()
    setattr(client, attribute, "first")
    load_catalog(client=client)
    assert load_catalog(client=client).source == "cache"
    setattr(client, attribute, "second")
    assert load_catalog(client=client).source == "api"
    assert client.models.calls == 2


def test_owned_catalog_client_is_closed_after_failure(monkeypatch):
    closed = []
    client = fake_client(fail=True)
    client.close = lambda: closed.append(True)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr("regress.catalog.openai.OpenAI", lambda **kwargs: client)
    assert load_catalog().source == "sdk"
    assert closed == [True]


def test_injected_catalog_client_is_not_closed():
    closed = []
    client = fake_client()
    client.close = lambda: closed.append(True)
    load_catalog(client=client)
    assert closed == []


def test_failed_cache_replace_preserves_previous_cache(monkeypatch):
    load_catalog(client=fake_client())
    path = user_cache_dir() / "models.json"
    original = path.read_bytes()

    def fail_replace(*args):
        raise OSError("disk is full")

    monkeypatch.setattr("regress.config.os.replace", fail_replace)
    assert load_catalog(refresh=True, client=fake_client()).source == "api"
    assert path.read_bytes() == original
    assert list(path.parent.iterdir()) == [path]


# --- user config -----------------------------------------------------------------------------


def test_user_config_sits_below_project_env_and_flags(tmp_path, monkeypatch):
    save_user_settings(model="gpt-user", rounds=2)
    settings = load_settings(tmp_path)
    assert (settings.model, settings.rounds, settings.model_source) == ("gpt-user", 2, "user config")

    (tmp_path / "regress.toml").write_text('model = "gpt-project"\n')
    assert load_settings(tmp_path).model_source == "regress.toml"
    assert load_settings(tmp_path).rounds == 2  # other user settings still apply

    monkeypatch.setenv("REGRESS_MODEL", "gpt-env")
    assert load_settings(tmp_path).model_is_explicit
    assert load_settings(tmp_path, model="gpt-flag").model_source == "--model"


def test_saving_user_settings_keeps_existing_keys():
    save_user_settings(model="gpt-6-sol", rounds=2)
    path = save_user_settings(ask_model=False)
    text = path.read_text()
    assert path == user_config_path()
    assert 'model = "gpt-6-sol"' in text and "rounds = 2" in text and "ask_model = false" in text
    assert load_settings(None).ask_model is False


def test_invalid_user_settings_are_not_saved():
    with pytest.raises(Exception, match="rounds"):
        save_user_settings(rounds=99)
    assert not user_config_path().exists()


def test_failed_config_replace_preserves_previous_settings(monkeypatch):
    path = save_user_settings(model="gpt-test", rounds=2)
    original = path.read_bytes()

    def fail_replace(*args):
        raise OSError("disk is full")

    monkeypatch.setattr("regress.config.os.replace", fail_replace)
    with pytest.raises(ProjectError, match="Could not save configuration"):
        save_user_settings(rounds=3)
    assert path.read_bytes() == original
    assert list(path.parent.iterdir()) == [path]


def test_saved_settings_are_normalized():
    save_user_settings(model=" gpt-test ")
    assert load_settings(None).model == "gpt-test"
    assert 'model = "gpt-test"' in user_config_path().read_text()


# --- picker ----------------------------------------------------------------------------------

CATALOG = Catalog(
    [ModelInfo("gpt-6-sol", 300), ModelInfo("gpt-5.5", 200)],
    "api",
    fetched_at=time.time(),
    all_ids=frozenset({"gpt-hidden"}),
)


def scripted(*answers):
    replies = list(answers)
    return lambda *args, **kwargs: replies.pop(0)


def test_picker_accepts_numbers_names_and_the_default():
    console = Console(file=None, quiet=True)
    assert choose_model(console, CATALOG, "gpt-5.5", ask=scripted("1"), confirm=scripted(False)) == ("gpt-6-sol", False)
    assert choose_model(console, CATALOG, "gpt-5.5", ask=scripted("gpt-5.5"), confirm=scripted(True)) == (
        "gpt-5.5",
        True,
    )
    assert (
        choose_model(console, CATALOG, "gpt-5.5", ask=scripted("gpt-hidden"), confirm=scripted(False))[0]
        == "gpt-hidden"
    )


def test_picker_reasks_on_bad_input():
    console = Console(record=True, width=120)
    choice = choose_model(console, CATALOG, "gpt-5.5", ask=scripted("7", "gpt-6-soll", "2"), confirm=scripted(False))
    assert choice == ("gpt-5.5", False)
    output = console.export_text()
    assert "Pick a number from 1 to 2" in output
    assert "gpt-6-soll is not available" in output and "Did you mean gpt-6-sol" in output


def test_picker_always_offers_the_default():
    console = Console(quiet=True)
    assert choose_model(console, CATALOG, "gpt-custom", ask=scripted("3"), confirm=scripted(False))[0] == "gpt-custom"


# --- asking before a run ---------------------------------------------------------------------


@pytest.fixture
def picker_calls(monkeypatch):
    calls = []
    monkeypatch.setattr(cli, "_interactive", lambda: True)
    monkeypatch.setattr(cli, "load_catalog", lambda: CATALOG)

    def fake_choose(console, catalog, default):
        calls.append(default)
        return "gpt-6-sol", True

    monkeypatch.setattr(cli, "choose_model", fake_choose)
    return calls


def test_asks_and_remembers_the_choice(tmp_path, picker_calls):
    assert cli._pick_model(load_settings(tmp_path), yes=False) == "gpt-6-sol"
    assert picker_calls == [DEFAULT_MODEL]

    remembered = load_settings(tmp_path)
    assert (remembered.model, remembered.ask_model) == ("gpt-6-sol", False)
    assert cli._pick_model(remembered, yes=False) == "gpt-6-sol"
    assert picker_calls == [DEFAULT_MODEL]  # not asked again


def test_does_not_ask_when_told_or_when_it_cannot(tmp_path, picker_calls, monkeypatch):
    assert cli._pick_model(load_settings(tmp_path), yes=True) == DEFAULT_MODEL
    assert cli._pick_model(load_settings(tmp_path, model="gpt-5.4"), yes=False) == "gpt-5.4"
    monkeypatch.setenv("REGRESS_MODEL", "gpt-env")
    assert cli._pick_model(load_settings(tmp_path), yes=False) == "gpt-env"
    monkeypatch.delenv("REGRESS_MODEL")
    monkeypatch.setattr(cli, "_interactive", lambda: False)
    assert cli._pick_model(load_settings(tmp_path), yes=False) == DEFAULT_MODEL
    assert picker_calls == []


# --- `regress models` ------------------------------------------------------------------------


@pytest.fixture
def run_models(monkeypatch):
    monkeypatch.setattr(cli, "load_catalog", lambda refresh=False: CATALOG)
    runner = CliRunner()
    return lambda *args: runner.invoke(cli.app, ["models", *args])


def test_models_lists_latest_and_shows_settings(run_models):
    result = run_models()
    assert result.exit_code == 0, result.output
    assert "gpt-6-sol" in result.output and "newest" in result.output
    assert "Default model" in result.output and "built-in default" in result.output
    assert "Ask before each run  yes" in result.output


def test_models_set_and_toggle_asking(run_models):
    result = run_models("--set", "gpt-6-sol", "--no-ask")
    assert result.exit_code == 0, result.output
    settings = load_settings(None)
    assert (settings.model, settings.ask_model, settings.model_source) == ("gpt-6-sol", False, "user config")
    run_models("--ask")
    assert load_settings(None).ask_model is True


def test_models_set_rejects_unknown_models(run_models):
    result = run_models("--set", "gpt-6-soll")
    assert result.exit_code == 1
    assert "Did you mean gpt-6-sol" in result.output
    assert not user_config_path().exists()


def test_durations_are_compact():
    assert [format_duration(s) for s in (0, 9.9, 59, 60, 125)] == ["0s", "9s", "59s", "1m 00s", "2m 05s"]
