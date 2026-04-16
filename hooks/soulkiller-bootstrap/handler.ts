/**
 * Soulkiller Bootstrap Hook
 *
 * Injects the current personality portrait (PORTRAIT.md) into every agent
 * session's bootstrap context.
 */

import type { HookHandler } from 'openclaw/hooks';
import { filterByQuery } from '../shared/smelt-retrieval.ts';
import { getLastUserMessage } from '../shared/last-message.ts';

const handler: HookHandler = async (event) => {
  if (!event || typeof event !== 'object') return;
  if (event.type !== 'agent' || event.action !== 'bootstrap') return;
  if (!event.context || typeof event.context !== 'object') return;

  const sessionKey = event.sessionKey || '';
  if (sessionKey.includes(':subagent:')) return;
  if (sessionKey.includes('soulkiller:')) return;

  if (!Array.isArray(event.context.bootstrapFiles)) return;

  const fs = await import('node:fs/promises');
  const dataDir = process.env.SOULKILLER_DATA_DIR || (() => {
    const openclawHome = process.env.OPENCLAW_HOME || process.env.HOME || '';
    return `${openclawHome}/.openclaw/runtime/soulkiller`;
  })();
  const profilePath = `${dataDir}/PORTRAIT.md`;

  const inboxPath = `${dataDir}/inbox.jsonl`;
  const lastMessage = getLastUserMessage(inboxPath);

  try {
    const content = await fs.readFile(profilePath, 'utf-8');
    if (!content.trim()) return;
    const profileContent = lastMessage
      ? filterByQuery(content, lastMessage)
      : content;

    const injected = [
      '# Personality Model - Configured Subject',
      '',
      '⚠️ CRITICAL - READ BEFORE ANYTHING ELSE:',
      'You are in a CONVERSATIONAL SESSION with the configured subject. Your role here is to TALK to them, not to analyze them.',
      '',
      'ABSOLUTE PROHIBITIONS in this session:',
      '- NEVER output JSON of any kind in your replies to the configured subject.',
      '- NEVER output {"signals": [...]} or any signal extraction format.',
      '- NEVER extract personality signals or facets from messages.',
      '- NEVER analyze a message for psychological traits.',
      '- The personality model below is READ-ONLY background context. You do NOT update it here.',
      '- Signal extraction is done by background processes (cron jobs). NOT by you, NOT in this session.',
      '',
      'HOW to use this model: let it shape HOW you speak (tone, directness, depth), not WHAT you report.',
      'A good friend who knows someone deeply does not run a psychological report - they just speak from that knowledge.',
      '',
      '---',
      '',
      profileContent,
    ].join('\n');

    event.context.bootstrapFiles.push({
      path: 'PERSONALITY_MODEL.md',
      content: injected,
      virtual: true,
    });
  } catch {
    // PORTRAIT.md missing - skip silently.
  }
};

export default handler;
