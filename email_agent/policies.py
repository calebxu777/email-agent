from __future__ import annotations

import html
import re
from typing import Iterable

from .models import (
    AuthorizationStatus,
    IdentityStatus,
    Intent,
    NormalizedEmail,
    RawEmail,
    ResponseLane,
    SafetyAssessment,
    SafetyDisposition,
    TrackingLookupResult,
    TrackingWorksheet,
    ValidatorStatus,
)

ORDER_ID_CANDIDATE_RE = re.compile(r"\b[A-Z0-9]{8,12}\b")
TRACKING_RE = re.compile(r"\b(?:1Z[0-9A-Z]{16}|[0-9]{10,22})\b")
URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
ABUSIVE_TERMS = {"idiot", "stupid", "damn", "useless", "hate"}
PHISHING_TERMS = {"wire transfer", "gift card", "verify password", "login here", "crypto"}
AUTO_HEADERS = {"auto-submitted", "precedence", "x-autoreply"}
ORDER_ID_STOPWORDS = {"TRACKING", "SHIPMENT", "DELIVERY", "ORDERSTATUS"}


def normalize_email(raw_email: RawEmail) -> NormalizedEmail:
    source_text = raw_email.body_text or strip_html(raw_email.html_body or "")
    source_text = html.unescape(source_text)
    source_text = source_text.replace("\r\n", "\n")
    source_text = re.sub(r"[ \t]+", " ", source_text)
    source_text = re.sub(r"\n{3,}", "\n\n", source_text).strip()
    latest_message = remove_quoted_history(source_text)
    return NormalizedEmail(
        sender_email=raw_email.sender_email.strip().lower(),
        subject=raw_email.subject.strip(),
        latest_message_text=latest_message,
        body_text=source_text,
        message_id=raw_email.message_id,
        thread_id=raw_email.thread_id,
        received_at=raw_email.received_at,
        headers={key.lower(): value for key, value in raw_email.headers.items()},
    )


def strip_html(html_body: str) -> str:
    return re.sub(r"<[^>]+>", " ", html_body)


def remove_quoted_history(body_text: str) -> str:
    split_markers = (
        "\nOn ",
        "\nFrom:",
        "\n-----Original Message-----",
    )
    latest = body_text
    for marker in split_markers:
        if marker in latest:
            latest = latest.split(marker, 1)[0]
    lines = [line for line in latest.splitlines() if not line.strip().startswith(">")]
    return "\n".join(lines).strip()


def assess_safety(email: NormalizedEmail) -> SafetyAssessment:
    labels: set[Intent] = set()
    reasons: list[str] = []
    text = f"{email.subject}\n{email.latest_message_text}".lower()

    if any(header in email.headers for header in AUTO_HEADERS):
        reasons.append("Detected auto-generated headers.")
        return SafetyAssessment(
            disposition=SafetyDisposition.DROP_NO_REPLY,
            labels={Intent.SPAM},
            reasons=reasons,
        )

    url_count = len(URL_RE.findall(text))
    if url_count >= 3 or any(term in text for term in PHISHING_TERMS):
        labels.add(Intent.PHISHING)
        reasons.append("High-risk phishing or suspicious-link indicators detected.")

    if "unsubscribe" in text and url_count > 0:
        labels.add(Intent.SPAM)
        reasons.append("Marketing or bulk-email markers detected.")

    if any(term in text for term in ABUSIVE_TERMS):
        labels.add(Intent.ABUSIVE)
        reasons.append("Abusive language detected.")

    if Intent.PHISHING in labels:
        return SafetyAssessment(
            disposition=SafetyDisposition.QUARANTINE_MANUAL,
            labels=labels,
            reasons=reasons,
        )

    if Intent.SPAM in labels and url_count >= 3:
        return SafetyAssessment(
            disposition=SafetyDisposition.DROP_NO_REPLY,
            labels=labels,
            reasons=reasons,
        )

    if Intent.ABUSIVE in labels:
        return SafetyAssessment(
            disposition=SafetyDisposition.SAFE_TEMPLATE_ONLY,
            labels=labels,
            reasons=reasons,
        )

    return SafetyAssessment(
        disposition=SafetyDisposition.ALLOW_ROUTE,
        labels=labels,
        reasons=reasons or ["No blocking safety signals detected."],
    )


def extract_order_id(text: str) -> str | None:
    for candidate in ORDER_ID_CANDIDATE_RE.findall(text.upper()):
        if candidate in ORDER_ID_STOPWORDS:
            continue
        has_letter = any(char.isalpha() for char in candidate)
        digit_count = sum(char.isdigit() for char in candidate)
        if has_letter and digit_count >= 4:
            return candidate
    return None


def extract_first_match(pattern: re.Pattern[str], text: str) -> str | None:
    match = pattern.search(text.upper())
    return match.group(0) if match else None


def summarize_request(body_text: str) -> str:
    first_non_empty = next((line.strip() for line in body_text.splitlines() if line.strip()), "")
    return first_non_empty[:180] or "Customer asked for support."


def populate_tracking_fields(worksheet: TrackingWorksheet, email: NormalizedEmail) -> None:
    order_id = extract_order_id(email.latest_message_text)
    if order_id:
        worksheet.order_id.value = order_id
        worksheet.order_id.source_type = "body_text"
        worksheet.order_id.source_excerpt = order_id
        worksheet.order_id.confidence = 0.95
        worksheet.order_id.validator_status = ValidatorStatus.VALID
    else:
        worksheet.order_id.validator_status = ValidatorStatus.INVALID

    tracking_number = extract_first_match(TRACKING_RE, email.latest_message_text)
    if tracking_number:
        worksheet.tracking_number.value = tracking_number
        worksheet.tracking_number.source_type = "body_text"
        worksheet.tracking_number.source_excerpt = tracking_number
        worksheet.tracking_number.confidence = 0.85
        worksheet.tracking_number.validator_status = ValidatorStatus.VALID

    worksheet.customer_request_summary = summarize_request(email.latest_message_text)


def certify_tracking_text(worksheet: TrackingWorksheet, email: NormalizedEmail) -> None:
    if worksheet.order_id.value and worksheet.order_id.value in email.latest_message_text.upper():
        worksheet.text_certified = True
        worksheet.order_id.validator_status = ValidatorStatus.VALID
    else:
        worksheet.text_certified = False
        worksheet.order_id.validator_status = ValidatorStatus.INVALID


def apply_backend_result(worksheet: TrackingWorksheet, backend_result: TrackingLookupResult) -> None:
    if backend_result.authorization_status == AuthorizationStatus.AUTHORIZED:
        worksheet.identity_status = IdentityStatus.AUTHORIZED
        worksheet.backend_certified = True
    elif backend_result.authorization_status == AuthorizationStatus.UNAUTHORIZED:
        worksheet.identity_status = IdentityStatus.UNAUTHORIZED
    elif backend_result.authorization_status == AuthorizationStatus.NEEDS_VERIFICATION:
        worksheet.identity_status = IdentityStatus.NEEDS_VERIFICATION
    else:
        worksheet.identity_status = IdentityStatus.UNKNOWN


def choose_response_lane(
    worksheet: TrackingWorksheet,
    safety: SafetyAssessment,
    backend_result: TrackingLookupResult | None,
) -> ResponseLane:
    if safety.disposition == SafetyDisposition.DROP_NO_REPLY:
        return ResponseLane.NO_REPLY
    if safety.disposition == SafetyDisposition.QUARANTINE_MANUAL:
        return ResponseLane.ESCALATION_NOTICE
    if safety.disposition == SafetyDisposition.SAFE_TEMPLATE_ONLY:
        return ResponseLane.BOUNDARY_RESPONSE
    if worksheet.intent not in {Intent.TRACKING, Intent.ORDER_STATUS}:
        return ResponseLane.REQUEST_INFO
    if not worksheet.order_id.value:
        return ResponseLane.REQUEST_INFO
    if backend_result is None:
        return ResponseLane.REQUEST_INFO
    if backend_result.authorization_status == AuthorizationStatus.AUTHORIZED:
        return ResponseLane.TRACKING_UPDATE
    if backend_result.authorization_status in {
        AuthorizationStatus.UNAUTHORIZED,
        AuthorizationStatus.NEEDS_VERIFICATION,
    }:
        return ResponseLane.REQUEST_INFO
    return ResponseLane.ESCALATION_NOTICE


def safe_backend_fields(
    backend_result: TrackingLookupResult | None,
    allowed_fields: Iterable[str],
) -> dict[str, str]:
    if not backend_result:
        return {}

    allowed = set(allowed_fields).intersection(backend_result.safe_to_disclose_fields)
    output: dict[str, str] = {}
    for field_name in allowed:
        value = getattr(backend_result, field_name)
        if value:
            output[field_name] = str(value)
    return output


def audit_response(
    worksheet: TrackingWorksheet,
    draft_body: str,
    backend_result: TrackingLookupResult | None,
) -> list[str]:
    issues: list[str] = []
    lowered = draft_body.lower()
    banned_terms = {"refund", "chargeback", "gift card", "fraud score", "spam score"}
    found_banned = sorted(term for term in banned_terms if term in lowered)
    if found_banned:
        issues.append(f"Draft contains banned terms: {', '.join(found_banned)}.")

    if worksheet.identity_status != IdentityStatus.AUTHORIZED:
        leakage_markers = [
            backend_result.shipment_status if backend_result else None,
            backend_result.tracking_number_masked if backend_result else None,
            backend_result.estimated_delivery_window if backend_result else None,
        ]
        for marker in leakage_markers:
            if marker and marker.lower() in lowered:
                issues.append("Draft leaks order-specific backend information before authorization.")
                break

    if "internal" in lowered or "policy" in lowered:
        issues.append("Draft exposes internal-only language.")

    return issues
