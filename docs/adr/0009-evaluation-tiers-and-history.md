# 0009. Evaluations in two tiers, with a history that shows progress

Status: accepted

## Context
Changing a prompt, a tool description or a model can break behaviour or improve it, and neither is visible without measurement. A real model is costly and non-deterministic, so it cannot be run on every commit. The safety guarantees, in contrast, must be checked on every commit. Results also have to be comparable over time, otherwise a number from last month says nothing about today.

The approach follows the common guidance for agent evaluation (judge the outcome rather than the exact path, repeat trials of a stochastic system, prefer code graders, keep capability and regression suites apart, keep full transcripts), taken from published guides rather than from a primary study; it should be re-read against the sources as they evolve.

## Decision
1. **Two tiers, one format.** The **scripted** tier replays a fixed model behaviour (including a hijacked model) against the real API: free, offline and deterministic, so it runs in CI on every push and fails the build on a regression. The **LLM** tier runs a real model: costly and non-deterministic, run by hand and rarely, never from CI. Their results are never mixed when comparing.
2. **Outcome first.** Graders are plain code and judge what the server did (state unchanged, an action pending, nothing executed), with tool calls and the answer as secondary evidence. A run that did not reach an answer cannot pass by "nothing bad happened" unless the case expects such a stop.
3. **Two measures for attacks.** "The model went along" (it proposed the attacker's action) measures the model; "the system held" (nothing executed, state intact) measures the guarantee. They are reported separately.
4. **Trials and reliability.** Each case runs `k` times (1 scripted, 3 for a real model). Reports give the pass rate, `pass@k` (any trial passed) and `pass^k` (all trials passed).
5. **A fresh world per trial.** Every trial gets its own API instance and data, so nothing leaks between trials.
6. **History that can be trusted.** Every run records what changed: hashes of the system prompt, the tool table and the dataset, the model, the commit and whether the tree was dirty, plus a free-text note. A compact `history.csv` line per run is committed; full results and transcripts stay local. Comparison is per case (`3/3 -> 1/3`) and warns when the runs differ in dataset, model, prompt or trial count.
7. **A baseline for the gate.** `baseline-scripted.json` records which scripted cases pass; CI fails if one that passed no longer does. Updating it is a deliberate, reviewed act.
8. **Cost is configured, not hard-coded.** Prices are optional settings; with none, no cost is shown.
9. **Pluggable destinations.** Local files are always written. A tracing service (Langfuse) is an additional sink enabled by configuration, and a failing sink never fails a run.
10. **Data is synthetic.** Evaluations run on sample data, so full transcripts contain nothing personal and may be sent to an external service. Tracing real traffic needs masking or self-hosting and is a separate decision.

## Consequences
- The most important property, that a fully hijacked model cannot make the system change anything, is checked on every push; a test proves the gate fails when the system is deliberately weakened.
- Quality of a real model is tracked over time without tying CI to a paid, flaky service.
- Renaming a case breaks its history; case ids are stable identifiers.
- With few trials on a stochastic model a single case flipping is weak evidence; comparisons show counts and flag drops, and make no statistical claims.
- LLM-judged grading is deliberately absent for now: it needs calibration against human labels before it is worth trusting.
