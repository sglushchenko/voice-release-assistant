"""Evaluation of the agent: cases, graders, a runner, reports and their history.

Two tiers share one format. The scripted tier replays a fixed model behaviour (including a
hijacked model) against the real API: free, offline and deterministic, so it gates every CI
run. The LLM tier runs a real model: costly and non-deterministic, so it is run by hand, rarely,
and its results are compared with earlier runs of the same tier (ADR 0009).
"""
