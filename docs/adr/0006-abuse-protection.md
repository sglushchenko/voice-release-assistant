# 0006. Abuse protection: rate limits and request size cap in the application

Status: accepted

## Context
The API is reachable by a human client and an LLM agent. An agent in a loop can hammer the API by accident, an exposed address invites scanning and password guessing, and some endpoints are expensive (password hashing) or consequential (proposing actions). The application should defend itself against the cheap failures, but it cannot be the defense against a real distributed attack.

## Decision
Layered limits, all configurable and all answered with `429` and `Retry-After` (or `413`) in the standard error shape (the first four exist from the start, per-route policies such as `reports` and `reviews` use the same mechanism):

1. **Per client address**, before authentication, on every route except the liveness probe. Cheap, and it stops a flood from reaching authentication, hashing or business code.
2. **Per principal**, after authentication. A runaway agent cannot starve the human: they are limited separately, by subject.
3. **Per client address on login**, strict, applied before any password hashing. Complements the failed-login lockout of ADR 0005 by bounding attempts, not just failures.
4. **Per principal on proposing actions**, since each proposal creates state and audit records.
5. **Request body cap.** Bodies above a configured size are refused with `413`, judged by the declared length and, for streamed bodies, by counting bytes as they arrive.

**Counting is delegated to the `limits` library** (moving-window algorithm; memory or Redis storage), not written in-house. The service adds only what is specific to it: named policies (`ip`, `principal`, `login`, `propose`, `reports`, `reviews`), declared in configuration as "N/minute" strings, attached to routes with a `limit("reports")` dependency, keyed by address or by caller, and reported in the API's error format. Defaults can be overridden per policy, and an unparseable or unknown policy stops startup.

The client address comes from the connection, or from `X-Forwarded-For` only when the connection is from a configured trusted proxy; otherwise anyone could pick their own address.

An earlier version of this feature used a hand-written token bucket. It was replaced because a library gives tested algorithms and, above all, shared storage (Redis) for multi-instance deployments, which a private implementation would have had to reinvent.

## What this does not do
This is protection against mistakes and simple abuse, not against a distributed denial of service. Volumetric attacks are absorbed before the application: by the reverse proxy, a CDN or the network provider, which can drop traffic without parsing it. With the default in-memory storage each process keeps its own counters, so several instances need the Redis storage.

## Consequences
- A looping agent gets 429s and backs off rather than flooding logs and state.
- Password guessing is bounded twice: by rate and by lockout.
- Behind a proxy that is not configured as trusted, every client shares the proxy's address and one noisy client can exhaust the shared per-address budget. Configuring trusted proxies correctly is part of deployment.
- With in-memory storage counters reset on restart.
- Moving windows have no separate "burst" setting: "20/second" means at most 20 in any second.
