/**
 * Last User Message - reads inbox.jsonl and returns the most recent message
 * from the configured demo subject, for use as SMELT retrieval query.
 */

import { readFileSync } from "node:fs";

const SUBJECT_FROM = `telegram:${process.env.SOULKILLER_SUBJECT_ID || "demo-subject"}`;

interface InboxEntry {
  message_id: string;
  from: string;
  content: string;
  channel_id: string;
  received_at: string;
}

/**
 * Returns the content of the most recent message from the configured subject in inbox.jsonl.
 * Falls back to "" if the file is missing, empty, or no matching entry exists.
 *
 * @param inboxPath  Absolute path to inbox.jsonl
 */
export function getLastUserMessage(inboxPath: string): string {
  let raw: string;
  try {
    raw = readFileSync(inboxPath, "utf-8");
  } catch {
    return "";
  }

  const entries: InboxEntry[] = [];
  for (const line of raw.split("\n")) {
    const trimmed = line.trim();
    if (!trimmed) continue;
    try {
      const entry = JSON.parse(trimmed) as InboxEntry;
      if (entry.from === SUBJECT_FROM && typeof entry.content === "string") {
        entries.push(entry);
      }
    } catch {
      // skip malformed lines
    }
  }

  if (entries.length === 0) return "";

  entries.sort((a, b) =>
    new Date(b.received_at).getTime() - new Date(a.received_at).getTime()
  );

  return entries[0].content;
}
