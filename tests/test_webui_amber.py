"""Tests for /api/amber/* endpoints.

Amber package is optional — tests skip if not installed.
The status/items/metrics/trace endpoints degrade gracefully when amber is unavailable.
"""
from __future__ import annotations
import sys
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]
AMBER_ROOT = ROOT.parent / "Amber-Soulkiller"
if AMBER_ROOT.exists() and str(AMBER_ROOT) not in sys.path:
    sys.path.insert(0, str(AMBER_ROOT))
sys.path.insert(0, str(ROOT / "src"))

from fastapi.testclient import TestClient
from soulkiller.webui import app

@pytest.fixture
def client():
    return TestClient(app)

# ── /api/amber/status ─────────────────────────────────────────────────────

def test_amber_status_returns_json(client):
    resp = client.get("/api/amber/status")
    assert resp.status_code == 200
    data = resp.json()
    assert "available" in data
    assert "healthy" in data

def test_amber_status_without_env_is_unavailable(client, monkeypatch):
    monkeypatch.delenv("AMBER_DATA_DIR", raising=False)
    resp = client.get("/api/amber/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["available"] is False

# ── /api/amber/items ──────────────────────────────────────────────────────

def test_amber_items_returns_list(client):
    resp = client.get("/api/amber/items")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)

def test_amber_items_supports_filter_params(client):
    resp = client.get("/api/amber/items?subject_id=demo-subject&review_status=confirmed&memory_type=constraint")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)

# ── /api/amber/metrics ────────────────────────────────────────────────────

def test_amber_metrics_returns_dict(client):
    resp = client.get("/api/amber/metrics")
    assert resp.status_code == 200
    assert isinstance(resp.json(), dict)

# ── /api/amber/trace ──────────────────────────────────────────────────────

def test_amber_trace_returns_list(client):
    resp = client.get("/api/amber/trace")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)

# ── /api/amber/items/{id}/review ─────────────────────────────────────────

def test_amber_review_rejects_invalid_action(client):
    resp = client.post("/api/amber/items/nonexistent/review", json={"action": "invalid_action"})
    assert resp.status_code == 422

def test_amber_review_returns_503_when_amber_unavailable(client, monkeypatch):
    monkeypatch.delenv("AMBER_DATA_DIR", raising=False)
    resp = client.post("/api/amber/items/nonexistent/review", json={"action": "confirm"})
    assert resp.status_code == 503
