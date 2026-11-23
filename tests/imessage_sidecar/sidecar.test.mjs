// Node-side tests for the iMessage sidecar (#1142), protocol v1 from the #1140 Plan.
// Run: node --test tests/imessage_sidecar/  (tests/test_imessage_sidecar_node.py runs this from pytest).
//
// The fake kit mirrors @photon-ai/imessage-kit 3.0.0's dispatcher: peers' rows go to
// onIncomingMessage, then onGroupMessage or onDirectMessage by chatKind; the owner's own
// rows go to onFromMeMessage whatever the chat kind.  It never runs osascript: `send`
// only records the request, so a test sees exactly which sends reached the kit.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { once } from 'node:events';
import { PassThrough } from 'node:stream';
import { readFileSync } from 'node:fs';
import net from 'node:net';
import { fileURLToPath } from 'node:url';

const SIDECAR = new URL('../../src/personal_agent/imessage_sidecar/sidecar.mjs', import.meta.url);
const { runSidecar, PROTOCOL_VERSION } = await import(SIDECAR.href);

const OWNER = '+15551230000';
const OTHER = '+15559870000';

class FakeKit {
  constructor({ startError = null, sendError = null } = {}) {
    this.startError = startError;
    this.sendError = sendError;
    this.events = null;
    this.sends = [];
    this.closed = false;
  }
  async startWatching(events = {}) {
    if (this.startError) throw this.startError;
    this.events = events;
  }
  async send(request) {
    this.sends.push(request);
    if (this.sendError) throw this.sendError;
  }
  async getMessages() { return []; }
  async stopWatching() { this.events = null; }
  async close() { this.closed = true; this.events = null; }

  async deliver(message) {
    const events = this.events || {};
    if (message.isFromMe) {
      await events.onFromMeMessage?.(message);
      return;
    }
    await events.onIncomingMessage?.(message);
    if (message.chatKind === 'group') await events.onGroupMessage?.(message);
    else if (message.chatKind === 'dm') await events.onDirectMessage?.(message);
  }
  async fail(error) { await this.events?.onError?.(error); }
}

let rowId = 0;
function row(overrides = {}) {
  rowId += 1;
  return {
    rowId,
    id: `guid-${rowId}`,
    chatId: `any;-;${overrides.participant ?? OWNER}`,
    chatKind: 'dm',
    participant: OWNER,
    service: 'iMessage',
    text: 'hello',
    kind: 'text',
    isFromMe: false,
    createdAt: new Date(2_000_000),
    reaction: null,
    attachments: [],
    hasAttachments: false,
    ...overrides,
  };
}

function kitError(code, message) {
  const error = new Error(message);
  error.name = 'IMessageError';
  error.code = code;
  return error;
}

async function settle() {
  for (let i = 0; i < 5; i += 1) await new Promise((resolve) => setImmediate(resolve));
}

/** A running sidecar on in-memory pipes: `send(op)` writes a line, `events` holds parsed output. */
async function start(kit = new FakeKit(), { hello = true, ...options } = {}) {
  const input = new PassThrough();
  const output = new PassThrough();
  const events = [];
  let buffer = '';
  output.on('data', (chunk) => {
    buffer += chunk.toString('utf8');
    let index;
    while ((index = buffer.indexOf('\n')) >= 0) {
      const line = buffer.slice(0, index);
      buffer = buffer.slice(index + 1);
      if (line.trim()) events.push(JSON.parse(line));
    }
  });
  let created = 0;
  const done = runSidecar({
    input, output, createKit: () => { created += 1; return kit; }, kitVersion: '3.0.0', ...options,
  });
  const sidecar = {
    kit, events, done, input,
    get created() { return created; },
    async send(op) { input.write(`${typeof op === 'string' ? op : JSON.stringify(op)}\n`); await settle(); },
    async stop() { await sidecar.send({ op: 'stop' }); await done; },
    of(ev) { return events.filter((event) => event.ev === ev); },
  };
  if (hello) await sidecar.send({ op: 'hello', v: 1 });
  return sidecar;
}

test('hello answers ready with the protocol and kit versions', async () => {
  const sidecar = await start();
  assert.equal(PROTOCOL_VERSION, 1);
  assert.deepEqual(sidecar.events, [{ ev: 'ready', v: 1, kit: '3.0.0' }]);
  await sidecar.stop();
});

test('an unsupported protocol version is a bad_request and no ready', async () => {
  const sidecar = await start(new FakeKit(), { hello: false });
  await sidecar.send({ op: 'hello', v: 2 });
  assert.equal(sidecar.of('ready').length, 0);
  assert.equal(sidecar.of('error')[0].code, 'bad_request');
  await sidecar.stop();
});

test('scope off is the default: nothing crosses before any scope', async () => {
  const sidecar = await start();
  await sidecar.kit.deliver(row());
  await sidecar.kit.deliver(row({ isFromMe: true }));
  await settle();
  assert.deepEqual(sidecar.events.map((event) => event.ev), ['ready']);
  await sidecar.stop();
});

test('scope off after paired stops every message event', async () => {
  const sidecar = await start();
  await sidecar.send({ op: 'scope', mode: 'paired', handle: OWNER, since_ms: 0 });
  await sidecar.send({ op: 'scope', mode: 'off' });
  await sidecar.kit.deliver(row());
  await sidecar.kit.deliver(row({ isFromMe: true }));
  await settle();
  assert.equal(sidecar.of('message').length + sidecar.of('from_me').length, 0);
  await sidecar.stop();
});

test('pairing emits only short 1:1 rows newer than since_ms, as message events', async () => {
  const sidecar = await start();
  await sidecar.send({ op: 'scope', mode: 'pairing', since_ms: 1_000_000, max_text_len: 6 });
  const fresh = row({ participant: OTHER, text: '123456', createdAt: new Date(1_500_000) });
  await sidecar.kit.deliver(fresh);
  await sidecar.kit.deliver(row({ participant: OTHER, text: '1234567', createdAt: new Date(1_500_000) }));
  await sidecar.kit.deliver(row({ participant: OTHER, text: '123', createdAt: new Date(1_000_000) }));
  await sidecar.kit.deliver(row({ participant: OTHER, text: '123', createdAt: new Date(999_000) }));
  await sidecar.kit.deliver(row({ participant: OTHER, text: '123', chatKind: 'group', chatId: 'chat123' }));
  await sidecar.kit.deliver(row({ participant: OTHER, text: '123', isFromMe: true, createdAt: new Date(1_500_000) }));
  await settle();
  assert.deepEqual(sidecar.of('message'), [{
    ev: 'message', guid: fresh.id, handle: OTHER, text: '123456', ts_ms: 1_500_000, reaction: null,
  }]);
  assert.equal(sidecar.of('from_me').length, 0);
  await sidecar.stop();
});

test('pairing never emits a row without text', async () => {
  const sidecar = await start();
  await sidecar.send({ op: 'scope', mode: 'pairing', since_ms: 0, max_text_len: 32 });
  await sidecar.kit.deliver(row({ text: null, hasAttachments: true }));
  await sidecar.kit.deliver(row({ text: '' }));
  await settle();
  assert.equal(sidecar.of('message').length, 0);
  await sidecar.stop();
});

test('paired emits only the paired handle, newer than since_ms, in both directions', async () => {
  const sidecar = await start();
  await sidecar.send({ op: 'scope', mode: 'paired', handle: OWNER, since_ms: 1_000_000 });
  const inbound = row({ text: 'a long message from the owner that pairing would never let through' });
  const outbound = row({ isFromMe: true, text: 'reply' });
  await sidecar.kit.deliver(inbound);
  await sidecar.kit.deliver(outbound);
  await sidecar.kit.deliver(row({ participant: OTHER }));
  await sidecar.kit.deliver(row({ participant: OTHER, isFromMe: true }));
  await sidecar.kit.deliver(row({ createdAt: new Date(999_999) }));
  await sidecar.kit.deliver(row({ isFromMe: true, createdAt: new Date(1_000_000) }));
  await settle();
  assert.deepEqual(sidecar.of('message'), [{
    ev: 'message', guid: inbound.id, handle: OWNER, text: inbound.text, ts_ms: 2_000_000, reaction: null,
  }]);
  assert.deepEqual(sidecar.of('from_me'), [{
    ev: 'from_me', guid: outbound.id, handle: OWNER, text: 'reply', ts_ms: 2_000_000,
    sent: false, delivered: false, error: null,
  }]);
  await sidecar.stop();
});

test('a group row never appears, even with the paired handle as participant', async () => {
  const sidecar = await start();
  await sidecar.send({ op: 'scope', mode: 'paired', handle: OWNER, since_ms: 0 });
  // The real dispatcher routes groups to onGroupMessage; deliver it to the dm callback too
  // so the sidecar's own chat-kind check is what keeps it out.
  const group = row({ chatKind: 'group', chatId: 'chat987654' });
  await sidecar.kit.deliver(group);
  await sidecar.kit.events.onDirectMessage?.(group);
  await sidecar.kit.deliver(row({ chatKind: 'group', chatId: 'chat987654', isFromMe: true }));
  await sidecar.kit.deliver(row({ chatKind: 'unknown' }));
  await settle();
  assert.equal(sidecar.of('message').length + sidecar.of('from_me').length, 0);
  await sidecar.stop();
});

test('an inbound row on the dm callback marked from-me is not a message event', async () => {
  const sidecar = await start();
  await sidecar.send({ op: 'scope', mode: 'paired', handle: OWNER, since_ms: 0 });
  await sidecar.kit.events.onDirectMessage?.(row({ isFromMe: true }));
  await settle();
  assert.equal(sidecar.of('message').length, 0);
  await sidecar.stop();
});

test('a tapback from the paired handle carries its kind and target', async () => {
  const sidecar = await start();
  await sidecar.send({ op: 'scope', mode: 'paired', handle: OWNER, since_ms: 0 });
  const tapback = row({
    text: null,
    reaction: { kind: 'like', targetMessageId: 'guid-target', emoji: null, textRange: { location: 0, length: 1 }, isRemoved: false },
  });
  await sidecar.kit.deliver(tapback);
  await settle();
  const [event] = sidecar.of('message');
  assert.equal(event.guid, tapback.id);
  assert.deepEqual(event.reaction, { kind: 'like', target_guid: 'guid-target', removed: false });
  await sidecar.stop();
});

test('send to the paired handle reaches the kit and answers sent', async () => {
  const sidecar = await start();
  await sidecar.send({ op: 'scope', mode: 'paired', handle: OWNER, since_ms: 0 });
  await sidecar.send({ op: 'send', id: 'r1', to: OWNER, text: 'hi' });
  assert.deepEqual(sidecar.kit.sends, [{ to: OWNER, text: 'hi' }]);
  assert.deepEqual(sidecar.of('sent'), [{ ev: 'sent', id: 'r1' }]);
  await sidecar.stop();
});

test('send to a handle that is not paired is a bad_request and never reaches osascript', async () => {
  const sidecar = await start();
  await sidecar.send({ op: 'send', id: 'r0', to: OWNER, text: 'before pairing' });
  await sidecar.send({ op: 'scope', mode: 'pairing', since_ms: 0, max_text_len: 32 });
  await sidecar.send({ op: 'send', id: 'r1', to: OWNER, text: 'while pairing' });
  await sidecar.send({ op: 'scope', mode: 'paired', handle: OWNER, since_ms: 0 });
  await sidecar.send({ op: 'send', id: 'r2', to: OTHER, text: 'elsewhere' });
  await sidecar.send({ op: 'send', id: 'r3', to: ` ${OWNER}`, text: 'padded' });
  await sidecar.send({ op: 'scope', mode: 'off' });
  await sidecar.send({ op: 'send', id: 'r4', to: OWNER, text: 'after off' });
  assert.deepEqual(sidecar.kit.sends, []);
  assert.equal(sidecar.of('sent').length, 0);
  assert.deepEqual(sidecar.of('send_failed').map((event) => [event.id, event.reason]),
    [['r0', 'bad_request'], ['r1', 'bad_request'], ['r2', 'bad_request'], ['r3', 'bad_request'], ['r4', 'bad_request']]);
  assert.ok(sidecar.of('error').every((event) => event.code === 'bad_request'));
  await sidecar.stop();
});

test('a failed send answers send_failed and names a Messages sign-in problem', async () => {
  const signedOut = kitError('SEND', 'Send failed - please check: 1) Is Messages signed in to iMessage, 2) Is recipient correct, 3) Network connection');
  const sidecar = await start(new FakeKit({ sendError: signedOut }));
  await sidecar.send({ op: 'scope', mode: 'paired', handle: OWNER, since_ms: 0 });
  await sidecar.send({ op: 'send', id: 'r1', to: OWNER, text: 'hi' });
  assert.equal(sidecar.of('sent').length, 0);
  assert.deepEqual(sidecar.of('send_failed'), [{ ev: 'send_failed', id: 'r1', reason: 'messages_not_signed_in' }]);
  await sidecar.stop();
});

test('a send without an id or text is a bad_request', async () => {
  const sidecar = await start();
  await sidecar.send({ op: 'scope', mode: 'paired', handle: OWNER, since_ms: 0 });
  await sidecar.send({ op: 'send', to: OWNER, text: 'no id' });
  await sidecar.send({ op: 'send', id: 'r2', to: OWNER, text: '' });
  assert.deepEqual(sidecar.kit.sends, []);
  assert.ok(sidecar.of('error').length >= 2);
  assert.ok(sidecar.of('error').every((event) => event.code === 'bad_request'));
  await sidecar.stop();
});

test('malformed lines, unknown ops and bad scopes are bad_request and the sidecar keeps running', async () => {
  const sidecar = await start();
  await sidecar.send('{not json');
  await sidecar.send({ op: 'teleport' });
  await sidecar.send({ op: 'scope', mode: 'everything' });
  await sidecar.send({ op: 'scope', mode: 'paired', since_ms: 0 });
  await sidecar.send({ op: 'scope', mode: 'pairing', since_ms: 'yesterday', max_text_len: 32 });
  assert.equal(sidecar.of('error').length, 5);
  assert.ok(sidecar.of('error').every((event) => event.code === 'bad_request'));
  // A rejected scope leaves the earlier one (off) in force.
  await sidecar.kit.deliver(row());
  await settle();
  assert.equal(sidecar.of('message').length, 0);
  await sidecar.send({ op: 'hello', v: 1 });
  assert.equal(sidecar.of('ready').length, 2);
  await sidecar.stop();
});

test('ops before hello are refused and open nothing', async () => {
  const sidecar = await start(new FakeKit(), { hello: false });
  await sidecar.send({ op: 'scope', mode: 'paired', handle: OWNER, since_ms: 0 });
  await sidecar.send({ op: 'send', id: 'r1', to: OWNER, text: 'hi' });
  assert.equal(sidecar.created, 0);
  assert.deepEqual(sidecar.kit.sends, []);
  assert.ok(sidecar.of('error').every((event) => event.code === 'bad_request'));
  await sidecar.stop();
});

test('a chat.db that Full Disk Access keeps closed is no_disk_access and no ready', async () => {
  const denied = kitError('DATABASE', 'Failed to open database: unable to open database file');
  const sidecar = await start(new FakeKit({ startError: denied }));
  assert.equal(sidecar.of('ready').length, 0);
  assert.equal(sidecar.of('error')[0].code, 'no_disk_access');
  await sidecar.stop();
});

test('an authorization-denied open is no_disk_access too', async () => {
  const denied = kitError('DATABASE', 'Failed to open database: authorization denied');
  const sidecar = await start(new FakeKit({ startError: denied }));
  assert.equal(sidecar.of('error')[0].code, 'no_disk_access');
  await sidecar.stop();
});

test('other database failures are db_unreadable, at start and while watching', async () => {
  const sidecar = await start(new FakeKit({ startError: kitError('DATABASE', 'Failed to read max ROWID: database disk image is malformed') }));
  assert.equal(sidecar.of('error')[0].code, 'db_unreadable');
  await sidecar.stop();

  const watching = await start();
  await watching.kit.fail(kitError('DATABASE', 'Failed to query messages: disk I/O error'));
  await settle();
  assert.equal(watching.of('error')[0].code, 'db_unreadable');
  await watching.stop();
});

test('error details carry no message text', async () => {
  const sidecar = await start();
  await sidecar.send({ op: 'scope', mode: 'paired', handle: OWNER, since_ms: 0 });
  await sidecar.send({ op: 'send', id: 'r1', to: OTHER, text: 'secret words for someone else' });
  for (const event of sidecar.of('error')) {
    assert.ok(!JSON.stringify(event).includes('secret words'), JSON.stringify(event));
  }
  await sidecar.stop();
});

test('stop closes the kit and ends the sidecar; end of input does too', async () => {
  const sidecar = await start();
  await sidecar.stop();
  assert.equal(sidecar.kit.closed, true);

  const other = await start();
  other.input.end();
  await other.done;
  assert.equal(other.kit.closed, true);
});

test('the sidecar opens no listener', async () => {
  const listened = [];
  const original = net.Server.prototype.listen;
  net.Server.prototype.listen = function listen(...args) { listened.push(args); throw new Error('no listener allowed'); };
  try {
    const sidecar = await start();
    await sidecar.send({ op: 'scope', mode: 'paired', handle: OWNER, since_ms: 0 });
    await sidecar.send({ op: 'send', id: 'r1', to: OWNER, text: 'hi' });
    await sidecar.kit.deliver(row());
    await sidecar.stop();
  } finally {
    net.Server.prototype.listen = original;
  }
  assert.deepEqual(listened, []);
  const source = readFileSync(fileURLToPath(SIDECAR), 'utf8');
  for (const module of ['net', 'http', 'https', 'http2', 'dgram', 'tls', 'ws']) {
    assert.ok(!new RegExp(`from\\s+['"](node:)?${module}['"]|import\\(\\s*['"](node:)?${module}['"]`).test(source),
      `sidecar.mjs imports ${module}`);
  }
});

test('stop before hello ends quietly', async () => {
  const sidecar = await start(new FakeKit(), { hello: false });
  await sidecar.stop();
  assert.deepEqual(sidecar.events, []);
  assert.equal(sidecar.created, 0);
});

test('a send still with osascript does not hold back a later scope change, and reports before stop ends', async () => {
  let release;
  const kit = new FakeKit();
  kit.send = async (request) => { kit.sends.push(request); await new Promise((resolve) => { release = resolve; }); };
  const sidecar = await start(kit);
  await sidecar.send({ op: 'scope', mode: 'paired', handle: OWNER, since_ms: 0 });
  await sidecar.send({ op: 'send', id: 'r1', to: OWNER, text: 'slow' });
  await sidecar.send({ op: 'scope', mode: 'off' });
  await sidecar.send({ op: 'send', id: 'r2', to: OWNER, text: 'after off' });
  assert.deepEqual(kit.sends, [{ to: OWNER, text: 'slow' }]);
  assert.deepEqual(sidecar.of('send_failed').map((event) => event.id), ['r2']);
  await kit.deliver(row());
  await settle();
  assert.equal(sidecar.of('message').length, 0);
  await sidecar.send({ op: 'stop' });
  assert.equal(kit.closed, false);
  release();
  await sidecar.done;
  assert.deepEqual(sidecar.of('sent'), [{ ev: 'sent', id: 'r1' }]);
  assert.equal(kit.closed, true);
});

test('a scope backfills rows newer than since_ms through the same filter', async () => {
  const kit = new FakeKit();
  const queries = [];
  const history = [
    row({ text: 'missed while down', createdAt: new Date(3_000) }),
    row({ participant: OTHER, text: 'someone else', createdAt: new Date(3_100) }),
    row({ chatKind: 'group', chatId: 'chat1', text: 'group', createdAt: new Date(3_200) }),
    row({ isFromMe: true, text: 'my reply', createdAt: new Date(3_300) }),
    row({ text: 'too old', createdAt: new Date(1_000) }),
    row({ isFromMe: true, participant: null, chatId: `any;-;${OWNER}`, text: 'reply without participant', createdAt: new Date(3_400) }),
  ];
  kit.getMessages = async (query) => {
    queries.push(query);
    const page = [...history].reverse().slice(query.offset ?? 0, (query.offset ?? 0) + query.limit);
    return page;
  };
  const sidecar = await start(kit);
  await sidecar.send({ op: 'scope', mode: 'paired', handle: OWNER, since_ms: 2_000 });
  await settle();
  assert.ok(queries.length >= 1);
  assert.equal(queries[0].since.getTime(), 2_000);
  // The kit's participant filter is handle.id = ?, which drops outgoing rows without a handle.
  assert.equal(queries[0].participant, undefined);
  assert.ok(Number.isInteger(queries[0].limit) && queries[0].limit > 0);
  assert.deepEqual(sidecar.of('message').map((event) => event.text), ['missed while down']);
  assert.deepEqual(sidecar.of('from_me').map((event) => event.text), ['my reply', 'reply without participant']);
  const asked = queries.length;
  await sidecar.send({ op: 'scope', mode: 'off' });
  assert.equal(queries.length, asked);
  await sidecar.stop();
});

test('a pairing scope backfills only short 1:1 inbound rows', async () => {
  const kit = new FakeKit();
  kit.getMessages = async () => [
    row({ participant: OTHER, text: '424242', createdAt: new Date(3_000) }),
    row({ participant: OTHER, text: 'a much longer message', createdAt: new Date(3_000) }),
    row({ participant: OTHER, text: '1', isFromMe: true, createdAt: new Date(3_000) }),
    row({ participant: OTHER, text: '2', chatKind: 'group', chatId: 'chat1', createdAt: new Date(3_000) }),
  ];
  const sidecar = await start(kit);
  await sidecar.send({ op: 'scope', mode: 'pairing', since_ms: 2_000, max_text_len: 6 });
  await settle();
  assert.deepEqual(sidecar.of('message').map((event) => event.text), ['424242']);
  assert.equal(sidecar.of('from_me').length, 0);
  await sidecar.stop();
});

test('a failing backfill is db_unreadable and the scope still applies to new rows', async () => {
  const kit = new FakeKit();
  kit.getMessages = async () => { throw kitError('DATABASE', 'Failed to query messages: disk I/O error'); };
  const sidecar = await start(kit);
  await sidecar.send({ op: 'scope', mode: 'paired', handle: OWNER, since_ms: 0 });
  await settle();
  assert.equal(sidecar.of('error')[0].code, 'db_unreadable');
  await kit.deliver(row());
  await settle();
  assert.equal(sidecar.of('message').length, 1);
  await sidecar.stop();
});

test('an outgoing 1:1 row without a participant takes its handle from the dm chat id', async () => {
  const sidecar = await start();
  await sidecar.send({ op: 'scope', mode: 'paired', handle: OWNER, since_ms: 0 });
  await sidecar.kit.deliver(row({ isFromMe: true, participant: null, chatId: `any;-;${OWNER}`, text: 'reply' }));
  await sidecar.kit.deliver(row({ isFromMe: true, participant: null, chatId: `iMessage;-;${OTHER}`, text: 'other' }));
  await sidecar.kit.deliver(row({ isFromMe: true, participant: null, chatId: `iMessage;+;chat123`, chatKind: 'group', text: 'g' }));
  await settle();
  assert.deepEqual(sidecar.of('from_me').map((event) => [event.handle, event.text]), [[OWNER, 'reply']]);
  await sidecar.stop();
});

test('a chat.db that does not exist means Messages was never signed in, not missing disk access', async () => {
  const { mkdtempSync, writeFileSync } = await import('node:fs');
  const { tmpdir } = await import('node:os');
  const { join } = await import('node:path');
  const dir = mkdtempSync(join(tmpdir(), 'imessage-sidecar-'));
  const denied = kitError('DATABASE', 'Failed to open database: unable to open database file');
  const run = async (databasePath) => {
    const input = new PassThrough();
    const output = new PassThrough();
    const chunks = [];
    output.on('data', (chunk) => chunks.push(chunk.toString()));
    const done = runSidecar({ input, output, createKit: () => new FakeKit({ startError: denied }), kitVersion: '3.0.0', databasePath });
    input.write('{"op":"hello","v":1}\n{"op":"stop"}\n');
    await done;
    return chunks.join('').trim().split('\n').map((line) => JSON.parse(line));
  };
  assert.equal((await run(join(dir, 'missing', 'chat.db')))[0].code, 'messages_not_signed_in');
  writeFileSync(join(dir, 'chat.db'), '');
  assert.equal((await run(join(dir, 'chat.db')))[0].code, 'no_disk_access');
});

test('a send while the Messages app is not running names that', async () => {
  const sidecar = await start(new FakeKit({ sendError: kitError('SEND', 'Messages app is not running') }));
  await sidecar.send({ op: 'scope', mode: 'paired', handle: OWNER, since_ms: 0 });
  await sidecar.send({ op: 'send', id: 'r1', to: OWNER, text: 'hi' });
  assert.deepEqual(sidecar.of('send_failed'), [{ ev: 'send_failed', id: 'r1', reason: 'messages_not_running' }]);
  await sidecar.stop();
});

test('the real entry runs through a symlinked directory and keeps stdout for protocol frames', async () => {
  const { mkdtempSync, mkdirSync, writeFileSync, copyFileSync, symlinkSync, realpathSync } = await import('node:fs');
  const { tmpdir } = await import('node:os');
  const { join } = await import('node:path');
  const { spawnSync } = await import('node:child_process');
  const base = realpathSync(mkdtempSync(join(tmpdir(), 'imessage-entry-')));
  const real = join(base, 'real');
  const kitDir = join(real, 'node_modules', '@photon-ai', 'imessage-kit');
  mkdirSync(kitDir, { recursive: true });
  copyFileSync(fileURLToPath(SIDECAR), join(real, 'sidecar.mjs'));
  writeFileSync(join(kitDir, 'package.json'), JSON.stringify({
    name: '@photon-ai/imessage-kit', version: '3.0.0', type: 'module', exports: { '.': { import: './index.js' } },
  }));
  writeFileSync(join(kitDir, 'index.js'), [
    "console.log('kit diagnostics at import');",
    'export class IMessageSDK {',
    "  constructor() { console.log('kit diagnostics in constructor'); }",
    '  async startWatching() {}',
    '  async getMessages() { return []; }',
    '  async send() {}',
    '  async close() {}',
    '}',
  ].join('\n'));
  symlinkSync(real, join(base, 'link'));
  const done = spawnSync(process.execPath, [join(base, 'link', 'sidecar.mjs')], {
    input: '{"op":"hello","v":1}\n{"op":"stop"}\n', encoding: 'utf8', timeout: 20_000,
    env: { PATH: '/usr/bin:/bin', HOME: base },
  });
  assert.equal(done.status, 0, done.stderr);
  assert.deepEqual(done.stdout.trim().split('\n').map((line) => JSON.parse(line)), [{ ev: 'ready', v: 1, kit: '3.0.0' }]);
  assert.match(done.stderr, /kit diagnostics/);
});

test('the real entry exits when Still closes its stdin, even if the kit never lets go (#1143)', async () => {
  const { mkdtempSync, mkdirSync, writeFileSync, copyFileSync, realpathSync } = await import('node:fs');
  const { tmpdir } = await import('node:os');
  const { join } = await import('node:path');
  const { spawn } = await import('node:child_process');
  const base = realpathSync(mkdtempSync(join(tmpdir(), 'imessage-eof-')));
  const kitDir = join(base, 'node_modules', '@photon-ai', 'imessage-kit');
  mkdirSync(kitDir, { recursive: true });
  copyFileSync(fileURLToPath(SIDECAR), join(base, 'sidecar.mjs'));
  writeFileSync(join(kitDir, 'package.json'), JSON.stringify({
    name: '@photon-ai/imessage-kit', version: '3.0.0', type: 'module', exports: { '.': { import: './index.js' } },
  }));
  // A watcher that keeps the event loop alive and a close() that never settles: only a hard exit ends this.
  writeFileSync(join(kitDir, 'index.js'), [
    'export class IMessageSDK {',
    '  async startWatching() { this.timer = setInterval(() => {}, 1000); }',
    '  async getMessages() { return []; }',
    '  async send() {}',
    '  close() { return new Promise(() => {}); }',
    '}',
  ].join('\n'));
  const child = spawn(process.execPath, [join(base, 'sidecar.mjs')], {
    stdio: ['pipe', 'pipe', 'pipe'], env: { PATH: '/usr/bin:/bin', HOME: base },
  });
  let out = '';
  child.stdout.on('data', (chunk) => { out += chunk; });
  child.stdin.write('{"op":"hello","v":1}\n');
  for (let i = 0; i < 100 && !out.includes('"ready"'); i += 1) await new Promise((resolve) => setTimeout(resolve, 50));
  assert.match(out, /"ready"/);
  const started = Date.now();
  const exited = once(child, 'exit');
  child.stdin.end();   // what Still's exit, crash or SIGKILL does to this pipe
  const timer = setTimeout(() => child.kill('SIGKILL'), 15_000);
  const [code, signal] = await exited;
  clearTimeout(timer);
  assert.equal(signal, null, 'exited on its own, not killed by the test');
  assert.equal(code, 0);
  assert.ok(Date.now() - started < 10_000);
});

test('a backfill pages through every row since since_ms, oldest first', async () => {
  const kit = new FakeKit();
  const history = Array.from({ length: 1_203 }, (_, index) => row({ text: `m${index}`, createdAt: new Date(10_000 + index) }));
  const newestFirst = [...history].reverse();
  const queries = [];
  kit.getMessages = async (query) => {
    queries.push(query);
    const offset = query.offset ?? 0;
    return newestFirst.slice(offset, offset + query.limit);
  };
  const sidecar = await start(kit);
  await sidecar.send({ op: 'scope', mode: 'paired', handle: OWNER, since_ms: 0 });
  for (let i = 0; i < 20 && sidecar.of('message').length < history.length; i += 1) await settle();
  assert.ok(queries.length > 1);
  const texts = sidecar.of('message').map((event) => event.text);
  assert.equal(new Set(texts).size, history.length);
  assert.deepEqual(texts, history.map((message) => message.text));
  await sidecar.stop();
});

test('a scope change stops a backfill that is still paging', async () => {
  const kit = new FakeKit();
  let release;
  const queries = [];
  kit.getMessages = async (query) => {
    queries.push(query);
    if ((query.offset ?? 0) > 0) await new Promise((resolve) => { release = resolve; });
    return Array.from({ length: query.limit }, (_, index) => row({ text: `p${query.offset ?? 0}-${index}` }));
  };
  const sidecar = await start(kit);
  await sidecar.send({ op: 'scope', mode: 'paired', handle: OWNER, since_ms: 0 });
  await sidecar.send({ op: 'scope', mode: 'off' });
  release?.();
  await settle();
  assert.equal(sidecar.of('message').length, 0);
  assert.ok(queries.length <= 2);
  await sidecar.stop();
});

// ---- #1179: delivery truth (protocol v1.1, additive) ------------------------------------
// Messages.app writes a from-me row for every send attempt, failed ones included, and sets
// is_sent / is_delivered / error on that row later. imessage-kit 3.0.0's watcher reads only rows
// above the last ROWID it saw, so a flag change on an existing row is never reported by it: the
// sidecar re-reads recent from-me rows of the paired chat itself (`recheckMs`).

const RECHECK = { recheckMs: 5 };

/** A kit whose getMessages answers from `rows` (guid -> message), newest first, like chat.db. */
function historyKit() {
  const kit = new FakeKit();
  kit.rows = new Map();
  kit.queries = [];
  kit.getMessages = async (query = {}) => {
    kit.queries.push(query);
    const since = query.since ? query.since.getTime() : -Infinity;
    const offset = query.offset ?? 0;
    const matching = [...kit.rows.values()]
      .filter((message) => message.createdAt.getTime() >= since)
      .sort((a, b) => b.createdAt - a.createdAt || b.rowId - a.rowId)
      .map((message) => ({ ...message }));
    return query.limit ? matching.slice(offset, offset + query.limit) : matching.slice(offset);
  };
  /** Insert a row: it lands in chat.db and the watcher reports it once. */
  kit.insert = async (message) => {
    kit.rows.set(message.id, { ...message });
    await kit.deliver({ ...message });
  };
  /** Change a row's flags in chat.db; the watcher reports nothing. */
  kit.update = (guid, changes) => { kit.rows.set(guid, { ...kit.rows.get(guid), ...changes }); };
  return kit;
}

function sentOnly(overrides = {}) {
  return row({ isFromMe: true, text: 'greeting', isSent: true, isDelivered: false, hasError: false, errorCode: 0,
    createdAt: new Date(Date.now()), ...overrides });
}

async function waitFor(check, ms = 1_000) {
  const deadline = Date.now() + ms;
  while (Date.now() < deadline) {
    if (check()) return true;
    await new Promise((resolve) => setTimeout(resolve, 5));
  }
  return check();
}

async function idle(ms = 60) { await new Promise((resolve) => setTimeout(resolve, ms)); await settle(); }

function flags(event) { return [event.sent, event.delivered, event.error]; }

test('from_me carries the row\'s sent, delivered and error flags', async () => {
  const sidecar = await start();
  await sidecar.send({ op: 'scope', mode: 'paired', handle: OWNER, since_ms: 0 });
  await sidecar.kit.deliver(row({ isFromMe: true, text: 'a', isSent: true, isDelivered: true, hasError: false, errorCode: 0 }));
  await sidecar.kit.deliver(row({ isFromMe: true, text: 'b', isSent: false, isDelivered: false, hasError: true, errorCode: 22 }));
  await sidecar.kit.deliver(row({ isFromMe: true, text: 'c', isSent: true, isDelivered: false, hasError: false, errorCode: 0 }));
  await settle();
  assert.deepEqual(sidecar.of('from_me').map((event) => [event.text, ...flags(event)]),
    [['a', true, true, null], ['b', false, false, 22], ['c', true, false, null]]);
  assert.equal(PROTOCOL_VERSION, 1, 'v1.1 is additive: hello stays v1');
  await sidecar.stop();
});

test('a from-me row that is sent and later delivered is emitted again with delivered', async () => {
  const kit = historyKit();
  const sidecar = await start(kit, RECHECK);
  await sidecar.send({ op: 'scope', mode: 'paired', handle: OWNER, since_ms: 0 });
  const greeting = sentOnly();
  await kit.insert(greeting);
  await settle();
  assert.deepEqual(sidecar.of('from_me').map(flags), [[true, false, null]]);
  await idle();
  assert.equal(sidecar.of('from_me').length, 1, 'unchanged flags are not emitted again');
  kit.update(greeting.id, { isDelivered: true, deliveredAt: new Date() });
  assert.ok(await waitFor(() => sidecar.of('from_me').length === 2), 'the delivered flag was re-observed');
  const [first, again] = sidecar.of('from_me');
  assert.deepEqual(again, { ...first, delivered: true });
  await idle();
  assert.equal(sidecar.of('from_me').length, 2, 'a settled row is emitted once more, not on every re-read');
  await sidecar.stop();
});

test('a from-me row that is sent and later fails is emitted again with its error', async () => {
  const kit = historyKit();
  const sidecar = await start(kit, RECHECK);
  await sidecar.send({ op: 'scope', mode: 'paired', handle: OWNER, since_ms: 0 });
  const greeting = sentOnly();
  await kit.insert(greeting);
  kit.update(greeting.id, { hasError: true, errorCode: 22 });
  assert.ok(await waitFor(() => sidecar.of('from_me').length === 2));
  assert.deepEqual(sidecar.of('from_me').map(flags), [[true, false, null], [true, false, 22]]);
  await sidecar.stop();
});

test('re-reads never emit a row outside the paired scope', async () => {
  const kit = historyKit();
  const sidecar = await start(kit, RECHECK);
  await sidecar.send({ op: 'scope', mode: 'paired', handle: OWNER, since_ms: 0 });
  const mine = sentOnly();
  const other = sentOnly({ participant: OTHER, chatId: `any;-;${OTHER}`, text: 'to someone else' });
  const group = sentOnly({ chatKind: 'group', chatId: 'chat987654', text: 'to a group' });
  const inbound = row({ text: 'from the owner', isSent: false, isDelivered: false, createdAt: new Date(Date.now()) });
  for (const message of [mine, other, group, inbound]) await kit.insert(message);
  // A paired from-me row the sidecar never emitted: written before the scope's since_ms.
  const old = sentOnly({ text: 'before pairing', createdAt: new Date(0) });
  kit.rows.set(old.id, { ...old });
  await settle();
  for (const message of [other, group, inbound, old]) kit.update(message.id, { isDelivered: true, hasError: true, errorCode: 1 });
  await idle();
  const guids = sidecar.of('from_me').map((event) => event.guid);
  assert.deepEqual(guids, [mine.id], 'only the paired row that was already emitted');
  assert.equal(sidecar.of('message').length, 1, 'the inbound row is read once, never re-emitted');

  // After the scope moves away, an earlier paired row's change crosses no more.
  await sidecar.send({ op: 'scope', mode: 'paired', handle: OTHER, since_ms: Date.now() + 60_000 });
  kit.update(mine.id, { isDelivered: true });
  await idle();
  await sidecar.send({ op: 'scope', mode: 'off' });
  kit.update(mine.id, { hasError: true, errorCode: 5 });
  await idle();
  assert.deepEqual(sidecar.of('from_me').map((event) => event.guid), [mine.id]);
  await sidecar.stop();
});

test('nothing is re-read while no emitted from-me row is waiting for its outcome', async () => {
  const kit = historyKit();
  const sidecar = await start(kit, RECHECK);
  await sidecar.send({ op: 'scope', mode: 'paired', handle: OWNER, since_ms: 0 });
  await settle();
  const afterBackfill = kit.queries.length;
  await kit.insert(row({ text: 'an inbound row' }));
  await kit.insert(sentOnly({ isDelivered: true }));
  await kit.insert(sentOnly({ hasError: true, errorCode: 22 }));
  await idle();
  assert.equal(kit.queries.length, afterBackfill, 'settled rows and inbound rows start no re-reads');
  await sidecar.stop();
});

// ---- #1179 second live finding: send into the paired conversation's chat ---------------
// imessage-kit 3.0.0 sends to a 1:1 recipient with `buddy "<handle>"` (even when given a 1:1 chat
// id, its resolveTarget reduces it to the address). On the owner's Mac that send was "not
// delivered", while `chat id "<chat guid>"` into the existing conversation arrived. The sidecar
// therefore hands the kit's own send pipeline a `chat` target (the kit escapes it and builds
// `set targetChat to chat id "..."`); the address is only the fallback when no chat exists.

/** A kit with chat rows (`listChats`) and the 3.0.0 sender pipeline, recording each send job. */
function chatKit(chats = []) {
  const kit = new FakeKit();
  kit.chats = chats;
  kit.chatQueries = [];
  kit.jobs = [];
  kit.listChats = async (query = {}) => {
    kit.chatQueries.push(query);
    return kit.chats.filter((chat) => (!query.kind || chat.kind === query.kind)
      && (!query.chatId || chat.chatId === query.chatId || chat.chatId.endsWith(`;${query.chatId}`)));
  };
  kit.sender = {
    async runPipeline(job) {
      kit.jobs.push(job);
      if (kit.sendError) throw kit.sendError;
    },
  };
  return kit;
}

function chat(chatId, overrides = {}) {
  return { chatId, kind: 'dm', service: 'iMessage', name: null, lastMessageAt: new Date(), ...overrides };
}

test('a send goes to the paired conversation\'s chat id, not the bare address', async () => {
  const kit = chatKit([chat(`any;-;${OWNER}`)]);
  const sidecar = await start(kit);
  await sidecar.send({ op: 'scope', mode: 'paired', handle: OWNER, since_ms: 0 });
  await sidecar.send({ op: 'send', id: 'r1', to: OWNER, text: 'hi' });
  assert.deepEqual(kit.jobs.map((job) => [job.target, job.text, job.attachments]),
    [[{ method: 'chat', identifier: `any;-;${OWNER}` }, 'hi', []]]);
  assert.deepEqual(kit.sends, [], 'no buddy send by address');
  assert.deepEqual(sidecar.of('sent'), [{ ev: 'sent', id: 'r1' }]);
  await sidecar.stop();
});

test('the chat id is the one Messages has, whatever its service prefix', async () => {
  const kit = chatKit([chat(`iMessage;-;${OWNER}`)]);
  const sidecar = await start(kit);
  await sidecar.send({ op: 'scope', mode: 'paired', handle: OWNER, since_ms: 0 });
  await sidecar.send({ op: 'send', id: 'r1', to: OWNER, text: 'hi' });
  assert.deepEqual(kit.jobs.map((job) => job.target), [{ method: 'chat', identifier: `iMessage;-;${OWNER}` }]);
  await sidecar.stop();
});

test('without a 1:1 chat for the paired handle the send falls back to the address', async () => {
  const kit = chatKit([
    chat(`any;-;${OTHER}`),
    chat('any;+;chat987654', { kind: 'group' }),
  ]);
  const sidecar = await start(kit);
  await sidecar.send({ op: 'scope', mode: 'paired', handle: OWNER, since_ms: 0 });
  await sidecar.send({ op: 'send', id: 'r1', to: OWNER, text: 'hi' });
  assert.deepEqual(kit.jobs, [], 'never another conversation or a group');
  assert.deepEqual(kit.sends, [{ to: OWNER, text: 'hi' }]);
  assert.deepEqual(sidecar.of('sent'), [{ ev: 'sent', id: 'r1' }]);
  await sidecar.stop();
});

test('a chat lookup that fails falls back to the address and the send still reports', async () => {
  const kit = chatKit();
  kit.listChats = async () => { throw kitError('DATABASE', 'Failed to list chats: disk I/O error'); };
  const sidecar = await start(kit);
  await sidecar.send({ op: 'scope', mode: 'paired', handle: OWNER, since_ms: 0 });
  await sidecar.send({ op: 'send', id: 'r1', to: OWNER, text: 'hi' });
  assert.deepEqual(kit.sends, [{ to: OWNER, text: 'hi' }]);
  assert.deepEqual(sidecar.of('sent'), [{ ev: 'sent', id: 'r1' }]);
  await sidecar.stop();
});

test('a failed send into the chat names its reason like any other send', async () => {
  const kit = chatKit([chat(`any;-;${OWNER}`)]);
  kit.sendError = kitError('SEND', 'Messages app is not running');
  const sidecar = await start(kit);
  await sidecar.send({ op: 'scope', mode: 'paired', handle: OWNER, since_ms: 0 });
  await sidecar.send({ op: 'send', id: 'r1', to: OWNER, text: 'hi' });
  assert.deepEqual(sidecar.of('send_failed'), [{ ev: 'send_failed', id: 'r1', reason: 'messages_not_running' }]);
  await sidecar.stop();
});

test('a refused send looks up no chat', async () => {
  const kit = chatKit([chat(`any;-;${OTHER}`)]);
  const sidecar = await start(kit);
  await sidecar.send({ op: 'scope', mode: 'paired', handle: OWNER, since_ms: 0 });
  await sidecar.send({ op: 'send', id: 'r1', to: OTHER, text: 'elsewhere' });
  assert.deepEqual(kit.chatQueries, []);
  assert.deepEqual(kit.jobs, []);
  await sidecar.stop();
});
