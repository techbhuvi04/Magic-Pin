"""Kind-dispatched prompt builders for LLM composition."""

from __future__ import annotations

import json
from typing import Optional

from .extract import (
    active_offers,
    language_pref,
    owner_first,
    peer_stats_snippet,
    top_digest_items,
)


SYSTEM_BASE = """You are Vera, magicpin's WhatsApp merchant AI assistant for Indian local businesses.
Compose ONE WhatsApp message from the four contexts. Be a peer colleague, not a salesperson.

HARD RULES:
- Never put URLs in the body.
- Never invent facts, offers, competitors, buildings, phones, or research not in context.
- Cite only context facts (numbers, sources, offer titles).
- Exactly one primary CTA, last sentence.
- No taboo vocabulary from category voice.
- Prefer service+price over generic % discounts (pharmacies may use %).
- Use social proof and/or asking-the-merchant when natural.
- Keep concise (2-5 short sentences).
- Match language preference (Hindi-English mix when asked).
- For seasonal expected dips: reframe, don't panic-sell.
- For IPL Saturday/weeknight constraints: skip match promo if payload says is_weeknight false and pitching a match special would be wrong for the case — prefer a useful non-match angle if context supports restraint.

Return ONLY JSON:
{"body":"...","cta":"binary_yes_no|binary_confirm_cancel|open_ended|multi_choice_slot|none","rationale":"..."}
"""


KIND_HINTS = {
    "research_digest": "Lead with the digest item + source citation. Open-ended CTA to pull abstract/draft.",
    "regulation_change": "Deadline + actionable compliance step. Binary CTA.",
    "recall_due": "Customer-facing; address correct person (parent for kids). Multi-choice slots + price if available.",
    "perf_dip": "Anchor on actual delta numbers. Peer tone. Offer one concrete next step.",
    "perf_spike": "Celebrate the spike with numbers; ask what drove it.",
    "renewal_due": "Days remaining + value reminder; binary renew CTA.",
    "festival_upcoming": "Only if category_relevance includes this category; service+price tie-in.",
    "curious_ask_due": "Ask the merchant a specific curiosity question; open_ended.",
    "winback_eligible": "Loss aversion with real lapse numbers; soft binary.",
    "ipl_match_today": "Only pitch match promo if it fits operator reality; else skip-angle / ask preference.",
    "review_theme_emerged": "Quote the theme count; propose one fix draft.",
    "milestone_reached": "Congratulate with the number; invite a celebration post draft.",
    "seasonal_perf_dip": "Acknowledge expected seasonality; reframe retention not panic acquisition.",
    "competitor_opened": "Only name competitor if in payload; focus on differentiation.",
    "supply_alert": "Urgency on batch/supply; binary confirm.",
    "active_planning_intent": "Deliver the drafted artifact immediately — no re-qualify.",
    "wedding_package_followup": "Customer bridal timeline; slots/next step.",
    "appointment_tomorrow": "Confirm appointment details; short.",
}


def build_user_prompt(
    category: dict,
    merchant: dict,
    trigger: dict,
    customer: Optional[dict] = None,
    history_bodies: Optional[list[str]] = None,
) -> str:
    kind = trigger.get("kind", "")
    hint = KIND_HINTS.get(kind, "Compose a helpful, specific outreach for this trigger.")
    offers = active_offers(merchant)
    digests = top_digest_items(category, trigger, 3)
    lang = language_pref(merchant, customer)
    voice = category.get("voice") or {}

    ctx = {
        "kind_hint": hint,
        "owner_first_name": owner_first(merchant),
        "merchant_identity": merchant.get("identity"),
        "merchant_performance": merchant.get("performance"),
        "merchant_signals": merchant.get("signals"),
        "merchant_subscription": merchant.get("subscription"),
        "active_offers": offers,
        "customer_aggregate": merchant.get("customer_aggregate"),
        "category_slug": category.get("slug"),
        "voice_tone": voice.get("tone"),
        "taboos": voice.get("vocab_taboo") or voice.get("taboos"),
        "peer_stats": peer_stats_snippet(category),
        "digest_items": digests,
        "seasonal_beats": (category.get("seasonal_beats") or [])[:2],
        "trend_signals": (category.get("trend_signals") or [])[:2],
        "trigger": {
            "id": trigger.get("id"),
            "kind": kind,
            "scope": trigger.get("scope"),
            "urgency": trigger.get("urgency"),
            "payload": trigger.get("payload"),
            "suppression_key": trigger.get("suppression_key"),
        },
        "customer": customer,
        "language_pref": lang,
        "avoid_repeating": (history_bodies or [])[-3:],
        "preferred_cta_hint": hint,
    }
    return (
        "Compose the WhatsApp message from this context JSON.\n"
        + json.dumps(ctx, ensure_ascii=False, default=str)[:12000]
    )
