# 0007. A provider-neutral agent core on the server

Status: accepted

## Context
Clients will be an iOS app and, later, possibly the web, with text and voice. Language models will come from several providers. Whatever runs the model must not be able to make the changes the user is asked to approve (ADRs 0002-0006), and it must be testable without paying for a model.

## Decision
1. **The loop runs on the server**, as a Python package beside the API. Model keys and the agent's credential never reach a device, one implementation serves every client, and tests and evaluations run in one language. The exception is a hosted voice platform whose loop runs at the provider: there only the tool descriptions and tool endpoints are ours, and they are the same ones.
2. **The agent is an ordinary API client.** It calls the HTTP API with a credential limited to `read` and `propose`, so every server-side safeguard applies to it unchanged. It does not call services in process.
3. **Two replaceable edges, both protocols.** `LLMClient` is the model (a scripted double, later provider adapters). `ToolBackend` runs tools (an HTTP implementation). The loop knows neither.
4. **The tool table is the agent's whole authority.** Reads, and one `propose_*` tool per action with typed arguments. There is no confirm tool, so a model that has been fully hijacked still has nothing to call. Tool routes are checked by a test against the committed OpenAPI contract so they cannot drift from the API.
5. **Safety does not depend on the model.** Limits (steps, tool calls per step, tokens) are enforced by the loop; unknown tools and invalid arguments become error results; identifiers placed in URLs are restricted to a safe alphabet; untrusted tool output (labelled by the API) is fenced and followed by a reminder, with the fence made unforgeable. The fence is a mitigation: the guarantee comes from the server refusing, which a scripted hijacked model demonstrates end to end.
6. **A stopped run ends with fixed text**, never model output, and the run reports the server's pending actions. A client must show the server's state, not the model's words, because a model can claim a change was made.
7. **Conversation history is stored behind a protocol** (in memory now). Trust is derived from the stored messages, so a resumed conversation is tainted again without a separate persisted flag.

## Consequences
- The most important safety scenario runs in CI, deterministically and for free, against the real API; real-model quality is measured separately with the same scenarios.
- Adding a provider means one adapter implementing `LLMClient`; voice sits on top as a transport.
- The agent makes an extra local HTTP hop per tool call, accepted in exchange for exercising the real security boundary.
- Argument models for proposals are shared with the application layer; if the agent becomes a separate service they would come from the API contract instead.
