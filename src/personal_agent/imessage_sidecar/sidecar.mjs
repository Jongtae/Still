import { Console } from 'node:console';
import { once } from 'node:events';
import { readFileSync, realpathSync, statSync } from 'node:fs';
import { homedir } from 'node:os';
import { join } from 'node:path';
import { createInterface } from 'node:readline';
import { fileURLToPath } from 'node:url';

export const PROTOCOL_VERSION = 1;
const BACKFILL_PAGE = 500;
/** How long the sidecar waits after Still closes its stdin before it exits regardless. */
const EOF_GRACE_MS = 3_000;

const DISK_DENIED = /unable to open|authorization denied|not authorized|operation not permitted|EPERM|EACCES|SQLITE_CANTOPEN|SQLITE_AUTH/i;
const SIGN_IN = /signed in|not signed|sign in/i;

function nonemptyString(value) {
  return typeof value === 'string' && value.length > 0;
}

function dmChatHandle(chatId) {
  if (typeof chatId !== 'string') return;
  const [service, separator, ...parts] = chatId.split(';');
  if (service && separator === '-') return parts.join(';') || undefined;
}

function validScope({ mode, handle, since_ms, max_text_len }) {
  if (mode === 'off') return true;
  if (!Number.isInteger(since_ms)) return false;
  if (mode === 'paired') return nonemptyString(handle);
  return mode === 'pairing' && Number.isInteger(max_text_len) && max_text_len > 0;
}

export async function runSidecar({
  input, output, createKit, kitVersion, databasePath, recheckMs = 2_000, recheckWindowMs = 600_000,
}) {
  const lines = createInterface({ input, crlfDelay: Infinity });
  let scope = { mode: 'off' };
  let kit;
  let stopped = false;
  const inflight = new Set();
  const watched = new Map();
  let recheckTimer;

  function emit(event) {
    output.write(JSON.stringify(event) + '\n');
  }

  function badRequest(detail, id) {
    emit({ ev: 'error', code: 'bad_request', detail });
    if (typeof id === 'string') emit({ ev: 'send_failed', id, reason: 'bad_request' });
  }

  function watchError(error, databaseOperation = false) {
    if ((databaseOperation || error?.code === 'DATABASE') &&
        (DISK_DENIED.test(error?.message) || error?.code === 'EPERM' || error?.code === 'EACCES')) {
      emit({ ev: 'error', code: 'no_disk_access', detail: 'Messages database access was denied.' });
    } else {
      emit({ ev: 'error', code: 'db_unreadable', detail: 'The Messages database could not be read.' });
    }
  }

  function startError(error) {
    if (databasePath !== undefined) {
      try {
        statSync(databasePath);
      } catch (statError) {
        if (statError.code === 'ENOENT' || statError.code === 'ENOTDIR') {
          emit({ ev: 'error', code: 'messages_not_signed_in', detail: 'The Messages database does not exist.' });
          return;
        }
        if (statError.code === 'EPERM' || statError.code === 'EACCES') {
          watchError(statError, true);
          return;
        }
      }
    }
    watchError(error, true);
  }

  function forward(message, fromMe) {
    if (scope.mode === 'off' || message.chatKind !== 'dm' || message.isFromMe !== fromMe) return;
    const ts_ms = message.createdAt.getTime();
    if (!(ts_ms > scope.since_ms)) return;
    let handle = message.participant;
    if (handle == null) handle = dmChatHandle(message.chatId) ?? handle;
    const text = message.text ?? '';
    if (scope.mode === 'paired') {
      if (handle !== scope.handle) return;
    } else if (fromMe || !nonemptyString(text) || text.length > scope.max_text_len) {
      return;
    }

    const event = {
      ev: fromMe ? 'from_me' : 'message', guid: message.id, handle, text, ts_ms,
    };
    if (fromMe) {
      const sent = message.isSent === true;
      const delivered = message.isDelivered === true;
      const error = message.hasError === true && Number.isFinite(message.errorCode) ? message.errorCode : null;
      const prior = watched.get(message.id);
      if (prior && prior.sent === sent && prior.delivered === delivered && prior.error === error) return;
      Object.assign(event, { sent, delivered, error });
      if (delivered || error !== null) watched.delete(message.id);
      else watched.set(message.id, { ts_ms, sent, delivered, error,
        expires: prior?.expires ?? performance.now() + recheckWindowMs });
    } else {
      const reaction = message.reaction;
      event.reaction = reaction ? {
        kind: reaction.kind, target_guid: reaction.targetMessageId, removed: Boolean(reaction.isRemoved),
      } : null;
    }
    emit(event);
    if (fromMe) scheduleRecheck();
  }

  async function readPages(since_ms, current, visit) {
    const since = new Date(since_ms);
    for (let offset = 0; current(); offset += BACKFILL_PAGE) {
      const page = await kit.getMessages({ since, limit: BACKFILL_PAGE, offset });
      if (!current()) return;
      for (const message of page) visit(message);
      if (page.length < BACKFILL_PAGE) return;
    }
  }

  function scheduleRecheck() {
    if (recheckTimer || !watched.size) return;
    const appliedScope = scope;
    recheckTimer = setTimeout(() => void recheck(appliedScope), recheckMs).unref();
  }

  function clearRecheck() {
    clearTimeout(recheckTimer);
    recheckTimer = undefined;
    watched.clear();
  }

  async function recheck(appliedScope) {
    const current = () => !stopped && scope === appliedScope;
    const active = () => {
      if (!current()) return false;
      const now = performance.now();
      for (const [guid, row] of watched) {
        if (row.expires <= now) watched.delete(guid);
      }
      return watched.size > 0;
    };
    try {
      if (!active()) return;
      // The kit's watcher only reports new ROWIDs, so query for changes on rows already emitted.
      let since_ms = Infinity;
      for (const row of watched.values()) since_ms = Math.min(since_ms, row.ts_ms);
      await readPages(since_ms, active, (message) => {
        if (watched.has(message.id)) forward(message, true);
      });
    } catch (error) {
      if (current()) watchError(error, true);
    } finally {
      if (current()) {
        recheckTimer = undefined;
        scheduleRecheck();
      }
    }
  }

  async function backfill(appliedScope) {
    // The watcher starts at the current max ROWID, so rows that arrived while the sidecar was
    // down are read here. No participant filter: the kit implements it as handle.id = ?, which
    // drops outgoing rows without a handle. forward() applies the scope to every row, as it
    // does for live rows; nothing outside the scope is emitted.
    const { since_ms } = appliedScope;
    const current = () => !stopped && scope === appliedScope;
    const rows = new Map();
    try {
      // Pages are newest first; a row arriving meanwhile shifts the next page by one, so keep by guid.
      await readPages(since_ms, current, (message) => rows.set(message.id, message));
      if (!current()) return;
      const ordered = [...rows.values()].sort((a, b) => a.createdAt - b.createdAt || a.rowId - b.rowId);
      for (const message of ordered) forward(message, message.isFromMe === true);
    } catch (error) {
      if (current()) watchError(error, true);
    }
  }

  async function hello(version) {
    if (version !== PROTOCOL_VERSION) {
      badRequest('Unsupported sidecar protocol version.');
      return;
    }
    if (!kit) {
      try {
        const candidate = createKit();
        kit = candidate;
        const active = () => !stopped && kit === candidate;
        await candidate.startWatching({
          onDirectMessage: (message) => { if (active()) forward(message, false); },
          onFromMeMessage: (message) => { if (active()) forward(message, true); },
          onError: (error) => { if (active()) watchError(error); },
        });
      } catch (error) {
        const failed = kit;
        kit = undefined;
        startError(error);
        try {
          await failed?.close();
        } catch {
          // The start error is already reported. A later hello can try a fresh kit.
        }
        return;
      }
    }
    emit({ ev: 'ready', v: PROTOCOL_VERSION, kit: kitVersion });
  }

  async function send({ id, to, text }) {
    if (typeof id !== 'string' || !nonemptyString(text) || scope.mode !== 'paired' || to !== scope.handle) {
      badRequest('Send requires a request ID, text, and the paired recipient.', id);
      return;
    }
    let chat;
    try {
      const chats = await kit.listChats?.({ chatId: to, kind: 'dm' });
      const matches = chats?.filter((row) => row.kind === 'dm' && dmChatHandle(row.chatId) === to);
      chat = matches?.find((row) => row.service === 'iMessage') ?? matches?.[0];
    } catch {
      // A lookup failure leaves the address send available.
    }
    try {
      // Kit 3.0.0 reduces 1:1 chat IDs to buddy sends. Use the pinned kit's reviewed runPipeline internal.
      if (chat && typeof kit.sender?.runPipeline === 'function') {
        await kit.sender.runPipeline({ target: { method: 'chat', identifier: chat.chatId }, text, attachments: [] });
      } else {
        await kit.send({ to, text });
      }
    } catch (error) {
      let reason = 'send_error';
      if (/not running/i.test(error?.message)) reason = 'messages_not_running';
      else if (SIGN_IN.test(error?.message)) reason = 'messages_not_signed_in';
      emit({ ev: 'send_failed', id, reason });
      return;
    }
    emit({ ev: 'sent', id });
  }

  async function handle(line) {
    let request;
    try {
      request = JSON.parse(line);
    } catch {
      badRequest('Expected a JSON object.');
      return;
    }
    if (request === null || typeof request !== 'object' || Array.isArray(request)) {
      badRequest('Expected a JSON object.');
      return;
    }
    if (request.op === 'hello') {
      await hello(request.v);
      return;
    }
    if (request.op === 'stop') {
      stopped = true;
      lines.close();
      return;
    }
    if (!kit) {
      badRequest('A successful hello is required.', request.op === 'send' ? request.id : undefined);
      return;
    }
    switch (request.op) {
      case 'scope': {
        const { mode, handle, since_ms, max_text_len } = request;
        if (validScope(request)) {
          clearRecheck();
          scope = { mode, handle, since_ms, max_text_len };
          if (mode !== 'off') void backfill(scope);
        } else badRequest('Invalid sidecar scope.');
        break;
      }
      case 'send':
        // Checked against the current scope now; osascript may take a while, so later ops do not wait for it.
        {
          const sending = send(request).finally(() => inflight.delete(sending));
          inflight.add(sending);
        }
        break;
      default:
        badRequest('Unknown sidecar operation.');
    }
  }

  const closed = once(lines, 'close');
  let pending = Promise.resolve();
  lines.on('line', (line) => {
    pending = pending.then(() => {
      if (!stopped) return handle(line);
    });
    void pending.catch(() => lines.close());
  });
  try {
    await closed;
    await pending;
  } finally {
    stopped = true;
    clearRecheck();
    lines.close();
    // Let a send already handed to osascript report its outcome before the kit closes.
    await Promise.allSettled([...inflight]);
    await kit?.close();
  }
}

if (process.argv[1] && fileURLToPath(import.meta.url) === realpathSync(process.argv[1])) {
  // The kit may log diagnostics. Reserve stdout for protocol frames, including during import.
  globalThis.console = new Console({ stdout: process.stderr, stderr: process.stderr });
  // Still holds this pipe. When it closes (Still stopped, crashed or was killed) the sidecar exits
  // after a short grace for an in-flight send, even if the kit's watcher or close() never lets go (#1143).
  const exitSoon = () => setTimeout(() => process.exit(0), EOF_GRACE_MS).unref();
  process.stdin.once('end', exitSoon);
  process.stdin.once('close', exitSoon);
  const { IMessageSDK } = await import('@photon-ai/imessage-kit');
  const manifest = new URL('./node_modules/@photon-ai/imessage-kit/package.json', import.meta.url);
  const { version } = JSON.parse(readFileSync(manifest, 'utf8'));
  await runSidecar({
    input: process.stdin, output: process.stdout, createKit: () => new IMessageSDK({}), kitVersion: version,
    databasePath: join(homedir(), 'Library', 'Messages', 'chat.db'),
  });
  await new Promise((resolve, reject) => {
    process.stdout.write('', (error) => { if (error) reject(error); else resolve(); });
  });
  process.exit(0);
}
