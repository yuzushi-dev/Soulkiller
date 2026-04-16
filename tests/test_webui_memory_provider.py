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
