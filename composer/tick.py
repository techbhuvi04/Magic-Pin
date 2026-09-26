"""Tick eligibility policy: urgency, suppression, consent, expiry, caps."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from state.store import ContextStore

from .compose import compose

HIGH_PRIORITY_KINDS = {
    "supply_alert",
    "renewal_due",
    "recall_due",
    "regulation_change",
    "active_planning_intent",
    "perf_dip",
    "appointment_tomorrow",
}

TEMPLATE_BY_KIND = {
    "research_digest": "vera_research_digest_v1",
    "recall_due": "merchant_recall_reminder_v1",
    "renewal_due": "vera_renewal_due_v1",
    "perf_dip": "vera_perf_dip_v1",
    "curious_ask_due": "vera_curious_ask_v1",
    "festival_upcoming": "vera_festival_v1",
    "regulation_change": "vera_compliance_v1",
    "winback_eligible": "vera_winback_v1",
    "ipl_match_today": "vera_ipl_v1",
    "review_theme_emerged": "vera_review_theme_v1",
    "milestone_reached": "vera_milestone_v1",
    "seasonal_perf_dip": "vera_seasonal_v1",
    "competitor_opened": "vera_competitor_v1",
    "supply_alert": "vera_supply_alert_v1",
    "active_planning_intent": "vera_planning_v1",
    "wedding_package_followup": "merchant_bridal_followup_v1",
}


def _parse_now(now: Optional[str]) -> datetime:
    if not now:
        return datetime.now(timezone.utc)
    try:
        text = now.replace("Z", "+00:00")
        return datetime.fromisoformat(text)
    except Exception:
        return datetime.now(timezone.utc)


def _is_expired(trigger: dict, now: datetime) -> bool:
    exp = trigger.get("expires_at")
    if not exp:
        return False
    try:
        exp_dt = datetime.fromisoformat(str(exp).replace("Z", "+00:00"))
        if exp_dt.tzinfo is None:
            exp_dt = exp_dt.replace(tzinfo=timezone.utc)
        return now > exp_dt
    except Exception:
        return False


def _consent_ok(trigger: dict, customer: Optional[dict]) -> bool:
    if trigger.get("scope") != "customer" and not trigger.get("customer_id"):
        return True
    if not customer:
        return False
    consent = customer.get("consent") or {}
    scope = consent.get("scope")
    if scope is None:
        return False
    if isinstance(scope, list) and len(scope) == 0:
        return False
    if consent.get("opted_in_at") is None and (not scope):
        return False
    # map trigger kinds to consent scopes loosely
    kind = trigger.get("kind") or ""
    if not scope:
        return False
    needed = {
        "recall_due": "recall",
        "appointment_tomorrow": "appointment",
        "wedding_package_followup": "bridal",
    }
    token = needed.get(kind)
    if token:
        return any(token in str(s).lower() for s in scope) or any(
            "reminder" in str(s).lower() or "promotional" in str(s).lower() for s in scope
        )
    return True


def _urgency(trigger: dict) -> int:
    try:
        return int(trigger.get("urgency") or 1)
    except Exception:
        return 1


def _priority_score(trigger: dict) -> int:
    u = _urgency(trigger)
    kind = trigger.get("kind") or ""
    bonus = 10 if kind in HIGH_PRIORITY_KINDS else 0
    return u * 10 + bonus


def _template_params(body: str, merchant: dict, customer: Optional[dict]) -> list[str]:
    owner = ((merchant.get("identity") or {}).get("owner_first_name") or "").split()[0]
    # split body into ~3 chunks for WhatsApp-style templates
    parts = [p.strip() for p in body.replace("?", "?").split(". ") if p.strip()]
    if len(parts) == 1:
        return [owner or "there", body[:200], "Reply to continue"]
    if len(parts) == 2:
        return [owner or "there", parts[0][:200], parts[1][:200]]
    return [owner or "there", ". ".join(parts[:-1])[:220], parts[-1][:160]]


def eligible_triggers(
    store: ContextStore,
    available_trigger_ids: list[str],
    now: datetime,
) -> list[tuple[str, dict, dict, dict, Optional[dict]]]:
    """
    Returns list of (trigger_id, trigger, merchant, category, customer)
    for eligible sends, sorted by priority.
    """
    candidates = []
    store.merchant_messaged_this_tick = set()

    for tid in available_trigger_ids:
        trigger = store.get("trigger", tid)
        if not trigger:
            continue
        # normalize id onto trigger
        trigger = {**trigger, "id": trigger.get("id") or tid}

        if _is_expired(trigger, now):
            continue
        sk = trigger.get("suppression_key")
        if store.is_suppressed(sk):
            continue

        mid = trigger.get("merchant_id")
        if not mid:
            # try payload
            mid = (trigger.get("payload") or {}).get("merchant_id")
        if not mid:
            continue
        merchant = store.get("merchant", mid)
        if not merchant:
            continue

        if store.has_open_conversation(mid) and trigger.get("scope") != "customer":
            # avoid stacking merchant threads
            continue

        cid = trigger.get("customer_id")
        customer = store.get("customer", cid) if cid else None
        if not _consent_ok(trigger, customer):
            continue

        category = store.resolve_merchant_category(merchant) or {}
        urgency = _urgency(trigger)
        kind = trigger.get("kind") or ""

        # spam budget: skip low urgency if merchant already messaged this tick
        if urgency < 3 and mid in store.merchant_messaged_this_tick and kind not in HIGH_PRIORITY_KINDS:
            continue

        candidates.append((tid, trigger, merchant, category, customer))

    candidates.sort(key=lambda x: _priority_score(x[1]), reverse=True)
    return candidates


def run_tick(store: ContextStore, now: Optional[str], available_triggers: list[str]) -> dict[str, Any]:
    now_dt = _parse_now(now)
    store.merchant_messaged_this_tick = set()
    actions: list[dict] = []
    eligible = eligible_triggers(store, available_triggers or [], now_dt)

    for tid, trigger, merchant, category, customer in eligible:
        if len(actions) >= 20:
            break
        mid = merchant.get("merchant_id") or trigger.get("merchant_id")
        # low urgency skip if already sent to this merchant in this tick
        if _urgency(trigger) < 4 and mid in store.merchant_messaged_this_tick:
            continue

        history = []
        for turn in (merchant.get("conversation_history") or [])[-5:]:
            if isinstance(turn, dict) and turn.get("body"):
                history.append(turn["body"])

        composed = compose(category, merchant, trigger, customer, history)
        body = composed["body"]
        cta = composed["cta"]
        send_as = composed["send_as"]
        sk = composed["suppression_key"]

        conv = store.create_conversation(
            merchant_id=mid,
            customer_id=trigger.get("customer_id"),
            trigger_id=tid,
            send_as=send_as,
            body=body,
            cta=cta,
        )
        store.mark_suppression(sk)
        store.merchant_messaged_this_tick.add(mid)

        kind = trigger.get("kind") or "generic"
        action = {
            "conversation_id": conv.conversation_id,
            "merchant_id": mid,
            "customer_id": trigger.get("customer_id"),
            "send_as": send_as,
            "trigger_id": tid,
            "template_name": TEMPLATE_BY_KIND.get(kind, f"vera_{kind}_v1"),
            "template_params": _template_params(body, merchant, customer),
            "body": body,
            "cta": cta,
            "suppression_key": sk,
            "rationale": composed.get("rationale") or "",
        }
        actions.append(action)

    return {"actions": actions}
