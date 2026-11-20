# 0008. The OpenAI adapter uses Chat Completions over the agent's own history

Status: accepted

## Context
The agent core (ADR 0007) keeps its own provider-neutral conversation. A provider adapter must turn that history into a request and the reply back into a turn. OpenAI offers two APIs for this: Chat Completions, and the newer Responses API, which OpenAI's own function-calling guide demonstrates and which is built around stored, provider-side state and typed output items.

## Decision
1. **Chat Completions.** Our messages map onto it one to one (system, user, assistant with tool calls, tool results), nothing provider-specific has to be carried between turns, and the same adapter shape works for OpenAI-compatible servers (a local model, a gateway), which a base-URL setting allows.
2. **Stateless and private.** Every request sends `store=false`: conversation content is not retained by the provider.
3. **Settings only configure.** Model name, output ceiling, reasoning effort and timeout are settings; the model name has a documented default but is the account's to choose. `reasoning_effort` is sent only when set, because models without reasoning reject it, and `temperature` is never sent, because reasoning models reject non-default values.
4. **The key is a secret end to end.** It is a `SecretStr`, absent from every `repr`, never logged, and provider error messages are discarded: only the kind of failure and its status code are kept, since an authentication error can quote part of the key.
5. **Failures become one error type.** Any provider or network failure raises `LLMError`, which ends the run with a fixed message instead of crashing the loop.
6. **Malformed tool arguments are survivable.** If the model emits arguments that are not a JSON object, the adapter passes a marker the tool backend rejects, so the model reads the error and retries rather than the run failing.
7. **Tests need no network and no key.** The adapter is exercised through a fake HTTP transport. One opt-in live test (`make live`) calls the real service and is excluded from normal runs.

## Consequences
- Provider-hidden reasoning items are not carried across turns. With Chat Completions the model does not expose them, so nothing is lost; if a future model or feature requires the Responses API (built-in tools, reasoning continuity), a second adapter can sit behind the same `LLMClient` protocol.
- Adding Anthropic or another provider means one more adapter, not changes to the loop.
- Provider pricing and model names change; the repository documents how to set them and does not pin a price.
