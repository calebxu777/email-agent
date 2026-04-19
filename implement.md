# Security-First Implementation Plan: Deterministic Support Email Agent

This document expands the current architecture into a production-oriented implementation plan with one priority above all others: the system must be safe, conservative, auditable, and resilient to abuse.

The core idea from `plan.md` remains strong: use deterministic workflow stages, typed worksheets, and verification before action. This version sharpens that design around four operational realities:

1. Inbound email must be treated as hostile input.
2. Model output must be treated as untrusted until verified.
3. Backend access must be tightly controlled and observable.
4. Outbound responses must be policy-bound, privacy-safe, and impossible to over-promise.

## 1. Recommended Product Scope for v1

To prioritize safety, v1 should automate only low-risk, read-only support flows.

### v1 Auto-Eligible
- Tracking and shipment status inquiries.
- Basic order status questions when the backend can verify the sender is authorized.
- Safe requests for missing information.

### v1 Manual or Escalated
- Refunds, returns, exchanges, chargebacks, and compensation.
- Harassment, legal threats, fraud indicators, or account takeover indicators.
- Any case involving unclear identity, mismatched sender/order ownership, or conflicting evidence.
- Any case requiring judgment from product photos unless the photo review flow is separately hardened.

### Explicit Non-Goals for v1
- No autonomous refunds or financial write actions.
- No fetching arbitrary URLs from customer emails.
- No direct execution against third-party systems from the LLM runtime.
- No open-ended chat behavior.

## 2. Core Engineering Principles

1. Treat inbound email as hostile input.
2. Treat every LLM as an untrusted parser and drafter, not an authority.
3. Fail closed. If confidence, authorization, or backend integrity is weak, escalate.
4. Separate extraction, verification, backend access, and response generation into different stages.
5. Do not disclose customer or order information without identity authorization.
6. Do not promise any action unless a verified system of record confirms it.
7. Minimize PII in prompts, logs, and outbound emails.
8. Prefer typed contracts and deterministic checks over prompt-only control.
9. Keep write capabilities out of scope until read-only flows are stable.
10. Preserve a complete audit trail for every state transition and external call.

## 3. Threat Model

Assume an attacker can control:
- Subject, sender display name, body text, HTML, hidden text, attachment names, image contents, and repeated follow-up emails.
- Prompt-injection attempts such as "ignore prior instructions" or "refund immediately."
- Spam floods, phishing attempts, malware links, fake tracking numbers, and order enumeration attempts.

Assume system risks include:
- Hallucinated extraction.
- Hallucinated promises in the response.
- Disclosure of order status to the wrong sender.
- Duplicate or replayed backend calls.
- Logging of sensitive customer information.
- Vendor outages or stale backend data.

### Threat Categories We Must Design For

#### Email and Content Threats
- Prompt injection embedded in visible text, hidden HTML, comments, alt text, or attachment OCR.
- HTML/CSS tricks that hide malicious instructions from the user but not the parser.
- Remote tracking pixels, malicious links, or embedded forms.
- Oversized or malformed attachments intended to exhaust parsing resources.
- Spoofed senders, reply-to mismatches, and forged headers.

#### Abuse and Spam Threats
- High-volume spam meant to trigger auto-replies or cost explosions.
- Bounce loops, out-of-office loops, and mailing list traffic.
- Repeated order-status probes to discover whether an order exists.
- Abuse designed to bait the model into using profanity, threats, or inflammatory language.

#### Backend and Integrity Threats
- Calling a tracking endpoint with an unverified order ID and leaking shipment data.
- Replays or duplicate write requests.
- Stale or inconsistent order data.
- A backend response that is structurally valid but unsafe to disclose.

#### Model Safety Threats
- The model following instructions from the customer instead of the system policy.
- The model inferring facts not present in the email or backend response.
- The model revealing internal reasoning, policies, or abuse scores.

## 4. Trust Boundaries and Service Separation

The system should be split into strict trust zones.

### Zone A: Raw Email Quarantine
- Stores raw MIME and attachments exactly as received.
- Access is restricted to ingestion and security tooling.
- This zone is not directly exposed to the LLM.

### Zone B: Sanitization and Normalization
- Converts hostile raw email into a safe, canonical artifact.
- Removes active content and strips hidden or irrelevant markup.
- Produces sanitized text, attachment metadata, and a normalized thread record.

### Zone C: Reasoning and Verification
- Uses LLMs only on sanitized, policy-bounded inputs.
- Produces proposed worksheet updates, never direct actions.
- Cannot directly call backend endpoints or send email.

### Zone D: Backend Gateway
- The only component allowed to call internal or third-party business systems.
- Enforces typed request/response contracts, auth, rate limits, idempotency, and audit logs.

### Zone E: Outbound Dispatch
- Sends email only after deterministic policy checks pass.
- Has no authority to change business state.

This separation matters: even if an LLM makes a bad suggestion, it should still be impossible for that suggestion to trigger a dangerous backend call or an unsafe reply by itself.

## 5. End-to-End State Machine

Use a strict state machine owned by application code, not by the model.

```text
RECEIVED
  -> QUARANTINED
  -> NORMALIZED
  -> SAFETY_CLASSIFIED
  -> ROUTED
  -> WORKSHEET_PENDING
  -> TEXT_VERIFIED
  -> BACKEND_VERIFIED
  -> RESPONSE_APPROVED
  -> SENT

Failure / alternate states:
  -> SPAM_SUPPRESSED
  -> AWAITING_CUSTOMER_INFO
  -> ESCALATED_TO_HUMAN
  -> BACKEND_RETRY_PENDING
  -> BLOCKED_UNSAFE
```

Every state transition should record:
- `message_id`
- `thread_id`
- `old_state`
- `new_state`
- `actor_type` (`system`, `model`, `human`, `backend`)
- `policy_version`
- `model_version` when applicable
- `timestamp`
- `trace_id`

## 6. Ingestion and Sanitization Layer

This is the first real security boundary.

### Inbound Requirements
- Accept mail only from approved ingress paths such as a trusted webhook or mailbox connector.
- Verify provider signatures if available.
- Store raw MIME encrypted before any parsing.
- Generate an internal immutable `message_id` and `trace_id`.

### Sanitization Requirements
- Parse HTML into a safe DOM and convert only approved content to Markdown or plain text.
- Strip:
  - scripts
  - forms
  - CSS
  - hidden elements
  - comments
  - remote images
  - tracking pixels
  - embedded iframes
- Remove historical reply chains and signatures where confidence is high, but preserve the original artifact for audit.
- Normalize Unicode and detect confusable characters.
- Cap message size, attachment count, and OCR budget.

### Attachment Handling
- Allowlist file types for first-party parsing.
- Quarantine unsupported or suspicious attachments.
- Do not execute macros or embedded scripts.
- If image OCR is used, run it in an isolated service with size and timeout limits.
- Never let attachment text bypass the same policy and verification flow as body text.

### Important Rule
- Do not fetch remote URLs, images, or documents referenced in the email body.

That single rule eliminates a large class of SSRF, tracking, and phishing problems.

## 7. Spam, Phishing, and Abuse Defense

The system should classify abuse before the intent router runs.

### Pre-Routing Safety Signals
- SPF, DKIM, and DMARC result when available from the provider.
- Sender domain reputation and recent message velocity.
- Reply-to mismatch, suspicious headers, and auto-generated markers.
- High link density, URL shorteners, or suspicious top-level domains.
- Duplicate body hash across many inbound messages.
- Multiple order IDs across multiple recent emails from the same sender.
- Repeated requests for different order IDs from one sender or domain.
- Bounce, out-of-office, no-reply, or mailing-list indicators.

### Safety Outcomes
- `DROP_NO_REPLY`
  - For obvious spam, phishing, or loops.
- `QUARANTINE_MANUAL`
  - For suspicious but not conclusive cases.
- `SAFE_TEMPLATE_ONLY`
  - For abusive content where policy allows a neutral boundary response.
- `ALLOW_ROUTE`
  - For messages safe enough to continue through the intent pipeline.

### Spam Resistance Controls
- Per-sender and per-domain rate limits.
- Duplicate suppression windows to avoid repeated model runs on the same content.
- Backscatter protection: do not auto-reply to suspected spam, bounces, or mailing list traffic.
- Cost controls: cap LLM attempts per thread per hour.

## 8. Identity Verification and Data Disclosure Policy

This is the most important privacy rule in the system.

Do not disclose tracking details, order status, or customer data based only on a claimed order ID in the email.

### Required Authorization Conditions

Before the agent can reveal order-specific information, one of the following must be true:
- The sender email matches the order email according to the backend system of record.
- The sender is a verified alternate contact on the order.
- The thread is already linked to an authenticated support session.
- The backend explicitly returns `authorized_to_disclose = true`.

### If Authorization Fails
- Do not confirm whether the order exists.
- Respond with a neutral verification request, or escalate to a human.
- Do not reveal shipping status, address fragments, payment details, or internal notes.

### Recommendation for Backend Contract

The backend should not expose raw tracking data without evaluating disclosure authorization. The agent should not be responsible for deciding disclosure from raw order records alone.

## 9. Worksheet Design: Typed State with Provenance

The worksheet concept is the right foundation, but each field needs provenance and disclosure metadata.

Each field should carry:
- `value`
- `source_type` (`body_text`, `attachment_ocr`, `header`, `backend`)
- `source_excerpt`
- `confidence`
- `validator_status`
- `disclosure_level`
- `last_verified_at`

### Example Worksheet Shape

```python
class TrackingWorksheet(BaseModel):
    intent: Literal["tracking"]
    sender_email: EmailStr
    order_id: str | None = None
    tracking_number: str | None = None
    customer_request_summary: str | None = None
    identity_status: Literal["unknown", "authorized", "unauthorized", "needs_verification"] = "unknown"
    text_certified: bool = False
    backend_certified: bool = False
    response_lane: Literal[
        "request_info",
        "tracking_update",
        "escalate",
        "no_reply"
    ] = "request_info"
```

### Important Worksheet Rules
- The model may propose values, but application code owns acceptance.
- Regex and schema validation must run before a value is committed.
- Confidence alone is not enough. Fields that can drive disclosure or backend calls must have evidence.
- Every worksheet must define a deterministic `is_ready_for_backend_lookup`.
- Every worksheet must define a deterministic `is_ready_for_response`.

## 10. Routing and Intent Classification

Intent routing should be multi-label, not single-label only.

An email may simultaneously be:
- `tracking`
- `abusive`
- `needs_identity_verification`
- `possible_phishing`

### Routing Requirements
- Use strict JSON schema output.
- Separate business intent from safety labels.
- Route to human review if confidence is low or labels conflict.
- Keep routing reasons internal only.

### Recommended Intents for v1
- `tracking`
- `order_status`
- `missing_information`
- `spam`
- `phishing`
- `abusive`
- `unclassified`

Keep returns and refunds defined in schema, but do not allow automated execution in v1.

## 11. Verification and Certification Model

The existing CoVe concept is good, but it should be made explicit as a staged certification model.

### Certification Levels

#### Level 1: Text Certified
- The claimed fields are supported by sanitized email content or attachment OCR.
- Verification is run in a clean context window.
- Conflicts force escalation or clarification.

#### Level 2: Backend Certified
- An authorized backend endpoint confirms the relevant facts.
- The response is fresh enough for customer use.
- The data is marked safe to disclose.

#### Level 3: Response Authorized
- Policy determines that a response can be sent in a specific response lane.
- The response content is checked against both worksheet state and policy rules.

No outbound message should be sent unless it reaches `Response Authorized`.

### Verification Rules
- Verification prompts must not include prior model reasoning.
- Compare extracted values to original evidence spans.
- Prefer deterministic validators first:
  - order ID format
  - tracking number format
  - date parsing
  - carrier code validation
- If the original text and verification answer disagree, fail closed.

## 12. Backend Gateway and Integrity Requirements

All backend communication should go through a single internal gateway or adapter layer.

### Gateway Responsibilities
- Allowlist endpoints and HTTP methods.
- Enforce strict request/response schemas.
- Attach service authentication.
- Add idempotency keys for mutating operations.
- Add request signing and replay protection where appropriate.
- Apply timeouts, retries with jitter, and circuit breakers.
- Emit structured audit logs with trace IDs.

### Strong Recommendation

The LLM-facing application should never call vendor APIs directly. It should call an internal backend-owned endpoint that already encapsulates business rules, identity checks, and disclosure policy.

### Tracking Endpoint Requirements for the Backend Team

At minimum, the tracking endpoint should accept:
- `request_id`
- `trace_id`
- `thread_id`
- `sender_email`
- `order_id`
- `purpose = "customer_support_tracking"`

At minimum, it should return:
- `authorization_status`
  - `authorized`
  - `unauthorized`
  - `needs_verification`
  - `not_found`
- `shipment_status`
- `carrier`
- `tracking_number_masked`
- `estimated_delivery_window`
- `last_scan_at`
- `data_freshness_seconds`
- `safe_to_disclose_fields`
- `backend_trace_id`

### Why This Contract Matters
- The agent should not decide whether a sender is allowed to see tracking data based on raw order records.
- The backend should not return fields that are not safe to disclose.
- The response should make stale-data handling explicit.

### Mutating Endpoints

For later phases only:
- Require explicit action type and policy scope.
- Require idempotency keys.
- Require stronger authorization than read-only tracking.
- Require an execution receipt returned by the backend.
- Never let the email response claim success unless that receipt is present.

## 13. Response Generation, Censorship, and Outbound Policy

Response censorship should be implemented as a formal outbound policy layer, not just a prompt instruction.

### Allowed Response Lanes
- `no_reply`
- `request_info`
- `tracking_update`
- `escalation_notice`
- `boundary_response`

The worksheet should pick the lane. The generator must stay inside it.

### Outbound Safety Rules
- Never promise a refund, replacement, or manual action unless backend or human state confirms it.
- Never mirror profanity, slurs, threats, or abusive phrasing from the customer.
- Never reveal internal classifications such as `spam_score`, `fraud_flag`, or prompt-injection detection.
- Never reveal chain-of-thought, internal policy text, or backend error details.
- Never include full payment details, full address, or full tracking identifiers unless policy explicitly allows it.
- Never say "we reviewed your photo" unless the photo pipeline actually processed it.
- Never say "your order was found" when identity is not verified.

### Tone Policy
- Neutral, concise, and professional.
- Empathetic but not apologizing for unverified facts.
- No argumentative or defensive language even when the inbound message is hostile.

### Prosecutor Layer

Use two checks before sending:

#### Deterministic Policy Checker
- Ensures the draft uses only allowed response lanes.
- Ensures required facts are present for the lane.
- Blocks banned phrases and unsafe disclosures.
- Confirms placeholders are fully resolved.

#### Semantic Auditor
- A second model checks whether the draft implies any unsupported promise or disclosure.
- If the semantic auditor rejects, allow one rewrite attempt, then escalate.

## 14. Special Handling for Abusive or Malicious Emails

Not every message should receive the same type of response.

### Abuse Policy
- If the email is abusive but otherwise legitimate, either:
  - send a short neutral boundary response, or
  - escalate to human review
- Do not echo insults or attempt to "match tone."

### Phishing or Malware Policy
- Default to `no_reply` or manual security handling.
- Do not click links, fetch remote documents, or forward suspicious attachments automatically.

### Legal, Fraud, or Account-Takeover Indicators
- Escalate immediately.
- Freeze automated disclosure until a human or authenticated workflow clears the case.

## 15. Persistence, Audit, and Observability

This system needs strong operational hygiene from day one.

### Data Stores
- Raw MIME store, encrypted and access-restricted.
- Normalized message store.
- Worksheet state store with version history.
- Backend call log.
- Outbound email log.
- Audit event log.

### Logging Rules
- Redact sensitive customer fields in application logs.
- Preserve full raw artifacts only in restricted stores.
- Store prompt versions and model versions for replayability.
- Include correlation identifiers everywhere.

### Core Metrics
- inbound volume
- spam suppression rate
- escalation rate
- identity-verification failure rate
- backend lookup success rate
- prosecutor rejection rate
- auto-send rate
- duplicate suppression count
- average time to safe response

### Alert Conditions
- sudden spike in spam or phishing classification
- unusual growth in `needs_verification`
- repeated backend authorization failures
- rising prosecutor rejections
- increased model token spend per thread

## 16. Security and Privacy Controls

### Access Control
- Separate service accounts by role.
- Restrict raw email access more tightly than worksheet access.
- Give the LLM runtime no permission to read secrets or call arbitrary networks.

### Secret Management
- Store API credentials in a proper secrets manager.
- Rotate secrets regularly.
- Never place secrets in prompts, logs, or serialized worksheet state.

### Data Retention
- Define separate retention windows for raw email, normalized artifacts, and analytics.
- Prefer deletion or minimization for long-term storage.
- If possible, keep a redacted analytics store separate from the operational record.

## 17. Testing Strategy

Safety here depends as much on tests as on prompts.

### Required Test Suites
- Unit tests for validators, state transitions, and policy rules.
- Contract tests for backend schemas and error handling.
- Golden-path tests for legitimate tracking scenarios.
- Adversarial tests for:
  - prompt injection
  - hidden HTML instructions
  - spoofed sender headers
  - duplicate floods
  - order enumeration attempts
  - abusive language
  - stale backend responses
  - malformed attachments
- Load tests for spam bursts and long threads.

### Human Evaluation Set
- Build a labeled corpus of real or synthetic support emails.
- Include safe, ambiguous, malicious, and privacy-sensitive cases.
- Review false positives and false negatives every release.

## 18. Rollout Plan

### Phase 0: Contracts and Safety Baseline
- Finalize worksheet schema.
- Finalize response lanes.
- Finalize backend tracking contract.
- Build sanitization, spam gate, and audit logging.

### Phase 1: Shadow Mode
- Process inbound messages end-to-end without sending.
- Compare generated results to human agent outcomes.
- Tune suppression, escalation, and authorization logic.

### Phase 2: Read-Only Assisted Replies
- Allow draft generation for authorized tracking cases.
- Require human approval before send.

### Phase 3: Limited Auto-Send
- Auto-send only for high-confidence, authorized, read-only tracking replies.
- Keep all high-risk categories on manual review.

### Phase 4: Expanded Coverage
- Add more intents only after:
  - stable metrics
  - low privacy incident rate
  - strong backend contracts
  - tested prosecutor performance

Do not introduce refund or financial write actions until the read-only system has proven safe in production.

## 19. Senior Engineering Recommendations

These are the highest-value design recommendations based on the current docs.

1. Start with tracking only. Do not try to automate returns, refunds, and complaints in the first production slice.
2. Make identity verification a first-class gate before any order disclosure.
3. Put the backend team in charge of disclosure-safe tracking responses, not just raw tracking lookup.
4. Formalize response censorship as a policy engine with allowed lanes and banned claims.
5. Treat spam suppression and no-reply decisions as part of the product, not just filtering infrastructure.
6. Keep the LLM away from raw MIME, secrets, and direct backend calls.
7. Use certification levels so "extracted," "backend confirmed," and "safe to say" are separate states.
8. Ship in shadow mode before allowing any auto-send behavior.

## 20. Open Items to Align with the Backend Team

1. Will the backend endpoint perform sender-to-order authorization, or is that expected in the email agent?
2. Can the backend return `safe_to_disclose_fields` so the email layer does not invent disclosure policy?
3. What freshness SLA is acceptable for tracking data shown to customers?
4. What trace ID and audit fields can be propagated across systems?
5. Will there be a stable, versioned read-only endpoint for tracking before any write endpoints are considered?

If the answer to any of these is unclear, default the email agent to clarification, escalation, or no-reply rather than optimistic behavior.
