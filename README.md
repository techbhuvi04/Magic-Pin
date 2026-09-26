# Vera — magicpin AI Challenge Bot

Stateful WhatsApp merchant AI that composes engagement from four contexts
(`category`, `merchant`, `trigger`, `customer`) and exposes the judge HTTP harness.

## Quick start

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# optional LLM (temperature=0). Without keys, a deterministic template composer is used.
export OPENAI_API_KEY=...          # or ANTHROPIC_API_KEY
export CONTACT_EMAIL=you@example.com

uvicorn bot:app --host 0.0.0.0 --port 8765
```

Health check: [http://127.0.0.1:8765/v1/healthz](http://127.0.0.1:8765/v1/healthz)

## Endpoints

| Method | Path | Role |
|--------|------|------|
| GET | `/v1/healthz` | Liveness + `contexts_loaded` |
| GET | `/v1/metadata` | Team identity / approach / model |
| POST | `/v1/context` | Versioned context upsert |
| POST | `/v1/tick` | Eligibility + compose (max 20 actions) |
| POST | `/v1/reply` | Multi-turn: send / wait / end |
| POST | `/v1/teardown` | Wipe in-memory state |

### Context version semantics

- same `version` → **200** idempotent no-op
- lower `version` → **409** `stale_version`
- higher `version` → replace atomically

## Dataset

Challenge seeds live under `dataset/`. Expand with:

```bash
python3 dataset/generate_dataset.py --seed-dir dataset --out dataset/expanded
```

Produces 5 categories, 50 merchants, 200 customers, 100 triggers, and `test_pairs.json`.

## Local judge

```bash
# edit BOT_URL / LLM keys at top of judge_simulator.py, then:
BOT_URL=http://127.0.0.1:8765 python3 judge_simulator.py
```

Structural scenarios (`warmup`, `auto_reply_hell`, `intent_transition`, `hostile`, `phase2_short`) work without an LLM judge key for the bot itself; the simulator still needs an LLM key for scoring.

## Approach

Kind-dispatched composer with post-validators (no URLs in body, category taboos, single CTA, verifiable anchors, anti-repeat). Tick policy enforces urgency prioritization, suppression keys, customer consent, expiry, and a 20-action cap. Reply router detects WhatsApp auto-replies, intent commits (action mode, no re-qualify), hostile opt-outs, and off-topic redirects.

## Team

- **Name:** Sandeep Verma
- **Contact:** set `CONTACT_EMAIL` (default `bhuvnesh.richhariya.ug23@nsut.ac.in`)
