"""The agent core: a provider-neutral loop that lets a language model use the assistant's API.

It sits beside the HTTP API, not inside it. The agent is an ordinary client of that API with
a credential that can read and propose but never confirm, so every safeguard of ADRs 0002-0006
applies to it exactly as to any other client.
"""
