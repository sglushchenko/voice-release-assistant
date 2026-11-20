# Voice Release Assistant

A voice assistant for iOS release managers: ask about builds, TestFlight testers and customer reviews, and approve risky App Store Connect actions by voice.

The project is also a side-by-side comparison of two voice-agent platforms — **ElevenLabs** and **OpenAI** — integrated at several levels of control, from a hosted agent with a ready-made SDK to a custom STT → LLM → TTS pipeline.

> Status: early development. See [docs/adr](docs/adr) for decisions made so far.

## Repository layout

| Path | Purpose |
|---|---|
| `backend/` | Python service: domain, tools, provider adapters, API |
| `ios/` | SwiftUI client |
| `evals/` | Datasets and scripts that measure agent quality |
| `benchmarks/` | Latency / cost / quality comparison of the voice providers |
| `docs/adr/` | Architecture Decision Records |

## Design principles

- **Clean Architecture.** Domain logic knows nothing about ElevenLabs, OpenAI or App Store Connect.
- **Protocol-first boundaries.** Every external system sits behind a protocol, so mock and real implementations are interchangeable.
- **Safe by default.** Destructive actions require explicit confirmation; secrets never live in the repo.
- **Measured, not assumed.** Behaviour is covered by evals and traced end to end.
