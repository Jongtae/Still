// AgentOS code-run runner (#1380): thin glue around the pinned sandbox-runtime.
//
// The AgentOS host process owns every decision. This file only starts the
// sandbox, asks the host about each outbound HTTP request and reports what
// ran. Protocol (one JSON object per line):
//   host -> runner, first line: {workdir, argv, allowedDomains, denyRead, allowRead,
//                                env, masked: [{name, value, injectHosts}],
//                                timeoutMs, outputBytes}
//   runner -> host: {type: "request", id, method, host, path, query, bodyBytes, bodyDigest, bodyText, headers}
//     bodyText (#1456): a non-GET body as UTF-8 text with each masked value's sentinel replaced by
//     {{NAME}}, so the host can show the owner the exact request and bind an approval to it across runs;
//     null when the body is not text or longer than BODY_TEXT_BYTES.
//     headers (#1489): [[name, value]] with the same {{NAME}} replacement, bounded; the host reads them
//     only in a function library's parameter-flow check, where an argument may travel in a header.
//   host -> runner: {id, allow, reason}
//   runner -> host, last line: {type: "result", exit, signal, timedOut, stdout, stderr}
//                              or {type: "error", message}
import { SandboxManager } from '@anthropic-ai/sandbox-runtime'
import { spawn } from 'node:child_process'
import { createHash } from 'node:crypto'
import { createInterface } from 'node:readline'

const send = message => process.stdout.write(JSON.stringify(message) + '\n')
const lines = createInterface({ input: process.stdin })
const pending = new Map()
let config = null
let nextId = 1
let child = null
// #1456: sentinel -> {{NAME}} of each masked value, filled once the sandbox registered them.
let sentinels = []
const BODY_TEXT_BYTES = 16 * 1024
const HEADER_COUNT = 64
const HEADER_CHARS = 2048

// The host stops a run with SIGTERM: take the sandboxed process group down too.
process.on('SIGTERM', () => {
  if (child) { try { process.kill(-child.pid, 'SIGKILL') } catch {} }
  process.exit(143)
})

const quote = value => "'" + String(value).replace(/'/g, "'\\''") + "'"

function ask (summary) {
  const id = nextId++
  return new Promise(resolve => {
    pending.set(id, resolve)
    send({ type: 'request', id, ...summary })
  })
}

async function filterRequest (request) {
  const url = new URL(request.url)
  let body = new Uint8Array()
  if (!['GET', 'HEAD'].includes(request.method)) {
    body = new Uint8Array(await request.clone().arrayBuffer())
  }
  // Masked values are still sentinels here: substitution happens after this filter.
  let bodyText = null
  if (body.byteLength && body.byteLength <= BODY_TEXT_BYTES) {
    try {
      bodyText = new TextDecoder('utf-8', { fatal: true }).decode(body)
      for (const [sentinel, name] of sentinels) bodyText = bodyText.split(sentinel).join(`{{${name}}}`)
    } catch { bodyText = null }
  }
  let headers = []
  try {
    headers = Array.from(request.headers.entries()).slice(0, HEADER_COUNT).map(([name, value]) => {
      let text = String(value).slice(0, HEADER_CHARS)
      for (const [sentinel, label] of sentinels) text = text.split(sentinel).join(`{{${label}}}`)
      return [name, text]
    })
  } catch { headers = [] }
  const decision = await ask({
    method: request.method,
    host: url.host,
    path: url.pathname || '/',
    query: url.search.replace(/^\?/, ''),
    bodyBytes: body.byteLength,
    bodyDigest: body.byteLength ? createHash('sha256').update(body).digest('hex') : '',
    bodyText: body.byteLength ? bodyText : '',
    headers
  })
  return decision.allow ? { action: 'allow' } : { action: 'deny', reason: String(decision.reason || 'denied by AgentOS') }
}

async function run () {
  for (const item of config.masked || []) process.env[item.name] = item.value
  await SandboxManager.initialize({
    network: {
      allowedDomains: config.allowedDomains,
      deniedDomains: [],
      tlsTerminate: {},
      filterRequest
    },
    filesystem: {
      // Explicit paths from the host: '~' would follow this process's HOME, which is the run directory.
      denyRead: config.denyRead,
      allowRead: [config.workdir, ...(config.allowRead || [])],
      allowWrite: [config.workdir],
      denyWrite: []
    },
    credentials: {
      envVars: (config.masked || []).map(item => ({ name: item.name, mode: 'mask', injectHosts: item.injectHosts }))
    }
  })
  // register() is idempotent on the name: it returns the sentinel already minted for that value.
  const registry = SandboxManager.getSentinelRegistry()
  sentinels = (config.masked || []).map(item => [registry.register(item.name, item.value, item.injectHosts), item.name])
  const command = `cd ${quote(config.workdir)} && ${config.argv.map(quote).join(' ')}`
  const wrapped = await SandboxManager.wrapWithSandbox(command)
  // Masked values reach the child only as the sentinels srt puts on the command line.
  const env = { ...config.env }
  child = spawn(wrapped, { shell: '/bin/sh', env, detached: true, stdio: ['ignore', 'pipe', 'pipe'] })
  const limit = config.outputBytes
  const out = { stdout: Buffer.alloc(0), stderr: Buffer.alloc(0) }
  for (const stream of ['stdout', 'stderr']) {
    child[stream].on('data', chunk => {
      if (out[stream].length < limit) out[stream] = Buffer.concat([out[stream], chunk]).subarray(0, limit)
    })
  }
  let timedOut = false
  const timer = setTimeout(() => {
    timedOut = true
    try { process.kill(-child.pid, 'SIGKILL') } catch {}
  }, config.timeoutMs)
  const [exit, signal] = await new Promise(resolve => child.on('close', (code, sig) => resolve([code, sig])))
  clearTimeout(timer)
  const stderr = SandboxManager.annotateStderrWithSandboxFailures(command, out.stderr.toString('utf8'))
  send({ type: 'result', exit, signal, timedOut, stdout: out.stdout.toString('utf8'), stderr })
  await SandboxManager.reset()
}

lines.on('line', line => {
  const message = JSON.parse(line)
  if (config === null) {
    config = message
    run().catch(error => {
      send({ type: 'error', message: String(error && error.message || error).slice(0, 500) })
      process.exitCode = 3
    }).finally(() => {
      // Let buffered pipe writes drain instead of process.exit() (pipes are asynchronous on macOS).
      lines.close()
      process.stdin.destroy()
    })
    return
  }
  const resolve = pending.get(message.id)
  if (resolve) {
    pending.delete(message.id)
    resolve(message)
  }
})
