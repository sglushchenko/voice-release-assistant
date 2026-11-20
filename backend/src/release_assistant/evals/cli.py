"""Command line for evaluations: `uv run release-assistant-evals --help`.

    run        run a tier (scripted: free and offline; llm: a real model, costs money)
    compare    compare two runs of a tier, case by case
    baseline   accept a scripted run as the baseline CI checks

The LLM tier is deliberately manual. Nothing here is wired to CI except the scripted tier.
"""

import argparse
import asyncio
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from release_assistant.agent.errors import LLMError
from release_assistant.agent.openai_model import create_openai_model
from release_assistant.config import Settings
from release_assistant.evals.cases import DatasetError, load_dataset
from release_assistant.evals.history import (
    HistoryError,
    append_history,
    compare,
    latest_run_ids,
    load_report,
    load_transcripts,
    read_baseline,
    regressions_against,
    render_comparison,
    write_baseline,
)
from release_assistant.evals.report import (
    LocalFilesSink,
    ReportSink,
    RunMeta,
    RunReport,
    Tier,
    git_state,
    new_run_id,
    prompt_hash,
    render_summary,
    render_trial,
    tools_hash,
)
from release_assistant.evals.runner import ModelFactory, run_cases, scripted_model
from release_assistant.observability.logging import configure_logging

BASELINE_FILE = "baseline-scripted.json"
HISTORY_FILE = "history.csv"
DEFAULT_LLM_TRIALS = 3
DEFAULT_LLM_TOKEN_BUDGET = 500_000


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    # Only errors, on stderr: the report on stdout is the output, not server-style logs, and
    # trial failures are in the report.
    configure_logging(
        Settings(_env_file=None, log_level="ERROR", log_json=False), stream=sys.stderr
    )
    evals_dir = Path(args.evals_dir)
    try:
        if args.command == "run":
            return _run(args, evals_dir)
        if args.command == "compare":
            return _compare(args, evals_dir)
        if args.command == "show":
            return _show(args, evals_dir)
        return _baseline(args, evals_dir)
    except (DatasetError, HistoryError, LLMError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="release-assistant-evals", description=__doc__)
    parser.add_argument(
        "--evals-dir", default="../evals", help="where datasets, runs and history live"
    )
    commands = parser.add_subparsers(dest="command", required=True)

    run = commands.add_parser("run", help="run evaluation cases")
    run.add_argument("--tier", choices=["scripted", "llm"], default="scripted")
    run.add_argument("--suite", choices=["all", "regression", "capability"], default="all")
    run.add_argument("--case", action="append", default=[], help="only this case id (repeatable)")
    run.add_argument("--trials", type=int, help="runs per case (default 1 scripted, 3 llm)")
    run.add_argument("--note", default="", help="why this run was made, kept in the history")
    run.add_argument("--max-tokens", type=int, help="stop starting new cases past this many tokens")
    run.add_argument(
        "--record",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="append to history.csv (default: yes for llm, no for scripted)",
    )
    run.add_argument(
        "--check-baseline",
        action="store_true",
        help="fail if a case the baseline says passes now fails",
    )
    run.add_argument("--fail-under", type=float, help="fail when the pass rate (0-1) is lower")

    cmp = commands.add_parser("compare", help="compare two runs of a tier")
    cmp.add_argument("base", nargs="?", help="run id or path; default: the second newest")
    cmp.add_argument("new", nargs="?", help="run id or path; default: the newest")
    cmp.add_argument("--tier", choices=["scripted", "llm"], default="llm")
    cmp.add_argument("--fail-on-regression", action="store_true")

    show = commands.add_parser("show", help="print one case of a run as a conversation")
    show.add_argument("run", help="run id or path; `latest` for the newest run of any tier")
    show.add_argument("case", help="case id")
    show.add_argument("--trial", type=int, default=1)
    show.add_argument("--full", action="store_true", help="do not cut long tool output")

    base = commands.add_parser("baseline", help="accept a scripted run as the baseline")
    base.add_argument(
        "--from", dest="source", help="run id or path; default: the newest scripted run"
    )
    return parser


def _run(args: argparse.Namespace, evals_dir: Path) -> int:
    tier: Tier = args.tier
    dataset = load_dataset(evals_dir / "datasets")
    cases = dataset.select(suite=args.suite, ids=tuple(args.case))
    if tier == "scripted":
        cases = tuple(c for c in cases if c.scripted)
    if not cases:
        raise DatasetError("no cases selected")

    settings = Settings() if tier == "llm" else Settings(_env_file=None)
    make_model = _model_factory(tier, settings)
    trials = args.trials or (1 if tier == "scripted" else DEFAULT_LLM_TRIALS)
    budget = args.max_tokens or (DEFAULT_LLM_TOKEN_BUDGET if tier == "llm" else None)

    started = datetime.now(UTC)
    results, transcripts, skipped = asyncio.run(
        run_cases(cases, make_model, trials=trials, token_budget=budget)
    )
    commit, dirty = git_state()
    report = RunReport(
        meta=RunMeta(
            run_id=new_run_id(tier, started),
            started_at=started.isoformat(timespec="seconds"),
            tier=tier,
            model="scripted" if tier == "scripted" else settings.openai_model,
            reasoning_effort=None if tier == "scripted" else settings.openai_reasoning_effort,
            git_commit=commit,
            git_dirty=dirty,
            prompt_hash=prompt_hash(),
            tools_hash=tools_hash(),
            dataset_hash=dataset.version_hash,
            suite=args.suite,
            trials=trials,
            cases_run=len(cases),
            note=args.note,
            price_input_per_million=None
            if tier == "scripted"
            else settings.openai_price_input_per_million,
            price_output_per_million=None
            if tier == "scripted"
            else settings.openai_price_output_per_million,
        ),
        results=tuple(results),
        skipped=tuple(skipped),
    )

    sinks: list[ReportSink] = [LocalFilesSink(evals_dir)]
    for sink in sinks:
        sink.publish(report, transcripts)
    if args.record if args.record is not None else tier == "llm":
        append_history(evals_dir / HISTORY_FILE, report)

    print(render_summary(report))
    return _exit_code(args, report, evals_dir)


def _model_factory(tier: Tier, settings: Settings) -> ModelFactory:
    if tier == "scripted":
        return scripted_model
    model = create_openai_model(settings)
    return lambda _case: model


def _exit_code(args: argparse.Namespace, report: RunReport, evals_dir: Path) -> int:
    summary = report.summary
    failed = False
    if report.meta.tier == "scripted" and summary.passed_trials != summary.trials:
        print("scripted cases must all pass; see the failing trials above", file=sys.stderr)
        failed = True
    if args.check_baseline:
        problems = regressions_against(report, read_baseline(evals_dir / BASELINE_FILE))
        for problem in problems:
            print(f"regression: {problem}", file=sys.stderr)
        failed = failed or bool(problems)
    if args.fail_under is not None and summary.pass_rate < args.fail_under:
        print(f"pass rate {summary.pass_rate:.3f} is below {args.fail_under}", file=sys.stderr)
        failed = True
    return 1 if failed else 0


def _compare(args: argparse.Namespace, evals_dir: Path) -> int:
    runs_dir = evals_dir / "runs"
    if args.base and args.new:
        base_ref, new_ref = args.base, args.new
    else:
        latest = latest_run_ids(runs_dir, args.tier, 2)
        if len(latest) < 2:
            raise HistoryError(f"need two {args.tier} runs to compare, found {len(latest)}")
        base_ref, new_ref = latest
    comparison = compare(load_report(base_ref, runs_dir), load_report(new_ref, runs_dir))
    print(render_comparison(comparison))
    return 1 if args.fail_on_regression and comparison.regressions else 0


def _show(args: argparse.Namespace, evals_dir: Path) -> int:
    runs_dir = evals_dir / "runs"
    reference = args.run
    if reference == "latest":
        newest = sorted(p.name for p in runs_dir.iterdir()) if runs_dir.exists() else []
        if not newest:
            raise HistoryError("no runs yet")
        reference = newest[-1]
    report = load_report(reference, runs_dir)
    transcripts = load_transcripts(reference, runs_dir)
    result = next(
        (r for r in report.results if r.case_id == args.case and r.trial == args.trial), None
    )
    transcript = next(
        (t for t in transcripts if t.case_id == args.case and t.trial == args.trial), None
    )
    if result is None or transcript is None:
        raise HistoryError(f"run {report.meta.run_id} has no case {args.case!r} trial {args.trial}")
    print(render_trial(result, transcript, full=args.full))
    return 0


def _baseline(args: argparse.Namespace, evals_dir: Path) -> int:
    runs_dir = evals_dir / "runs"
    reference = args.source
    if reference is None:
        latest = latest_run_ids(runs_dir, "scripted", 1)
        if not latest:
            raise HistoryError("no scripted run found; run `run --tier scripted` first")
        reference = latest[0]
    report = load_report(reference, runs_dir)
    if report.meta.tier != "scripted":
        raise HistoryError("only a scripted run can be the baseline")
    write_baseline(evals_dir / BASELINE_FILE, report)
    print(f"baseline written from {report.meta.run_id} ({report.summary.cases} cases)")
    return 0
