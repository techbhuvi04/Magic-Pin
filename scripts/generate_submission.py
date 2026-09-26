#!/usr/bin/env python3
"""Offline compose over test_pairs.json → submission.jsonl (local QA only)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from composer.compose import compose  # noqa: E402


def load_json(path: Path):
    with open(path) as f:
        return json.load(f)


def main():
    expanded = ROOT / "dataset" / "expanded"
    pairs = load_json(expanded / "test_pairs.json")["pairs"]
    cats = {p.stem: load_json(p) for p in (expanded / "categories").glob("*.json")}
    merchants = {load_json(p)["merchant_id"]: load_json(p) for p in (expanded / "merchants").glob("*.json")}
    customers = {}
    for p in (expanded / "customers").glob("*.json"):
        c = load_json(p)
        customers[c["customer_id"]] = c
    triggers = {}
    for p in (expanded / "triggers").glob("*.json"):
        t = load_json(p)
        triggers[t["id"]] = t

    out = ROOT / "submission.jsonl"
    with open(out, "w") as fp:
        for pair in pairs:
            mid = pair["merchant_id"]
            tid = pair["trigger_id"]
            cid = pair.get("customer_id")
            m = merchants.get(mid)
            t = triggers.get(tid)
            if not m or not t:
                continue
            cat = cats.get(m.get("category_slug"), {})
            cust = customers.get(cid) if cid else None
            msg = compose(cat, m, t, cust)
            row = {"test_id": pair["test_id"], **msg}
            fp.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
