# 0005. Single-operator authentication: password login for the user, keys for the agent

Status: accepted. Refines 0004 for how credentials are issued.

## Context
The service is run by one operator for one account; there is no user database and none is wanted yet. Two kinds of caller exist: the human, through a client app, and an LLM agent acting for that human. Both must resolve to the same `owner` (ADR 0004) so the agent can propose and the human can confirm.

## Decision
1. **Two principals, one owner.** The human logs in with a username and password and holds confirm scopes. The agent uses a static API key (ADR 0004) limited to `read` and `propose`. Both map to the owner configured as the admin username.
2. **Credentials live in the server's configuration.** The operator sets the username and an **argon2id hash** of the password (never the password itself) through environment variables or a secret store, plus the agent keys as SHA-256 hashes. Changing the password means restarting with a new hash. A `release-assistant-hash-password` command produces the hash.
3. **Login issues an opaque session token.** `POST /v1/auth/login` returns a random token stored only on the server. It lasts 8 hours and every authenticated use extends it (sliding expiry). `POST /v1/auth/logout` revokes it, and a restart revokes all of them. Opaque server-side tokens, not JWT, because they are trivially revocable and have fewer ways to go wrong.
4. **Brute-force protection.** Failed logins are counted per client address; after a configured number the address is locked out for a period (`429` with `Retry-After`). Wrong username and wrong password are indistinguishable in response and timing. Password verification runs off the event loop with a small concurrency cap, since argon2 is deliberately memory-hungry. Password length is bounded.
5. **Fail fast.** The server refuses to start if the admin hash is malformed, if only one of username and hash is set, or if any agent key belongs to a different owner than the admin (it would be unable to share actions with the user).
6. **Transport.** Passwords must only travel over TLS, so the server binds to loopback and is exposed through a TLS-terminating reverse proxy.

## Consequences
- No user table, no password storage beyond one hash, no registration or reset flows.
- Moving to SaaS later means producing a `Principal` from a user database and issuing JWTs; scopes, owners, sessions and confirmation rules stay as they are.
- Per-address lockout can be bypassed by a distributed attacker and can lock out users sharing an address; argon2 cost and the unguessable-password requirement are the remaining defenses. A general request rate limit is a separate concern (next ADR/feature).
- Tokens and lockout state are in memory, so they reset on restart, which matches the stated way of rotating credentials.
