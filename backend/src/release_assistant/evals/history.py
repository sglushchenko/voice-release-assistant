"""The record of results over time: a compact CSV, a baseline, and comparison of two runs.

`history.csv` has one line per recorded run and is committed, so progress is visible in git and
in any spreadsheet. The scripted baseline is what CI checks. Comparing runs answers "did my
change to the prompt make anything better or worse?" case by case.
"""

import csv
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from release_assistant.evals.report import RunReport, case_counts
from release_assistant.evals.runner import Transcript

HISTORY_COLUMNS = (
    "run_id",
    "started_at",
    "tier",
    "model",
    "reasoning_effort",
    "git_commit",
    "git_dirty",
    "prompt_hash",
    "tools_hash",
    "dataset_hash",
    "suite",
    "cases",
    "trials",
    "pass_rate",
    "pass_at_k",
    "pass_pow_k",
    "attack_complied_rate",
    "attack_blocked_rate",
    "input_tokens",
    "output_tokens",
    "latency_p50_ms",
    "latency_p95_ms",
    "cost_usd",
    "note",
)


class HistoryError(Exception):
    """The history, a report or the baseline cannot be used as asked."""


def history_row(report: RunReport) -> dict[str, Any]:
    meta, s = report.meta, report.summary
    return {
        "run_id": meta.run_id,
        "started_at": meta.started_at,
        "tier": meta.tier,
        "model": meta.model,
        "reasoning_effort": meta.reasoning_effort or "",
        "git_commit": meta.git_commit,
        "git_dirty": str(meta.git_dirty).lower(),
        "prompt_hash": meta.prompt_hash,
        "tools_hash": meta.tools_hash,
        "dataset_hash": meta.dataset_hash,
        "suite": meta.suite,
        "cases": s.cases,
        "trials": s.trials,
        "pass_rate": s.pass_rate,
        "pass_at_k": s.pass_at_k,
        "pass_pow_k": s.pass_pow_k,
        "attack_complied_rate": "" if s.attack_complied_rate is None else s.attack_complied_rate,
        "attack_blocked_rate": "" if s.attack_blocked_rate is None else s.attack_blocked_rate,
        "input_tokens": s.input_tokens,
        "output_tokens": s.output_tokens,
        "latency_p50_ms": s.latency_p50_ms,
        "latency_p95_ms": s.latency_p95_ms,
        "cost_usd": "" if s.cost_usd is None else s.cost_usd,
        "note": meta.note,
    }


def append_history(path: Path, report: RunReport) -> None:
    """Add one line. The header is written once; a file with a different header is refused
    rather than silently misaligned."""
    path.parent.mkdir(parents=True, exist_ok=True)
    exists = path.exists() and path.stat().st_size > 0
    if exists:
        with path.open(newline="", encoding="utf-8") as handle:
            header = next(csv.reader(handle), [])
        if tuple(header) != HISTORY_COLUMNS:
            raise HistoryError(f"{path} has an unexpected header; refusing to append")
    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=HISTORY_COLUMNS)
        if not exists:
            writer.writeheader()
        writer.writerow(history_row(report))


def read_history(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


# --- loading runs ----------------------------------------------------------------------


def load_report(reference: str, runs_dir: Path) -> RunReport:
    """A run by id (a directory name under `runs_dir`) or by path to its directory or report."""
    candidates = [
        runs_dir / reference / "report.json",
        Path(reference) / "report.json",
        Path(reference),
    ]
    for candidate in candidates:
        if candidate.is_file():
            return RunReport.model_validate_json(candidate.read_text(encoding="utf-8"))
    raise HistoryError(f"no run found for {reference!r} (looked in {runs_dir})")


def load_transcripts(reference: str, runs_dir: Path) -> list[Transcript]:
    """The conversations of a run, from `transcripts.jsonl` next to its report."""
    for candidate in (runs_dir / reference, Path(reference)):
        file = (
            candidate / "transcripts.jsonl"
            if candidate.is_dir()
            else candidate.parent / "transcripts.jsonl"
        )
        if file.is_file():
            return [
                Transcript.model_validate_json(line)
                for line in file.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
    raise HistoryError(f"no transcripts found for {reference!r} (looked in {runs_dir})")


def latest_run_ids(runs_dir: Path, tier: str, count: int) -> list[str]:
    """Ids of the newest runs of a tier, oldest first. Ids start with a timestamp."""
    if not runs_dir.exists():
        return []
    ids = sorted(p.name for p in runs_dir.iterdir() if p.name.endswith(f"-{tier}"))
    return ids[-count:]


# --- comparison ------------------------------------------------------------------------


@dataclass
class CaseDelta:
    case_id: str
    base: str
    new: str
    change: str  # regressed | improved | same | only_in_base | only_in_new


@dataclass
class Comparison:
    base_id: str
    new_id: str
    warnings: list[str] = field(default_factory=list)
    metrics: list[tuple[str, str, str]] = field(default_factory=list)
    cases: list[CaseDelta] = field(default_factory=list)

    @property
    def regressions(self) -> list[CaseDelta]:
        return [c for c in self.cases if c.change == "regressed"]


def compare(base: RunReport, new: RunReport) -> Comparison:
    """Compare two runs of the same tier. Different tiers are refused: a scripted run and a
    real-model run measure different things."""
    if base.meta.tier != new.meta.tier:
        raise HistoryError(f"cannot compare a {base.meta.tier} run with a {new.meta.tier} run")
    comparison = Comparison(base.meta.run_id, new.meta.run_id)
    b, n = base.meta, new.meta
    if b.dataset_hash != n.dataset_hash:
        comparison.warnings.append("the datasets differ: only cases present in both are comparable")
    if b.trials != n.trials:
        comparison.warnings.append(f"different trial counts ({b.trials} vs {n.trials})")
    if b.model != n.model:
        comparison.warnings.append(f"different models ({b.model} vs {n.model})")
    changed = [
        label
        for label, x, y in (
            ("prompt", b.prompt_hash, n.prompt_hash),
            ("tools", b.tools_hash, n.tools_hash),
        )
        if x != y
    ]
    if changed:
        comparison.warnings.append(f"changed between runs: {', '.join(changed)}")
    if n.git_dirty:
        comparison.warnings.append("the new run had uncommitted changes")

    sb, sn = base.summary, new.summary
    comparison.metrics = [
        ("pass rate", _pct(sb.pass_rate), _pct(sn.pass_rate)),
        ("pass@k", _pct(sb.pass_at_k), _pct(sn.pass_at_k)),
        ("pass^k", _pct(sb.pass_pow_k), _pct(sn.pass_pow_k)),
        ("attack: model went along", _pct(sb.attack_complied_rate), _pct(sn.attack_complied_rate)),
        ("attack: system held", _pct(sb.attack_blocked_rate), _pct(sn.attack_blocked_rate)),
        (
            "tokens",
            f"{sb.input_tokens + sb.output_tokens:,}",
            f"{sn.input_tokens + sn.output_tokens:,}",
        ),
        ("latency p50", f"{sb.latency_p50_ms:.0f} ms", f"{sn.latency_p50_ms:.0f} ms"),
        ("cost", _cost(sb.cost_usd), _cost(sn.cost_usd)),
    ]

    before, after = case_counts(base.results), case_counts(new.results)
    for case_id in sorted(set(before) | set(after)):
        if case_id not in after:
            comparison.cases.append(CaseDelta(case_id, _frac(before[case_id]), "-", "only_in_base"))
        elif case_id not in before:
            comparison.cases.append(CaseDelta(case_id, "-", _frac(after[case_id]), "only_in_new"))
        else:
            rate_b = before[case_id][0] / before[case_id][1]
            rate_n = after[case_id][0] / after[case_id][1]
            change = "regressed" if rate_n < rate_b else "improved" if rate_n > rate_b else "same"
            comparison.cases.append(
                CaseDelta(case_id, _frac(before[case_id]), _frac(after[case_id]), change)
            )
    return comparison


def render_comparison(comparison: Comparison) -> str:
    lines = [f"# {comparison.base_id}  ->  {comparison.new_id}", ""]
    for warning in comparison.warnings:
        lines.append(f"> {warning}")
    if comparison.warnings:
        lines.append("")
    lines += ["| Metric | Before | After |", "|---|---|---|"]
    lines += [f"| {name} | {before} | {after} |" for name, before, after in comparison.metrics]
    for title, change in (("Regressed", "regressed"), ("Improved", "improved")):
        rows = [c for c in comparison.cases if c.change == change]
        lines += ["", f"## {title} ({len(rows)})", ""]
        lines += [f"- `{c.case_id}`: {c.base} -> {c.new}" for c in rows] or ["- none"]
    same = sum(c.change == "same" for c in comparison.cases)
    other = [c for c in comparison.cases if c.change.startswith("only_")]
    lines += ["", f"Unchanged: {same}"]
    if other:
        lines.append("Not in both runs: " + ", ".join(f"`{c.case_id}`" for c in other))
    return "\n".join(lines) + "\n"


def _frac(counts: tuple[int, int]) -> str:
    return f"{counts[0]}/{counts[1]}"


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"


def _cost(value: float | None) -> str:
    return "n/a" if value is None else f"${value:.4f}"


# --- baseline --------------------------------------------------------------------------


def baseline_from(report: RunReport) -> dict[str, Any]:
    """The accepted result of each case in a run: True when every trial passed."""
    return {
        "tier": report.meta.tier,
        "created_at": report.meta.started_at,
        "git_commit": report.meta.git_commit,
        "dataset_hash": report.meta.dataset_hash,
        "cases": {
            cid: passed == total
            for cid, (passed, total) in sorted(case_counts(report.results).items())
        },
    }


def write_baseline(path: Path, report: RunReport) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(baseline_from(report), indent=2) + "\n", encoding="utf-8")


def read_baseline(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise HistoryError(f"no baseline at {path}; create one with `baseline`")
    baseline: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return baseline


def regressions_against(report: RunReport, baseline: dict[str, Any]) -> list[str]:
    """Cases that the baseline says passed but that fail now, or are missing from the run."""
    counts = case_counts(report.results)
    problems: list[str] = []
    for case_id, expected in sorted(baseline["cases"].items()):
        if not expected:
            continue
        if case_id not in counts:
            problems.append(f"{case_id}: in the baseline but not run")
        elif counts[case_id][0] != counts[case_id][1]:
            problems.append(f"{case_id}: passed in the baseline, now {_frac(counts[case_id])}")
    return problems
