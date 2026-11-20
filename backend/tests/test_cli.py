import pytest

from release_assistant.api.login import PasswordVerifier
from release_assistant.api.main import hash_password


def answers(monkeypatch: pytest.MonkeyPatch, *typed: str) -> None:
    queue = iter(typed)
    monkeypatch.setattr("getpass.getpass", lambda _prompt="": next(queue))


def test_hash_password_prints_a_hash_that_verifies(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    answers(monkeypatch, "a long enough password", "a long enough password")

    hash_password()

    printed = capsys.readouterr().out.strip()
    assert printed.startswith("$argon2id$")
    assert PasswordVerifier("alice", printed).verify("alice", "a long enough password")


def test_a_short_password_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    answers(monkeypatch, "short")

    with pytest.raises(SystemExit, match="at least 12"):
        hash_password()


def test_a_mismatched_repeat_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    answers(monkeypatch, "a long enough password", "a different password")

    with pytest.raises(SystemExit, match="do not match"):
        hash_password()
