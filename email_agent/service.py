from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

from .backends import InMemoryTrackingBackend, ResilientTrackingGateway, TrackingBackend
from .brains import HeuristicSupportBrain, SupportBrain
from .models import (
    AuditEvent,
    Intent,
    NormalizedEmail,
    OrchestrationResult,
    ProcessingPolicy,
    RawEmail,
    ResponseLane,
    RoutingDecision,
    SafetyAssessment,
    SafetyDisposition,
    TrackingWorksheet,
    WorkflowState,
)
from .orchestrator import EmailOrchestrator
from .policies import normalize_email
from .storage import SQLiteRepository


class EmailAgentService:
    def __init__(
        self,
        *,
        repository: SQLiteRepository | None = None,
        brain: SupportBrain | None = None,
        backend: TrackingBackend | None = None,
        policy: ProcessingPolicy | None = None,
    ) -> None:
        self.repository = repository or SQLiteRepository()
        self.policy = policy or ProcessingPolicy()
        gateway = ResilientTrackingGateway(backend or InMemoryTrackingBackend())
        self.orchestrator = EmailOrchestrator(brain=brain or HeuristicSupportBrain(), backend=gateway)

    def handle_email(self, raw_email: RawEmail) -> OrchestrationResult:
        trace_id = str(uuid4())
        self.repository.store_raw_email(raw_email, trace_id)

        normalized = normalize_email(raw_email)
        self.repository.store_normalized_email(normalized, trace_id)

        duplicate_since = normalized.received_at - self.policy.duplicate_window
        if self.repository.seen_recent_duplicate(normalized.sender_email, normalized.body_hash, duplicate_since):
            result = self._preempt_result(
                raw_email=raw_email,
                normalized=normalized,
                trace_id=trace_id,
                final_state=WorkflowState.SPAM_SUPPRESSED,
                safety=SafetyAssessment(
                    disposition=SafetyDisposition.DROP_NO_REPLY,
                    labels={Intent.SPAM},
                    reasons=["Recent duplicate body detected for the same sender."],
                    risk_score=0.9,
                ),
                routing=RoutingDecision(
                    intent=Intent.SPAM,
                    confidence=1.0,
                    reasoning="Suppressed duplicate inbound email.",
                ),
                response_lane="no_reply",
                event_detail="Duplicate suppression window triggered.",
            )
            self.repository.persist_result(raw_email, result)
            return result

        hourly_volume = self.repository.recent_sender_volume(
            normalized.sender_email,
            normalized.received_at - timedelta(hours=1),
        )
        if hourly_volume >= self.policy.max_messages_per_sender_per_hour:
            result = self._preempt_result(
                raw_email=raw_email,
                normalized=normalized,
                trace_id=trace_id,
                final_state=WorkflowState.ESCALATED_TO_HUMAN,
                safety=SafetyAssessment(
                    disposition=SafetyDisposition.QUARANTINE_MANUAL,
                    labels={Intent.SPAM},
                    reasons=["Sender exceeded allowed hourly message volume."],
                    risk_score=0.95,
                ),
                routing=RoutingDecision(
                    intent=Intent.SPAM,
                    confidence=0.99,
                    reasoning="Escalated due to sender rate limit.",
                ),
                response_lane="escalation_notice",
                event_detail="Sender rate limit triggered.",
            )
            self.repository.persist_result(raw_email, result)
            return result

        result = self.orchestrator.process(raw_email, trace_id=trace_id, policy=self.policy)
        self.repository.persist_result(raw_email, result)
        return result

    def metrics_snapshot(self) -> dict[str, int]:
        return self.repository.metrics_snapshot()

    def _preempt_result(
        self,
        *,
        raw_email: RawEmail,
        normalized: NormalizedEmail,
        trace_id: str,
        final_state: WorkflowState,
        safety: SafetyAssessment,
        routing: RoutingDecision,
        response_lane: str,
        event_detail: str,
    ) -> OrchestrationResult:
        worksheet = TrackingWorksheet(
            intent=routing.intent,
            sender_email=normalized.sender_email,
            message_id=normalized.message_id,
            thread_id=normalized.thread_id,
        )
        if response_lane == "no_reply":
            worksheet.response_lane = ResponseLane.NO_REPLY
            draft = None
        else:
            worksheet.response_lane = ResponseLane.ESCALATION_NOTICE
            draft = self.orchestrator._compose_response(normalized, worksheet, None)

        events = [
            AuditEvent(
                message_id=raw_email.message_id,
                thread_id=raw_email.thread_id,
                trace_id=trace_id,
                old_state=WorkflowState.RECEIVED,
                new_state=WorkflowState.QUARANTINED,
                actor_type="system",
                detail="Raw email accepted into quarantine boundary.",
                policy_version=self.policy.policy_version,
                model_version=self.policy.model_version,
            ),
            AuditEvent(
                message_id=raw_email.message_id,
                thread_id=raw_email.thread_id,
                trace_id=trace_id,
                old_state=WorkflowState.QUARANTINED,
                new_state=WorkflowState.NORMALIZED,
                actor_type="system",
                detail="Email normalized before repository checks.",
                policy_version=self.policy.policy_version,
                model_version=self.policy.model_version,
            ),
            AuditEvent(
                message_id=raw_email.message_id,
                thread_id=raw_email.thread_id,
                trace_id=trace_id,
                old_state=WorkflowState.NORMALIZED,
                new_state=final_state,
                actor_type="system",
                detail=event_detail,
                policy_version=self.policy.policy_version,
                model_version=self.policy.model_version,
            ),
        ]

        return OrchestrationResult(
            trace_id=trace_id,
            policy_version=self.policy.policy_version,
            final_state=final_state,
            normalized_email=normalized,
            safety=safety,
            routing=routing,
            worksheet=worksheet,
            backend_result=None,
            draft_response=draft,
            audit_events=events,
        )
