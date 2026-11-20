# 0006. Abuse protection: rate limits and request size cap in the application

Status: accepted

## Context
The API is reachable by a human client and an LLM agent. An agent in a loop can hammer the API by accident, an exposed address invites scanning and password guessing, and some endpoints are expensive (password hashing) or consequential (proposing actions). The application should defend itself against the cheap failures, but it cannot be the defense against a real distributed attack.

## Decision
Layered limits, all configurable and all answered with `429` and `Retry-After` (or `413`) in the standard error shape:

1. **Per client address**, before authentication, on every route except the liveness probe. Cheap, and it stops a flood from reaching authentication, hashing or business code.
2. **Per principal**, after authentication. A runaway agent cannot starve the human: they are limited separately, by subject.
3. **Per client address on login**, strict, applied before any password hashing. Complements the failed-login lockout of ADR 0005 by bounding attempts, not just failures.
4. **Per principal on proposing actions**, since each proposal creates state and audit records.
5. **Request body cap.** Bodies above a configured size are refused with `413`, judged by the declared length and, for streamed bodies, by counting bytes as they arrive.

Limits are token buckets (a burst allowance that refills steadily), held in memory with bounded size. The client address comes from the connection, or from `X-Forwarded-For` only when the connection is from a configured trusted proxy; otherwise anyone could pick their own address.

## What this does not do
This is protection against mistakes and simple abuse, not against a distributed denial of service. Volumetric attacks are absorbed before the application: by the reverse proxy, a CDN or the network provider, which can drop traffic without parsing it. The in-process limiter assumes a single process; behind several instances each keeps its own counters, and a shared store (for example Redis) would replace it.

## Consequences
- A looping agent gets 429s and backs off rather than flooding logs and state.
- Password guessing is bounded twice: by rate and by lockout.
- Behind a proxy that is not configured as trusted, every client shares the proxy's address and one noisy client can exhaust the shared per-address budget. Configuring trusted proxies correctly is part of deployment.
- Counters reset on restart.
