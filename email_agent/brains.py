from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Protocol

from .models import Intent, NormalizedEmail, RoutingDecision, SafetyAssessment


class SupportBrain(Protocol):
    def route(self, email: NormalizedEmail, safety: SafetyAssessment) -> RoutingDecision:
        ...

    def summarize(self, email: NormalizedEmail) -> str:
        ...


@dataclass(slots=True)
class HeuristicSupportBrain:
    def route(self, email: NormalizedEmail, safety: SafetyAssessment) -> RoutingDecision:
        text = f"{email.subject}\n{email.latest_message_text}".lower()
        if Intent.PHISHING in safety.labels:
            return RoutingDecision(Intent.PHISHING, 0.99, "Suspicious links or phishing markers.")
        if Intent.SPAM in safety.labels:
            return RoutingDecision(Intent.SPAM, 0.95, "Bulk or marketing email markers.")
        if any(token in text for token in ("track", "tracking", "where is my package", "shipment")):
            return RoutingDecision(Intent.TRACKING, 0.91, "Customer asked for shipment tracking.")
        if any(token in text for token in ("order status", "order update", "order")):
            return RoutingDecision(Intent.ORDER_STATUS, 0.82, "Customer asked about order status.")
        if Intent.ABUSIVE in safety.labels:
            return RoutingDecision(Intent.ABUSIVE, 0.88, "Abusive content detected.")
        return RoutingDecision(Intent.UNCLASSIFIED, 0.45, "No confident intent match.")

    def summarize(self, email: NormalizedEmail) -> str:
        for line in email.latest_message_text.splitlines():
            cleaned = line.strip()
            if cleaned:
                return cleaned[:180]
        return "Customer asked for support."


class LangChainSupportBrain:
    """Optional adapter for a LangChain chat model.

    The orchestrator does not depend on LangChain for control flow, but this lets
    us plug a chat model into routing and summarization later without changing the
    rest of the application.
    """

    def __init__(self, router_chain, summary_chain):
        self._router_chain = router_chain
        self._summary_chain = summary_chain

    @classmethod
    def from_chat_model(cls, chat_model):
        try:
            from langchain_core.output_parsers import StrOutputParser
            from langchain_core.prompts import ChatPromptTemplate
        except ImportError as exc:
            raise RuntimeError(
                "LangChain support requires the optional dependency group: pip install .[llm]"
            ) from exc

        router_prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    "Classify the email intent. Return compact JSON with keys: intent, confidence, reasoning. "
                    "Valid intents: tracking, order_status, missing_information, spam, phishing, abusive, unclassified.",
                ),
                (
                    "human",
                    "Subject: {subject}\nSender: {sender}\nSafety labels: {labels}\n\nLatest message:\n{body}",
                ),
            ]
        )
        summary_prompt = ChatPromptTemplate.from_messages(
            [
                ("system", "Summarize the customer's request in one short sentence."),
                ("human", "Subject: {subject}\n\n{body}"),
            ]
        )
        parser = StrOutputParser()
        return cls(router_prompt | chat_model | parser, summary_prompt | chat_model | parser)

    def route(self, email: NormalizedEmail, safety: SafetyAssessment) -> RoutingDecision:
        raw = self._router_chain.invoke(
            {
                "subject": email.subject,
                "sender": email.sender_email,
                "labels": ", ".join(label.value for label in sorted(safety.labels, key=lambda item: item.value)) or "none",
                "body": email.latest_message_text,
            }
        )
        try:
            payload = json.loads(raw)
            intent = Intent(payload.get("intent", Intent.UNCLASSIFIED.value))
            confidence = float(payload.get("confidence", 0.0))
            reasoning = str(payload.get("reasoning", "LangChain route."))
            return RoutingDecision(intent=intent, confidence=confidence, reasoning=reasoning)
        except (ValueError, TypeError, json.JSONDecodeError):
            return RoutingDecision(
                intent=Intent.UNCLASSIFIED,
                confidence=0.0,
                reasoning="LangChain output could not be parsed safely.",
            )

    def summarize(self, email: NormalizedEmail) -> str:
        summary = self._summary_chain.invoke({"subject": email.subject, "body": email.latest_message_text})
        return str(summary).strip()[:180] or "Customer asked for support."
