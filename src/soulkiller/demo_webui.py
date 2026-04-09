"""Static demo console for the public Soulkiller OSS flow."""

from __future__ import annotations

import argparse
import json
from html import escape
from pathlib import Path

from .public_runtime import (
    DEMO_CONSOLE_FILENAME,
    DELIVERY_LOG_FILENAME,
    EVENT_LOG_FILENAME,
    MODEL_PORTRAIT_FILENAME,
    MODEL_PROFILE_FILENAME,
    SUMMARY_FILENAME,
)


VARIANTS = {
    "executive": {
        "label": "Executive Propaganda Deck",
        "bg": "#070709",
        "surface": "#0f0b0d",
        "panel": "#151012",
        "panel_2": "#1b1316",
        "border": "#3c2226",
        "border_strong": "#7d0f1c",
        "text": "#f3ece8",
        "text_muted": "#a69691",
        "red": "#d72638",
        "red_soft": "#ff5a6b",
        "gold": "#c6a667",
        "green": "#84d1a4",
        "ash": "#d9cfca",
        "hero_quote": "Longitudinal behavioral modeling framed like a corporate control deck.",
    },
    "blacksite": {
        "label": "Blacksite Surveillance Deck",
        "bg": "#050506",
        "surface": "#0a0b0d",
        "panel": "#101317",
        "panel_2": "#141920",
        "border": "#25303a",
        "border_strong": "#9d1525",
        "text": "#e7eef2",
        "text_muted": "#95a3ad",
        "red": "#cf2334",
        "red_soft": "#ff6a78",
        "gold": "#8fa2b6",
        "green": "#83cdb3",
        "ash": "#d5dde3",
        "hero_quote": "Cold telemetry, sealed compartments, and a quieter version of corporate threat.",
    },
    "directive": {
        "label": "Directive Broadcast Deck",
        "bg": "#0a0706",
        "surface": "#120c0b",
        "panel": "#1a1210",
        "panel_2": "#241815",
        "border": "#4d3025",
        "border_strong": "#b31c2f",
        "text": "#f6ede6",
        "text_muted": "#b6a198",
        "red": "#ef3b4f",
        "red_soft": "#ff7a88",
        "gold": "#d4a35f",
        "green": "#9fd39d",
        "ash": "#ead5c6",
        "hero_quote": "Louder hierarchy, brighter command accents, and a more theatrical broadcast posture.",
    },
}

DEFAULT_VARIANT = "executive"


def _read_required(path: Path) -> str:
    if not path.exists():
        raise FileNotFoundError(
            f"Missing demo artifact: {path.name}. Run the demo runner first."
        )
    return path.read_text(encoding="utf-8")


def _variant_or_default(variant: str | None) -> tuple[str, dict[str, str]]:
    name = variant or DEFAULT_VARIANT
    if name not in VARIANTS:
        supported = ", ".join(sorted(VARIANTS))
        raise ValueError(f"Unsupported variant '{name}'. Supported variants: {supported}")
    return name, VARIANTS[name]


def build_demo_console(output_dir: Path, variant: str | None = None) -> str:
    output_dir = Path(output_dir)
    variant_name, theme = _variant_or_default(variant)

    summary = json.loads(_read_required(output_dir / SUMMARY_FILENAME))
    profile_md = _read_required(output_dir / MODEL_PROFILE_FILENAME)
    portrait_md = _read_required(output_dir / MODEL_PORTRAIT_FILENAME)
    event_log = _read_required(output_dir / EVENT_LOG_FILENAME)
    delivery_log = _read_required(output_dir / DELIVERY_LOG_FILENAME)
    event_rows = [json.loads(line) for line in event_log.splitlines() if line.strip()]

    artifact_rows = [
        ("Summary", SUMMARY_FILENAME),
        ("Profile", MODEL_PROFILE_FILENAME),
        ("Portrait", MODEL_PORTRAIT_FILENAME),
        ("Event Log", EVENT_LOG_FILENAME),
        ("Delivery Log", DELIVERY_LOG_FILENAME),
    ]
    variant_tokens = "".join(
        f'<span class="token {"active-token" if name == variant_name else ""}">{escape(name)}</span>'
        for name in VARIANTS
    )
    top_traits = "".join(
        f'<span class="token">{escape(trait)}</span>'
        for trait in summary.get("top_traits", [])
    )
    goals = "".join(f"<li>{escape(goal)}</li>" for goal in summary.get("goals", []))
    artifacts = "".join(
        f"<tr><td>{escape(label)}</td><td class=\"mono\">{escape(name)}</td></tr>"
        for label, name in artifact_rows
    )
    transcript = "".join(
        (
            '<article class="transcript-item">'
            f'<div class="transcript-meta">{escape(row["received_at"])} · {escape(row["from"])}</div>'
            f'<div class="transcript-text">{escape(row["content"])}</div>'
            "</article>"
        )
        for row in event_rows
    )
    subject_slug = escape(summary["subject_name"]).upper().replace(" ", "-")

    facet_count = escape(str(summary.get("facet_count", "—")))
    obs_seed = escape(str(summary.get("observation_count_seed", "—")))
    obs_demo = escape(str(summary.get("observation_count_demo_pass", "—")))
    hypothesis_count = escape(str(summary.get("hypothesis_count", "—")))

    high_conf_rows = "".join(
        f'<tr>'
        f'<td class="mono" style="color:var(--ash)">{escape(f["id"])}</td>'
        f'<td style="color:var(--gold)">{escape(str(round(f["position"], 2)))}</td>'
        f'<td style="color:var(--green)">{escape(str(round(f["confidence"], 2)))}</td>'
        f'</tr>'
        for f in summary.get("high_confidence_facets", [])
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Soulkiller · Secure Your Soul · Arasaka Corp</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@300;400;500;600;700&family=IBM+Plex+Mono:wght@400;500&family=Rajdhani:wght@500;600;700&display=swap" rel="stylesheet">
  <style>
    :root {{
      --bg: {theme["bg"]};
      --surface: {theme["surface"]};
      --panel: {theme["panel"]};
      --panel-2: {theme["panel_2"]};
      --border: {theme["border"]};
      --border-strong: {theme["border_strong"]};
      --text: {theme["text"]};
      --text-muted: {theme["text_muted"]};
      --red: {theme["red"]};
      --red-soft: {theme["red_soft"]};
      --gold: {theme["gold"]};
      --green: {theme["green"]};
      --ash: {theme["ash"]};
      --font: 'IBM Plex Sans', sans-serif;
      --head: 'Rajdhani', sans-serif;
      --mono: 'IBM Plex Mono', monospace;
    }}
    * {{ box-sizing: border-box; }}
    html {{ scroll-behavior: smooth; }}
    body {{
      margin: 0;
      min-height: 100vh;
      font-family: var(--font);
      background:
        radial-gradient(circle at top right, color-mix(in srgb, var(--red) 24%, transparent), transparent 24%),
        radial-gradient(circle at left center, color-mix(in srgb, var(--gold) 12%, transparent), transparent 30%),
        linear-gradient(180deg, rgba(255,255,255,.02), rgba(255,255,255,0) 120px),
        var(--bg);
      color: var(--text);
      letter-spacing: .01em;
      overflow-x: hidden;
    }}
    body::before {{
      content: '';
      position: fixed;
      inset: 14px;
      pointer-events: none;
      border: 1px solid color-mix(in srgb, var(--gold) 18%, transparent);
      box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--red) 10%, transparent);
    }}
    body::after {{
      content: 'SAVE YOUR SOUL';
      position: fixed;
      bottom: 28px;
      right: 32px;
      pointer-events: none;
      font-family: var(--mono);
      font-size: 10px;
      letter-spacing: .32em;
      color: color-mix(in srgb, var(--red) 18%, transparent);
      text-transform: uppercase;
      z-index: 0;
    }}
    .scanlines {{
      position: fixed;
      inset: 0;
      pointer-events: none;
      background: linear-gradient(rgba(255,255,255,.016) 1px, transparent 1px);
      background-size: 100% 4px;
      opacity: .12;
      mix-blend-mode: soft-light;
      z-index: 9998;
    }}
    .layout {{
      display: grid;
      grid-template-columns: 272px 1fr;
      min-height: 100vh;
    }}
    .rail {{
      padding: 24px 18px;
      background: linear-gradient(180deg, color-mix(in srgb, var(--red) 10%, transparent), transparent 140px), var(--surface);
      border-right: 1px solid var(--border-strong);
      position: relative;
    }}
    .rail::after {{
      content: 'NO LIVE RUNTIME';
      position: absolute;
      right: -42px;
      top: 120px;
      transform: rotate(90deg);
      font-family: var(--mono);
      font-size: 10px;
      letter-spacing: .24em;
      color: color-mix(in srgb, var(--gold) 50%, transparent);
    }}
    .brand {{
      font-family: var(--head);
      font-size: 34px;
      font-weight: 700;
      letter-spacing: .16em;
      text-transform: uppercase;
    }}
    .brand span {{ color: var(--red-soft); }}
    .sub {{
      margin-top: 8px;
      font-family: var(--mono);
      font-size: 11px;
      letter-spacing: .14em;
      color: var(--gold);
      text-transform: uppercase;
    }}
    .rail-block {{
      margin-top: 24px;
      padding: 14px;
      border: 1px solid var(--border);
      background: linear-gradient(180deg, rgba(255,255,255,.02), rgba(255,255,255,0)), var(--panel);
    }}
    .rail-block .metric {{
      font-size: 46px;
      line-height: 1;
      font-family: var(--head);
    }}
    .rail-copy {{
      margin-top: 10px;
      color: var(--text-muted);
      font-size: 13px;
      line-height: 1.6;
    }}
    .rail-section {{
      margin-top: 28px;
    }}
    .rail-label {{
      font-family: var(--mono);
      font-size: 10px;
      letter-spacing: .16em;
      color: var(--gold);
      text-transform: uppercase;
      margin-bottom: 10px;
    }}
    .rail-item {{
      padding: 10px 12px;
      margin-bottom: 8px;
      border: 1px solid var(--border);
      background: color-mix(in srgb, var(--red) 12%, transparent);
      color: var(--text);
      text-transform: uppercase;
      letter-spacing: .08em;
      font-size: 12px;
    }}
    .content {{
      padding: 28px 32px 40px;
      position: relative;
    }}
    .watermark {{
      position: absolute;
      top: 24px;
      right: 32px;
      font-family: var(--head);
      font-size: 150px;
      line-height: .85;
      letter-spacing: .1em;
      color: rgba(255,255,255,.02);
      text-transform: uppercase;
      pointer-events: none;
    }}
    .hero {{
      border: 1px solid color-mix(in srgb, var(--gold) 16%, transparent);
      background: linear-gradient(180deg, rgba(255,255,255,.018), rgba(255,255,255,0)), var(--surface);
      padding: 28px;
      position: relative;
      overflow: hidden;
    }}
    .hero::before {{
      content: '';
      position: absolute;
      inset: 0 auto auto 0;
      width: 140px;
      height: 2px;
      background: linear-gradient(90deg, var(--red), var(--gold), transparent);
    }}
    .hero::after {{
      content: '';
      position: absolute;
      right: -80px;
      top: -80px;
      width: 320px;
      height: 320px;
      background: radial-gradient(circle, color-mix(in srgb, var(--red) 20%, transparent), transparent 60%);
      filter: blur(10px);
    }}
    .hero-grid {{
      display: grid;
      grid-template-columns: minmax(0, 1.6fr) minmax(320px, .9fr);
      gap: 24px;
      position: relative;
      z-index: 1;
    }}
    .eyebrow {{
      font-family: var(--mono);
      font-size: 11px;
      letter-spacing: .18em;
      color: var(--gold);
      text-transform: uppercase;
    }}
    h1 {{
      margin: 10px 0 8px;
      font-family: var(--head);
      font-size: 48px;
      letter-spacing: .1em;
      text-transform: uppercase;
    }}
    .lede {{
      max-width: 760px;
      color: var(--text-muted);
      line-height: 1.75;
      font-size: 15px;
    }}
    .hero-quote {{
      margin-top: 18px;
      max-width: 680px;
      font-family: var(--head);
      font-size: 22px;
      letter-spacing: .05em;
      text-transform: uppercase;
      color: var(--ash);
    }}
    .hero-side {{
      border: 1px solid color-mix(in srgb, var(--gold) 18%, transparent);
      background: rgba(8,7,8,.55);
      padding: 18px;
      backdrop-filter: blur(4px);
    }}
    .hero-side .token-row {{
      margin-top: 14px;
    }}
    .summary-strip {{
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      gap: 12px;
      margin-top: 16px;
    }}
    .strip-item {{
      padding: 12px 14px;
      border: 1px solid var(--border);
      background: linear-gradient(180deg, color-mix(in srgb, var(--red) 10%, transparent), transparent), var(--panel-2);
    }}
    .strip-label {{
      font-family: var(--mono);
      font-size: 10px;
      letter-spacing: .16em;
      color: var(--gold);
      text-transform: uppercase;
    }}
    .strip-value {{
      margin-top: 8px;
      font-family: var(--head);
      font-size: 24px;
      letter-spacing: .08em;
      text-transform: uppercase;
    }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(12, 1fr);
      gap: 16px;
      margin-top: 18px;
    }}
    .card {{
      grid-column: span 12;
      background: linear-gradient(180deg, rgba(255,255,255,.02), rgba(255,255,255,0)), var(--panel);
      border: 1px solid var(--border);
      padding: 18px;
      position: relative;
    }}
    .card::before {{
      content: '';
      position: absolute;
      inset: 0 auto auto 0;
      width: 72px;
      height: 2px;
      background: var(--red);
    }}
    .card.span-4 {{ grid-column: span 4; }}
    .card.span-6 {{ grid-column: span 6; }}
    .card.span-8 {{ grid-column: span 8; }}
    .card-title {{
      margin-bottom: 14px;
      font-family: var(--mono);
      font-size: 11px;
      letter-spacing: .16em;
      color: var(--gold);
      text-transform: uppercase;
    }}
    .metric {{
      font-family: var(--head);
      font-size: 34px;
      letter-spacing: .06em;
    }}
    .metric-sub {{
      margin-top: 6px;
      font-size: 12px;
      color: var(--text-muted);
      text-transform: uppercase;
      letter-spacing: .08em;
    }}
    .eyeline {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      margin-bottom: 14px;
    }}
    .status-chip {{
      display: inline-flex;
      align-items: center;
      gap: 8px;
      padding: 4px 8px;
      border: 1px solid color-mix(in srgb, var(--green) 28%, transparent);
      background: color-mix(in srgb, var(--green) 12%, transparent);
      color: var(--green);
      font-family: var(--mono);
      font-size: 11px;
      letter-spacing: .08em;
      text-transform: uppercase;
    }}
    .status-dot {{
      width: 8px;
      height: 8px;
      border-radius: 999px;
      background: var(--green);
      box-shadow: 0 0 12px color-mix(in srgb, var(--green) 70%, transparent);
    }}
    .token-row {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
    }}
    .token {{
      display: inline-flex;
      padding: 4px 8px;
      border: 1px solid color-mix(in srgb, var(--red-soft) 34%, transparent);
      background: color-mix(in srgb, var(--red) 14%, transparent);
      font-size: 11px;
      font-family: var(--mono);
      text-transform: uppercase;
      letter-spacing: .08em;
      color: color-mix(in srgb, var(--text) 88%, white);
    }}
    .active-token {{
      background: color-mix(in srgb, var(--gold) 16%, transparent);
      border-color: color-mix(in srgb, var(--gold) 42%, transparent);
      color: var(--gold);
    }}
    ul {{
      margin: 0;
      padding-left: 18px;
    }}
    li + li {{
      margin-top: 8px;
    }}
    pre {{
      margin: 0;
      white-space: pre-wrap;
      word-break: break-word;
      font-family: var(--mono);
      font-size: 12px;
      line-height: 1.6;
      color: var(--text);
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      font-size: 13px;
    }}
    td {{
      padding: 10px 0;
      border-bottom: 1px solid rgba(166,150,145,.14);
    }}
    .mono {{
      font-family: var(--mono);
      color: var(--text-muted);
    }}
    .lead-copy {{
      color: var(--text-muted);
      line-height: 1.7;
      max-width: 72ch;
    }}
    .transcript-item {{
      padding: 14px 0;
      border-bottom: 1px solid rgba(166,150,145,.14);
    }}
    .transcript-item:last-child {{
      border-bottom: none;
    }}
    .transcript-meta {{
      font-family: var(--mono);
      font-size: 11px;
      color: var(--gold);
      letter-spacing: .08em;
      text-transform: uppercase;
    }}
    .transcript-text {{
      margin-top: 8px;
      color: var(--text);
      line-height: 1.7;
      font-size: 15px;
    }}
    .artifact-note {{
      margin-top: 14px;
      color: var(--text-muted);
      line-height: 1.6;
      font-size: 13px;
    }}
    .footer {{
      margin-top: 18px;
      font-family: var(--mono);
      font-size: 11px;
      letter-spacing: .14em;
      color: var(--text-muted);
      text-transform: uppercase;
    }}
    @media (max-width: 1200px) {{
      .hero-grid,
      .summary-strip {{
        grid-template-columns: 1fr;
      }}
    }}
    @media (max-width: 1080px) {{
      .card.span-4, .card.span-6, .card.span-8 {{ grid-column: span 12; }}
    }}
    @media (max-width: 860px) {{
      .layout {{ grid-template-columns: 1fr; }}
      .rail {{ border-right: none; border-bottom: 1px solid var(--border-strong); }}
      .rail::after,
      .watermark {{ display: none; }}
      .content {{ padding: 20px; }}
      h1 {{ font-size: 38px; }}
    }}
    /* ── Mobile ── */
    .mobile-toggle {{ display: none; }}
    @media (max-width: 640px) {{
      .mobile-toggle {{
        display: flex; align-items: center; gap: 8px;
        position: fixed; top: 14px; left: 14px; z-index: 200;
        background: var(--panel); border: 1px solid var(--border-strong);
        padding: 7px 12px; cursor: pointer;
        font-family: var(--mono); font-size: 11px;
        letter-spacing: .12em; color: var(--gold);
        text-transform: uppercase;
        box-shadow: 0 2px 10px rgba(0,0,0,.5);
      }}
      .rail {{
        position: fixed; inset: 0; z-index: 100;
        overflow-y: auto; padding-top: 60px;
        transform: translateX(-100%); transition: transform .22s ease;
        box-shadow: 4px 0 24px rgba(0,0,0,.6);
      }}
      .rail.open {{ transform: translateX(0); }}
      .rail-backdrop {{
        display: none; position: fixed; inset: 0; z-index: 99;
        background: rgba(0,0,0,.6); backdrop-filter: blur(2px);
      }}
      .rail-backdrop.open {{ display: block; }}
      .layout {{ grid-template-columns: 1fr; }}
      .content {{ padding: 60px 14px 20px; }}
      h1 {{ font-size: 28px; }}
      .hero-grid {{ grid-template-columns: 1fr; }}
      .summary-strip {{ grid-template-columns: 1fr 1fr; }}
      .card.span-4, .card.span-6, .card.span-8, .card.span-12 {{ grid-column: span 12; }}
    }}
    @media (max-width: 400px) {{
      .summary-strip {{ grid-template-columns: 1fr; }}
      h1 {{ font-size: 22px; }}
    }}
  </style>
</head>
<body>
  <div class="scanlines"></div>
  <button class="mobile-toggle" onclick="toggleRail()" aria-label="Menu">☰ INFO</button>
  <div class="rail-backdrop" id="rail-backdrop" onclick="toggleRail()"></div>
  <div class="layout">
    <aside class="rail">
      <div class="brand">Soul<span>killer</span></div>
      <div class="sub">SECURE YOUR SOUL · MIKOSHI PROTOCOL</div>

      <div class="rail-section">
        <div class="rail-label">MODE · 荒坂 CORP</div>
        <div class="rail-item">Synthetic Demo</div>
        <div class="rail-item">No Live Runtime</div>
        <div class="rail-item">No Private Data</div>
      </div>

      <div class="rail-block">
        <div class="rail-label">Signal Count</div>
        <div class="metric">{escape(str(summary["message_count"]))}</div>
        <div class="rail-copy">
          Demo-safe observations rendered as an executive preview console.
        </div>
      </div>

      <div class="rail-section">
        <div class="rail-label">Artifacts</div>
        <div class="rail-item">{escape(SUMMARY_FILENAME)}</div>
        <div class="rail-item">{escape(MODEL_PROFILE_FILENAME)}</div>
        <div class="rail-item">{escape(MODEL_PORTRAIT_FILENAME)}</div>
      </div>
    </aside>

    <main class="content">
      <div class="watermark">MIKOSHI<br>{subject_slug}</div>
      <section class="hero">
        <div class="hero-grid">
          <div>
            <div class="eyebrow">荒坂 CORP · ENGRAMMATIC TRANSFER SYSTEM · DEMO</div>
            <h1>{escape(summary["subject_name"])}</h1>
            <div class="lede">
              Public-facing synthetic console generated from demo artifacts only.
              This view is isolated from the private Soulkiller runtime — no live databases, schedulers, or personal logs are accessed. The soul map below is entirely synthetic.
            </div>
            <div class="hero-quote">{escape(theme["hero_quote"])}</div>
          </div>
          <aside class="hero-side">
            <div class="eyebrow">SOUL MAP · SNAPSHOT</div>
            <div class="lead-copy" style="margin-top:12px">
              {escape(summary["subject_name"])} is represented here through synthetic behavioral traces, facet positions, and stable goal patterns. This is a public showcase artifact — not a live surveillance surface.
            </div>
            <div class="token-row" style="margin-top:14px">{variant_tokens}</div>
            <div class="token-row">{top_traits}</div>
          </aside>
        </div>
        <div class="summary-strip">
          <div class="strip-item">
            <div class="strip-label">Facets Modeled</div>
            <div class="strip-value">{facet_count} / 60</div>
          </div>
          <div class="strip-item">
            <div class="strip-label">Observations</div>
            <div class="strip-value">{obs_seed} seed</div>
          </div>
          <div class="strip-item">
            <div class="strip-label">Extraction Pass</div>
            <div class="strip-value">{obs_demo} signals</div>
          </div>
          <div class="strip-item">
            <div class="strip-label">Hypotheses</div>
            <div class="strip-value">{hypothesis_count} cross-facet</div>
          </div>
        </div>
      </section>

      <section class="grid">
        <article class="card span-4">
          <div class="card-title">Behavioral Signal Snapshot</div>
          <div class="metric">{escape(str(summary["message_count"]))}</div>
          <div class="metric-sub">Synthetic messages</div>
        </article>
        <article class="card span-4">
          <div class="card-title">Top Traits</div>
          <div class="token-row">{top_traits}</div>
        </article>
        <article class="card span-4">
          <div class="card-title">Active Goals</div>
          <ul>{goals}</ul>
        </article>

        <article class="card span-8">
          <div class="eyeline">
            <div class="card-title" style="margin:0">Synthetic Transcript</div>
            <div class="status-chip"><span class="status-dot"></span> Demo Safe</div>
          </div>
          {transcript}
        </article>
        <article class="card span-4">
          <div class="card-title">Top Confidence Facets</div>
          <table>
            <thead><tr>
              <td class="mono" style="font-size:10px;letter-spacing:.12em;color:var(--gold);padding-bottom:6px">FACET</td>
              <td class="mono" style="font-size:10px;letter-spacing:.12em;color:var(--gold);padding-bottom:6px">POS</td>
              <td class="mono" style="font-size:10px;letter-spacing:.12em;color:var(--gold);padding-bottom:6px">CONF</td>
            </tr></thead>
            <tbody>{high_conf_rows}</tbody>
          </table>
        </article>

        <article class="card span-6">
          <div class="eyeline">
            <div class="card-title" style="margin:0">Profile Output</div>
            <div class="status-chip"><span class="status-dot"></span> Generated</div>
          </div>
          <pre>{escape(profile_md)}</pre>
        </article>
        <article class="card span-6">
          <div class="eyeline">
            <div class="card-title" style="margin:0">Portrait Output</div>
            <div class="status-chip"><span class="status-dot"></span> Generated</div>
          </div>
          <pre>{escape(portrait_md)}</pre>
        </article>

        <article class="card span-6">
          <div class="card-title">Runtime Artifacts</div>
          <table>{artifacts}</table>
          <div class="artifact-note">
            These files are the full boundary of the public UI.
            No live database queries, scheduler reads, or private channel hooks are used here.
          </div>
        </article>
        <article class="card span-6">
          <div class="card-title">Raw Event Log</div>
          <pre>{escape(event_log)}</pre>
        </article>

        <article class="card span-12">
          <div class="card-title">Synthetic Delivery Log</div>
          <pre>{escape(delivery_log)}</pre>
          <div class="footer">荒坂 CORP · SOULKILLER · SECURE YOUR SOUL · Engrammatic transfer demo — no live runtime. Unauthorized access is a capital offense.</div>
        </article>
      </section>
    </main>
  </div>
  <script>
    function toggleRail() {{
      document.querySelector('.rail').classList.toggle('open');
      document.getElementById('rail-backdrop').classList.toggle('open');
      document.body.style.overflow =
        document.querySelector('.rail').classList.contains('open') ? 'hidden' : '';
    }}
  </script>
</body>
</html>
"""


def write_demo_console(
    output_dir: Path, html_path: Path | None = None, variant: str | None = None
) -> Path:
    output_dir = Path(output_dir)
    variant_name, _ = _variant_or_default(variant)
    html_path = html_path or (output_dir / DEMO_CONSOLE_FILENAME)
    html_path.write_text(
        build_demo_console(output_dir, variant=variant_name), encoding="utf-8"
    )
    return html_path


def write_all_demo_variants(output_dir: Path) -> list[Path]:
    output_dir = Path(output_dir)
    generated = []
    for variant_name in VARIANTS:
        path = output_dir / f"demo_console.{variant_name}.html"
        generated.append(write_demo_console(output_dir, path, variant=variant_name))
    return generated


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the static Soulkiller demo console.")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("demo") / "generated",
        help="Directory containing demo artifacts from the demo runner.",
    )
    parser.add_argument(
        "--html-path",
        type=Path,
        default=None,
        help="Destination path for the generated demo console HTML.",
    )
    parser.add_argument(
        "--variant",
        default=DEFAULT_VARIANT,
        choices=sorted(VARIANTS),
        help="Visual variant to generate.",
    )
    parser.add_argument(
        "--all-variants",
        action="store_true",
        help="Generate all supported visual variants next to the default HTML.",
    )
    args = parser.parse_args()

    html_path = write_demo_console(args.output_dir, args.html_path, variant=args.variant)
    print(f"Demo console written to {html_path}")
    if args.all_variants:
        generated = write_all_demo_variants(args.output_dir)
        print("Generated variants:")
        for path in generated:
            print(f"- {path}")


if __name__ == "__main__":
    main()
