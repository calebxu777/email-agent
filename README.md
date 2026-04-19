# Email Agent Prototype

This repository now contains a broader implementation of the security-first support email agent described in [implement.md](C:/Users/Rita/Desktop/new%20project/implement.md).

## What is implemented

- Deterministic orchestration flow with explicit workflow states.
- Quarantine, normalization, safety classification, routing, worksheet verification, backend verification, and response generation.
- SQLite-backed persistence for raw email metadata, normalized records, worksheet snapshots, audit events, backend calls, outbound drafts, and sender activity.
- Duplicate suppression and sender-rate controls before normal processing.
- Backend gateway wrapper with retries and a strict request shape.
- Conservative response lanes with outbound auditing to prevent unsafe promises or data leakage.
- Optional LangChain adapter for routing and summarization without handing over workflow control.

## Current scope

This version still keeps the v1 boundaries from the implementation plan:

- Tracking and basic order-status flows only.
- No refunds, returns, exchanges, or financial write actions.
- No remote URL fetching from inbound email.
- Demo backend only until the real backend contract is available.

## Run the demo

```bash
python main.py --metrics
```

To run with a JSON payload:

```bash
python main.py --file sample-email.json --db local-agent.db --metrics
```

The JSON shape matches the `RawEmail` dataclass in [email_agent/models.py](C:/Users/Rita/Desktop/new%20project/email_agent/models.py).

## Key modules

- [email_agent/service.py](C:/Users/Rita/Desktop/new%20project/email_agent/service.py): top-level service with persistence and abuse controls.
- [email_agent/orchestrator.py](C:/Users/Rita/Desktop/new%20project/email_agent/orchestrator.py): deterministic workflow engine.
- [email_agent/policies.py](C:/Users/Rita/Desktop/new%20project/email_agent/policies.py): normalization, safety policy, extraction, and outbound audit rules.
- [email_agent/storage.py](C:/Users/Rita/Desktop/new%20project/email_agent/storage.py): SQLite audit and operational storage.
- [email_agent/backends.py](C:/Users/Rita/Desktop/new%20project/email_agent/backends.py): backend gateway and in-memory backend contract.
