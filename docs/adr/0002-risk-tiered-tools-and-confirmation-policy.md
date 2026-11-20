# 0002. Risk-tiered tools and configurable confirmation policy

Status: accepted

## Context
The agent can change real App Store Connect state. Some actions are harmless to repeat, others are public or irreversible. Confirmation must be convenient by voice, yet must not depend on the model behaving well.

## Decision
1. Every tool declares a **risk tier**: `read`, `reversible_write`, `public_write`, `irreversible`.
2. A risky tool never executes directly. It returns a `PendingAction` (id, human-readable summary, arguments, expiry).
3. Execution happens only through `confirm(action_id)`, performed by deterministic code (`ConfirmationPolicy`), never by the model.
4. The policy is configuration, not prompt text. Each tool maps to an **approval mode**:
   - `button_only` — only an explicit tap in the client (e.g. delete tester, submit for review);
   - `voice_or_button` — a spoken confirmation delivered as a separate STT event, or a tap;
   - `implicit_if_clear` — executes without extra confirmation only when all arguments come verbatim from the user's own utterance (see ADR 0003, provenance).
5. The configured mode is a ceiling the policy can only tighten at runtime, never relax.
6. Every pending, confirmed, rejected and expired action is written to an audit log.

## Consequences
- Safety is testable: policy decisions are plain functions covered by unit tests and evals.
- Voice stays convenient for low-risk actions; dangerous ones cannot be unlocked by speech.
- Slightly more code than "ask the model to confirm", which is the point.
