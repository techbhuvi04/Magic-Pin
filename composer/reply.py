"""Multi-turn reply handler: auto-reply, intent, hostile, off-topic."""

from __future__ import annotations

import re
from typing import Any, Optional

from state.store import ContextStore

from .compose import compose
from .extract import owner_first
from .validate import normalize_body

AUTO_REPLY_PATTERNS = [
    r"thank you for contacting",
    r"our team will (get back|respond|reply)",
    r"we are currently unavailable",
    r"automated (assistant|reply|response)",
    r"aapki jaankari ke liye bahut",
    r"main ek automated",
    r"thanks for (your )?message",
    r"we(’|'|)ll get back to you",
    r"business hours are",
    r"leave a message",
]

HOSTILE_PATTERNS = [
    r"\bstop\b",
    r"\bunsubscribe\b",
    r"\bspam\b",
    r"not interested",
    r"don't (message|text|contact)",
    r"do not (message|text|contact)",
    r"band karo",
    r"mat bhejo",
    r"useless",
    r"fuck",
    r"idiot",
    r"harassment",
]

INTENT_PATTERNS = [
    r"\blet'?s do it\b",
    r"\bok let'?s\b",
    r"\bgo ahead\b",
    r"\bjudrna\b",
    r"\bjudna\b",
    r"\bi want to join\b",
    r"\byes please\b",
    r"\byes,? (do|send|please|go)\b",
    r"\bconfirm\b",
    r"\bproceed\b",
    r"\bsend (it|the|now)\b",
    r"\bdraft\b.*\bsend\b",
    r"\bwhats next\b",
    r"\bwhat'?s next\b",
    r"\bok(ay)?\b.*\bdo it\b",
]

OFF_TOPIC_PATTERNS = [
    r"\bgst\b",
    r"\bincome tax\b",
    r"\bpan card\b",
    r"\bloan\b",
    r"\bhire (a )?developer\b",
    r"\bfile my\b",
    r"\baccounting\b",
    r"\bca\b.*\bhelp\b",
]


def _is_auto_reply(message: str) -> bool:
    lower = (message or "").lower()
    return any(re.search(p, lower) for p in AUTO_REPLY_PATTERNS)


def _is_hostile(message: str) -> bool:
    lower = (message or "").lower()
    return any(re.search(p, lower) for p in HOSTILE_PATTERNS)


def _is_intent(message: str) -> bool:
    lower = (message or "").lower()
    return any(re.search(p, lower) for p in INTENT_PATTERNS)


def _is_off_topic(message: str) -> bool:
    lower = (message or "").lower()
    return any(re.search(p, lower) for p in OFF_TOPIC_PATTERNS)


def _action_mode_body(merchant: dict, trigger: Optional[dict], message: str) -> str:
    owner = owner_first(merchant) or "there"
    kind = (trigger or {}).get("kind") or "request"
    payload = (trigger or {}).get("payload") or {}
    topic = payload.get("intent_topic") or payload.get("top_item_id") or kind
    return (
        f"Done — switching to action mode, {owner}. "
        f"I'm sending the draft for {str(topic).replace('_', ' ')} now and confirming next steps. "
        f"Proceeding without more qualification questions."
    )


def _soft_retry_body(merchant: dict) -> str:
    owner = owner_first(merchant) or "there"
    return (
        f"Samajh gayi, {owner}. If this was an auto-reply, no worries — "
        f"when you're free, reply YES and I'll continue with the one concrete next step."
    )


def _off_topic_redirect(merchant: dict, trigger: Optional[dict]) -> str:
    owner = owner_first(merchant) or "there"
    kind = (trigger or {}).get("kind") or "the earlier note"
    return (
        f"I'll leave that to your CA/specialist, {owner} — outside what I can do directly. "
        f"Coming back to {str(kind).replace('_', ' ')}: want me to continue with the draft, or pause?"
    )


def handle_reply(
    store: ContextStore,
    conversation_id: str,
    merchant_id: str,
    message: str,
    turn_number: int = 1,
    customer_id: Optional[str] = None,
    from_role: str = "merchant",
) -> dict[str, Any]:
    conv = store.ensure_conversation(conversation_id, merchant_id, customer_id)
    if conv.ended or conversation_id in store.ended_conversations:
        return {
            "action": "end",
            "rationale": "Conversation already ended",
        }

    store.append_turn(conversation_id, from_role or "merchant", message)
    merchant = store.get("merchant", merchant_id) or {
        "merchant_id": merchant_id,
        "identity": {"owner_first_name": "there", "name": merchant_id},
        "category_slug": "dentists",
        "performance": {},
        "offers": [],
        "signals": [],
    }
    trigger = store.get("trigger", conv.trigger_id) if conv.trigger_id else None
    category = store.resolve_merchant_category(merchant) or {"slug": merchant.get("category_slug"), "voice": {}}
    customer = store.get("customer", customer_id or conv.customer_id) if (customer_id or conv.customer_id) else None

    norm = normalize_body(message)

    # --- hostile / opt-out ---
    if _is_hostile(message):
        store.end_conversation(conversation_id)
        return {
            "action": "end",
            "rationale": "Merchant opted out or hostile; ending gracefully",
        }

    # --- auto-reply detection ---
    is_auto = _is_auto_reply(message)
    same_as_last = bool(conv.last_merchant_normalized and conv.last_merchant_normalized == norm)
    if is_auto or same_as_last:
        conv.auto_reply_count += 1
        store.merchant_auto_reply_hits[merchant_id] = store.merchant_auto_reply_hits.get(merchant_id, 0) + 1
        merchant_hits = store.merchant_auto_reply_hits[merchant_id]

        # Pattern B: one soft retry, then wait/end
        if conv.auto_reply_count == 1 and merchant_hits <= 1:
            body = _soft_retry_body(merchant)
            store.append_turn(conversation_id, "vera", body, cta="binary_yes_no")
            conv.last_merchant_normalized = norm
            return {
                "action": "send",
                "body": body,
                "cta": "binary_yes_no",
                "rationale": "First canned auto-reply — one soft retry",
            }
        if conv.auto_reply_count >= 3 or merchant_hits >= 3:
            store.end_conversation(conversation_id)
            return {
                "action": "end",
                "rationale": "Repeated auto-reply pattern (3+); ending",
            }
        conv.last_merchant_normalized = norm
        return {
            "action": "wait",
            "wait_seconds": 14400,
            "rationale": "Detected merchant auto-reply; backing off 4 hours",
        }

    conv.last_merchant_normalized = norm
    conv.auto_reply_count = 0

    # --- intent handoff ---
    if _is_intent(message):
        body = _action_mode_body(merchant, trigger, message)
        # enrich with compose artifact if we have trigger context
        if trigger and category:
            try:
                drafted = compose(category, merchant, trigger, customer, [t.body for t in conv.turns])
                # Prefer action language
                body = (
                    f"Done — sending now. Draft ready: {drafted['body'][:280]} "
                    f"Confirm if you want me to schedule it."
                )
                if not any(w in body.lower() for w in ("done", "sending", "draft")):
                    body = "Done — sending the draft now. " + body
            except Exception:
                pass
        store.append_turn(conversation_id, "vera", body, cta="binary_confirm_cancel")
        return {
            "action": "send",
            "body": body,
            "cta": "binary_confirm_cancel",
            "rationale": "Explicit commit detected — action mode, no re-qualify",
        }

    # --- off-topic ---
    if _is_off_topic(message):
        body = _off_topic_redirect(merchant, trigger)
        store.append_turn(conversation_id, "vera", body, cta="open_ended")
        return {
            "action": "send",
            "body": body,
            "cta": "open_ended",
            "rationale": "Off-mission ask declined; redirect to thread",
        }

    # --- continue helpful turn ---
    history = [t.body for t in conv.turns if t.role == "vera"]
    if trigger and category:
        # recompose a follow-up shaped reply
        follow_trigger = {
            **trigger,
            "kind": "active_planning_intent" if "yes" in norm else trigger.get("kind"),
            "payload": {
                **(trigger.get("payload") or {}),
                "merchant_last_message": message[:200],
                "intent_topic": (trigger.get("payload") or {}).get("intent_topic")
                or trigger.get("kind"),
            },
        }
        composed = compose(category, merchant, follow_trigger, customer, history)
        body = composed["body"]
        # avoid re-intro
        if body.lower().startswith("hi ") and turn_number and turn_number > 1:
            body = re.sub(r"^Hi [^,]+,\s*", "", body, flags=re.I)
        cta = composed.get("cta") or "open_ended"
        rationale = "Continue helpful turn from context"
    else:
        owner = owner_first(merchant) or "there"
        body = (
            f"Got it, {owner}. I'm on it — sending the next concrete step from your latest context. "
            f"Draft coming up; reply STOP anytime to pause."
        )
        cta = "open_ended"
        rationale = "Continue without trigger context"

    # 3 unanswered nudges — if merchant message is empty-ish we wouldn't be here;
    # track soft non-answers
    if len(norm) < 3:
        conv.unanswered_nudges += 1
        if conv.unanswered_nudges >= 3:
            store.end_conversation(conversation_id)
            return {"action": "end", "rationale": "3 unanswered nudges — stopping"}

    store.append_turn(conversation_id, "vera", body, cta=cta)
    return {
        "action": "send",
        "body": body,
        "cta": cta,
        "rationale": rationale,
    }
