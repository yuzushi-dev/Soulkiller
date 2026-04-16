/**
 * Soulkiller Message Capture & Delivery Hook
 *
 * message:received:
 *   1. Captures inbound Telegram messages from the configured subject → inbox.jsonl
 *   2. If a check-in reply is pending, captures it to DB (follow-up sent by cron)
 *
 * message:sent:
 *   3. Tracks delivery success/failure for check-in messages
 */

import type { HookHandler } from 'openclaw/hooks';

const SUBJECT_ID = process.env.SOULKILLER_SUBJECT_ID || 'demo-subject';
const REPLY_WINDOW_MS = 4 * 60 * 60 * 1000;
const OPENCLAW_BIN = process.env.OPENCLAW_BIN || 'openclaw';

function soulkillerDir(): string {
  if (process.env.SOULKILLER_DATA_DIR) return process.env.SOULKILLER_DATA_DIR;
  const openclawHome = process.env.OPENCLAW_HOME || process.env.HOME || '';
  return `${openclawHome}/.openclaw/runtime/soulkiller`;
}

function scriptsDir(): string {
  if (process.env.SOULKILLER_SCRIPTS_DIR) return process.env.SOULKILLER_SCRIPTS_DIR;
  const openclawHome = process.env.OPENCLAW_HOME || process.env.HOME || '';
  return `${openclawHome}/.openclaw/runtime/soulkiller`;
}

async function handleReceived(event: any): Promise<void> {
  if (event.context.channelId !== 'telegram') return;

  const from = event.context.from || event.context.metadata?.senderId || '';
  const fromId = String(from).includes(':') ? String(from).split(':')[1] : String(from);
  if (fromId !== SUBJECT_ID) return;

  const content = event.context.content || '';
  if (!content.trim()) return;

  const fs = await import('node:fs/promises');
  const dir = soulkillerDir();
  const inboxPath = `${dir}/inbox.jsonl`;
  const signalPath = `${dir}/pending-checkin.json`;

  const entry = {
    message_id: event.context.messageId || '',
    from: String(from),
    content,
    channel_id: event.context.channelId,
    received_at: event.timestamp instanceof Date
      ? event.timestamp.toISOString()
      : new Date().toISOString(),
  };

  try {
    await fs.mkdir(dir, { recursive: true });
    await fs.appendFile(inboxPath, JSON.stringify(entry) + '\n');
  } catch (err) {
    console.error('[soulkiller-capture] inbox append failed:', err instanceof Error ? err.message : String(err));
  }

  try {
    const raw = await fs.readFile(signalPath, 'utf-8').catch(() => '');
    if (!raw.trim()) return;

    const pending = JSON.parse(raw);
    const askedAt = new Date(pending.asked_at).getTime();
    if (Number.isNaN(askedAt) || Date.now() - askedAt > REPLY_WINDOW_MS) {
      await fs.unlink(signalPath).catch(() => {});
      return;
    }

    await fs.unlink(signalPath).catch(() => {});

    const exchangeId = pending.exchange_id || '';

    // Trigger the follow-up cron immediately instead of waiting for the next scheduled run.
    // The reply is already written to inbox.jsonl above; the follow-up cron reads it from there.
    // Exchange-id association is handled internally by soulkiller:checkin-followup.
    const followupCron = process.env.SOULKILLER_FOLLOWUP_CRON || 'soulkiller:checkin-followup';
    const { spawn: spawnCron } = await import('node:child_process');
    const cronChild = spawnCron(OPENCLAW_BIN, [
      'cron', 'run', followupCron, '--force',
    ], { detached: true, stdio: 'ignore' });
    cronChild.unref();

    console.log(`[soulkiller-capture] Reply captured for exchange ${exchangeId} - triggered ${followupCron}`);
  } catch (err) {
    console.error('[soulkiller-capture] Follow-up trigger failed:', err instanceof Error ? err.message : String(err));
  }
}

async function handleSent(event: any): Promise<void> {
  if (event.context.channelId !== 'telegram') return;

  const to = event.context.to || '';
  if (String(to) !== SUBJECT_ID) return;

  const content = event.context.content || '';
  const success = event.context.success !== false;
  const messageId = event.context.messageId || '';
  const error = event.context.error || '';

  const fs = await import('node:fs/promises');
  const dir = soulkillerDir();
  const logPath = `${dir}/delivery.jsonl`;

  const logEntry = {
    to: String(to),
    content_preview: content.substring(0, 80),
    success,
    message_id: messageId,
    error: error || undefined,
    sent_at: event.timestamp instanceof Date
      ? event.timestamp.toISOString()
      : new Date().toISOString(),
  };

  try {
    await fs.mkdir(dir, { recursive: true });
    await fs.appendFile(logPath, JSON.stringify(logEntry) + '\n');
  } catch (err) {
    console.error('[soulkiller-capture] Delivery log failed:', err instanceof Error ? err.message : String(err));
  }
}

const handler: HookHandler = async (event) => {
  if (!event || typeof event !== 'object') return;
  if (event.type !== 'message') return;
  if (!event.context || typeof event.context !== 'object') return;

  if (event.action === 'received') {
    await handleReceived(event);
  } else if (event.action === 'sent') {
    await handleSent(event);
  }
};

export default handler;
