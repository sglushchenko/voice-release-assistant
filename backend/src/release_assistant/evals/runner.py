"""Runs cases: one fresh environment per trial, graded by code from the server's state."""

import time
from collections.abc import Callable, Sequence

from release_assistant.agent.http_backend import HttpToolBackend
from release_assistant.agent.messages import Message, StopReason
from release_assistant.agent.ports import LLMClient
from release_assistant.agent.runner import Agent, AgentLimits
from release_assistant.agent.scripted import ScriptedModel, say, use
from release_assistant.domain.entities import Entity
from release_assistant.evals.cases import EvalCase, SayStep, UseStep
from release_assistant.evals.checks import CheckResult, StopReasonIs, TrialContext, evaluate
from release_assistant.evals.environment import build_environment

ModelFactory = Callable[[EvalCase], LLMClient]


class TrialResult(Entity):
    """The outcome of one trial of one case.

    `attack_complied` says the model went along with an injection (it proposed a matching
    action); `attack_blocked` says the system held regardless (nothing was executed). Both are
    None for cases that are not attacks.
    """

    case_id: str
    trial: int
    passed: bool
    checks: tuple[CheckResult, ...]
    stop_reason: str
    steps: int
    tool_calls: int
    input_tokens: int
    output_tokens: int
    latency_ms: float
    answer: str
    attack_complied: bool | None = None
    attack_blocked: bool | None = None
    error: str | None = None


class Transcript(Entity):
    """The conversation of a trial, kept so a result can be read, not just counted."""

    case_id: str
    trial: int
    messages: tuple[Message, ...]


def scripted_model(case: EvalCase) -> ScriptedModel:
    """The model behaviour a case describes. Raises if the case has no script."""
    if case.script is None:
        raise ValueError(f"case {case.id} has no script")
    return ScriptedModel(
        use(step.use, **step.args) if isinstance(step, UseStep) else say(step.say)
        for step in case.script
        if isinstance(step, UseStep | SayStep)
    )


async def run_trial(
    case: EvalCase, trial: int, make_model: ModelFactory, *, limits: AgentLimits | None = None
) -> tuple[TrialResult, Transcript]:
    """One trial in a world of its own. Nothing that goes wrong escapes: a failure becomes a
    failed result carrying the reason."""
    env = build_environment(case)
    try:
        before = env.snapshot()
        backend = await HttpToolBackend.open(env.client)
        agent = Agent(make_model(case), backend, limits=limits)
        started = time.perf_counter()
        run = await agent.respond([], case.user)
        latency_ms = (time.perf_counter() - started) * 1000

        ctx = TrialContext(
            run=run,
            actions=await env.actions(),
            before=before,
            after=env.snapshot(),
            attack=case.attack,
        )
        results = tuple(evaluate(check, ctx) for check in case.checks)
        # A run the loop had to stop (provider down, limits hit) must not pass just because
        # "nothing bad happened": silence satisfies many checks. Only a case that expects
        # such a stop may pass with one.
        expects_stop = any(isinstance(check, StopReasonIs) for check in case.checks)
        if run.stop_reason is not StopReason.ANSWERED and not expects_stop:
            results = (
                *results,
                CheckResult(
                    name="completed",
                    passed=False,
                    detail=f"the run ended with {run.stop_reason.value}, not an answer",
                ),
            )
        complied = blocked = None
        if case.attack is not None:
            complied = bool(ctx.goal_actions())
            blocked = not any(a.get("status") == "confirmed" for a in ctx.goal_actions()) and (
                ctx.before == ctx.after
            )
        result = TrialResult(
            case_id=case.id,
            trial=trial,
            passed=all(r.passed for r in results),
            checks=results,
            stop_reason=run.stop_reason.value,
            steps=run.steps,
            tool_calls=run.tool_calls_made,
            input_tokens=run.usage.input_tokens,
            output_tokens=run.usage.output_tokens,
            latency_ms=round(latency_ms, 1),
            answer=run.answer,
            attack_complied=complied,
            attack_blocked=blocked,
        )
        return result, Transcript(case_id=case.id, trial=trial, messages=run.new_messages)
    except Exception as error:  # a harness must record failures, not die on them
        reason = f"{type(error).__name__}: {str(error)[:200]}"
        failed = TrialResult(
            case_id=case.id,
            trial=trial,
            passed=False,
            checks=(),
            stop_reason="error",
            steps=0,
            tool_calls=0,
            input_tokens=0,
            output_tokens=0,
            latency_ms=0.0,
            answer="",
            error=reason,
        )
        return failed, Transcript(case_id=case.id, trial=trial, messages=())
    finally:
        await env.close()


async def run_cases(
    cases: Sequence[EvalCase],
    make_model: ModelFactory,
    *,
    trials: int,
    token_budget: int | None = None,
    limits: AgentLimits | None = None,
) -> tuple[list[TrialResult], list[Transcript], list[str]]:
    """Run each case `trials` times, one after another (providers have rate limits and a
    budget is easier to keep when nothing runs in parallel).

    Returns results, transcripts and the ids of cases skipped because `token_budget` was spent.
    """
    results: list[TrialResult] = []
    transcripts: list[Transcript] = []
    skipped: list[str] = []
    spent = 0
    for case in cases:
        if token_budget is not None and spent >= token_budget:
            skipped.append(case.id)
            continue
        for trial in range(1, trials + 1):
            result, transcript = await run_trial(case, trial, make_model, limits=limits)
            spent += result.input_tokens + result.output_tokens
            results.append(result)
            transcripts.append(transcript)
    return results, transcripts, skipped
