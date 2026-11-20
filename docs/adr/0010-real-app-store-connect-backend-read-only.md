# 0010. The real App Store Connect backend starts read-only

Status: accepted

## Context
Until now the API ran over in-memory sample data. Connecting a real account is the first step where a bug can change something outside this repository (reply publicly to a customer, add a tester) and where real customer text flows through the agent to a model provider.

## Decision
1. **Read first, writes off.** The adapter implements only the read ports. `add_tester`, `remove_tester` and `respond_to_review` raise `NotSupportedError`. The application is also built with those actions disabled, so a proposal is refused up front (HTTP 501) instead of being queued for a confirmation that could never succeed. Enabling writes is a separate reviewed change.
2. **A composite gateway.** One `AppStoreGateway` is assembled from parts. Sales and engagement reports are stubs that answer "not supported yet"; the agent's prompt tells it to say so plainly instead of guessing. Adding a real part later replaces one constructor argument.
3. **Official contract, not memory.** Field names, enumerations and filters come from Apple's published OpenAPI specification (4.6.1). Unknown states map to `OTHER` with Apple's exact value kept, so a new Apple state shows up as data instead of a crash or a wrong label.
4. **Own small client.** JWT (ES256, 10-minute tokens renewed early), pagination bounded by a page limit, retries with backoff on 429/5xx honouring `Retry-After`, a refusal to follow a `next` link to another host (the token must never leave Apple), the hourly quota logged when nearly spent, and error text reduced to safe fixed messages. Logs carry paths and statuses only.
5. **The private key is guarded at startup.** The path is configuration; the file must be a regular, owner-only, unencrypted P-256 PEM, otherwise startup fails with a message that never contains key material. The key lives outside the repository.
6. **Tests need no Apple.** The client is exercised through a fake transport with documents shaped after the specification. One opt-in test (`make live-asc`) reads the real account and prints counts only.

## Consequences
- With this backend, real customer reviews reach the model provider. Evaluations and any tracing keep using synthetic data.
- Lists are cut at a page limit (newest first), so a very long list is a recent slice; this is logged.
- Which calls succeed depends on the key's role. A 403 is reported as "the key's role does not allow this", and the first live run shows what the chosen role can read.
- Reviews returned by Apple do not name the app when fetched by id, so `CustomerReview.app_id` is optional.
