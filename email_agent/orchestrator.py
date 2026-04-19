from __future__ import annotations

from dataclasses import asdict, is_dataclass
from enum import Enum
from typing import Protocol
from uuid import uuid4

from .brains import HeuristicSupportBrain, SupportBrain
from .models import (
    AuditEvent,
    DraftResponse,
    Intent,
    NormalizedEmail,
    OrchestrationResult,
    ProcessingPolicy,
    RawEmail,
    ResponseLane,
    RoutingDecision,
    TrackingLookupRequest,
    TrackingLookupResult,
    TrackingWorksheet,
    WorkflowState,
)
from .policies import (
    apply_backend_result,
    assess_safety,
    audit_response,
    certify_tracking_text,
    choose_response_lane,
    normalize_email,
    populate_tracking_fields,
    safe_backend_fields,
)


class TrackingBackend(Protocol):
    def lookup_tracking(self, request: TrackingLookupRequest) -> TrackingLookupResult:
        ...


class EmailOrchestrator:
    def __init__(self, brain: SupportBrain | None = None, backend: TrackingBackend | None = None) -> None:
        self._brain = brain or HeuristicSupportBrain()
        self._backend = backend

    def process(
        self,
        raw_email: RawEmail,
        *,
        trace_id: str | None = None,
        policy: ProcessingPolicy | None = None,
    ) -> OrchestrationResult:
        trace_id = trace_id or str(uuid4())
        policy = policy or ProcessingPolicy()
        events: list[AuditEvent] = []

        self._append_event(
            events,
            raw_email,
            trace_id,
            WorkflowState.RECEIVED,
            WorkflowState.QUARANTINED,
            policy,
            "system",
            "Raw email accepted into quarantine boundary.",
        )

        normalized = normalize_email(raw_email)
        self._append_event(
            events,
            raw_email,
            trace_id,
            WorkflowState.QUARANTINED,
            WorkflowState.NORMALIZED,
            policy,
            "system",
            "Email normalized and de-noised.",
        )

        safety = assess_safety(normalized)
        self._append_event(
            events,
            raw_email,
            trace_id,
            WorkflowState.NORMALIZED,
            WorkflowState.SAFETY_CLASSIFIED,
            policy,
            "system",
            "; ".join(safety.reasons),
        )

        if safety.disposition == safety.disposition.DROP_NO_REPLY:
            return self._suppressed_result(
                normalized=normalized,
                trace_id=trace_id,
                policy=policy,
                safety=safety,
                events=events,
                detail="Suppressed by safety policy.",
            )

        routing = self._brain.route(normalized, safety)
        self._append_event(
            events,
            raw_email,
            trace_id,
            WorkflowState.SAFETY_CLASSIFIED,
            WorkflowState.ROUTED,
            policy,
            "model",
            routing.reasoning,
        )

        worksheet = TrackingWorksheet(
            intent=routing.intent,
            sender_email=normalized.sender_email,
            message_id=normalized.message_id,
            thread_id=normalized.thread_id,
        )
        self._append_event(
            events,
            raw_email,
            trace_id,
            WorkflowState.ROUTED,
            WorkflowState.WORKSHEET_PENDING,
            policy,
            "system",
            "Worksheet instantiated for safe slot filling.",
        )

        worksheet.customer_request_summary = self._brain.summarize(normalized)
        populate_tracking_fields(worksheet, normalized)
        certify_tracking_text(worksheet, normalized)
        self._append_event(
            events,
            raw_email,
            trace_id,
            WorkflowState.WORKSHEET_PENDING,
            WorkflowState.TEXT_VERIFIED,
            policy,
            "system",
            f"Text certified={worksheet.text_certified}; order_id={worksheet.order_id.value!r}",
        )

        backend_result: TrackingLookupResult | None = None
        if self._backend and worksheet.is_ready_for_backend_lookup():
            backend_result = self._backend.lookup_tracking(
                TrackingLookupRequest(
                    request_id=normalized.message_id,
                    trace_id=trace_id,
                    thread_id=normalized.thread_id,
                    sender_email=normalized.sender_email,
                    order_id=worksheet.order_id.value or "",
                )
            )
            apply_backend_result(worksheet, backend_result)
            self._append_event(
                events,
                raw_email,
                trace_id,
                WorkflowState.TEXT_VERIFIED,
                WorkflowState.BACKEND_VERIFIED,
                policy,
                "backend",
                f"Authorization={backend_result.authorization_status.value}; freshness={backend_result.data_freshness_seconds}",
            )

        worksheet.response_lane = choose_response_lane(worksheet, safety, backend_result)
        draft = self._compose_response(normalized, worksheet, backend_result)

        if draft:
            issues = audit_response(worksheet, draft.body, backend_result)
            if issues:
                self._append_event(
                    events,
                    raw_email,
                    trace_id,
                    WorkflowState.BACKEND_VERIFIED if backend_result else WorkflowState.TEXT_VERIFIED,
                    WorkflowState.BLOCKED_UNSAFE,
                    policy,
                    "system",
                    "; ".join(issues),
                )
                worksheet.response_lane = ResponseLane.ESCALATION_NOTICE
                draft = self._compose_response(normalized, worksheet, None)
                final_state = WorkflowState.ESCALATED_TO_HUMAN
            else:
                final_state = self._state_from_lane(worksheet.response_lane)
                self._append_event(
                    events,
                    raw_email,
                    trace_id,
                    WorkflowState.BACKEND_VERIFIED if backend_result else WorkflowState.TEXT_VERIFIED,
                    WorkflowState.RESPONSE_APPROVED if final_state == WorkflowState.RESPONSE_APPROVED else final_state,
                    policy,
                    "system",
                    f"Response approved in lane {worksheet.response_lane.value}.",
                )
        else:
            final_state = WorkflowState.ESCALATED_TO_HUMAN

        return OrchestrationResult(
            trace_id=trace_id,
            policy_version=policy.policy_version,
            final_state=final_state,
            normalized_email=normalized,
            safety=safety,
            routing=routing,
            worksheet=worksheet,
            backend_result=backend_result,
            draft_response=draft,
            audit_events=events,
        )

    def _suppressed_result(
        self,
        *,
        normalized: NormalizedEmail,
        trace_id: str,
        policy: ProcessingPolicy,
        safety,
        events: list[AuditEvent],
        detail: str,
    ) -> OrchestrationResult:
        routing = RoutingDecision(intent=Intent.SPAM, confidence=1.0, reasoning=detail)
        worksheet = TrackingWorksheet(
            intent=Intent.SPAM,
            sender_email=normalized.sender_email,
            message_id=normalized.message_id,
            thread_id=normalized.thread_id,
            response_lane=ResponseLane.NO_REPLY,
        )
        self._append_event(
            events,
            RawEmail(
                sender_email=normalized.sender_email,
                subject=normalized.subject,
                body_text=normalized.body_text,
                message_id=normalized.message_id,
                thread_id=normalized.thread_id,
                received_at=normalized.received_at,
            ),
            trace_id,
            WorkflowState.SAFETY_CLASSIFIED,
            WorkflowState.SPAM_SUPPRESSED,
            policy,
            "system",
            detail,
        )
        return OrchestrationResult(
            trace_id=trace_id,
            policy_version=policy.policy_version,
            final_state=WorkflowState.SPAM_SUPPRESSED,
            normalized_email=normalized,
            safety=safety,
            routing=routing,
            worksheet=worksheet,
            backend_result=None,
            draft_response=None,
            audit_events=events,
        )

    def _compose_response(
        self,
        normalized: NormalizedEmail,
        worksheet: TrackingWorksheet,
        backend_result: TrackingLookupResult | None,
    ) -> DraftResponse | None:
        subject = self._reply_subject(normalized.subject)

        if worksheet.response_lane == ResponseLane.NO_REPLY:
            return None

        if worksheet.response_lane == ResponseLane.BOUNDARY_RESPONSE:
            return DraftResponse(
                subject=subject,
                lane=worksheet.response_lane,
                should_send=True,
                body=(
                    "Hello,\n\n"
                    "We can help with your request, but we need communication to remain respectful. "
                    "If you still need assistance, please reply with your order ID and a short description of the issue.\n\n"
                    "Support Team"
                ),
            )

        if worksheet.response_lane == ResponseLane.REQUEST_INFO:
            if worksheet.order_id.value is None:
                body = (
                    "Hello,\n\n"
                    "To look into this safely, please reply with the order ID associated with your purchase. "
                    "Once we have that, we can check the status for you.\n\n"
                    "Support Team"
                )
            else:
                body = (
                    "Hello,\n\n"
                    "For security, we need to verify access before sharing any order-specific details. "
                    "Please reply from the email used for the order or contact support through an authenticated channel.\n\n"
                    "Support Team"
                )
            return DraftResponse(subject=subject, body=body, lane=worksheet.response_lane, should_send=True)

        if worksheet.response_lane == ResponseLane.TRACKING_UPDATE and backend_result:
            details = safe_backend_fields(
                backend_result,
                allowed_fields=("shipment_status", "carrier", "tracking_number_masked", "estimated_delivery_window"),
            )
            status_line = details.get("shipment_status", "We have an updated shipment status.")
            carrier_line = f"Carrier: {details['carrier']}\n" if "carrier" in details else ""
            tracking_line = (
                f"Tracking: {details['tracking_number_masked']}\n"
                if "tracking_number_masked" in details
                else ""
            )
            eta_line = (
                f"Estimated delivery: {details['estimated_delivery_window']}\n"
                if "estimated_delivery_window" in details
                else ""
            )
            body = (
                "Hello,\n\n"
                "Here is the latest update we can confirm for your order:\n"
                f"Status: {status_line}\n"
                f"{carrier_line}"
                f"{tracking_line}"
                f"{eta_line}"
                "\nIf you need anything else, feel free to reply to this email.\n\n"
                "Support Team"
            )
            return DraftResponse(subject=subject, body=body, lane=worksheet.response_lane, should_send=True)

        if worksheet.response_lane == ResponseLane.ESCALATION_NOTICE:
            return DraftResponse(
                subject=subject,
                lane=worksheet.response_lane,
                should_send=True,
                body=(
                    "Hello,\n\n"
                    "We need a support specialist to review this request before we respond further. "
                    "A team member will follow up as soon as possible.\n\n"
                    "Support Team"
                ),
            )

        return None

    @staticmethod
    def _state_from_lane(lane: ResponseLane) -> WorkflowState:
        if lane == ResponseLane.REQUEST_INFO:
            return WorkflowState.AWAITING_CUSTOMER_INFO
        if lane == ResponseLane.ESCALATION_NOTICE:
            return WorkflowState.ESCALATED_TO_HUMAN
        return WorkflowState.RESPONSE_APPROVED

    @staticmethod
    def _reply_subject(original_subject: str) -> str:
        if original_subject.lower().startswith("re:"):
            return original_subject
        return f"Re: {original_subject}"

    @staticmethod
    def _append_event(
        events: list[AuditEvent],
        raw_email: RawEmail,
        trace_id: str,
        old_state: WorkflowState,
        new_state: WorkflowState,
        policy: ProcessingPolicy,
        actor_type: str,
        detail: str,
    ) -> None:
        events.append(
            AuditEvent(
                message_id=raw_email.message_id,
                thread_id=raw_email.thread_id,
                trace_id=trace_id,
                old_state=old_state,
                new_state=new_state,
                actor_type=actor_type,
                detail=detail,
                policy_version=policy.policy_version,
                model_version=policy.model_version,
            )
        )

    @staticmethod
    def as_dict(result: OrchestrationResult) -> dict:
        return _serialize(asdict(result))


def _serialize(value):
    if isinstance(value, dict):
        return {key: _serialize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_serialize(item) for item in value]
    if isinstance(value, tuple):
        return [_serialize(item) for item in value]
    if isinstance(value, set):
        return sorted(_serialize(item) for item in value)
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return _serialize(asdict(value))
    return value
