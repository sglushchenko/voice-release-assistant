# 0012. A chat endpoint whose agent is an ordinary, less-privileged API client

Status: accepted

## Context
Phone and web clients need one way to talk to the agent. The agent can propose changes; only a person may approve them (ADRs 0002-0004). Each message can spend money with a model provider, and a leaked login must not become an unlimited bill.

## Decision
1. **`POST /v1/chat`**: one message in, the answer out, plus `pending_actions` read from the server's own records. Clients show those, not the model's words (ADR 0007, point 6). No streaming yet: a reply is one JSON document, which keeps every client simple; streaming can be added to the same route later.
2. **The agent still calls the API over HTTP, in process.** The server mints a random credential per account, held only in memory and never sent to anyone, with `read` and `propose` scopes. It cannot confirm, and it cannot call chat itself, so every safeguard applies to it exactly as to an outside client.
3. **A new `chat` scope.** A login has it; a static client gets it only by configuration. Holding `read` does not allow spending tokens.
4. **Conversations belong to the server and to an account.** Ids are random and chosen by the server; a client cannot invent one, and another account's id is reported as not found. A conversation is one API session plus a stored history. When the session expires (30 idle minutes) the conversation ends, because its trust state (ADR 0003) lived in that session; a client starts a new one. A resumed conversation can therefore never have lost its "untrusted" mark.
5. **Cost controls in code.** A `chat` rate limit (10/minute), a 4000-character message cap, the loop's per-message limits, and a per-account daily token budget that answers 429 `budget_exceeded`.
6. **No model configured is a normal state.** The server starts, logs a warning and answers 503 `chat_unavailable`.

## Consequences
- Conversations and budgets are in memory: a restart ends them (the same as sessions). Persistence is a later, separate step behind the existing `ConversationStore` protocol.
- Voice will sit on this endpoint or on the same service, not beside it.
- The budget counts model tokens reported by the provider, not money; converting to money needs current prices, which the repository does not pin.
