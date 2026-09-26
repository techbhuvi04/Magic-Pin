"""Extract verifiable anchors from the four contexts."""

from __future__ import annotations

import re
from typing import Any, Optional


def active_offers(merchant: dict) -> list[dict]:
    offers = merchant.get("offers") or []
    return [o for o in offers if isinstance(o, dict) and o.get("status") == "active"]


def digest_by_id(category: dict, item_id: Optional[str]) -> Optional[dict]:
    if not item_id:
        return None
    for item in category.get("digest") or []:
        if isinstance(item, dict) and item.get("id") == item_id:
            return item
    return None


def top_digest_items(category: dict, trigger: dict, limit: int = 3) -> list[dict]:
    digest = [d for d in (category.get("digest") or []) if isinstance(d, dict)]
    payload = trigger.get("payload") or {}
    top_id = payload.get("top_item_id")
    ranked: list[dict] = []
    if top_id:
        hit = digest_by_id(category, top_id)
        if hit:
            ranked.append(hit)
    # also accept embedded top_item
    top_item = payload.get("top_item")
    if isinstance(top_item, dict) and top_item not in ranked:
        ranked.append(top_item)
    for d in digest:
        if d not in ranked:
            ranked.append(d)
        if len(ranked) >= limit:
            break
    return ranked[:limit]


def owner_first(merchant: dict) -> str:
    ident = merchant.get("identity") or {}
    name = ident.get("owner_first_name") or ""
    if name:
        return name.split()[0]
    full = ident.get("name") or "there"
    # "Dr. Meera's Dental Clinic" → Meera
    m = re.search(r"Dr\.?\s*([A-Za-z]+)", full)
    if m:
        return m.group(1)
    return full.split()[0]


def customer_display_name(customer: dict) -> str:
    ident = customer.get("identity") or {}
    name = ident.get("name") or "there"
    # "Aanya (parent: Sneha)" → address parent Sneha when messaging
    m = re.search(r"parent:\s*([A-Za-z]+)", name, re.I)
    if m:
        return m.group(1)
    m = re.search(r"^([A-Za-z]+)", name)
    return m.group(1) if m else name


def language_pref(merchant: dict, customer: Optional[dict] = None) -> str:
    if customer:
        pref = ((customer.get("identity") or {}).get("language_pref") or "").lower()
        if pref:
            return pref
    langs = (merchant.get("identity") or {}).get("languages") or []
    if "hi" in langs:
        return "hi-en mix"
    return "en"


def peer_stats_snippet(category: dict) -> str:
    ps = category.get("peer_stats") or {}
    parts = []
    if "avg_ctr" in ps:
        parts.append(f"peer CTR {ps['avg_ctr']}")
    if "avg_rating" in ps:
        parts.append(f"avg rating {ps['avg_rating']}")
    if "avg_review_count" in ps:
        parts.append(f"avg reviews {ps['avg_review_count']}")
    return ", ".join(parts)


def performance_anchors(merchant: dict) -> list[str]:
    perf = merchant.get("performance") or {}
    anchors = []
    for k in ("views", "calls", "directions", "ctr", "leads"):
        if k in perf and perf[k] is not None:
            anchors.append(f"{k}={perf[k]}")
    delta = perf.get("delta_7d") or {}
    for k, v in delta.items():
        if v is not None:
            anchors.append(f"delta_7d.{k}={v}")
    return anchors


def collect_fact_anchors(
    category: dict,
    merchant: dict,
    trigger: dict,
    customer: Optional[dict] = None,
) -> list[str]:
    """Concrete strings the validator can check are present (or digit/source)."""
    anchors: list[str] = []
    for o in active_offers(merchant):
        title = o.get("title")
        if title:
            anchors.append(str(title))
    for d in top_digest_items(category, trigger, 5):
        if d.get("source"):
            anchors.append(str(d["source"]))
        if d.get("title"):
            # short distinctive tokens from title
            anchors.append(str(d["title"])[:40])
        if d.get("trial_n"):
            anchors.append(str(d["trial_n"]))
    payload = trigger.get("payload") or {}
    for k in ("delta_pct", "days_remaining", "days_until", "occurrences_30d", "value_now", "milestone_value"):
        if k in payload and payload[k] is not None:
            anchors.append(str(payload[k]))
    for a in performance_anchors(merchant):
        # extract numeric portion
        m = re.search(r"(-?\d+\.?\d*)", a)
        if m:
            anchors.append(m.group(1))
    if customer:
        rel = customer.get("relationship") or {}
        if rel.get("last_visit"):
            anchors.append(str(rel["last_visit"]))
        if rel.get("visits_total") is not None:
            anchors.append(str(rel["visits_total"]))
    return [a for a in anchors if a]


def trigger_kind_cta(kind: str) -> str:
    """Default CTA by trigger kind."""
    kind = (kind or "").lower()
    info_kinds = {
        "research_digest", "milestone_reached", "curious_ask_due",
        "category_trend_movement", "local_news_event",
    }
    booking_kinds = {"recall_due", "appointment_tomorrow", "wedding_package_followup"}
    action_kinds = {
        "perf_dip", "perf_spike", "renewal_due", "winback_eligible",
        "regulation_change", "supply_alert", "competitor_opened",
        "festival_upcoming", "review_theme_emerged", "seasonal_perf_dip",
        "active_planning_intent", "dormant_with_vera", "customer_lapsed_soft",
        "ipl_match_today", "weather_heatwave",
    }
    if kind in booking_kinds:
        return "multi_choice_slot"
    if kind in info_kinds:
        return "open_ended"
    if kind in action_kinds:
        return "binary_yes_no"
    return "open_ended"


def is_placeholder_payload(trigger: dict) -> bool:
    payload = trigger.get("payload") or {}
    return bool(payload.get("placeholder"))


def flatten_payload_facts(payload: dict) -> list[str]:
    facts = []
    for k, v in (payload or {}).items():
        if k == "placeholder":
            continue
        if isinstance(v, (str, int, float)) and str(v):
            facts.append(f"{k}={v}")
        elif isinstance(v, dict):
            for kk, vv in v.items():
                if isinstance(vv, (str, int, float)):
                    facts.append(f"{kk}={vv}")
        elif isinstance(v, list) and v and isinstance(v[0], dict):
            for item in v[:3]:
                label = item.get("label") or item.get("title") or item.get("iso")
                if label:
                    facts.append(str(label))
    return facts
