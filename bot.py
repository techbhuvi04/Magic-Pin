"""
Vera WhatsApp merchant AI — magicpin AI Challenge HTTP bot.

Endpoints:
  POST /v1/context
  POST /v1/tick
  POST /v1/reply
  GET  /v1/healthz
  GET  /v1/metadata
  POST /v1/teardown
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from composer.llm import get_llm
from composer.reply import handle_reply
from composer.tick import run_tick
from state.store import store

APP_VERSION = "1.0.0"
SUBMITTED_AT = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

app = FastAPI(title="Vera magicpin Challenge Bot", version=APP_VERSION)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.isdir(_static_dir):
    app.mount("/static", StaticFiles(directory=_static_dir), name="static")


@app.get("/")
def serve_ui():
    index = os.path.join(_static_dir, "index.html")
    if os.path.isfile(index):
        return FileResponse(index, media_type="text/html")
    return JSONResponse({"message": "Vera bot running. No UI found in static/"})


class ContextRequest(BaseModel):
    scope: str
    context_id: str
    version: int
    payload: dict[str, Any] = Field(default_factory=dict)
    delivered_at: Optional[str] = None


class TickRequest(BaseModel):
    now: Optional[str] = None
    available_triggers: list[str] = Field(default_factory=list)


class ReplyRequest(BaseModel):
    conversation_id: str
    merchant_id: str
    customer_id: Optional[str] = None
    from_role: str = "merchant"
    message: str = ""
    received_at: Optional[str] = None
    turn_number: int = 1


def _metadata() -> dict[str, Any]:
    llm = get_llm()
    model = llm.model if llm.available else "deterministic-template-composer"
    if llm.available and llm.provider:
        model = f"{llm.provider}:{llm.model}"
    return {
        "team_name": os.environ.get("TEAM_NAME", "Sandeep Verma"),
        "team_members": ["Sandeep Verma"],
        "model": model,
        "approach": (
            "Kind-dispatched engagement composer over the 4-context framework "
            "(category, merchant, trigger, customer) with post-validators "
            "(no URLs, taboos, single CTA, verifiable anchors, anti-repeat), "
            "tick eligibility (urgency/suppression/consent/expiry), and "
            "multi-turn reply routing (auto-reply, intent handoff, hostile end, off-topic redirect). "
            "Uses OpenAI/Anthropic at temperature=0 when keyed; otherwise a high-quality "
            "deterministic template composer that still binds all four contexts."
        ),
        "contact_email": os.environ.get("CONTACT_EMAIL", "bhuvnesh.richhariya.ug23@nsut.ac.in"),
        "version": APP_VERSION,
        "submitted_at": SUBMITTED_AT,
    }


@app.get("/v1/healthz")
def healthz():
    return {
        "status": "ok",
        "uptime_seconds": store.uptime_seconds(),
        "contexts_loaded": store.contexts_loaded(),
    }


@app.get("/v1/metadata")
def metadata():
    return _metadata()


@app.post("/v1/context")
async def push_context(req: ContextRequest):
    status, body = store.upsert(req.scope, req.context_id, req.version, req.payload)
    return JSONResponse(status_code=status, content=body)


@app.post("/v1/tick")
async def tick(req: TickRequest):
    try:
        result = run_tick(store, req.now, req.available_triggers or [])
        # hard cap
        result["actions"] = (result.get("actions") or [])[:20]
        return result
    except Exception as e:
        return JSONResponse(
            status_code=400,
            content={"actions": [], "error": "tick_failed", "details": str(e)[:200]},
        )


@app.post("/v1/reply")
async def reply(req: ReplyRequest):
    try:
        if not req.conversation_id or not req.merchant_id:
            return JSONResponse(
                status_code=400,
                content={"accepted": False, "reason": "conversation_id and merchant_id required"},
            )
        result = handle_reply(
            store,
            conversation_id=req.conversation_id,
            merchant_id=req.merchant_id,
            message=req.message or "",
            turn_number=req.turn_number or 1,
            customer_id=req.customer_id,
            from_role=req.from_role or "merchant",
        )
        return result
    except Exception as e:
        return JSONResponse(
            status_code=400,
            content={"action": "end", "rationale": f"reply_error: {str(e)[:160]}"},
        )


@app.post("/v1/teardown")
async def teardown():
    cleared = store.teardown()
    return {"ok": True, **cleared}


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):
    return JSONResponse(
        status_code=400,
        content={"accepted": False, "reason": "bad_request", "details": str(exc)[:200]},
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("bot:app", host="0.0.0.0", port=int(os.environ.get("PORT", "8765")), reload=False)
