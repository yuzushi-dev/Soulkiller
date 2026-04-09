#!/usr/bin/env python3
"""Soulkiller Budget Bridge

Reads Actual Budget CSV exports, classifies transactions via LLM,
computes behavioral statistics, and inserts personality observations
into the soulkiller DB.

Personality signals extracted:
  - cognitive.information_gathering: impulse vs researched spending
  - cognitive.risk_tolerance: savings rate, investments
  - temporal.planning_horizon: scheduled bills vs spontaneous
  - values.core_values: spending priority distribution
  - emotional.joy_sources: experiential vs material spending
  - cognitive.decision_speed: installment usage frequency

Cron: soulkiller:budget-bridge, weekly Monday 05:00 Europe/Rome

State file: soulkiller/budget-bridge-state.json
  {"last_processed_date": "YYYY-MM-DD"}
"""

from __future__ import annotations
import os

import csv
import glob
import http.client
import json
import re
import urllib.parse
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lib.config import load_nanobot_config
from lib.log import info, warn, error

SCRIPT = "soulkiller_budget_bridge"
LLM_TIMEOUT_SECONDS = 120
DEFAULT_MODEL = "openrouter/meta-llama/llama-3.3-70b-instruct:free"

EXPORTS_DIR = Path(__file__).resolve().parents[1] / "deploy" / "actual-budget" / "exports"
DB_PATH = Path(os.environ.get("SOULKILLER_DATA_DIR") or str(Path(__file__).resolve().parents[1] / "soulkiller")) / "soulkiller.db"
STATE_FILE = Path(os.environ.get("SOULKILLER_DATA_DIR") or str(Path(__file__).resolve().parents[1] / "soulkiller")) / "budget-bridge-state.json"

# Batch size for LLM classification
CLASSIFY_BATCH = 40

# Facets this module feeds
FACETS = {
    "cognitive.information_gathering": "satisficer ... maximizer",
    "cognitive.risk_tolerance": "risk-averse ... risk-seeking",
    "temporal.planning_horizon": "breve termine ... lungo termine",
    "values.core_values": "lista di evidenze accumulate",
    "emotional.joy_sources": "strumentale ... intrinseca",
    "cognitive.decision_speed": "impulsivo ... deliberato",
}

# Category taxonomy for classification
CATEGORIES = {
    "food_home": "Grocery / supermarket shopping",
    "food_delivery": "Food delivery (JustEat, Deliveroo, Glovo, Uber Eats)",
    "food_restaurant": "Restaurant / bar / cafe / fast food in person",
    "transport": "Transport: fuel, tolls (Unipolmove), parking, public transit",
    "bills_fixed": "Fixed recurring bills: rent, mortgage, utilities (ENI, electricity, gas, internet)",
    "bills_flexible": "Variable bills: phone, streaming subscriptions, SaaS, insurance",
    "tech_hardware": "Hardware, electronics, gadgets",
    "tech_software": "Software, apps, digital purchases, game/app stores",
    "health": "Pharmacy, medical, therapy, gym",
    "clothing": "Clothes, shoes, accessories",
    "entertainment": "Cinema, concerts, events, hobbies",
    "travel": "Travel, hotels, flights, tourism",
    "investment": "Savings, investments, bonds (Bondora, etc.)",
    "income": "Salary, freelance, refunds, incoming money",
    "transfer": "Internal transfers between own accounts/cards",
    "impulse_online": "Unclear/impulse online purchases (Amazon, AliExpress, random PayPal)",
    "japan_culture": "Japan-related: manga, anime, J-pop, Japanese products, Amazon.co.jp",
    "other": "Anything that doesn't fit above",
}


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_transactions() -> list[dict]:
    """Load and deduplicate all CSV exports."""
    all_txn: dict[str, dict] = {}
    for f in sorted(EXPORTS_DIR.glob("actual-export-*.csv")):
        with open(f) as fh:
            for row in csv.DictReader(fh):
                tid = row.get("Transaction ID", "")
                if tid and row.get("Date"):
                    all_txn[tid] = row
    txns = sorted(all_txn.values(), key=lambda r: r["Date"])
    return txns


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"last_processed_date": "2000-01-01"}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2))


# ---------------------------------------------------------------------------
# LLM helpers
# ---------------------------------------------------------------------------

def _call_llm(prompt: str, model: str) -> Any:
    parts = model.split("/", 1)
    if len(parts) != 2:
        raise ValueError(f"Invalid model: {model}")
    provider, model_id = parts

    config = load_nanobot_config()
    cfg = (config.get("providers") or {}).get(provider)
    if not cfg:
        raise ValueError(f"Provider {provider} not found")

    parsed = urllib.parse.urlparse(cfg["apiBase"])
    host = parsed.netloc
    api_base = parsed.path.lstrip("/")
    use_https = parsed.scheme.lower() == "https"

    payload = {
        "model": model_id,
        "messages": [
            {"role": "system", "content": "Return STRICT JSON only. No markdown, no explanation."},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.0,
        "max_tokens": 2000,
    }

    conn_cls = http.client.HTTPSConnection if use_https else http.client.HTTPConnection
    conn = conn_cls(host, timeout=LLM_TIMEOUT_SECONDS)
    headers = {"Content-Type": "application/json"}
    if cfg.get("apiKey"):
        headers["Authorization"] = f"Bearer {cfg['apiKey']}"

    try:
        conn.request("POST", f"/{api_base}/chat/completions", json.dumps(payload), headers)
        resp = conn.getresponse()
        body = resp.read().decode("utf-8")
        if resp.status != 200:
            raise RuntimeError(f"API {resp.status}: {body[:200]}")
        result = json.loads(body)
        msg = result["choices"][0]["message"]
        content = msg.get("content") or msg.get("reasoning") or ""
        if not content:
            raise RuntimeError("Empty response")

        # Strip fences
        s = content.strip()
        if s.startswith("```"):
            lines = s.split("\n")
            s = "\n".join(lines[1:] if lines[0].startswith("```") else lines)
            if s.rstrip().endswith("```"):
                s = s.rstrip()[:-3].strip()

        # Fix missing commas (glm4.7 pattern)
        s = re.sub(r"(\})\s*\n(\s*\{)", r"\1,\n\2", s)

        start, end = s.find("{"), s.rfind("}")
        if start != -1 and end > start:
            return json.loads(s[start:end + 1])
        start2 = s.find("[")
        end2 = s.rfind("]")
        if start2 != -1 and end2 > start2:
            return json.loads(s[start2:end2 + 1])
        raise RuntimeError(f"No JSON in response: {content[:200]}")
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

CLASSIFY_PROMPT = """Classify each transaction description into one of these categories:
{categories}

Input (list of {{id, description, amount}}):
{transactions}

Return JSON: {{"classifications": [{{"id": "...", "category": "...", "merchant": "...", "is_impulse": true/false}}]}}

Rules:
- merchant: clean merchant name (e.g. "PAM", "JustEat", "Iper Conad", "Amazon JP")
- is_impulse: true if the purchase seems unplanned (small online purchases, food delivery, random items)
- Use "transfer" for internal money movements
- Use "income" for incoming salary/payments
"""


def classify_batch(txns: list[dict], model: str) -> dict[str, dict]:
    """Returns mapping transaction_id -> {category, merchant, is_impulse}"""
    cats_text = "\n".join(f"  {k}: {v}" for k, v in CATEGORIES.items())
    txn_input = [
        {"id": t["Transaction ID"], "description": t["Notes"][:120], "amount": t["Amount"]}
        for t in txns
    ]
    prompt = CLASSIFY_PROMPT.format(
        categories=cats_text,
        transactions=json.dumps(txn_input, ensure_ascii=False)
    )

    try:
        result = _call_llm(prompt, model)
        classifications = result.get("classifications", [])
        return {
            c["id"]: {"category": c.get("category", "other"),
                      "merchant": c.get("merchant", ""),
                      "is_impulse": c.get("is_impulse", False)}
            for c in classifications
        }
    except Exception as e:
        warn(SCRIPT, "classify_error", error=str(e))
        return {}


# ---------------------------------------------------------------------------
# Signal computation
# ---------------------------------------------------------------------------

def compute_signals(txns: list[dict], classifications: dict[str, dict]) -> list[dict]:
    """Compute behavioral statistics → personality signals."""
    signals = []

    # Augment transactions with classifications
    for t in txns:
        cls = classifications.get(t["Transaction ID"], {})
        t["_category"] = cls.get("category", "other")
        t["_merchant"] = cls.get("merchant", "")
        t["_is_impulse"] = cls.get("is_impulse", False)
        try:
            t["_amount"] = float(t["Amount"])
        except (ValueError, TypeError):
            t["_amount"] = 0.0

    expenses = [t for t in txns if t["_amount"] < 0]
    income_txns = [t for t in txns if t["_category"] == "income" and t["_amount"] > 0]

    total_income = sum(t["_amount"] for t in income_txns)
    total_expense = abs(sum(t["_amount"] for t in expenses))
    investment_spend = abs(sum(t["_amount"] for t in expenses if t["_category"] == "investment"))

    # Group by category
    by_cat: dict[str, list[dict]] = defaultdict(list)
    for t in expenses:
        by_cat[t["_category"]].append(t)

    # --- Signal 1: Savings / investment rate → risk_tolerance ---
    if total_income > 0:
        savings_rate = investment_spend / total_income
        # 0 savings = risk-averse (0.3), high savings = risk-seeking (0.7)
        risk_pos = min(0.8, max(0.3, 0.3 + savings_rate * 2))
        signals.append({
            "facet_id": "cognitive.risk_tolerance",
            "signal_position": risk_pos,
            "signal_strength": 0.6,
            "content": f"Savings/investment rate: {savings_rate:.1%} of income ({investment_spend:.0f}€ invested of {total_income:.0f}€ income)",
            "source_type": "budget_analysis",
        })

    # --- Signal 2: Food delivery frequency → decision_speed ---
    delivery_count = len(by_cat["food_delivery"])
    delivery_total = abs(sum(t["_amount"] for t in by_cat["food_delivery"]))
    restaurant_count = len(by_cat["food_restaurant"])
    home_food_total = abs(sum(t["_amount"] for t in by_cat["food_home"]))
    weeks = max(1, len(set(t["Date"][:7] for t in txns)) * 7 / 7)  # approx weeks

    if delivery_count > 0:
        # High delivery = more impulsive (0.3), low = more deliberate (0.7)
        delivery_per_week = delivery_count / (len(set(t["Date"][:7] for t in txns)) or 1)
        impulse_pos = max(0.2, min(0.6, 0.6 - delivery_per_week * 0.1))
        signals.append({
            "facet_id": "cognitive.decision_speed",
            "signal_position": impulse_pos,
            "signal_strength": 0.5,
            "content": f"Food delivery: {delivery_count} orders ({delivery_total:.0f}€ total). Restaurants: {restaurant_count}. Home groceries: {home_food_total:.0f}€.",
            "source_type": "budget_analysis",
        })

    # --- Signal 3: Impulse purchases → information_gathering ---
    impulse_txns = [t for t in expenses if t["_is_impulse"]]
    non_impulse = [t for t in expenses if not t["_is_impulse"] and t["_category"] not in ("bills_fixed", "income", "transfer")]
    impulse_count = len(impulse_txns)
    total_count = len(expenses)

    if total_count > 0:
        impulse_ratio = impulse_count / total_count
        # High impulse ratio = satisficer (0.3), low = maximizer (0.7)
        gathering_pos = max(0.3, min(0.8, 0.8 - impulse_ratio * 0.8))
        signals.append({
            "facet_id": "cognitive.information_gathering",
            "signal_position": gathering_pos,
            "signal_strength": 0.55,
            "content": f"Impulse purchase ratio: {impulse_ratio:.1%} ({impulse_count}/{total_count} transactions). Top impulse merchants: {', '.join(set(t['_merchant'] for t in impulse_txns[:5]))}",
            "source_type": "budget_analysis",
        })

    # --- Signal 4: Installment/Klarna use → planning_horizon ---
    installment_txns = [t for t in txns if "paga in 3" in t["Notes"].lower() or "klarna" in t["Notes"].lower()]
    if installment_txns:
        installment_total = abs(sum(t["_amount"] for t in installment_txns))
        # Using installments suggests short-term planning / cash flow management
        # Not necessarily negative — could be maximizer behavior
        signals.append({
            "facet_id": "temporal.planning_horizon",
            "signal_position": 0.4,  # slightly short-term (uses installments)
            "signal_strength": 0.5,
            "content": f"Uses installment payments {len(installment_txns)}x (Klarna/Paga in 3 rate, {installment_total:.0f}€ total). Suggests cash-flow-aware spending, not necessarily short-horizon.",
            "source_type": "budget_analysis",
        })

    # --- Signal 5: Spending category distribution → values.core_values ---
    cat_totals = {
        cat: abs(sum(t["_amount"] for t in txns_list))
        for cat, txns_list in by_cat.items()
        if txns_list
    }
    top_cats = sorted(cat_totals.items(), key=lambda x: x[1], reverse=True)[:6]
    top_desc = ", ".join(f"{cat}({amt:.0f}€)" for cat, amt in top_cats)

    if top_cats:
        signals.append({
            "facet_id": "values.core_values",
            "signal_position": None,
            "signal_strength": 0.6,
            "content": f"Budget allocation (3 months): {top_desc}. Tech/Japan culture spend: {cat_totals.get('tech_hardware', 0) + cat_totals.get('tech_software', 0) + cat_totals.get('japan_culture', 0):.0f}€.",
            "source_type": "budget_analysis",
        })

    # --- Signal 6: Experiential vs material → joy_sources ---
    experiential = abs(sum(t["_amount"] for t in by_cat.get("entertainment", []) + by_cat.get("travel", []) + by_cat.get("food_restaurant", [])))
    material = abs(sum(t["_amount"] for t in by_cat.get("tech_hardware", []) + by_cat.get("clothing", []) + by_cat.get("impulse_online", [])))

    if experiential + material > 0:
        exp_ratio = experiential / (experiential + material)
        # Higher experiential = intrinsic joy (0.7), more material = instrumental (0.3)
        joy_pos = max(0.3, min(0.8, 0.3 + exp_ratio * 0.5))
        signals.append({
            "facet_id": "emotional.joy_sources",
            "signal_position": joy_pos,
            "signal_strength": 0.5,
            "content": f"Experiential spend: {experiential:.0f}€ (restaurants, entertainment, travel) vs material: {material:.0f}€ (tech, clothes, online). Ratio: {exp_ratio:.1%} experiential.",
            "source_type": "budget_analysis",
        })

    return signals


# ---------------------------------------------------------------------------
# DB insertion
# ---------------------------------------------------------------------------

def get_db():
    import sqlite3
    db = sqlite3.connect(str(DB_PATH))
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    return db


def insert_signals(signals: list[dict]) -> int:
    db = get_db()
    now = datetime.now(timezone.utc).isoformat()
    inserted = 0
    try:
        for s in signals:
            # Check if a budget signal for this facet already exists from today
            existing = db.execute(
                """SELECT id FROM observations
                   WHERE facet_id=? AND source_type='budget_analysis'
                   AND date(created_at) = date('now')""",
                (s["facet_id"],)
            ).fetchone()
            if existing:
                continue

            db.execute(
                """INSERT INTO observations
                   (facet_id, source_type, source_ref, content, extracted_signal,
                    signal_strength, signal_position, context, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    s["facet_id"],
                    s["source_type"],
                    "actual_budget",
                    s["content"],
                    s["content"][:200],
                    s["signal_strength"],
                    s["signal_position"],
                    "Derived from Actual Budget transaction analysis",
                    now,
                )
            )
            inserted += 1
        db.commit()
    finally:
        db.close()
    return inserted


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run(model: str = DEFAULT_MODEL, full: bool = False) -> None:
    state = load_state()
    last_date = "2000-01-01" if full else state.get("last_processed_date", "2000-01-01")

    txns = load_transactions()
    new_txns = [t for t in txns if t["Date"] > last_date]

    if not new_txns and not full:
        info(SCRIPT, "no_new_transactions", last_date=last_date)
        return

    # For signal computation, use ALL transactions for statistical validity
    # but only run if we have new data to process
    info(SCRIPT, "run_start", total=len(txns), new=len(new_txns), model=model)

    # Classify in batches
    classifications: dict[str, dict] = {}
    for i in range(0, len(txns), CLASSIFY_BATCH):
        batch = txns[i:i + CLASSIFY_BATCH]
        result = classify_batch(batch, model)
        classifications.update(result)
        info(SCRIPT, "classify_batch_done",
             batch=i // CLASSIFY_BATCH + 1,
             classified=len(result))

    # Compute behavioral signals
    signals = compute_signals(txns, classifications)
    info(SCRIPT, "signals_computed", count=len(signals))

    for s in signals:
        info(SCRIPT, "signal",
             facet=s["facet_id"],
             pos=s.get("signal_position"),
             content=s["content"][:100])

    # Insert into soulkiller DB
    inserted = insert_signals(signals)
    info(SCRIPT, "run_complete", signals_computed=len(signals), inserted=inserted)

    # Update state
    if txns:
        save_state({"last_processed_date": txns[-1]["Date"]})


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Soulkiller Budget Bridge")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--full", action="store_true",
                        help="Reprocess all transactions regardless of last run date")
    args = parser.parse_args()

    run(model=args.model, full=args.full)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
