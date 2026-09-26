"""In-memory context + conversation store with version semantics."""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass
class VersionedContext:
    context_id: str
    scope: str
    version: int
    payload: dict
    stored_at: str


@dataclass
class ConversationTurn:
    role: str
    body: str
    ts: str
    cta: Optional[str] = None


@dataclass
class Conversation:
    conversation_id: str
    merchant_id: str
    customer_id: Optional[str]
    trigger_id: Optional[str]
    send_as: str
    created_at: str
    turns: list[ConversationTurn] = field(default_factory=list)
    ended: bool = False
    auto_reply_count: int = 0
    unanswered_nudges: int = 0
    last_merchant_normalized: Optional[str] = None
    first_touch_done: bool = False


class ContextStore:
    SCOPES = ("category", "merchant", "customer", "trigger")

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.started_at = time.time()
        # scope -> context_id -> VersionedContext
        self._contexts: dict[str, dict[str, VersionedContext]] = {s: {} for s in self.SCOPES}
        self.conversations: dict[str, Conversation] = {}
        self.used_suppression_keys: set[str] = set()
        self.open_by_merchant: dict[str, str] = {}  # merchant_id -> conversation_id
        self.merchant_auto_reply_hits: dict[str, int] = {}
        self.merchant_messaged_this_tick: set[str] = set()
        self.ended_conversations: set[str] = set()

    def uptime_seconds(self) -> int:
        return int(time.time() - self.started_at)

    def contexts_loaded(self) -> dict[str, int]:
        with self._lock:
            return {s: len(self._contexts[s]) for s in self.SCOPES}

    def upsert(
        self,
        scope: str,
        context_id: str,
        version: int,
        payload: dict,
    ) -> tuple[int, dict]:
        """
        Returns (http_status, body).
        same version → 200 idempotent; lower → 409; higher → replace.
        """
        if scope not in self.SCOPES:
            return 400, {"accepted": False, "reason": "invalid_scope", "details": f"scope must be one of {self.SCOPES}"}
        if not context_id or not isinstance(version, int) or version < 1:
            return 400, {"accepted": False, "reason": "invalid_request", "details": "context_id and version>=1 required"}
        if not isinstance(payload, dict):
            return 400, {"accepted": False, "reason": "invalid_payload", "details": "payload must be object"}

        with self._lock:
            bucket = self._contexts[scope]
            existing = bucket.get(context_id)
            if existing is not None:
                if version < existing.version:
                    return 409, {
                        "accepted": False,
                        "reason": "stale_version",
                        "current_version": existing.version,
                    }
                if version == existing.version:
                    return 200, {
                        "accepted": True,
                        "ack_id": f"ack_{context_id}_v{version}",
                        "stored_at": existing.stored_at,
                        "idempotent": True,
                    }

            stored_at = utc_now_iso()
            bucket[context_id] = VersionedContext(
                context_id=context_id,
                scope=scope,
                version=version,
                payload=payload,
                stored_at=stored_at,
            )
            return 200, {
                "accepted": True,
                "ack_id": f"ack_{context_id}_v{version}",
                "stored_at": stored_at,
            }

    def get(self, scope: str, context_id: str) -> Optional[dict]:
        with self._lock:
            vc = self._contexts.get(scope, {}).get(context_id)
            return vc.payload if vc else None

    def get_version(self, scope: str, context_id: str) -> Optional[int]:
        with self._lock:
            vc = self._contexts.get(scope, {}).get(context_id)
            return vc.version if vc else None

    def all_ids(self, scope: str) -> list[str]:
        with self._lock:
            return list(self._contexts.get(scope, {}).keys())

    def resolve_merchant_category(self, merchant: dict) -> Optional[dict]:
        slug = merchant.get("category_slug")
        if not slug:
            return None
        return self.get("category", slug)

    def mark_suppression(self, key: Optional[str]) -> None:
        if key:
            with self._lock:
                self.used_suppression_keys.add(key)

    def is_suppressed(self, key: Optional[str]) -> bool:
        if not key:
            return False
        with self._lock:
            return key in self.used_suppression_keys

    def create_conversation(
        self,
        merchant_id: str,
        customer_id: Optional[str],
        trigger_id: Optional[str],
        send_as: str,
        body: str,
        cta: Optional[str] = None,
    ) -> Conversation:
        with self._lock:
            cid = f"conv_{uuid.uuid4().hex[:16]}"
            conv = Conversation(
                conversation_id=cid,
                merchant_id=merchant_id,
                customer_id=customer_id,
                trigger_id=trigger_id,
                send_as=send_as,
                created_at=utc_now_iso(),
                turns=[ConversationTurn(role="vera", body=body, ts=utc_now_iso(), cta=cta)],
                first_touch_done=True,
            )
            self.conversations[cid] = conv
            if not customer_id:
                self.open_by_merchant[merchant_id] = cid
            return conv

    def get_conversation(self, conversation_id: str) -> Optional[Conversation]:
        with self._lock:
            return self.conversations.get(conversation_id)

    def ensure_conversation(
        self,
        conversation_id: str,
        merchant_id: str,
        customer_id: Optional[str] = None,
    ) -> Conversation:
        with self._lock:
            conv = self.conversations.get(conversation_id)
            if conv:
                return conv
            conv = Conversation(
                conversation_id=conversation_id,
                merchant_id=merchant_id,
                customer_id=customer_id,
                trigger_id=None,
                send_as="vera",
                created_at=utc_now_iso(),
            )
            self.conversations[conversation_id] = conv
            return conv

    def append_turn(self, conversation_id: str, role: str, body: str, cta: Optional[str] = None) -> None:
        with self._lock:
            conv = self.conversations.get(conversation_id)
            if not conv:
                return
            conv.turns.append(ConversationTurn(role=role, body=body, ts=utc_now_iso(), cta=cta))

    def end_conversation(self, conversation_id: str) -> None:
        with self._lock:
            conv = self.conversations.get(conversation_id)
            if conv:
                conv.ended = True
                self.ended_conversations.add(conversation_id)
                if self.open_by_merchant.get(conv.merchant_id) == conversation_id:
                    self.open_by_merchant.pop(conv.merchant_id, None)

    def has_open_conversation(self, merchant_id: str) -> bool:
        with self._lock:
            cid = self.open_by_merchant.get(merchant_id)
            if not cid:
                return False
            conv = self.conversations.get(cid)
            return bool(conv and not conv.ended)

    def teardown(self) -> dict[str, Any]:
        with self._lock:
            counts = self.contexts_loaded()
            self._contexts = {s: {} for s in self.SCOPES}
            self.conversations.clear()
            self.used_suppression_keys.clear()
            self.open_by_merchant.clear()
            self.merchant_auto_reply_hits.clear()
            self.merchant_messaged_this_tick.clear()
            self.ended_conversations.clear()
            self.started_at = time.time()
            return {"cleared": True, "previous_contexts": counts}


store = ContextStore()
