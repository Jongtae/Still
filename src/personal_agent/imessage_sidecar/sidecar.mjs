import { Console } from 'node:console';
import { once } from 'node:events';
import { readFileSync, realpathSync, statSync } from 'node:fs';
import { homedir } from 'node:os';
import { join } from 'node:path';
import { createInterface } from 'node:readline';
import { fileURLToPath } from 'node:url';

export const PROTOCOL_VERSION = 1;

const DISK_DENIED = /unable to open|authorization denied|not authorized|operation not permitted|EPERM|EACCES|SQLITE_CANTOPEN|SQLITE_AUTH/i;
const SIGN_IN = /signed in|not signed|sign in/i;

function nonemptyString(value) {
  return typeof value === 'string' && value.length > 0;
}

function validScope({ mode, handle, since_ms, max_text_len }) {
  if (mode === 'off') return true;
  if (!Number.isInteger(since_ms)) return false;
  if (mode === 'paired') return nonemptyString(handle);
  return mode === 'pairing' && Number.isInteger(max_text_len) && max_text_len > 0;
}

export async function runSidecar({ input, output, createKit, kitVersion, databasePath }) {
  const lines = createInterface({ input, crlfDelay: Infinity });
  let scope = { mode: 'off' };
  let kit;
  let stopped = false;
  const inflight = new Set();

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
    if (handle == null && typeof message.chatId === 'string') {
      const [service, separator, ...parts] = message.chatId.split(';');
      if (service && separator === '-') handle = parts.join(';') || handle;
    }
    const text = message.text ?? '';
    if (scope.mode === 'paired') {
      if (handle !== scope.handle) return;
    } else if (fromMe || !nonemptyString(text) || text.length > scope.max_text_len) {
      return;
    }

    const event = {
      ev: fromMe ? 'from_me' : 'message', guid: message.id, handle, text, ts_ms,
    };
    if (!fromMe) {
      const reaction = message.reaction;
      event.reaction = reaction ? {
        kind: reaction.kind, target_guid: reaction.targetMessageId, removed: Boolean(reaction.isRemoved),
      } : null;
    }
    emit(event);
  }

  async function backfill(appliedScope) {
    const { mode, handle, since_ms } = appliedScope;
    const current = () => !stopped && scope === appliedScope;
    try {
      const messages = await kit.getMessages({
        since: new Date(since_ms),
        limit: 500,
        ...(mode === 'paired' ? { participant: handle } : {}),
      });
      if (!current()) return;
      messages.sort((a, b) => a.createdAt - b.createdAt || a.rowId - b.rowId);
      for (const message of messages) forward(message, message.isFromMe === true);
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
    try {
      await kit.send({ to, text });
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
    lines.close();
    // Let a send already handed to osascript report its outcome before the kit closes.
    await Promise.allSettled([...inflight]);
    await kit?.close();
  }
}

if (process.argv[1] && fileURLToPath(import.meta.url) === realpathSync(process.argv[1])) {
  // The kit may log diagnostics. Reserve stdout for protocol frames, including during import.
  globalThis.console = new Console({ stdout: process.stderr, stderr: process.stderr });
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
