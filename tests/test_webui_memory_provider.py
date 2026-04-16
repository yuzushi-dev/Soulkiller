"""Tests for /api/memory/provider/* endpoints."""
from __future__ import annotations
import sys
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from fastapi.testclient import TestClient
from soulkiller.webui import app

@pytest.fixture
def client():
    return TestClient(app)

def test_provider_status_returns_provider_name(client):
    resp = client.get("/api/memory/provider/status")
    assert resp.status_code == 200
    data = resp.json()
    assert "provider" in data
    assert "healthy" in data
    assert data["provider"] in ("null", "soulkiller", "amber", "error")

def test_inspect_returns_bundle_shape(client):
    resp = client.post("/api/memory/provider/inspect", json={
        "subject_id": "demo-subject",
        "query_text": "current stress level",
        "agent_role": "assistant",
        "session_context": {},
    })
    assert resp.status_code == 200
    data = resp.json()
    for key in ("constraints", "priorities", "open_loops", "recent_decisions",
                "relationship_context", "contradictions", "interaction_rules",
                "session_relevant", "trace"):
        assert key in data

def test_inspect_bundle_values_are_lists(client):
    resp = client.post("/api/memory/provider/inspect", json={
        "subject_id": "demo-subject",
        "query_text": "anything",
        "agent_role": "assistant",
        "session_context": {},
    })
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data["constraints"], list)
    assert isinstance(data["trace"], list)
