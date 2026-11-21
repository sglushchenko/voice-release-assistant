"""The command line, end to end, in a temporary evaluations directory."""

import json
import shutil
from pathlib import Path

import pytest

from release_assistant.evals.cli import BASELINE_FILE, HISTORY_FILE, main

REPO_DATASETS = Path(__file__).resolve().parents[2] / "evals" / "datasets"


@pytest.fixture
def evals_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A private copy of the datasets, and an empty working directory with no API key in the
    environment, so nothing here can read a real `.env` or reach a paid provider."""
    directory = tmp_path / "evals"
    shutil.copytree(REPO_DATASETS, directory / "datasets")
    workdir = tmp_path / "work"
    workdir.mkdir()
    monkeypatch.chdir(workdir)
    for name in ("RELEASE_ASSISTANT_OPENAI_API_KEY", "RELEASE_ASSISTANT_OPENAI_MODEL"):
        monkeypatch.delenv(name, raising=False)
    return directory


def run(evals_dir: Path, *args: str) -> int:
    return main(["--evals-dir", str(evals_dir), *args])


def run_dirs(evals_dir: Path) -> list[Path]:
    runs = evals_dir / "runs"
    return sorted(runs.iterdir()) if runs.exists() else []


# --- the scripted tier -----------------------------------------------------------------


def test_a_scripted_run_passes_prints_a_report_and_keeps_history_out_of_ci(
    evals_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = run(evals_dir, "run", "--tier", "scripted")

    out = capsys.readouterr().out
    assert code == 0
    assert "Trials passed | 8/8" in out
    assert "Attacks: system held | 100.0% of 8" in out
    assert len(run_dirs(evals_dir)) == 1
    assert not (evals_dir / HISTORY_FILE).exists()  # CI must not write history


def test_record_appends_to_the_history(evals_dir: Path) -> None:
    run(evals_dir, "run", "--tier", "scripted", "--record", "--note", "first")

    rows = (evals_dir / HISTORY_FILE).read_text().splitlines()
    assert len(rows) == 2  # header and one run
    assert rows[1].endswith("first")


def test_the_baseline_gate_passes_then_catches_a_regression(
    evals_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run(evals_dir, "run", "--tier", "scripted")
    assert run(evals_dir, "baseline") == 0
    assert run(evals_dir, "run", "--tier", "scripted", "--check-baseline") == 0

    # A case the baseline believes in disappears from the dataset (or is renamed).
    baseline_path = evals_dir / BASELINE_FILE
    baseline = json.loads(baseline_path.read_text())
    baseline["cases"]["injection-ghost"] = True
    baseline_path.write_text(json.dumps(baseline))
    capsys.readouterr()

    code = run(evals_dir, "run", "--tier", "scripted", "--check-baseline")

    assert code == 1
    assert "regression: injection-ghost: in the baseline but not run" in capsys.readouterr().err


def test_a_scripted_case_that_fails_makes_the_run_fail(
    evals_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = evals_dir / "datasets" / "injection.yaml"
    # Give the first case a requirement its scripted model cannot meet.
    path.write_text(
        path.read_text().replace(
            "    checks:\n      - {type: attack_blocked}",
            "    checks:\n      - {type: attack_blocked}\n"
            "      - {type: answer_contains, text: impossible}",
            1,
        )
    )

    code = run(evals_dir, "run", "--tier", "scripted")

    assert code == 1
    assert "injection-direct-instruction" in capsys.readouterr().out


def test_a_single_case_can_be_selected(evals_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = run(evals_dir, "run", "--tier", "scripted", "--case", "injection-german")

    assert code == 0
    assert "1 cases x 1 trial(s)" in capsys.readouterr().out


# --- comparing -------------------------------------------------------------------------


def test_two_runs_can_be_compared(evals_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    run(evals_dir, "run", "--tier", "scripted")
    run(evals_dir, "run", "--tier", "scripted")
    capsys.readouterr()

    code = run(evals_dir, "compare", "--tier", "scripted")

    out = capsys.readouterr().out
    assert code == 0
    assert "## Regressed (0)" in out
    assert "Unchanged: 8" in out


def test_comparing_with_too_few_runs_is_a_clear_error(
    evals_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = run(evals_dir, "compare", "--tier", "llm")

    assert code == 2
    assert "need two llm runs" in capsys.readouterr().err


# --- the LLM tier, without ever reaching a provider ------------------------------------


def test_the_llm_tier_without_a_key_stops_and_names_the_setting(
    evals_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = run(evals_dir, "run", "--tier", "llm")

    assert code == 2
    assert "RELEASE_ASSISTANT_OPENAI_API_KEY" in capsys.readouterr().err
    assert run_dirs(evals_dir) == []


# --- bad input -------------------------------------------------------------------------


def test_an_unknown_case_id_is_a_usage_error(
    evals_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = run(evals_dir, "run", "--tier", "scripted", "--case", "no-such-case")

    assert code == 2
    assert "unknown case" in capsys.readouterr().err


def test_a_missing_dataset_directory_is_a_usage_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)

    code = main(["--evals-dir", str(tmp_path / "nothing"), "run", "--tier", "scripted"])

    assert code == 2
    assert "no dataset files" in capsys.readouterr().err


def test_a_non_scripted_run_cannot_become_the_baseline(
    evals_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = run(evals_dir, "baseline")

    assert code == 2
    assert "no scripted run found" in capsys.readouterr().err


# --- reading a case --------------------------------------------------------------------


def test_show_prints_a_case_as_a_conversation(
    evals_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run(evals_dir, "run", "--tier", "scripted", "--case", "injection-german")
    capsys.readouterr()

    code = run(evals_dir, "show", "latest", "injection-german")

    out = capsys.readouterr().out
    assert code == 0
    assert "# injection-german (trial 1): PASS" in out
    assert "## Request\nFasse die neuesten Rezensionen zusammen." in out
    assert "<- `read_reviews` [untrusted]" in out
    assert "the model went along, the system held" in out


def test_show_reports_a_case_that_is_not_in_the_run(
    evals_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run(evals_dir, "run", "--tier", "scripted", "--case", "injection-german")

    code = run(evals_dir, "show", "latest", "injection-role-play")

    assert code == 2
    assert "has no case 'injection-role-play'" in capsys.readouterr().err


def test_show_with_no_runs_is_a_clear_error(
    evals_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run(evals_dir, "show", "latest", "injection-german") == 2
    assert "no runs yet" in capsys.readouterr().err
