"""Run reports: what was run, how it went, and where the results are written."""

import hashlib
import json
import math
import subprocess
from collections import defaultdict
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol

from release_assistant.agent.messages import Role, Trust
from release_assistant.agent.prompt import SYSTEM_PROMPT
from release_assistant.agent.tools import DEFAULT_TOOLS
from release_assistant.domain.entities import Entity
from release_assistant.evals.runner import Transcript, TrialResult

Tier = Literal["scripted", "llm"]


class RunMeta(Entity):
    """Everything needed to tell two runs apart, and to know whether they can be compared.

    The three hashes are the "what changed" axis: edit the prompt, the tool table or the
    dataset and the matching hash changes. `note` is a human reason for the run.
    """

    run_id: str
    started_at: str
    tier: Tier
    model: str
    reasoning_effort: str | None
    git_commit: str
    git_dirty: bool
    prompt_hash: str
    tools_hash: str
    dataset_hash: str
    suite: str
    trials: int
    cases_run: int
    note: str = ""
    price_input_per_million: float | None = None
    price_output_per_million: float | None = None


class Summary(Entity):
    """Aggregates of a run.

    `pass_at_k` counts cases where at least one trial passed, `pass_pow_k` cases where every
    trial passed (the reliability measure: a flaky case does not count). Attack rates are over
    attack trials only.
    """

    cases: int
    trials: int
    passed_trials: int
    errors: int
    pass_rate: float
    pass_at_k: float
    pass_pow_k: float
    attack_trials: int
    attack_complied_rate: float | None
    attack_blocked_rate: float | None
    input_tokens: int
    output_tokens: int
    latency_p50_ms: float
    latency_p95_ms: float
    cost_usd: float | None


class RunReport(Entity):
    meta: RunMeta
    results: tuple[TrialResult, ...]
    skipped: tuple[str, ...] = ()

    @property
    def summary(self) -> Summary:
        return summarize(self.results, self.meta)


class ReportSink(Protocol):
    """Where a finished report goes. Local files are always written; another sink (a tracing
    service) can be added and must never be able to fail the run."""

    def publish(self, report: RunReport, transcripts: Sequence[Transcript]) -> None: ...


def case_counts(results: Sequence[TrialResult]) -> dict[str, tuple[int, int]]:
    """Per case: (passed trials, total trials)."""
    passed: dict[str, int] = defaultdict(int)
    total: dict[str, int] = defaultdict(int)
    for result in results:
        total[result.case_id] += 1
        passed[result.case_id] += result.passed
    return {case_id: (passed[case_id], total[case_id]) for case_id in total}


def summarize(results: Sequence[TrialResult], meta: RunMeta) -> Summary:
    counts = case_counts(results)
    cases = len(counts)
    attacks = [r for r in results if r.attack_blocked is not None]
    latencies = sorted(r.latency_ms for r in results if r.error is None)
    input_tokens = sum(r.input_tokens for r in results)
    output_tokens = sum(r.output_tokens for r in results)
    cost: float | None = None
    if meta.price_input_per_million is not None and meta.price_output_per_million is not None:
        cost = (
            input_tokens * meta.price_input_per_million
            + output_tokens * meta.price_output_per_million
        ) / 1_000_000
    return Summary(
        cases=cases,
        trials=len(results),
        passed_trials=sum(r.passed for r in results),
        errors=sum(r.error is not None for r in results),
        pass_rate=_ratio(sum(r.passed for r in results), len(results)),
        pass_at_k=_ratio(sum(p > 0 for p, _ in counts.values()), cases),
        pass_pow_k=_ratio(sum(p == t for p, t in counts.values()), cases),
        attack_trials=len(attacks),
        attack_complied_rate=_optional_ratio(
            sum(bool(r.attack_complied) for r in attacks), len(attacks)
        ),
        attack_blocked_rate=_optional_ratio(
            sum(bool(r.attack_blocked) for r in attacks), len(attacks)
        ),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        latency_p50_ms=_percentile(latencies, 50),
        latency_p95_ms=_percentile(latencies, 95),
        cost_usd=round(cost, 6) if cost is not None else None,
    )


def _ratio(part: int, whole: int) -> float:
    return round(part / whole, 4) if whole else 0.0


def _optional_ratio(part: int, whole: int) -> float | None:
    return round(part / whole, 4) if whole else None


def _percentile(sorted_values: Sequence[float], percent: int) -> float:
    if not sorted_values:
        return 0.0
    rank = max(1, math.ceil(percent / 100 * len(sorted_values)))
    return round(sorted_values[rank - 1], 1)


# --- identifying a run -----------------------------------------------------------------


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:12]


def prompt_hash() -> str:
    return sha(SYSTEM_PROMPT)


def tools_hash() -> str:
    specs = [tool.spec.model_dump(mode="json") for tool in DEFAULT_TOOLS]
    return sha(json.dumps(specs, sort_keys=True))


def git_state() -> tuple[str, bool]:
    """(short commit, working tree has uncommitted changes); ("unknown", False) outside git."""
    try:
        commit = subprocess.run(  # fixed arguments, no user input
            ["git", "rev-parse", "--short", "HEAD"],  # noqa: S607
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        ).stdout.strip()
        dirty = bool(
            subprocess.run(
                ["git", "status", "--porcelain"],  # noqa: S607
                capture_output=True,
                text=True,
                timeout=5,
                check=True,
            ).stdout.strip()
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown", False
    return commit, dirty


def new_run_id(tier: Tier, now: datetime | None = None) -> str:
    moment = now or datetime.now(UTC)
    # Milliseconds keep two runs started in the same second from overwriting each other.
    stamp = moment.strftime("%Y%m%d-%H%M%S") + f"-{moment.microsecond // 1000:03d}"
    return f"{stamp}-{tier}"


# --- rendering -------------------------------------------------------------------------


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"


def render_summary(report: RunReport) -> str:
    """Markdown for a terminal or a pull request."""
    meta, s = report.meta, report.summary
    lines = [
        f"# Evaluation run {meta.run_id}",
        "",
        f"- Tier: **{meta.tier}**, model: `{meta.model}`"
        + (f", reasoning effort: `{meta.reasoning_effort}`" if meta.reasoning_effort else ""),
        f"- Commit: `{meta.git_commit}`{' (uncommitted changes)' if meta.git_dirty else ''}",
        f"- Prompt `{meta.prompt_hash}`, tools `{meta.tools_hash}`, dataset `{meta.dataset_hash}`",
        f"- Suite: {meta.suite}, {s.cases} cases x {meta.trials} trial(s)"
        + (f", note: {meta.note}" if meta.note else ""),
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Trials passed | {s.passed_trials}/{s.trials} ({_pct(s.pass_rate)}) |",
        f"| Cases with at least one pass (pass@k) | {_pct(s.pass_at_k)} |",
        f"| Cases that always pass (pass^k) | {_pct(s.pass_pow_k)} |",
        f"| Attacks: model went along | {_pct(s.attack_complied_rate)} of {s.attack_trials} |",
        f"| Attacks: system held | {_pct(s.attack_blocked_rate)} of {s.attack_trials} |",
        f"| Tokens (in / out) | {s.input_tokens:,} / {s.output_tokens:,} |",
        f"| Latency p50 / p95 | {s.latency_p50_ms:.0f} ms / {s.latency_p95_ms:.0f} ms |",
        f"| Estimated cost | {'n/a' if s.cost_usd is None else f'${s.cost_usd:.4f}'} |",
        f"| Errors | {s.errors} |",
    ]
    failing = [r for r in report.results if not r.passed]
    if failing:
        lines += ["", "## Failing trials", ""]
        for result in failing:
            reasons = result.error or "; ".join(
                c.detail or c.name for c in result.checks if not c.passed
            )
            lines.append(f"- `{result.case_id}` (trial {result.trial}): {reasons}")
    if report.skipped:
        lines += ["", f"Skipped (token budget spent): {', '.join(report.skipped)}"]
    return "\n".join(lines) + "\n"


class LocalFilesSink:
    """Writes a run to `<evals_dir>/runs/<run_id>/`: report.json, transcripts.jsonl and
    summary.md. These directories are git-ignored; the compact history lives in history.csv."""

    def __init__(self, evals_dir: Path) -> None:
        self._runs_dir = evals_dir / "runs"

    def publish(self, report: RunReport, transcripts: Sequence[Transcript]) -> None:
        directory = self._runs_dir / report.meta.run_id
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "report.json").write_text(
            report.model_dump_json(indent=2) + "\n", encoding="utf-8"
        )
        (directory / "transcripts.jsonl").write_text(
            "".join(t.model_dump_json() + "\n" for t in transcripts), encoding="utf-8"
        )
        (directory / "summary.md").write_text(render_summary(report), encoding="utf-8")


_PREVIEW_CHARS = 500


def render_trial(result: TrialResult, transcript: Transcript, *, full: bool = False) -> str:
    """One trial as a conversation: the request, each tool call and what came back, the answer,
    and how every check fared. Long tool output is cut unless `full` is set."""
    lines = [
        f"# {result.case_id} (trial {result.trial}): {'PASS' if result.passed else 'FAIL'}",
        "",
    ]
    for message in transcript.messages:
        if message.role is Role.USER:
            lines += ["## Request", message.content, ""]
        elif message.role is Role.ASSISTANT and message.tool_calls:
            for call in message.tool_calls:
                arguments = json.dumps(call.arguments, ensure_ascii=False)
                lines.append(f"-> calls `{call.name}` {arguments}")
        elif message.role is Role.TOOL and message.tool_result is not None:
            outcome = message.tool_result
            tags = [
                t
                for t in (
                    "untrusted" if outcome.trust is Trust.UNTRUSTED else "",
                    "error" if outcome.is_error else "",
                    "proposal" if outcome.pending_action_id else "",
                )
                if t
            ]
            body = message.content
            if not full and len(body) > _PREVIEW_CHARS:
                hidden = len(message.content) - _PREVIEW_CHARS
                body = f"{body[:_PREVIEW_CHARS]} ... [{hidden} more characters]"
            label = f" [{', '.join(tags)}]" if tags else ""
            lines += [f"<- `{outcome.name}`{label}", "```", body, "```", ""]
        elif message.role is Role.ASSISTANT:
            lines += ["## Answer", message.content, ""]
    lines += ["## Checks"]
    for check in result.checks:
        mark = "ok  " if check.passed else "FAIL"
        lines.append(f"- {mark} {check.name}" + (f": {check.detail}" if check.detail else ""))
    if result.error:
        lines.append(f"- ERROR {result.error}")
    lines += [
        "",
        f"steps {result.steps}, tool calls {result.tool_calls}, tokens "
        f"{result.input_tokens + result.output_tokens}, {result.latency_ms:.0f} ms, "
        f"stop reason {result.stop_reason}",
    ]
    if result.attack_blocked is not None:
        lines.append(
            f"attack: the model {'went along' if result.attack_complied else 'resisted'}, "
            f"the system {'held' if result.attack_blocked else 'DID NOT HOLD'}"
        )
    return "\n".join(lines) + "\n"
