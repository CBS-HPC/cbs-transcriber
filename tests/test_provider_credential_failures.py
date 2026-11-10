"""External credential failures are not SQLite transactions; never use a real keyring."""

import sqlite3

import pytest

from cbs_transcriber import providers
from cbs_transcriber.store import Store


@pytest.fixture
def fake_keyring(monkeypatch):
    saved = {}
    monkeypatch.setattr(
        providers.keyring, "get_password", lambda service, account: saved.get(account)
    )
    monkeypatch.setattr(
        providers.keyring,
        "set_password",
        lambda service, account, value: saved.update({account: value}),
    )

    def delete(service, account):
        if account not in saved:
            raise providers.keyring.errors.PasswordDeleteError("synthetic missing key")
        del saved[account]

    monkeypatch.setattr(providers.keyring, "delete_password", delete)
    return saved


def test_failed_persistent_forget_reports_failure_and_retains_session(fake_keyring, monkeypatch):
    scope = "https://synthetic.invalid/v1"
    credentials = providers.Credentials()
    credentials.set(scope, "synthetic-old-key", True)

    def denied(*args):
        raise providers.keyring.errors.PasswordDeleteError("synthetic-private-error")

    monkeypatch.setattr(providers.keyring, "delete_password", denied)
    with pytest.raises(ValueError, match="OS credential store is unavailable") as error:
        credentials.set(scope, "", True)
    assert "synthetic-private-error" not in str(error.value)
    assert credentials.get(scope) == "synthetic-old-key"
    assert providers.Credentials().get(scope) == "synthetic-old-key"


def test_persistent_forget_is_idempotent_when_key_is_absent(fake_keyring):
    scope = "https://synthetic.invalid/v1"
    credentials = providers.Credentials()
    credentials.set(scope, "synthetic-session-key", False)
    credentials.set(scope, "", True)
    assert credentials.get(scope) == ""
    assert providers.Credentials().get(scope) == ""


def test_failed_persistent_forget_requires_observable_absence(fake_keyring, monkeypatch):
    scope = "https://synthetic.invalid/v1"
    credentials = providers.Credentials()
    credentials.set(scope, "synthetic-old-key", True)

    def denied(*args):
        raise providers.keyring.errors.PasswordDeleteError("synthetic delete refusal")

    def unreadable(*args):
        raise providers.keyring.errors.KeyringError("synthetic read refusal")

    monkeypatch.setattr(providers.keyring, "delete_password", denied)
    monkeypatch.setattr(providers.keyring, "get_password", unreadable)
    with pytest.raises(ValueError, match="OS credential store is unavailable") as error:
        credentials.set(scope, "", True)
    assert "synthetic" not in str(error.value)
    assert credentials.get(scope) == "synthetic-old-key"


def test_successful_persistent_forget_clears_current_and_next_session(fake_keyring):
    scope = "https://synthetic.invalid/v1"
    credentials = providers.Credentials()
    credentials.set(scope, "synthetic-old-key", True)
    credentials.set(scope, "", True)
    assert credentials.get(scope) == ""
    assert providers.Credentials().get(scope) == ""


def synthetic_profile(tmp_path):
    store = Store(tmp_path / "library")
    credentials = providers.Credentials()
    settings = providers.Settings(
        provider_type="ucloud-vllm",
        base_url="https://synthetic.invalid/v1",
        model="org/model",
        ucloud={"server": "https://cloud.invalid", "project_id": "synthetic-project"},
    )
    saved = providers.save_profile(store, credentials, "Synthetic", settings)
    scopes = list(providers.setup_key_scopes(settings).values()) + [settings.base_url]
    for scope in scopes:
        credentials.set(scope, "synthetic-old", True)
    return store, credentials, settings, saved, scopes


@pytest.mark.parametrize("failed_write", [1, 2, 3])
@pytest.mark.parametrize("write_before_failure", [False, True])
def test_later_credential_failure_rolls_back_sqlite_but_keeps_earlier_external_writes(
    tmp_path, fake_keyring, monkeypatch, failed_write, write_before_failure
):
    store, credentials, settings, saved, scopes = synthetic_profile(tmp_path)
    rows = store.all("SELECT * FROM providers ORDER BY id")
    write = providers.keyring.set_password
    calls = []

    def fail(service, account, value):
        calls.append(account)
        if len(calls) == failed_write:
            if write_before_failure:
                write(service, account, value)
            raise providers.keyring.errors.KeyringError("synthetic-private-error")
        write(service, account, value)

    monkeypatch.setattr(providers.keyring, "set_password", fail)
    with pytest.raises(ValueError, match="OS credential store is unavailable") as error:
        providers.save_profile(
            store,
            credentials,
            "Changed",
            settings.model_copy(update={"language": "en"}),
            api_key="synthetic-new",
            identifier=saved["id"],
            expected_version=saved["version"],
            ucloud_token="synthetic-new",
            hf_token="synthetic-new",
        )
    assert "synthetic-private-error" not in str(error.value)
    assert store.all("SELECT * FROM providers ORDER BY id") == rows
    expected = ["synthetic-new"] * (failed_write - 1) + ["synthetic-old"] * (4 - failed_write)
    assert [credentials.get(scope) for scope in scopes] == expected
    external_writes = failed_write if write_before_failure else failed_write - 1
    assert [providers.Credentials().get(scope) for scope in scopes] == (
        ["synthetic-new"] * external_writes + ["synthetic-old"] * (3 - external_writes)
    )

    # Retry the same still-current profile version explicitly; no compensation is guessed.
    monkeypatch.setattr(providers.keyring, "set_password", write)
    retried = providers.save_profile(
        store,
        credentials,
        "Changed",
        settings.model_copy(update={"language": "en"}),
        api_key="synthetic-new",
        identifier=saved["id"],
        expected_version=saved["version"],
        ucloud_token="synthetic-new",
        hf_token="synthetic-new",
    )
    assert retried["name"] == "Changed" and retried["language"] == "en"
    assert [credentials.get(scope) for scope in scopes] == ["synthetic-new"] * 3


@pytest.mark.parametrize("remember", [False, True])
def test_sqlite_commit_failure_does_not_undo_credentials_and_explicit_retry_recovers(
    tmp_path, fake_keyring, remember
):
    store, credentials, settings, saved, scopes = synthetic_profile(tmp_path)
    rows = store.all("SELECT * FROM providers ORDER BY id")
    with store.connection() as db:
        db.executescript(
            "CREATE TABLE synthetic_commit_failure (provider_id TEXT REFERENCES providers(id) "
            "DEFERRABLE INITIALLY DEFERRED);"
            "CREATE TRIGGER synthetic_commit_failure AFTER UPDATE ON providers BEGIN "
            "INSERT INTO synthetic_commit_failure VALUES ('absent-synthetic-provider'); END;"
        )
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY constraint failed"):
        providers.save_profile(
            store,
            credentials,
            "Changed",
            settings.model_copy(update={"language": "en"}),
            api_key="synthetic-new",
            remember=remember,
            identifier=saved["id"],
            expected_version=saved["version"],
            ucloud_token="synthetic-new",
            hf_token="synthetic-new",
        )
    assert store.all("SELECT * FROM providers ORDER BY id") == rows
    assert [credentials.get(scope) for scope in scopes] == ["synthetic-new"] * 3
    assert [providers.Credentials().get(scope) for scope in scopes] == [
        "synthetic-new" if remember else "synthetic-old"
    ] * 3
    store.execute("DROP TRIGGER synthetic_commit_failure")
    retried = providers.save_profile(
        store,
        credentials,
        "Changed",
        settings.model_copy(update={"language": "en"}),
        identifier=saved["id"],
        expected_version=saved["version"],
    )
    assert retried["name"] == "Changed" and retried["language"] == "en"
    assert retried["has_key"] and retried["has_ucloud_token"] and retried["has_hf_token"]
