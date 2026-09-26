"""Thin export of multi-turn reply API for file-submit framing."""

from __future__ import annotations

from typing import Any, Optional

from composer.reply import handle_reply
from state.store import store


class ConversationState:
    def __init__(
        self,
        conversation_id: str,
        merchant_id: str,
        customer_id: Optional[str] = None,
        turns: Optional[list[dict]] = None,
    ):
        self.conversation_id = conversation_id
        self.merchant_id = merchant_id
        self.customer_id = customer_id
        self.turns = turns or []


def respond(state: ConversationState, merchant_message: str) -> dict[str, Any]:
    """Given conversation state + merchant message, produce the next reply action."""
    return handle_reply(
        store,
        conversation_id=state.conversation_id,
        merchant_id=state.merchant_id,
        message=merchant_message,
        turn_number=len(state.turns) + 1,
        customer_id=state.customer_id,
        from_role="merchant",
    )
