from __future__ import annotations

import argparse
import json
from pathlib import Path

from email_agent import EmailOrchestrator, InMemoryTrackingBackend, RawEmail


def load_email_payload(path: Path | None) -> RawEmail:
    if path is None:
        return RawEmail(
            sender_email="customer@example.com",
            subject="Where is my package?",
            body_text="Hi team, can you track order AB12345678 for me? Thanks.",
            message_id="demo-1",
            thread_id="thread-1",
        )

    payload = json.loads(path.read_text(encoding="utf-8"))
    return RawEmail(**payload)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the email support orchestrator on a JSON payload.")
    parser.add_argument("--file", type=Path, help="Path to a JSON file describing a RawEmail payload.")
    args = parser.parse_args()

    raw_email = load_email_payload(args.file)
    orchestrator = EmailOrchestrator(backend=InMemoryTrackingBackend())
    result = orchestrator.process(raw_email)
    print(json.dumps(orchestrator.as_dict(result), indent=2, default=str))


if __name__ == "__main__":
    main()
