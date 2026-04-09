"""Public-facing runtime names for the OSS demo flow."""

from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parents[2]
DEMO_DIR = PACKAGE_ROOT / "demo"

EVENT_LOG_FILENAME = "event_log.sample.jsonl"
DELIVERY_LOG_FILENAME = "delivery_log.sample.jsonl"
MODEL_PROFILE_FILENAME = "model_profile.md"
MODEL_PORTRAIT_FILENAME = "model_portrait.md"
SUMMARY_FILENAME = "summary.json"
DEMO_CONSOLE_FILENAME = "demo_console.html"
PROFILE_SEED_FILENAME = "profile.seed.json"
CONFIG_EXAMPLE_FILENAME = "config.example.env"
