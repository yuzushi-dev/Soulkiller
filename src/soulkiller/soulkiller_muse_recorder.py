#!/usr/bin/env python3
"""Soulkiller Muse 2 EEG Recorder — Sprint 2.

Captures EEG buffers from Muse 2 (BLE) or a simulated source, computes
per-session band power and session-level metrics, and stores them in the
soulkiller SQLite DB.

Usage:
  python3 soulkiller_muse_recorder.py --context coding [--note "...]
  python3 soulkiller_muse_recorder.py --list-sessions
"""
from __future__ import annotations
import os

import argparse
import math
import statistics
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

# ── DB schema ─────────────────────────────────────────────────────────────────

EEG_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS eeg_sessions (
    session_id   TEXT PRIMARY KEY,
    started_at   TEXT NOT NULL,
    ended_at     TEXT,
    duration_sec REAL,
    context_tag  TEXT,
    context_note TEXT,
    quality_score REAL
);
CREATE TABLE IF NOT EXISTS eeg_band_power (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      TEXT NOT NULL REFERENCES eeg_sessions(session_id),
    minute_idx      INTEGER,
    delta           REAL,
    theta           REAL,
    alpha           REAL,
    beta            REAL,
    gamma           REAL,
    frontal_asymmetry REAL,
    signal_quality  REAL,
    artifact_pct    REAL
);
CREATE TABLE IF NOT EXISTS eeg_session_metrics (
    session_id          TEXT PRIMARY KEY REFERENCES eeg_sessions(session_id),
    avg_delta           REAL,
    avg_theta           REAL,
    avg_alpha           REAL,
    avg_beta            REAL,
    avg_gamma           REAL,
    theta_beta_ratio    REAL,
    engagement_index    REAL,
    avg_frontal_asymmetry REAL,
    alpha_variability   REAL,
    beta_variability    REAL,
    focus_score         REAL,
    calm_score          REAL,
    context_tag         TEXT
);
"""

# EEG band definitions: (low_hz_inclusive, high_hz_exclusive)
BANDS = {
    "delta": (1.0,  4.0),
    "theta": (4.0,  8.0),
    "alpha": (8.0, 13.0),
    "beta":  (13.0, 30.0),
    "gamma": (30.0, 100.0),
}

ARTIFACT_THRESHOLD_UV = 100.0  # μV


# ── Signal processing ─────────────────────────────────────────────────────────

def compute_band_power(signal: np.ndarray, fs: int = 256) -> dict[str, float]:
    """FFT → relative band power per standard EEG band.

    Returns dict with keys delta/theta/alpha/beta/gamma summing to ~1.0.
    """
    n = len(signal)
    freqs = np.fft.rfftfreq(n, d=1.0 / fs)
    power = np.abs(np.fft.rfft(signal.astype(np.float64))) ** 2

    band_power: dict[str, float] = {}
    for name, (low, high) in BANDS.items():
        mask = (freqs >= low) & (freqs < high)
        band_power[name] = float(np.sum(power[mask]))

    total = sum(band_power.values())
    if total > 0:
        return {k: v / total for k, v in band_power.items()}
    return {k: 0.0 for k in BANDS}


def compute_frontal_asymmetry(af7_alpha: float, af8_alpha: float) -> float | None:
    """Frontal alpha asymmetry: ln(right / left).

    Positive = right-dominant = approach motivation.
    Negative = left-dominant = withdrawal motivation.
    Returns None if either channel has zero power.
    """
    if af7_alpha <= 0 or af8_alpha <= 0:
        return None
    return math.log(af8_alpha / af7_alpha)


def detect_artifacts(signal: np.ndarray, threshold: float = ARTIFACT_THRESHOLD_UV) -> float:
    """Return fraction of samples exceeding the amplitude threshold (0.0–1.0)."""
    return float(np.mean(np.abs(signal) > threshold))


# ── Session metrics ───────────────────────────────────────────────────────────

def compute_session_metrics(band_rows: list[dict]) -> dict | None:
    """Aggregate per-buffer rows into session-level EEG metrics.

    Returns dict with engagement_index, theta_beta_ratio, focus_score (0-100),
    calm_score (0-100), alpha_variability, beta_variability, avg_frontal_asymmetry.
    Returns None for empty input.
    """
    if not band_rows:
        return None

    avg_alpha = statistics.mean(r["alpha"] for r in band_rows)
    avg_beta  = statistics.mean(r["beta"]  for r in band_rows)
    avg_theta = statistics.mean(r["theta"] for r in band_rows)
    avg_delta = statistics.mean(r["delta"] for r in band_rows)
    avg_gamma = statistics.mean(r["gamma"] for r in band_rows)

    alpha_vals = [r["alpha"] for r in band_rows]
    beta_vals  = [r["beta"]  for r in band_rows]

    # Engagement index: beta / (alpha + theta)
    denom = avg_alpha + avg_theta
    engagement_index = avg_beta / denom if denom > 0 else 0.0

    # Theta–beta ratio (higher = more cognitive load / less focus)
    theta_beta_ratio = avg_theta / avg_beta if avg_beta > 0 else 0.0

    # Focus score 0–100: engagement=3 → 100%; 0 → 0%
    focus_score = min(100.0, max(0.0, engagement_index / 3.0 * 100.0))

    # Calm score 0–100: alpha=0.5 → 100%; 0 → 0%
    calm_score = min(100.0, max(0.0, avg_alpha / 0.5 * 100.0))

    # Variability (std) of alpha and beta across buffers
    alpha_variability = statistics.stdev(alpha_vals) if len(alpha_vals) > 1 else 0.0
    beta_variability  = statistics.stdev(beta_vals)  if len(beta_vals)  > 1 else 0.0

    # Average frontal asymmetry (skipping None values)
    fa_vals = [r["frontal_asymmetry"] for r in band_rows
               if r.get("frontal_asymmetry") is not None]
    avg_fa = statistics.mean(fa_vals) if fa_vals else None

    return {
        "engagement_index":     engagement_index,
        "theta_beta_ratio":     theta_beta_ratio,
        "focus_score":          focus_score,
        "calm_score":           calm_score,
        "alpha_variability":    alpha_variability,
        "beta_variability":     beta_variability,
        "avg_frontal_asymmetry": avg_fa,
        "avg_alpha":            avg_alpha,
        "avg_beta":             avg_beta,
        "avg_theta":            avg_theta,
        "avg_delta":            avg_delta,
        "avg_gamma":            avg_gamma,
    }


# ── Recorder ──────────────────────────────────────────────────────────────────

class MuseRecorder:
    """Records a single EEG session to the soulkiller SQLite DB.

    Each call to process_buffer() handles a multi-channel numpy dict (one
    array per channel: TP9, AF7, AF8, TP10) and stores per-buffer band
    power averages in memory.  end_session() flushes everything to DB.
    """

    def __init__(
        self,
        db,
        session_id: str | None = None,
        context_tag: str = "",
        note: str | None = None,
        fs: int = 256,
    ) -> None:
        import sqlite3

        self.db = db
        self.session_id = session_id or str(uuid.uuid4())
        self.context_tag = context_tag
        self.fs = fs
        self._band_rows: list[dict] = []
        self._started_at = datetime.now(timezone.utc)

        db.executescript(EEG_SCHEMA_SQL)
        db.execute(
            "INSERT OR IGNORE INTO eeg_sessions "
            "(session_id, started_at, context_tag, context_note) VALUES (?, ?, ?, ?)",
            (self.session_id, self._started_at.isoformat(), context_tag, note),
        )
        db.commit()

    def process_buffer(self, buffer_dict: dict[str, np.ndarray]) -> dict:
        """Process one multi-channel EEG buffer (typically 5 s of data).

        Returns a dict with averaged band powers, frontal_asymmetry,
        signal_quality, and artifact_pct for this buffer.
        """
        # Per-channel band power + artifact fraction
        all_bands: list[dict] = []
        artifact_fracs: list[float] = []

        for ch_signal in buffer_dict.values():
            all_bands.append(compute_band_power(ch_signal, fs=self.fs))
            artifact_fracs.append(detect_artifacts(ch_signal))

        # Average across channels
        avg_bands = {
            band: statistics.mean(b[band] for b in all_bands)
            for band in BANDS
        }

        # Frontal asymmetry from AF7 / AF8 channels
        af7 = buffer_dict.get("AF7")
        af8 = buffer_dict.get("AF8")
        fa: float | None = None
        if af7 is not None and af8 is not None:
            af7_bands = compute_band_power(af7, fs=self.fs)
            af8_bands = compute_band_power(af8, fs=self.fs)
            fa = compute_frontal_asymmetry(af7_bands["alpha"], af8_bands["alpha"])

        avg_artifact = statistics.mean(artifact_fracs) if artifact_fracs else 0.0
        signal_quality = 1.0 - avg_artifact

        row = {
            **avg_bands,
            "frontal_asymmetry": fa,
            "signal_quality":    signal_quality,
            "artifact_pct":      avg_artifact,
        }
        self._band_rows.append(row)
        return row

    def end_session(self) -> dict | None:
        """Flush band-power rows and session metrics to DB. Returns metrics dict."""
        ended_at = datetime.now(timezone.utc)
        duration_sec = max(0.001, (ended_at - self._started_at).total_seconds())

        # Save individual buffer rows
        for i, row in enumerate(self._band_rows):
            self.db.execute(
                "INSERT INTO eeg_band_power "
                "(session_id, minute_idx, delta, theta, alpha, beta, gamma, "
                "frontal_asymmetry, signal_quality, artifact_pct) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    self.session_id, i,
                    row["delta"], row["theta"], row["alpha"],
                    row["beta"],  row["gamma"],
                    row.get("frontal_asymmetry"),
                    row.get("signal_quality"),
                    row.get("artifact_pct"),
                ),
            )

        # Compute and save session-level metrics
        metrics = compute_session_metrics(self._band_rows)
        avg_quality = (
            statistics.mean(r["signal_quality"] for r in self._band_rows)
            if self._band_rows else None
        )

        if metrics:
            self.db.execute(
                "INSERT OR REPLACE INTO eeg_session_metrics "
                "(session_id, avg_delta, avg_theta, avg_alpha, avg_beta, avg_gamma, "
                "theta_beta_ratio, engagement_index, avg_frontal_asymmetry, "
                "alpha_variability, beta_variability, focus_score, calm_score, context_tag) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    self.session_id,
                    metrics["avg_delta"],    metrics["avg_theta"],
                    metrics["avg_alpha"],    metrics["avg_beta"],
                    metrics["avg_gamma"],    metrics["theta_beta_ratio"],
                    metrics["engagement_index"], metrics["avg_frontal_asymmetry"],
                    metrics["alpha_variability"], metrics["beta_variability"],
                    metrics["focus_score"],  metrics["calm_score"],
                    self.context_tag,
                ),
            )

        self.db.execute(
            "UPDATE eeg_sessions SET ended_at=?, duration_sec=?, quality_score=? "
            "WHERE session_id=?",
            (ended_at.isoformat(), duration_sec, avg_quality, self.session_id),
        )
        self.db.commit()
        return metrics


# ── CLI ───────────────────────────────────────────────────────────────────────

def _get_db():
    import sqlite3
    db_path = Path(os.environ.get("SOULKILLER_DATA_DIR") or str(Path(__file__).resolve().parents[1] / "soulkiller")) / "soulkiller.db"
    db = sqlite3.connect(str(db_path))
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    return db


def _run_lsl_session(rec: "MuseRecorder", duration_sec: int | None) -> None:
    """Stream EEG from Muse 2 via Lab Streaming Layer (muselsl).

    Requires: pip install muselsl pylsl
    Setup:    muselsl stream --name Muse2   (in a separate terminal)
    """
    try:
        from pylsl import StreamInlet, resolve_byprop
    except ImportError:
        raise SystemExit(
            "pylsl not installed. Run: pip install pylsl muselsl\n"
            "Then start the Muse stream: muselsl stream --name Muse2"
        )

    print("Searching for EEG stream (LSL)...", flush=True)
    streams = resolve_byprop("type", "EEG", timeout=10.0)
    if not streams:
        raise SystemExit("No EEG stream found. Is 'muselsl stream' running?")

    inlet = StreamInlet(streams[0])
    info = streams[0]
    fs = int(info.nominal_srate())
    channels = ["TP9", "AF7", "AF8", "TP10"]
    buffer_size = fs * 5  # 5-second buffers

    print(f"Connected — fs={fs}Hz  context={rec.context_tag}", flush=True)
    if duration_sec:
        print(f"Session duration: {duration_sec}s  (Ctrl+C to stop early)", flush=True)
    else:
        print("Recording… press Ctrl+C to end.", flush=True)

    import time
    raw_buf: list[list[float]] = [[] for _ in channels]
    start_time = time.time()
    last_flush = start_time

    while True:
        if duration_sec and (time.time() - start_time) >= duration_sec:
            break

        sample, _ = inlet.pull_sample(timeout=1.0)
        if sample is None:
            continue

        for i, ch in enumerate(channels):
            if i < len(sample):
                raw_buf[i].append(sample[i])

        # Process every 5 seconds
        if len(raw_buf[0]) >= buffer_size:
            buf_dict = {
                ch: np.array(raw_buf[i][:buffer_size], dtype=np.float32)
                for i, ch in enumerate(channels)
            }
            result = rec.process_buffer(buf_dict)
            # Trim processed samples
            for i in range(len(channels)):
                raw_buf[i] = raw_buf[i][buffer_size:]

            elapsed = time.time() - start_time
            print(
                f"  [{elapsed:5.0f}s] α={result['alpha']:.2f}  β={result['beta']:.2f}  "
                f"θ={result['theta']:.2f}  q={result['signal_quality']:.2f}",
                flush=True,
            )


def main() -> None:
    parser = argparse.ArgumentParser(description="Soulkiller Muse 2 EEG Recorder")
    parser.add_argument("--context", default="manual",
                        help="Session context tag (e.g. coding, meditation, morning_baseline)")
    parser.add_argument("--note", default=None, help="Free-text note for this session")
    parser.add_argument("--duration", type=int, default=None,
                        help="Session duration in seconds (default: indefinite, stop with Ctrl+C)")
    parser.add_argument("--list-sessions", action="store_true",
                        help="List recent EEG sessions")
    args = parser.parse_args()

    db = _get_db()
    db.executescript(EEG_SCHEMA_SQL)

    if args.list_sessions:
        rows = db.execute(
            "SELECT session_id, started_at, context_tag, quality_score "
            "FROM eeg_sessions ORDER BY started_at DESC LIMIT 20"
        ).fetchall()
        for r in rows:
            q = f"{r['quality_score']:.2f}" if r['quality_score'] is not None else "?"
            print(f"{r['started_at'][:16]}  {r['context_tag']:20}  quality={q}  {r['session_id']}")
        return

    print(f"Starting EEG session (context={args.context})")
    rec = MuseRecorder(db=db, context_tag=args.context, note=args.note)
    try:
        _run_lsl_session(rec, duration_sec=args.duration)
    except KeyboardInterrupt:
        print("\nStopped by user.")
    finally:
        metrics = rec.end_session()
        if metrics:
            print(
                f"\nSession ended — focus={metrics['focus_score']:.1f}  "
                f"calm={metrics['calm_score']:.1f}  "
                f"engagement={metrics['engagement_index']:.2f}  "
                f"FA={metrics['avg_frontal_asymmetry']:+.3f}"
                if metrics.get("avg_frontal_asymmetry") is not None
                else f"\nSession ended — focus={metrics['focus_score']:.1f}  "
                     f"calm={metrics['calm_score']:.1f}"
            )
        db.close()


if __name__ == "__main__":
    main()
