"""Kind-dispatched engagement composer (LLM or high-quality templates)."""

from __future__ import annotations

from typing import Any, Optional

from .extract import (
    active_offers,
    collect_fact_anchors,
    customer_display_name,
    flatten_payload_facts,
    is_placeholder_payload,
    language_pref,
    owner_first,
    peer_stats_snippet,
    top_digest_items,
    trigger_kind_cta,
)
from .llm import get_llm
from .prompts import SYSTEM_BASE, build_user_prompt
from .validate import (
    inject_anchor,
    looks_pure_english,
    strip_urls,
    validate_message,
    wants_hi_mix,
)


def _hi_touch(text: str, lang: str) -> str:
    if not wants_hi_mix(lang) or not looks_pure_english(text):
        return text
    lower = text.lower()
    if "chalega" in lower or "dekho" in lower or "aap" in lower:
        return text
    # light natural mix without rewriting whole message
    if text.rstrip().endswith("?"):
        return text.rstrip()[:-1].rstrip() + " — chalega?"
    return text.rstrip() + " Dekho once."


def _salutation(merchant: dict, category: dict) -> str:
    owner = owner_first(merchant)
    slug = category.get("slug") or merchant.get("category_slug") or ""
    if slug == "dentists" and owner:
        return f"Dr. {owner}"
    return owner or ((merchant.get("identity") or {}).get("name") or "there")


def _offer_line(merchant: dict, category: dict) -> str:
    offers = active_offers(merchant)
    if offers:
        return offers[0].get("title") or ""
    catalog = category.get("offer_catalog") or []
    if catalog and isinstance(catalog[0], dict):
        # only use catalog as soft suggestion wording if merchant has no offers —
        # but never claim merchant currently runs it as active. Phrase as idea.
        return ""
    return ""


def _template_compose(
    category: dict,
    merchant: dict,
    trigger: dict,
    customer: Optional[dict] = None,
) -> dict[str, Any]:
    kind = (trigger.get("kind") or "generic").lower()
    payload = trigger.get("payload") or {}
    if is_placeholder_payload(trigger):
        # fall back to category digest / merchant signals only
        payload = {}
    sal = _salutation(merchant, category)
    lang = language_pref(merchant, customer)
    digests = top_digest_items(category, trigger, 3)
    top = digests[0] if digests else None
    offer = _offer_line(merchant, category)
    perf = merchant.get("performance") or {}
    signals = merchant.get("signals") or []
    peer = peer_stats_snippet(category)
    locality = (merchant.get("identity") or {}).get("locality") or ""
    city = (merchant.get("identity") or {}).get("city") or ""
    agg = merchant.get("customer_aggregate") or {}
    cta = trigger_kind_cta(kind)
    body = ""
    rationale = f"kind={kind}; template composer using category+merchant+trigger"

    if kind == "research_digest" and top:
        source = top.get("source") or "category digest"
        title = top.get("title") or "new research item"
        trial = f" (n={top['trial_n']})" if top.get("trial_n") else ""
        segment = top.get("patient_segment") or top.get("summary") or ""
        body = (
            f"{sal}, {source} landed — {title}{trial}. "
            f"{('Relevant to your ' + str(segment).replace('_', ' ') + ' cohort. ') if segment else ''}"
            f"Want me to pull the 2-min abstract + draft a patient WhatsApp you can share?"
        )
        cta = "open_ended"
        rationale = "Research digest with source citation and open CTA"

    elif kind == "regulation_change" and top:
        deadline = payload.get("deadline_iso") or ""
        actionable = top.get("actionable") or top.get("summary") or top.get("title")
        body = (
            f"{sal}, compliance ping: {top.get('title')}. "
            f"{f'Deadline {deadline}. ' if deadline else ''}"
            f"{actionable}. Source: {top.get('source')}. "
            f"Want me to draft your SOP checklist?"
        )
        cta = "binary_yes_no"

    elif kind == "recall_due" and customer:
        cname = customer_display_name(customer)
        clinic = (merchant.get("identity") or {}).get("name") or "our clinic"
        slots = payload.get("available_slots") or []
        slot_txt = " / ".join(s.get("label", "") for s in slots[:2] if isinstance(s, dict))
        service = (payload.get("service_due") or "checkup").replace("_", " ")
        price = offer or "your usual service"
        last = (customer.get("relationship") or {}).get("last_visit") or ""
        body = (
            f"Hi {cname}, {clinic} here. Your {service} is due"
            f"{f' (last visit {last})' if last else ''}. "
            f"{('Slots: ' + slot_txt + '. ') if slot_txt else ''}"
            f"{price}. Reply 1 or 2 for a slot, or suggest another time."
        )
        cta = "multi_choice_slot"
        rationale = "Customer recall with slots + price; merchant_on_behalf"

    elif kind == "perf_dip":
        metric = payload.get("metric") or "calls"
        delta = payload.get("delta_pct")
        baseline = payload.get("vs_baseline")
        views = perf.get("views")
        ctr = perf.get("ctr")
        peer_ctr = (category.get("peer_stats") or {}).get("avg_ctr")
        body = (
            f"{sal}, quick heads-up from your 7d dashboard: {metric} "
            f"{'dropped ' + str(int(abs(delta)*100)) + '%' if isinstance(delta, (int, float)) else 'dipped'} "
            f"{f'(baseline ~{baseline}) ' if baseline is not None else ''}"
            f"— you are at views={views}, CTR={ctr}"
            f"{f' vs peer {peer_ctr}' if peer_ctr else ''}. "
            f"{('Local peers in ' + locality + ' usually hold this with a fresh Google post. ') if locality else ''}"
            f"Want me to draft 2 posts using your live offer?"
        )
        if offer:
            body = body.rstrip("?") + f" ({offer})?"
        cta = "binary_yes_no"

    elif kind == "perf_spike":
        metric = payload.get("metric") or "views"
        delta = payload.get("delta_pct")
        body = (
            f"{sal}, nice spike — {metric} "
            f"{('+' + str(int(delta*100)) + '% ') if isinstance(delta, (int, float)) else ''}"
            f"this window (views {perf.get('views')}, calls {perf.get('calls')}). "
            f"What changed on your side this week — offer, hours, or a post?"
        )
        cta = "open_ended"

    elif kind == "renewal_due":
        days = payload.get("days_remaining") or (merchant.get("subscription") or {}).get("days_remaining")
        plan = payload.get("plan") or (merchant.get("subscription") or {}).get("plan") or "Pro"
        amount = payload.get("renewal_amount")
        body = (
            f"{sal}, your {plan} plan has {days} days left"
            f"{f' (renewal ₹{amount})' if amount else ''}. "
            f"You had {perf.get('views')} views / {perf.get('calls')} calls in 30d — worth keeping the runway. "
            f"Reply YES to renew, STOP to pause."
        )
        cta = "binary_yes_no"

    elif kind == "festival_upcoming":
        festival = payload.get("festival") or "upcoming festival"
        days = payload.get("days_until")
        body = (
            f"{sal}, {festival} is coming"
            f"{f' in {days} days' if days is not None else ''}. "
            f"{('A service+price hook works better than % off for your category. ') if category.get('slug') != 'pharmacies' else ''}"
            f"{('I can set ' + offer + ' as the lead. ') if offer else 'I can draft a service+price post from your catalog. '}"
            f"Want me to draft it?"
        )
        cta = "binary_yes_no"

    elif kind == "curious_ask_due":
        ask = payload.get("ask_template") or "what_service_in_demand_this_week"
        ask_human = ask.replace("_", " ")
        body = (
            f"{sal}, quick curiosity (30 sec): {ask_human}? "
            f"Peers in {city or 'your city'} usually share this and I use it to tune your next post. "
            f"{f'Peer benchmark: {peer}. ' if peer else ''}"
            f"What's hottest for you this week?"
        )
        cta = "open_ended"

    elif kind == "winback_eligible":
        days = payload.get("days_since_expiry")
        dip = payload.get("perf_dip_pct")
        lapsed = payload.get("lapsed_customers_added_since_expiry")
        body = (
            f"{sal}, it's been {days} days since expiry"
            f"{f' and views dipped {int(abs(dip)*100)}%' if isinstance(dip, (int, float)) else ''}"
            f"{f' with {lapsed} more lapsed customers' if lapsed else ''}. "
            f"Want a 5-min reactivation plan (no pitch dump)?"
        )
        cta = "binary_yes_no"

    elif kind == "ipl_match_today":
        # Contrarian: if weekend/non-weeknight, don't force match promo
        match = payload.get("match") or "tonight's match"
        is_weeknight = payload.get("is_weeknight")
        if is_weeknight is False:
            body = (
                f"{sal}, {match} is on but Saturday match nights can be noisy for dine-in. "
                f"I won't push a match blast — want a quiet weekday lunch special draft instead"
                f"{f' ({offer})' if offer else ''}?"
            )
            cta = "binary_yes_no"
            rationale = "Contrarian skip of IPL Saturday match promo"
        else:
            body = (
                f"{sal}, {match} tonight at {payload.get('venue') or city}. "
                f"{('Lead with ' + offer + '. ') if offer else ''}"
                f"Want a short match-night WhatsApp for your regulars?"
            )
            cta = "binary_yes_no"

    elif kind == "review_theme_emerged":
        theme = (payload.get("theme") or "service").replace("_", " ")
        n = payload.get("occurrences_30d")
        quote = payload.get("common_quote")
        body = (
            f"{sal}, review theme rising: '{theme}'"
            f"{f' ({n}x in 30d)' if n is not None else ''}"
            f"{f' — e.g. \"{quote}\"' if quote else ''}. "
            f"Want me to draft a short owner reply + one ops checklist?"
        )
        cta = "binary_yes_no"

    elif kind == "milestone_reached":
        metric = payload.get("metric") or "reviews"
        value = payload.get("value_now")
        milestone = payload.get("milestone_value")
        body = (
            f"{sal}, you're at {value} {metric.replace('_', ' ')}"
            f"{f' — {milestone} is close' if payload.get('is_imminent') else ''}. "
            f"Want a 1-line celebration Google post draft?"
        )
        cta = "open_ended"

    elif kind == "seasonal_perf_dip":
        note = payload.get("season_note") or "seasonal soft patch"
        delta = payload.get("delta_pct")
        body = (
            f"{sal}, {('views ' + str(int(delta*100)) + '% ') if isinstance(delta, (int, float)) else 'soft patch '}"
            f"this week — expected for {note.replace('_', ' ')}. "
            f"I'd reframe to retention (not panic acquisition). "
            f"Your 6mo retention is {agg.get('retention_6mo_pct', 'n/a')}. "
            f"Want a retention WhatsApp for lapsed members?"
        )
        cta = "binary_yes_no"

    elif kind == "competitor_opened":
        # only name if in payload
        name = payload.get("competitor_name") or payload.get("name")
        dist = payload.get("distance_km")
        if name:
            body = (
                f"{sal}, GBP shows {name}"
                f"{f' ~{dist}km away' if dist is not None else ''} opened nearby. "
                f"Your edge: {offer or 'your verified profile + reviews'}. "
                f"Want a differentiation post draft (no competitor bashing)?"
            )
        else:
            body = (
                f"{sal}, a nearby competitor listing appeared on GBP"
                f"{f' in {locality}' if locality else ''}. "
                f"I won't invent their name. Want me to tighten your photos/hours + one service+price hook"
                f"{f' ({offer})' if offer else ''}?"
            )
        cta = "binary_yes_no"

    elif kind == "supply_alert":
        batch = payload.get("batch_number") or payload.get("sku") or payload.get("item")
        body = (
            f"{sal}, supply alert"
            f"{f' for batch {batch}' if batch else ''}: {payload.get('message') or payload.get('note') or 'stock change flagged'}. "
            f"Confirm you want me to notify affected customers?"
        )
        cta = "binary_yes_no"

    elif kind == "active_planning_intent":
        topic = (payload.get("intent_topic") or "the plan").replace("_", " ")
        last = payload.get("merchant_last_message") or ""
        body = (
            f"{sal}, drafting now for {topic}"
            f"{(' — heard: \"' + last[:80] + '\"') if last else ''}. "
            f"Here's the outline: package tiers, price anchors"
            f"{f' (e.g. {offer})' if offer else ''}, and a 3-line WhatsApp pitch. "
            f"Sending the full draft next — confirm to proceed."
        )
        cta = "binary_confirm_cancel"
        rationale = "Intent already present — deliver artifact, no re-qualify"

    elif kind == "wedding_package_followup" and customer:
        cname = customer_display_name(customer)
        wdate = payload.get("wedding_date") or ((customer.get("preferences") or {}).get("wedding_date"))
        nxt = (payload.get("next_step_window_open") or "next prep step").replace("_", " ")
        body = (
            f"Hi {cname}, wedding prep check-in"
            f"{f' for {wdate}' if wdate else ''}. Window open for {nxt}. "
            f"Reply YES to book a slot, or tell us a preferred date."
        )
        cta = "binary_yes_no"

    elif kind in ("dormant_with_vera", "scheduled_recurring"):
        body = (
            f"{sal}, checking in — last chat was a while ago. "
            f"Your dashboard: {perf.get('views')} views, CTR {perf.get('ctr')}"
            f"{f'; peers at {peer}' if peer else ''}. "
            f"One question: what's your most-asked service this week?"
        )
        cta = "open_ended"

    else:
        # generic but still anchored
        facts = flatten_payload_facts(payload)[:4]
        fact_txt = "; ".join(facts) if facts else f"signals={signals[:2]}"
        if top:
            fact_txt = f"{top.get('title')} ({top.get('source')})"
        body = (
            f"{sal}, quick note ({kind.replace('_', ' ')}): {fact_txt}. "
            f"{('Offer live: ' + offer + '. ') if offer else ''}"
            f"Want me to take the next step?"
        )
        cta = trigger_kind_cta(kind)

    body = _hi_touch(body, lang)
    return {
        "body": body.strip(),
        "cta": cta,
        "rationale": rationale,
    }


def compose(
    category: dict,
    merchant: dict,
    trigger: dict,
    customer: Optional[dict] = None,
    history_bodies: Optional[list[str]] = None,
) -> dict[str, Any]:
    """
    compose(category, merchant, trigger, customer?) →
      {body, cta, send_as, suppression_key, rationale}
    """
    send_as = "merchant_on_behalf" if (
        trigger.get("scope") == "customer" or trigger.get("customer_id") or customer
    ) else "vera"
    # if customer scope but no customer object, still merchant_on_behalf when customer_id set
    if trigger.get("scope") == "customer":
        send_as = "merchant_on_behalf"
    elif not customer and not trigger.get("customer_id"):
        send_as = "vera"

    anchors = collect_fact_anchors(category, merchant, trigger, customer)
    llm = get_llm()
    result: dict[str, Any]

    if llm.available:
        try:
            data = llm.complete_json(SYSTEM_BASE, build_user_prompt(
                category, merchant, trigger, customer, history_bodies
            ))
            if data.get("body"):
                result = {
                    "body": str(data["body"]).strip(),
                    "cta": data.get("cta") or trigger_kind_cta(trigger.get("kind", "")),
                    "rationale": data.get("rationale") or "LLM compose",
                }
            else:
                result = _template_compose(category, merchant, trigger, customer)
        except Exception:
            result = _template_compose(category, merchant, trigger, customer)
    else:
        result = _template_compose(category, merchant, trigger, customer)

    body = strip_urls(result["body"])
    cta = result.get("cta") or trigger_kind_cta(trigger.get("kind", ""))
    ok, errors = validate_message(body, cta, category, anchors, history_bodies)

    if not ok:
        # one repair pass
        if "url_in_body" in errors:
            body = strip_urls(body)
        if "missing_anchor" in errors:
            body = inject_anchor(body, anchors)
        if any(e.startswith("taboo:") for e in errors):
            for e in errors:
                if e.startswith("taboo:"):
                    for tok in e.split(":", 1)[1].split(","):
                        body = body.replace(tok, "").replace(tok.title(), "")
        if "invalid_cta" in "".join(errors) or cta not in {
            "binary_yes_no", "binary_confirm_cancel", "open_ended", "multi_choice_slot", "none"
        }:
            cta = trigger_kind_cta(trigger.get("kind", ""))
        if "repeat_body" in errors:
            body = body.rstrip(".") + " — thoughts?"
        if llm.available and ("missing_anchor" in errors or any(e.startswith("taboo:") for e in errors)):
            try:
                repair = llm.complete_json(
                    SYSTEM_BASE + "\nPrevious draft failed validation: " + ", ".join(errors),
                    build_user_prompt(category, merchant, trigger, customer, history_bodies),
                )
                if repair.get("body"):
                    body = strip_urls(str(repair["body"]))
                    cta = repair.get("cta") or cta
            except Exception:
                pass
        # final safety
        body = inject_anchor(strip_urls(body), anchors)
        ok2, _ = validate_message(body, cta, category, anchors, history_bodies)
        if not ok2 and not body.strip():
            body = f"{_salutation(merchant, category)}, update on {trigger.get('kind', 'your account')} — {anchors[0] if anchors else 'see dashboard'}."

    return {
        "body": body.strip(),
        "cta": cta,
        "send_as": send_as,
        "suppression_key": trigger.get("suppression_key") or f"sk:{trigger.get('id')}",
        "rationale": result.get("rationale") or "composed",
    }
