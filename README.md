# Email Agent Prototype

This repository now contains a first-pass main orchestrator for a security-first support email agent.

## What is implemented

- Deterministic orchestration flow for inbound email.
- Safety classification before intent routing.
- Typed worksheet state for tracking and order-status requests.
- Backend lookup through a narrow gateway interface.
- Conservative response generation with disclosure checks.
- Optional LangChain adapter for routing and summarization.

## Current scope

This version is intentionally narrow:

- Optimized for tracking and basic order-status requests.
- Does not perform refunds, returns, or any write action.
- Uses an in-memory backend adapter as a placeholder for the real backend team contract.

## Run the demo

```bash
python main.py
```

To run with a JSON payload:

```bash
python main.py --file sample-email.json
```

The JSON shape matches the `RawEmail` dataclass in [email_agent/models.py](C:/Users/Rita/Desktop/new%20project/email_agent/models.py).

## Optional LangChain support

If you want to plug in a LangChain chat model later:

```bash
pip install .[llm]
```

Then instantiate `LangChainSupportBrain.from_chat_model(...)` and pass it into `EmailOrchestrator`.
