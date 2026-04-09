/**
 * SMELT Layer 4 — Query-Conditioned Retrieval (TypeScript port)
 *
 * Porta il Layer 4 di SMELT (github.com/TooCas/SMELT) in TypeScript per
 * l'uso negli hook OpenClaw. Riceve markdown come stringa + query dell'utente,
 * restituisce solo le sezioni rilevanti via TF-IDF con weighting strutturale.
 *
 * Layer 1 (zstd archival), Layer 2 (schema codes), Layer 3 (macro dict)
 * sono omessi intenzionalmente: per gli hook serve solo il retrieval query-aware.
 *
 * Riduzione tipica su file Soulkiller: 80-98% su query targeted.
 * Fallback: restituisce l'intero contenuto se nessun record supera il cutoff.
 */

// ── Stopwords ────────────────────────────────────────────────────────────────

export const STOPWORDS = new Set([
  // English
  "a","about","all","am","an","and","any","are","as","at","be","because",
  "been","before","being","between","both","but","by","can","could","did",
  "do","does","for","from","get","got","had","has","have","he","her","here",
  "him","his","how","i","if","in","into","is","it","its","just","like","may",
  "maybe","me","more","most","my","no","not","of","on","one","or","our","out",
  "please","real","really","same","she","should","so","some","tell","than",
  "that","the","their","them","then","there","these","they","this","those",
  "through","to","up","us","use","using","very","want","was","we","well",
  "were","what","when","where","which","who","why","with","would","you","your",
  // Italiano
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
  "un","una","uno","vi","già","fare","fatto","essere",
]);

// ── Tokenizer ────────────────────────────────────────────────────────────────

function normalizeToken(token: string): string {
  token = token.replace(/'s$/, "");
  // English
  if (token.length > 4 && token.endsWith("ies")) return token.slice(0, -3) + "y";
  if (token.length > 4 && token.endsWith("s") && !token.endsWith("ss")) return token.slice(0, -1);
  // Italian: plurale maschile -i → -o (solo parole > 5 chars per evitare falsi positivi)
  if (token.length > 5 && token.endsWith("i") && !token.endsWith("ii")) {
    const stem = token.slice(0, -1) + "o";
    return stem;
  }
  return token;
}

export function tokenize(text: string): string[] {
  const tokens: string[] = [];
  for (const match of text.toLowerCase().matchAll(/[a-z0-9][a-z0-9'/_-]*/g)) {
    const t = normalizeToken(match[0].replace(/^'+|'+$/g, ""));
    if (t.length > 1 && !STOPWORDS.has(t)) tokens.push(t);
  }
  return tokens;
}

function bigrams(tokens: string[]): string[] {
  return tokens.slice(0, -1).map((t, i) => `${t} ${tokens[i + 1]}`);
}

// ── Markdown parser ───────────────────────────────────────────────────────────

interface Block {
  /** Heading sotto cui cade questo blocco (testo puro, senza #) */
  headingText: string;
  /** Indice del record heading in `allBlocks` */
  headingIdx: number;
  /** Testo completo del blocco (per scoring) */
  text: string;
  /** Righe originali da includere nell'output */
  raw: string;
  isHeading: boolean;
}

function parseMarkdown(content: string): Block[] {
  const lines = content.split("\n");
  const blocks: Block[] = [];
  let currentHeading = "";
  let currentHeadingIdx = 0;
  let buf: string[] = [];

  function flush() {
    if (buf.length === 0) return;
    const raw = buf.join("\n");
    const text = raw.replace(/[*_`#>]/g, " ").replace(/\s+/g, " ").trim();
    if (text) {
      blocks.push({ headingText: currentHeading, headingIdx: currentHeadingIdx, text, raw, isHeading: false });
    }
    buf = [];
  }

  for (const line of lines) {
    const hm = line.match(/^(#{1,6})\s+(.+)$/);
    if (hm) {
      flush();
      currentHeadingIdx = blocks.length;
      currentHeading = hm[2].replace(/[*_`]/g, "").trim();
      blocks.push({ headingText: currentHeading, headingIdx: currentHeadingIdx, text: currentHeading, raw: line, isHeading: true });
    } else if (line.trim() === "") {
      flush();
    } else {
      buf.push(line);
    }
  }
  flush();
  return blocks;
}

// ── TF-IDF scoring ────────────────────────────────────────────────────────────

interface ScoredBlock extends Block {
  score: number;
}

const MIN_CONTENT_CHARS = 300; // file sotto questa soglia: restituisci tutto

function scoreBlocks(blocks: Block[], queryTerms: string[]): ScoredBlock[] {
  if (queryTerms.length === 0) return blocks.map(b => ({ ...b, score: 0 }));

  const qBigrams = bigrams(queryTerms);
  const fullQuery = queryTerms.join(" ");
  const contentBlocks = blocks.filter(b => !b.isHeading);
  const N = Math.max(contentBlocks.length, 1);

  // Document frequency per IDF
  const df = new Map<string, number>();
  for (const b of contentBlocks) {
    const seen = new Set([...tokenize(b.text), ...tokenize(b.headingText)]);
    for (const t of seen) df.set(t, (df.get(t) ?? 0) + 1);
  }

  return blocks.map(b => {
    if (b.isHeading) return { ...b, score: 0 };

    const textTok = new Set(tokenize(b.text));
    const headTok = new Set(tokenize(b.headingText));
    const combined = (b.text + " " + b.headingText).toLowerCase();

    let score = 0;
    for (const term of queryTerms) {
      const idf = 1 + Math.log((1 + N) / (1 + (df.get(term) ?? 0)));
      if (textTok.has(term))       score += 3.0 * idf;
      else if (headTok.has(term))  score += 1.7 * idf;
      else if (combined.includes(term)) score += 1.1 * idf;
    }
    for (const bg of qBigrams) {
      if (combined.includes(bg)) score += 2.4;
    }
    if (fullQuery && combined.includes(fullQuery)) score += 3.5;

    // Bonus strutturale: field label (riga con "key=value" o "**Key:**")
    if (/[a-z_]+=\S/.test(b.raw) || /\*\*[^*]+:\*\*/.test(b.raw)) score += 0.5;

    return { ...b, score };
  });
}

// ── Entry point ───────────────────────────────────────────────────────────────

/**
 * Filtra il contenuto markdown restituendo solo le sezioni rilevanti per la query.
 *
 * @param content   Contenuto markdown completo (stringa, non path)
 * @param query     Ultimo messaggio dell'utente o altra query di contesto
 * @param maxBlocks Numero massimo di blocchi contenuto da includere (default 25)
 * @returns         Markdown filtrato; fallback all'originale se nessun match
 */
export function filterByQuery(content: string, query: string, maxBlocks = 25): string {
  if (content.length < MIN_CONTENT_CHARS) return content;

  const blocks = parseMarkdown(content);
  const queryTerms = tokenize(query);

  // Query vuota o irrilevante: restituisci tutto
  if (queryTerms.length === 0) return content;

  const scored = scoreBlocks(blocks, queryTerms);
  const contentScored = scored.filter(b => !b.isHeading);

  if (contentScored.length === 0) return content;

  const topScore = Math.max(...contentScored.map(b => b.score));
  if (topScore < 1.0) return content; // nessun match significativo → fallback

  const cutoff = Math.max(1.5, topScore * 0.35);
  const chosen = contentScored
    .filter(b => b.score >= cutoff)
    .sort((a, b) => b.score - a.score)
    .slice(0, maxBlocks);

  if (chosen.length === 0) return content;

  // Raccogli heading unici dei blocchi selezionati
  const includedHeadingIdxs = new Set(chosen.map(b => b.headingIdx));

  // Ricostruisci output in ordine originale
  const output: string[] = [];
  const chosenSet = new Set(chosen.map(b => b.raw));
  let lastWasHeading = false;

  for (const b of blocks) {
    if (b.isHeading) {
      if (includedHeadingIdxs.has(b.headingIdx)) {
        output.push(b.raw);
        lastWasHeading = true;
      }
    } else if (chosenSet.has(b.raw)) {
      output.push(b.raw);
      lastWasHeading = false;
    }
  }

  const result = output.join("\n\n").trim() + "\n";
  // Sanity check: non restituire mai meno del 5% del contenuto originale
  return result.length >= content.length * 0.05 ? result : content;
}
