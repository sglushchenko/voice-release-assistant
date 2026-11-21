# Experiment 1 — ElevenLabs hosted agent (browser)

The least-control level: ElevenLabs runs speech-to-text, the LLM and text-to-speech; the browser only
captures the microphone and plays the reply through `@elevenlabs/client` over WebRTC.

```bash
npm install
npm run dev          # http://localhost:5173 — localhost counts as a secure context, so the mic works
```

Paste the agent id into the page and press Start.

## Measurements

| Run | Model / voice | Agent turn latency (≈ ms) | Notes |
|---|---|---|---|
|  |  |  |  |

The latency shown on the page is from the last final user transcript to the agent starting to
speak. It includes speech-to-text finalisation, so only compare numbers measured the same way.
