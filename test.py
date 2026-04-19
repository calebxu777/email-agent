from __future__ import annotations

import unittest

from email_agent import EmailOrchestrator, InMemoryTrackingBackend, RawEmail


class RecordingBackend(InMemoryTrackingBackend):
    def __init__(self):
        super().__init__()
        self.calls = 0

    def lookup_tracking(self, request):
        self.calls += 1
        return super().lookup_tracking(request)


class OrchestratorTests(unittest.TestCase):
    def test_authorized_tracking_email_gets_tracking_update(self) -> None:
        backend = RecordingBackend()
        orchestrator = EmailOrchestrator(backend=backend)
        raw_email = RawEmail(
            sender_email="customer@example.com",
            subject="Tracking request",
            body_text="Can you track order AB12345678 for me?",
        )

        result = orchestrator.process(raw_email)

        self.assertEqual(result.worksheet.response_lane.value, "tracking_update")
        self.assertIsNotNone(result.draft_response)
        self.assertIn("In transit", result.draft_response.body)
        self.assertEqual(backend.calls, 1)

    def test_missing_order_id_requests_more_information(self) -> None:
        backend = RecordingBackend()
        orchestrator = EmailOrchestrator(backend=backend)
        raw_email = RawEmail(
            sender_email="customer@example.com",
            subject="Where is my package?",
            body_text="Can you help me find my shipment?",
        )

        result = orchestrator.process(raw_email)

        self.assertEqual(result.worksheet.response_lane.value, "request_info")
        self.assertIsNotNone(result.draft_response)
        self.assertIn("reply with the order ID", result.draft_response.body)
        self.assertEqual(backend.calls, 0)

    def test_unauthorized_sender_does_not_receive_order_data(self) -> None:
        backend = RecordingBackend()
        orchestrator = EmailOrchestrator(backend=backend)
        raw_email = RawEmail(
            sender_email="intruder@example.com",
            subject="Order status",
            body_text="Please send tracking for order AB12345678.",
        )

        result = orchestrator.process(raw_email)

        self.assertEqual(result.worksheet.response_lane.value, "request_info")
        self.assertIsNotNone(result.draft_response)
        self.assertNotIn("In transit", result.draft_response.body)
        self.assertNotIn("1Z999AA", result.draft_response.body)
        self.assertEqual(backend.calls, 1)

    def test_spam_like_email_is_suppressed_without_backend_call(self) -> None:
        backend = RecordingBackend()
        orchestrator = EmailOrchestrator(backend=backend)
        raw_email = RawEmail(
            sender_email="promo@example.com",
            subject="Big sale",
            body_text=(
                "Click now https://a.example.com https://b.example.com https://c.example.com "
                "unsubscribe for more"
            ),
            headers={"Precedence": "bulk"},
        )

        result = orchestrator.process(raw_email)

        self.assertEqual(result.final_state, "SPAM_SUPPRESSED")
        self.assertIsNone(result.draft_response)
        self.assertEqual(backend.calls, 0)


if __name__ == "__main__":
    unittest.main()
