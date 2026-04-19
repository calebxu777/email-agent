from __future__ import annotations

import argparse
import json
from pathlib import Path

from email_agent import EmailAgentService, RawEmail, SQLiteRepository
from email_agent.orchestrator import EmailOrchestrator


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
    parser = argparse.ArgumentParser(description="Run the security-first email support agent.")
    parser.add_argument("--file", type=Path, help="Path to a JSON file describing a RawEmail payload.")
    parser.add_argument("--db", type=Path, default=Path("email_agent.db"), help="SQLite database path for audit storage.")
    parser.add_argument("--metrics", action="store_true", help="Print repository metrics after processing.")
    args = parser.parse_args()

    raw_email = load_email_payload(args.file)
    repository = SQLiteRepository(args.db)
    service = EmailAgentService(repository=repository)
    result = service.handle_email(raw_email)
    print(json.dumps(EmailOrchestrator.as_dict(result), indent=2, default=str))

    if args.metrics:
        print(json.dumps(service.metrics_snapshot(), indent=2))


if __name__ == "__main__":
    main()
