# 0004. HTTP API: scoped credentials decide who may confirm what

Status: accepted

## Context
ADR 0002 makes confirmation channel-based (`button`, `voice`) and ADR 0003 assumes a model can propose but never confirm. Over HTTP, "channel" is just a field in a request, so an agent that can call the API could claim `button`. The API must make that impossible by construction, and must tie pending actions to the user who proposed them.

## Decision
1. **Authentication.** Every `/v1` request carries `Authorization: Bearer <api key>`. The server stores only SHA-256 hashes of keys, compared in constant time. No configured clients means every request is rejected (fail closed).
2. **Authority comes from the credential, not the request.** Each client has scopes: `read`, `propose`, `confirm:button`, `confirm:voice`. The confirmation channel is derived from the scope the credential holds; a request body can never choose it. An agent's credential gets `read` and `propose` only, so it cannot confirm anything. A human-facing client (the iOS app) holds the confirm scopes.
3. **Ownership.** A pending action records the subject that proposed it, and only that subject may confirm or reject it. Possessing an action id is not authorization.
4. **Sessions are server-side.** Trust state (ADR 0003) lives in a session the server stores, owned by the subject that created it. Clients never send or reset trust flags.
5. **Errors** use a problem-details shape (`title`, `status`, `detail`, `code`) and never leak internals.
6. **Correlation.** Every request gets a request id (accepted from `X-Request-ID` or generated), returned in the response and bound to every log line.
7. **Schemas.** Domain entities are exposed directly while they match what clients need; dedicated API schemas are introduced the moment the two diverge, so the domain stays free to change.
8. **Versioning.** Routes live under `/v1`.

## Consequences
- A compromised or injected agent cannot execute a risky action even by calling the API directly: it lacks the scope.
- Revoking a client is removing one hash from configuration.
- Static keys are a deliberate starting point; swapping to OAuth/JWT later changes only how a `Principal` is produced.
- A direct-execute endpoint for human UIs (one click, no separate confirm) is a possible later addition behind its own scope; it must never be grantable to an agent credential.
