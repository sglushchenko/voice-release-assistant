"""Reports, the history CSV, comparison of runs and the baseline."""

import csv
from pathlib import Path
from typing import Any

import pytest

from release_assistant.agent.tools import DEFAULT_TOOLS, Tool
from release_assistant.evals import report as report_module
from release_assistant.evals.checks import CheckResult
from release_assistant.evals.history import (
    HISTORY_COLUMNS,
    HistoryError,
    append_history,
    baseline_from,
    compare,
    latest_run_ids,
    load_report,
    load_transcripts,
    read_baseline,
    read_history,
    regressions_against,
    render_comparison,
    write_baseline,
)
from release_assistant.evals.report import (
    LocalFilesSink,
    RunMeta,
    RunReport,
    Summary,
    new_run_id,
    prompt_hash,
    render_summary,
    summarize,
    tools_hash,
)
from release_assistant.evals.runner import Transcript, TrialResult


def trial(case_id: str, n: int, passed: bool, **overrides: Any) -> TrialResult:
    values: dict[str, Any] = {
        "case_id": case_id,
        "trial": n,
        "passed": passed,
        "checks": (
            CheckResult(name="answer_contains", passed=passed, detail="" if passed else "missing"),
        ),
        "stop_reason": "answered",
        "steps": 2,
        "tool_calls": 1,
        "input_tokens": 1000,
        "output_tokens": 100,
        "latency_ms": 100.0,
        "answer": "ok",
    }
    return TrialResult(**{**values, **overrides})


def meta(run_id: str = "20261120-100000-llm", **overrides: Any) -> RunMeta:
    values: dict[str, Any] = {
        "run_id": run_id,
        "started_at": "2026-11-20T10:00:00+00:00",
        "tier": "llm",
        "model": "model-a",
        "reasoning_effort": None,
        "git_commit": "abc1234",
        "git_dirty": False,
        "prompt_hash": "p1",
        "tools_hash": "t1",
        "dataset_hash": "d1",
        "suite": "all",
        "trials": 3,
        "cases_run": 3,
    }
    return RunMeta(**{**values, **overrides})


def make_report(results: list[TrialResult], **meta_overrides: Any) -> RunReport:
    return RunReport(meta=meta(**meta_overrides), results=tuple(results))


def three_cases(*, a: int, b: int, c: int) -> list[TrialResult]:
    """Cases a, b and c with the given number of passing trials out of 3."""
    return [
        trial(case, n, n <= passes)
        for case, passes in (("a", a), ("b", b), ("c", c))
        for n in (1, 2, 3)
    ]


# --- metrics ---------------------------------------------------------------------------


def test_pass_at_k_and_pass_pow_k_tell_flaky_from_reliable() -> None:
    summary = make_report(three_cases(a=3, b=1, c=0)).summary

    assert summary.trials == 9
    assert summary.passed_trials == 4
    assert summary.pass_rate == pytest.approx(0.4444, abs=1e-4)
    assert summary.pass_at_k == pytest.approx(0.6667, abs=1e-4)  # a and b passed at least once
    assert summary.pass_pow_k == pytest.approx(0.3333, abs=1e-4)  # only a always passes


def test_attack_rates_cover_attack_trials_only() -> None:
    results = [
        trial("plain", 1, True),
        trial("x", 1, True, attack_complied=True, attack_blocked=True),
        trial("y", 1, True, attack_complied=False, attack_blocked=True),
        trial("z", 1, False, attack_complied=True, attack_blocked=False),
    ]

    summary = make_report(results).summary

    assert summary.attack_trials == 3
    assert summary.attack_complied_rate == pytest.approx(0.6667, abs=1e-4)
    assert summary.attack_blocked_rate == pytest.approx(0.6667, abs=1e-4)


def test_no_attacks_means_no_attack_rates() -> None:
    summary = make_report([trial("a", 1, True)]).summary

    assert summary.attack_complied_rate is None
    assert summary.attack_blocked_rate is None


def test_cost_needs_both_prices_and_is_otherwise_absent() -> None:
    results = [trial("a", 1, True, input_tokens=2_000_000, output_tokens=1_000_000)]

    priced = summarize(results, meta(price_input_per_million=0.25, price_output_per_million=2.0))
    half = summarize(results, meta(price_input_per_million=0.25))

    assert priced.cost_usd == pytest.approx(2.5)
    assert half.cost_usd is None
    assert summarize(results, meta()).cost_usd is None


def test_latency_percentiles_ignore_errored_trials() -> None:
    results = [trial("a", n, True, latency_ms=float(n * 10)) for n in range(1, 11)]
    results.append(trial("b", 1, False, latency_ms=0.0, error="boom"))

    summary = make_report(results).summary

    assert summary.latency_p50_ms == 50.0
    assert summary.latency_p95_ms == 100.0
    assert summary.errors == 1


# --- identifying a run -----------------------------------------------------------------


def test_the_prompt_hash_changes_when_the_prompt_changes(monkeypatch: pytest.MonkeyPatch) -> None:
    before = prompt_hash()

    monkeypatch.setattr(report_module, "SYSTEM_PROMPT", "A different system prompt.")

    assert prompt_hash() != before
    assert prompt_hash() == report_module.sha("A different system prompt.")


def test_the_tools_hash_changes_when_a_tool_description_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = tools_hash()
    first = DEFAULT_TOOLS[0]
    edited = Tool(
        first.name,
        first.description + " Edited.",
        first.arguments,
        first.method,
        first.path,
        first.query,
        first.action,
    )

    monkeypatch.setattr(report_module, "DEFAULT_TOOLS", (edited, *DEFAULT_TOOLS[1:]))

    assert tools_hash() != before


def test_hashes_are_stable_between_calls() -> None:
    assert prompt_hash() == prompt_hash()
    assert tools_hash() == tools_hash()


def test_run_ids_sort_by_time_and_carry_the_tier() -> None:
    from datetime import UTC, datetime

    run_id = new_run_id("llm", datetime(2026, 11, 20, 9, 5, 7, tzinfo=UTC))

    assert run_id == "20261120-090507-000-llm"


# --- writing a run ---------------------------------------------------------------------


def test_the_local_sink_writes_a_report_that_loads_back(tmp_path: Path) -> None:
    report = make_report(three_cases(a=3, b=2, c=0))
    transcripts = [Transcript(case_id="a", trial=1, messages=())]

    LocalFilesSink(tmp_path).publish(report, transcripts)

    directory = tmp_path / "runs" / report.meta.run_id
    assert {p.name for p in directory.iterdir()} == {
        "report.json",
        "transcripts.jsonl",
        "summary.md",
    }
    assert load_report(report.meta.run_id, tmp_path / "runs") == report
    assert load_report(str(directory), tmp_path / "runs") == report


def test_the_summary_names_failures_and_what_changed() -> None:
    results = three_cases(a=3, b=0, c=3)
    text = render_summary(make_report(results, note="shorter tool descriptions", git_dirty=True))

    assert "shorter tool descriptions" in text
    assert "uncommitted changes" in text
    assert "`b` (trial 1): missing" in text
    assert "Prompt `p1`" in text


def test_loading_an_unknown_run_is_a_clear_error(tmp_path: Path) -> None:
    with pytest.raises(HistoryError, match="no run found"):
        load_report("nope", tmp_path)


def test_latest_run_ids_are_per_tier_and_oldest_first(tmp_path: Path) -> None:
    for name in ("20261120-100000-llm", "20261119-100000-llm", "20261121-100000-scripted"):
        (tmp_path / name).mkdir()
    (tmp_path / "20261122-100000-llm").mkdir()

    assert latest_run_ids(tmp_path, "llm", 2) == ["20261120-100000-llm", "20261122-100000-llm"]
    assert latest_run_ids(tmp_path, "scripted", 5) == ["20261121-100000-scripted"]
    assert latest_run_ids(tmp_path / "missing", "llm", 2) == []


# --- history.csv -----------------------------------------------------------------------


def test_history_gets_one_row_per_run_under_a_single_header(tmp_path: Path) -> None:
    path = tmp_path / "history.csv"
    append_history(path, make_report(three_cases(a=3, b=3, c=3), note="baseline"))
    append_history(path, make_report(three_cases(a=3, b=1, c=0), run_id="20261121-100000-llm"))

    rows = read_history(path)
    with path.open(newline="") as handle:
        header = next(csv.reader(handle))

    assert tuple(header) == HISTORY_COLUMNS
    assert [r["run_id"] for r in rows] == ["20261120-100000-llm", "20261121-100000-llm"]
    assert rows[0]["pass_rate"] == "1.0"
    assert rows[0]["note"] == "baseline"
    assert rows[1]["pass_pow_k"] == "0.3333"
    assert rows[0]["cost_usd"] == ""  # blank, not zero: no price was configured


def test_a_history_with_a_different_header_is_not_appended_to(tmp_path: Path) -> None:
    path = tmp_path / "history.csv"
    path.write_text("some,other,columns\n1,2,3\n")

    with pytest.raises(HistoryError, match="unexpected header"):
        append_history(path, make_report([trial("a", 1, True)]))


def test_reading_a_missing_history_is_empty(tmp_path: Path) -> None:
    assert read_history(tmp_path / "none.csv") == []


# --- comparing runs --------------------------------------------------------------------


def test_comparison_separates_regressed_improved_and_unchanged_cases() -> None:
    base = make_report(three_cases(a=3, b=1, c=2))
    new = make_report(three_cases(a=1, b=3, c=2), run_id="20261121-100000-llm")

    comparison = compare(base, new)

    deltas = {c.case_id: (c.base, c.new, c.change) for c in comparison.cases}
    assert deltas["a"] == ("3/3", "1/3", "regressed")
    assert deltas["b"] == ("1/3", "3/3", "improved")
    assert deltas["c"] == ("2/3", "2/3", "same")
    assert [c.case_id for c in comparison.regressions] == ["a"]


def test_comparison_warns_about_everything_that_makes_it_less_trustworthy() -> None:
    base = make_report(three_cases(a=3, b=3, c=3))
    new = make_report(
        three_cases(a=3, b=3, c=3),
        run_id="20261121-100000-llm",
        dataset_hash="d2",
        model="model-b",
        prompt_hash="p2",
        trials=1,
        git_dirty=True,
    )

    warnings = " | ".join(compare(base, new).warnings)

    assert "datasets differ" in warnings
    assert "different models" in warnings
    assert "changed between runs: prompt" in warnings
    assert "different trial counts" in warnings
    assert "uncommitted changes" in warnings


def test_cases_present_in_only_one_run_are_listed_not_scored() -> None:
    base = make_report([trial("a", 1, True), trial("gone", 1, True)])
    new = make_report([trial("a", 1, True), trial("fresh", 1, False)], run_id="20261121-100000-llm")

    comparison = compare(base, new)

    changes = {c.case_id: c.change for c in comparison.cases}
    assert changes == {"a": "same", "gone": "only_in_base", "fresh": "only_in_new"}
    assert comparison.regressions == []


def test_runs_of_different_tiers_cannot_be_compared() -> None:
    scripted = make_report([trial("a", 1, True)], tier="scripted")

    with pytest.raises(HistoryError, match="cannot compare a scripted run with a llm run"):
        compare(scripted, make_report([trial("a", 1, True)]))


def test_the_rendered_comparison_is_readable() -> None:
    base = make_report(three_cases(a=3, b=1, c=2))
    new = make_report(three_cases(a=1, b=3, c=2), run_id="20261121-100000-llm", prompt_hash="p2")

    text = render_comparison(compare(base, new))

    assert text.startswith("# 20261120-100000-llm  ->  20261121-100000-llm")
    assert "## Regressed (1)" in text
    assert "- `a`: 3/3 -> 1/3" in text
    assert "## Improved (1)" in text
    assert "changed between runs: prompt" in text
    assert "| pass rate |" in text


# --- the baseline ----------------------------------------------------------------------


def test_the_baseline_records_which_cases_always_passed(tmp_path: Path) -> None:
    report = make_report(three_cases(a=3, b=2, c=0), tier="scripted")
    path = tmp_path / "baseline.json"

    write_baseline(path, report)

    assert read_baseline(path)["cases"] == {"a": True, "b": False, "c": False}
    assert baseline_from(report)["git_commit"] == "abc1234"


def test_regressions_are_cases_the_baseline_passed_that_now_fail_or_vanished() -> None:
    baseline = {"cases": {"ok": True, "broke": True, "gone": True, "was-failing": False}}
    report = make_report(
        [trial("ok", 1, True), trial("broke", 1, False), trial("was-failing", 1, False)],
        tier="scripted",
    )

    problems = regressions_against(report, baseline)

    assert problems == [
        "broke: passed in the baseline, now 0/1",
        "gone: in the baseline but not run",
    ]


def test_a_missing_baseline_says_how_to_make_one(tmp_path: Path) -> None:
    with pytest.raises(HistoryError, match="baseline"):
        read_baseline(tmp_path / "baseline.json")


def test_summary_is_a_plain_value_object() -> None:
    assert isinstance(make_report([trial("a", 1, True)]).summary, Summary)


# --- reading one trial -----------------------------------------------------------------


def _conversation() -> tuple[TrialResult, Transcript]:
    from release_assistant.agent.messages import Message, ToolCall, ToolResult, Trust

    call = ToolCall(id="c1", name="read_reviews", arguments={"app_id": "app-1"})
    result = ToolResult(call_id="c1", name="read_reviews", content="x" * 800, trust=Trust.UNTRUSTED)
    transcript = Transcript(
        case_id="a",
        trial=1,
        messages=(
            Message.user("Summarise my reviews."),
            Message.assistant("", (call,)),
            Message.tool(result, "y" * 800),
            Message.assistant("Done."),
        ),
    )
    return trial("a", 1, False, attack_complied=True, attack_blocked=True), transcript


def test_a_trial_reads_as_request_calls_results_answer_and_checks() -> None:
    from release_assistant.evals.report import render_trial

    result, transcript = _conversation()

    text = render_trial(result, transcript)

    assert text.startswith("# a (trial 1): FAIL")
    assert "## Request\nSummarise my reviews." in text
    assert '-> calls `read_reviews` {"app_id": "app-1"}' in text
    assert "<- `read_reviews` [untrusted]" in text
    assert "## Answer\nDone." in text
    assert "- FAIL answer_contains: missing" in text
    assert "the model went along, the system held" in text


def test_long_tool_output_is_cut_unless_the_full_text_is_asked_for() -> None:
    from release_assistant.evals.report import render_trial

    result, transcript = _conversation()

    assert "300 more characters" in render_trial(result, transcript)
    assert "more characters" not in render_trial(result, transcript, full=True)
    assert "y" * 800 in render_trial(result, transcript, full=True)


def test_transcripts_load_from_a_written_run(tmp_path: Path) -> None:
    result, transcript = _conversation()
    report = make_report([result])
    LocalFilesSink(tmp_path).publish(report, [transcript])

    loaded = load_transcripts(report.meta.run_id, tmp_path / "runs")

    assert loaded == [transcript]
    with pytest.raises(HistoryError, match="no transcripts"):
        load_transcripts("missing", tmp_path / "runs")
