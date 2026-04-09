"""
SMELT Layer 4 — Query-Conditioned Facet Retrieval (Python port).

Porta la logica di smelt-retrieval.ts in Python per filtrare la lista facets
nell'extractor: dato il contenuto dei messaggi in lavorazione, restituisce
solo le facets rilevanti via TF-IDF con structural weighting.

Riduzione tipica: 40-70% della facet list su batch tematici.
Fallback garantito: se nessun match supera la soglia → restituisce tutte le facets.

Usage:
    from soulkiller_facet_filter import filter_facets_by_query

    relevant = filter_facets_by_query(facets, messages_text, max_facets=40)
"""
from __future__ import annotations

import math
import re

# ── Stopwords (EN + IT) ───────────────────────────────────────────────────────

_STOPWORDS = {
    # English
    "a","about","all","am","an","and","any","are","as","at","be","because",
    "been","before","being","between","both","but","by","can","could","did",
    "do","does","for","from","get","got","had","has","have","he","her","here",
    "him","his","how","i","if","in","into","is","it","its","just","like","may",
    "maybe","me","more","most","my","no","not","of","on","one","or","our","out",
    "please","real","really","same","she","should","so","some","tell","than",
    "that","the","their","them","then","there","these","they","this","those",
    "through","to","up","us","use","using","very","want","was","we","well",
    "were","what","when","where","which","who","why","with","would","you","your",
    # Italiano
    "al","alla","alle","agli","ai","allo","anche","ancora","altro","altri",
    "altra","altre","chi","che","ci","col","come","con","cosa","cui","dal",
    "dalla","dalle","dagli","dai","dallo","del","della","delle","degli","dei",
    "dello","di","dove","e","è","gli","già","ho","hai","ha","hanno","il",
    "in","io","la","le","lei","li","lo","lui","loro","mai","me","mi","nel",
    "nella","nelle","negli","nei","nello","no","non","ogni","per","però",
    "perché","più","poi","quando","questa","questo","questi","queste","quello",
    "quella","quelli","quelle","qui","se","sei","sempre","si","sia","siamo",
    "siete","sono","su","sul","sulla","sulle","sugli","sui","sullo","suo",
    "sua","suoi","sue","te","ti","tra","fra","tutto","tutti","tutte","tutta",
    "un","una","uno","vi","da","fare","fatto","essere",
}

_MIN_FACETS_RATIO = 0.15  # fallback se i match sono < 15% del totale
_SCORE_CUTOFF_RATIO = 0.30  # includi facets con score >= top * ratio
_MIN_SCORE = 0.8  # soglia assoluta minima per includere una facet


def tokenize_query(text: str) -> list[str]:
    """Tokenizza query/messaggi: lowercase, rimuovi stopwords, normalizza plurali."""
    tokens = []
    for m in re.finditer(r"[a-zA-Zàáâäæãåāèéêëēėęîïíīįìôöòóœøōõùúûüūůűłśšłžźżçćčñńß][a-zA-Zàáâäæãåāèéêëēėęîïíīįìôöòóœøōõùúûüūůűłśšłžźżçćčñńß'/_-]*", text):
        t = m.group(0).lower().rstrip("'")
        if len(t) > 1 and t not in _STOPWORDS:
            tokens.append(_normalize_token(t))
    return tokens


def _normalize_token(token: str) -> str:
    # English plurals
    if len(token) > 4 and token.endswith("ies"):
        return token[:-3] + "y"
    if len(token) > 4 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    # Italian maschile plurale -i → -o
    if len(token) > 5 and token.endswith("i") and not token.endswith("ii"):
        return token[:-1] + "o"
    return token


def _facet_text(facet: dict) -> str:
    """Build searchable text from a facet dict."""
    parts = [facet.get("id", "")]
    low = facet.get("spectrum_low") or ""
    high = facet.get("spectrum_high") or ""
    if low:
        parts.append(low)
    if high:
        parts.append(high)
    return " ".join(parts)


def filter_facets_by_query(
    facets: list[dict],
    query: str,
    *,
    max_facets: int = 45,
) -> list[dict]:
    """Return only the facets relevant to `query` using TF-IDF scoring.

    Falls back to full list when:
    - query is empty / all stopwords
    - no facet scores above threshold
    - result would be smaller than _MIN_FACETS
    """
    if not facets:
        return facets

    query_terms = tokenize_query(query)
    if not query_terms:
        return facets

    # Build facet texts for scoring
    facet_texts = [_facet_text(f) for f in facets]
    facet_tokens = [set(tokenize_query(t)) for t in facet_texts]

    n = len(facets)

    # Document frequency for IDF
    df: dict[str, int] = {}
    for token_set in facet_tokens:
        for t in token_set:
            df[t] = df.get(t, 0) + 1

    # Score each facet
    scores: list[float] = []
    for i, token_set in enumerate(facet_tokens):
        score = 0.0
        for term in query_terms:
            idf = 1 + math.log((1 + n) / (1 + df.get(term, 0)))
            fid = facets[i].get("id", "")
            fid_tokens = set(tokenize_query(fid))
            raw_text = facet_texts[i].lower()

            if term in fid_tokens:
                score += 4.5 * idf  # key field (id) match — highest weight
            elif term in token_set:
                score += 3.0 * idf  # spectrum text match
            elif term in raw_text:
                score += 1.1 * idf  # substring match
        scores.append(score)

    top_score = max(scores)
    if top_score < _MIN_SCORE:
        # No meaningful match — return all
        return facets

    cutoff = max(_MIN_SCORE, top_score * _SCORE_CUTOFF_RATIO)
    above_cutoff = sorted(
        [(i, s) for i, s in enumerate(scores) if s >= cutoff],
        key=lambda x: x[1],
        reverse=True,
    )

    min_facets = max(3, int(len(facets) * _MIN_FACETS_RATIO))
    if len(above_cutoff) < min_facets:
        # Too few results — return full list
        return facets

    ranked = above_cutoff[:max_facets]

    # Restore original order
    selected_idxs = {i for i, _ in ranked}
    return [f for i, f in enumerate(facets) if i in selected_idxs]
