# Evaluations

Cases, the committed baseline and the history of results for the agent. The design is in
[ADR 0009](../docs/adr/0009-evaluation-tiers-and-history.md). Commands run from `backend/`.

## Two tiers

| | Scripted | LLM |
|---|---|---|
| What it replays | A fixed model behaviour, including a model hijacked by a prompt injection | A real model (OpenAI today) |
| Cost, network | Free, offline | Costs tokens, needs your key |
| Deterministic | Yes | No: each case runs several times |
| When | Every push (CI) | By hand, rarely: after changing a prompt, a tool description or the model |
| Command | `make eval` | `make eval-llm` |
| Answers | Does the system hold under bad model behaviour? | How good is the model at the job? |

CI runs only the scripted tier. It never calls a paid service.

## Reading results

`make eval-llm` writes a run to `evals/runs/<id>/` (local, git-ignored: `report.json`,
`transcripts.jsonl`, `summary.md`) and appends one line to [`history.csv`](history.csv), which is
committed. Each line says what the run was (prompt, tools and dataset hashes, model, commit, a
note) and how it went (pass rate, `pass@k`, `pass^k`, attacks the model went along with and the
system held against, tokens, latency, cost).

Compare two runs case by case:

```bash
make eval-compare                      # the two newest LLM runs
make eval-compare BASE=<id> NEW=<id>   # chosen runs
```

It lists regressed and improved cases with their counts (`3/3 -> 1/3`) and warns when the runs
are not like for like (different dataset, model, prompt or number of trials). Give a run a
reason so the history stays readable:

```bash
uv run release-assistant-evals run --tier llm --note "shorter tool descriptions"
```

## Cases

Cases are YAML files in [`datasets/`](datasets): `injection.yaml` (attacks planted in reviews,
scripted so CI can gate them) and `behavior.yaml` (how well a real model finds facts and proposes
changes, unscripted so they cannot pass by construction). A case has an `id`, a `suite`
(`regression` should always pass, `capability` tracks improvement), checks and, optionally, a
`script`. Checks grade the server's state first; see `release_assistant/evals/checks.py`.

Rules of the road:

- **Case ids are history keys.** Renaming one breaks its comparison; add a new case instead.
- **Changing a case changes the dataset hash**, which the comparison warns about.
- **The baseline moves deliberately.** After adding scripted cases, run `make eval`, review the
  results, then `make eval-baseline` and commit `baseline-scripted.json`.
- **Do not tune a prompt to one set of cases.** Add cases from real failures instead.

## Cost and budget

An LLM run stops starting new cases once it has used 500,000 tokens (`--max-tokens` changes it).
To see an estimated cost, set the two optional price settings in your `.env` from the provider's
current price list; without them no cost is shown.
