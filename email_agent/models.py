from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Generic, Sequence, TypeVar


class Intent(str, Enum):
    TRACKING = "tracking"
    ORDER_STATUS = "order_status"
    MISSING_INFORMATION = "missing_information"
    SPAM = "spam"
    PHISHING = "phishing"
    ABUSIVE = "abusive"
    UNCLASSIFIED = "unclassified"


class SafetyDisposition(str, Enum):
    ALLOW_ROUTE = "allow_route"
    SAFE_TEMPLATE_ONLY = "safe_template_only"
    QUARANTINE_MANUAL = "quarantine_manual"
    DROP_NO_REPLY = "drop_no_reply"


class ResponseLane(str, Enum):
    NO_REPLY = "no_reply"
    REQUEST_INFO = "request_info"
    TRACKING_UPDATE = "tracking_update"
    ESCALATION_NOTICE = "escalation_notice"
    BOUNDARY_RESPONSE = "boundary_response"


class IdentityStatus(str, Enum):
    UNKNOWN = "unknown"
    AUTHORIZED = "authorized"
    UNAUTHORIZED = "unauthorized"
    NEEDS_VERIFICATION = "needs_verification"


class ValidatorStatus(str, Enum):
    UNVERIFIED = "unverified"
    VALID = "valid"
    INVALID = "invalid"


class AuthorizationStatus(str, Enum):
    AUTHORIZED = "authorized"
    UNAUTHORIZED = "unauthorized"
    NEEDS_VERIFICATION = "needs_verification"
    NOT_FOUND = "not_found"


T = TypeVar("T")


@dataclass(slots=True)
class EvidenceField(Generic[T]):
    value: T | None = None
    source_type: str | None = None
    source_excerpt: str | None = None
    confidence: float = 0.0
    validator_status: ValidatorStatus = ValidatorStatus.UNVERIFIED


@dataclass(slots=True)
class RawEmail:
    sender_email: str
    subject: str
    body_text: str
    html_body: str | None = None
    message_id: str = "demo-message"
    thread_id: str = "demo-thread"
    received_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    headers: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class NormalizedEmail:
    sender_email: str
    subject: str
    latest_message_text: str
    body_text: str
    message_id: str
    thread_id: str
    received_at: datetime
    headers: dict[str, str]


@dataclass(slots=True)
class SafetyAssessment:
    disposition: SafetyDisposition
    labels: set[Intent] = field(default_factory=set)
    reasons: list[str] = field(default_factory=list)


@dataclass(slots=True)
class RoutingDecision:
    intent: Intent
    confidence: float
    reasoning: str


@dataclass(slots=True)
class TrackingLookupRequest:
    request_id: str
    trace_id: str
    thread_id: str
    sender_email: str
    order_id: str
    purpose: str = "customer_support_tracking"


@dataclass(slots=True)
class TrackingLookupResult:
    authorization_status: AuthorizationStatus
    shipment_status: str | None = None
    carrier: str | None = None
    tracking_number_masked: str | None = None
    estimated_delivery_window: str | None = None
    last_scan_at: str | None = None
    data_freshness_seconds: int | None = None
    safe_to_disclose_fields: tuple[str, ...] = ()
    backend_trace_id: str = "backend-demo"


@dataclass(slots=True)
class TrackingWorksheet:
    intent: Intent
    sender_email: str
    message_id: str
    thread_id: str
    customer_request_summary: str | None = None
    order_id: EvidenceField[str] = field(default_factory=EvidenceField)
    tracking_number: EvidenceField[str] = field(default_factory=EvidenceField)
    identity_status: IdentityStatus = IdentityStatus.UNKNOWN
    text_certified: bool = False
    backend_certified: bool = False
    response_lane: ResponseLane = ResponseLane.REQUEST_INFO

    def is_ready_for_backend_lookup(self) -> bool:
        return (
            self.intent in {Intent.TRACKING, Intent.ORDER_STATUS}
            and self.text_certified
            and self.order_id.value is not None
            and self.order_id.validator_status == ValidatorStatus.VALID
        )

    def is_ready_for_response(self) -> bool:
        if self.response_lane in {ResponseLane.NO_REPLY, ResponseLane.BOUNDARY_RESPONSE}:
            return True
        if self.response_lane == ResponseLane.REQUEST_INFO:
            return True
        if self.response_lane == ResponseLane.ESCALATION_NOTICE:
            return True
        return (
            self.response_lane == ResponseLane.TRACKING_UPDATE
            and self.backend_certified
            and self.identity_status == IdentityStatus.AUTHORIZED
        )


@dataclass(slots=True)
class DraftResponse:
    subject: str
    body: str
    lane: ResponseLane
    should_send: bool


@dataclass(slots=True)
class AuditEvent:
    old_state: str
    new_state: str
    actor_type: str
    detail: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(slots=True)
class OrchestrationResult:
    trace_id: str
    final_state: str
    normalized_email: NormalizedEmail
    safety: SafetyAssessment
    routing: RoutingDecision
    worksheet: TrackingWorksheet
    backend_result: TrackingLookupResult | None
    draft_response: DraftResponse | None
    audit_events: Sequence[AuditEvent]
