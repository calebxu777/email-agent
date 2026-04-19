from __future__ import annotations

from dataclasses import dataclass, field
from time import sleep
from typing import Protocol

from .models import AuthorizationStatus, TrackingLookupRequest, TrackingLookupResult


class TrackingBackend(Protocol):
    def lookup_tracking(self, request: TrackingLookupRequest) -> TrackingLookupResult:
        ...


class BackendGatewayError(RuntimeError):
    pass


@dataclass(slots=True)
class DemoOrderRecord:
    order_id: str
    customer_email: str
    shipment_status: str
    carrier: str
    tracking_number_masked: str
    estimated_delivery_window: str
    last_scan_at: str
    data_freshness_seconds: int = 300
    safe_to_disclose_fields: tuple[str, ...] = (
        "shipment_status",
        "carrier",
        "tracking_number_masked",
        "estimated_delivery_window",
        "last_scan_at",
    )


@dataclass(slots=True)
class InMemoryTrackingBackend:
    orders: dict[str, DemoOrderRecord] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.orders:
            return
        self.orders = {
            "AB12345678": DemoOrderRecord(
                order_id="AB12345678",
                customer_email="customer@example.com",
                shipment_status="In transit",
                carrier="UPS",
                tracking_number_masked="1Z999AA********84",
                estimated_delivery_window="Arriving in 2-3 business days",
                last_scan_at="2026-04-19T14:30:00Z",
            ),
            "ZX99990000": DemoOrderRecord(
                order_id="ZX99990000",
                customer_email="other@example.com",
                shipment_status="Delivered",
                carrier="FedEx",
                tracking_number_masked="612999********",
                estimated_delivery_window="Delivered on April 18",
                last_scan_at="2026-04-18T09:10:00Z",
                data_freshness_seconds=7200,
            ),
        }

    def lookup_tracking(self, request: TrackingLookupRequest) -> TrackingLookupResult:
        if request.purpose != "customer_support_tracking":
            raise BackendGatewayError("Unsupported lookup purpose.")

        record = self.orders.get(request.order_id)
        if not record:
            return TrackingLookupResult(
                authorization_status=AuthorizationStatus.NOT_FOUND,
                safe_to_disclose_fields=(),
                backend_trace_id=f"missing:{request.order_id}",
            )

        if request.sender_email != record.customer_email.lower():
            return TrackingLookupResult(
                authorization_status=AuthorizationStatus.UNAUTHORIZED,
                safe_to_disclose_fields=(),
                backend_trace_id=f"unauthorized:{request.order_id}",
            )

        return TrackingLookupResult(
            authorization_status=AuthorizationStatus.AUTHORIZED,
            shipment_status=record.shipment_status,
            carrier=record.carrier,
            tracking_number_masked=record.tracking_number_masked,
            estimated_delivery_window=record.estimated_delivery_window,
            last_scan_at=record.last_scan_at,
            data_freshness_seconds=record.data_freshness_seconds,
            safe_to_disclose_fields=record.safe_to_disclose_fields,
            backend_trace_id=f"authorized:{request.order_id}",
        )


@dataclass(slots=True)
class ResilientTrackingGateway:
    backend: TrackingBackend
    max_attempts: int = 3
    retry_delay_seconds: float = 0.05

    def lookup_tracking(self, request: TrackingLookupRequest) -> TrackingLookupResult:
        last_error: Exception | None = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                return self.backend.lookup_tracking(request)
            except Exception as exc:  # pragma: no cover - defensive wrapper
                last_error = exc
                if attempt == self.max_attempts:
                    break
                sleep(self.retry_delay_seconds * attempt)
        raise BackendGatewayError(f"Tracking lookup failed after retries: {last_error}") from last_error
