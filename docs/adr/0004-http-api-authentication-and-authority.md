# 0004. HTTP API: scoped credentials decide who may confirm what

Status: accepted. How credentials are issued is refined by [0005](0005-single-operator-authentication.md).

## Context
ADR 0002 makes confirmation channel-based (`button`, `voice`) and ADR 0003 assumes a model can propose but never confirm. Over HTTP, "channel" is just a field in a request, so an agent that can call the API could claim `button`. The API must make that impossible by construction, and must tie pending actions to the user who proposed them.

## Decision
1. **Authentication.** Every `/v1` request carries `Authorization: Bearer <api key>`. The server stores only SHA-256 hashes of keys, compared in constant time. No configured clients means every request is rejected (fail closed).
2. **Authority comes from the credential, not the request.** Each client has scopes: `read`, `propose`, `confirm:button`, `confirm:voice`. A confirm request names the channel it came through, and the credential must hold the matching `confirm:<channel>` scope, so a client can only claim a channel it is trusted to speak for. An agent's credential gets `read` and `propose` only, so it cannot confirm anything. A human-facing client (the iOS app) holds the confirm scopes.
3. **Ownership belongs to the account, not the credential.** Every credential has a `subject` (which client is calling, used in logs) and an `owner` (the account it acts for). Sessions and pending actions belong to the owner. An agent proposes and the user's app confirms with different credentials but the same owner; any other owner sees "not found", so ids cannot be probed. (An earlier draft bound actions to the proposing subject, which would have made the agent-proposes, human-confirms flow impossible.)
4. **Sessions are server-side.** Trust state (ADR 0003) lives in a session the server stores, owned by the subject that created it. Clients never send or reset trust flags.
5. **Errors** use a problem-details shape (`title`, `status`, `detail`, `code`) and never leak internals.
6. **Correlation.** Every request gets a request id (accepted from `X-Request-ID` or generated), returned in the response and bound to every log line.
7. **Schemas.** Domain entities are exposed directly while they match what clients need; dedicated API schemas are introduced the moment the two diverge, so the domain stays free to change.
8. **Versioning.** Routes live under `/v1`.

## Consequences
- A compromised or injected agent cannot execute a risky action even by calling the API directly: it lacks the scope.
- Revoking a client is removing one hash from configuration.
- Static keys are a deliberate starting point; swapping to user login (OAuth/JWT) later changes only how a `Principal` is produced, because `owner` already models the user.
- Reviews are reachable only inside a session and their payload is labelled `trust: untrusted`, so third-party text cannot be read without tainting the session.
- An action that resolves to `implicit_if_clear` executes at propose time even for an agent credential. That is the point of the mode, so it is only assigned to low-risk actions, and none uses it by default.
- A direct-execute endpoint for human UIs (one click, no separate confirm) is a possible later addition behind its own scope; it must never be grantable to an agent credential.
