# 0001. One public monorepo, working notes kept elsewhere

Status: accepted

## Context
The project spans a Python backend, a SwiftUI client, evals and benchmarks. Contributors and readers need one place to see how they fit together. Drafts, posts and discussion notes are personal and unfinished.

## Decision
- All code, evals, benchmarks and architectural decisions live in this single public repository.
- Drafts, social posts and working notes live in a separate private repository.

## Consequences
- Shared contracts (tool schemas, protocols) cannot drift between client and backend.
- Benchmarks are reproducible against exactly the code they measured.
- Public history stays clean; nothing personal can leak through a bad `git add`.
