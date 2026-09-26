"""Post-compose validators: no URL, taboos, CTA, anchors, anti-repeat."""

from __future__ import annotations

import re
from typing import Optional
from urllib.parse import urlparse

URL_RE = re.compile(
    r"(https?://|www\.)\S+|\b[a-z0-9\-]+\.(com|in|org|net|io|co)\b",
    re.I,
)
DIGIT_RE = re.compile(r"\d")

VALID_CTAS = {
    "binary_yes_no",
    "binary_confirm_cancel",
    "open_ended",
    "multi_choice_slot",
    "none",
}


def normalize_body(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def has_url(body: str) -> bool:
    return bool(URL_RE.search(body or ""))


def taboo_hits(body: str, category: dict) -> list[str]:
    voice = category.get("voice") or {}
    taboos = voice.get("vocab_taboo") or voice.get("taboos") or []
    hits = []
    lower = (body or "").lower()
    for t in taboos:
        token = str(t).split("(")[0].strip().lower()
        if token and token in lower:
            hits.append(token)
    return hits


def has_anchor(body: str, anchors: list[str]) -> bool:
    if not body:
        return False
    if DIGIT_RE.search(body):
        return True
    lower = body.lower()
    for a in anchors:
        a = str(a).strip()
        if len(a) >= 3 and a.lower() in lower:
            return True
        # source-like fragments
        if a and any(part.lower() in lower for part in re.split(r"[\s,]+", a) if len(part) > 4):
            return True
    return False


def is_repeat(body: str, history_bodies: list[str]) -> bool:
    norm = normalize_body(body)
    if not norm:
        return False
    for prev in history_bodies:
        if normalize_body(prev) == norm:
            return True
    return False


def validate_message(
    body: str,
    cta: str,
    category: dict,
    anchors: list[str],
    history_bodies: Optional[list[str]] = None,
) -> tuple[bool, list[str]]:
    errors: list[str] = []
    if not (body or "").strip():
        errors.append("empty_body")
    if has_url(body):
        errors.append("url_in_body")
    hits = taboo_hits(body, category)
    if hits:
        errors.append(f"taboo:{','.join(hits)}")
    if cta not in VALID_CTAS:
        errors.append(f"invalid_cta:{cta}")
    if not has_anchor(body, anchors):
        errors.append("missing_anchor")
    if history_bodies and is_repeat(body, history_bodies):
        errors.append("repeat_body")
    return (len(errors) == 0, errors)


def strip_urls(body: str) -> str:
    return URL_RE.sub("", body or "").strip()


def inject_anchor(body: str, anchors: list[str]) -> str:
    """Append a concrete fact if body lacks an anchor."""
    if has_anchor(body, anchors):
        return body
    pick = next((a for a in anchors if DIGIT_RE.search(str(a)) or len(str(a)) > 5), None)
    if not pick:
        return body
    return f"{body.rstrip()} ({pick})."


def wants_hi_mix(lang_pref: str) -> bool:
    p = (lang_pref or "").lower()
    return "hi" in p and "mix" in p or p in ("hi", "hi-en", "hi-en mix")


def looks_pure_english(body: str) -> bool:
    # heuristic: Devanagari absent and no common Hinglish tokens
    if re.search(r"[\u0900-\u097F]", body or ""):
        return False
    hinglish = ["hai", "nahi", "kya", "aap", "kar", "chahiye", "bhej", "dekho", "theek", "chalega", "samajh", "please reply"]
    lower = (body or "").lower()
    return not any(f" {w} " in f" {lower} " for w in hinglish)
