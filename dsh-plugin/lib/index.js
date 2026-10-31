import { spawn, execFile } from 'node:child_process'
import { promisify } from 'node:util'
import { existsSync, mkdirSync } from 'node:fs'
import { homedir } from 'node:os'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const run = promisify(execFile)

export class MemoryBridge {
  constructor({ python, script, store, timeout = 120000 }) {
    this.python = python
    this.script = script
    this.store = store
    this.timeout = timeout
    this.pending = new Map()
    this.sequence = 0
    this.child = null
    this.starting = null
    this.closed = false
  }

  async ensure() {
    if (this.closed) throw new Error('Memory bridge is disposed')
    if (this.starting) return this.starting
    if (this.child) return
    if (!existsSync(this.script)) throw new Error('Memory runtime is missing; reinstall the packaged plugin')
    mkdirSync(dirname(this.store), { recursive: true })
    this.child = spawn(this.python, ['-u', '-X', 'utf8', this.script], {
      windowsHide: true, stdio: ['pipe', 'pipe', 'pipe'],
      env: { ...process.env, MEMORY_STORE: this.store, PYTHONUTF8: '1', PYTHONIOENCODING: 'utf-8' },
    })
    const child = this.child
    let buffer = ''
    child.stdout.setEncoding('utf8')
    child.stdout.on('data', chunk => {
      buffer += chunk
      if (buffer.length > 4 * 1024 * 1024) return this.stop(new Error('Memory response exceeded limit'))
      let newline
      while ((newline = buffer.indexOf('\n')) >= 0) {
        const line = buffer.slice(0, newline)
        buffer = buffer.slice(newline + 1)
        let message
        try { message = JSON.parse(line) } catch { this.stop(new Error('Invalid memory RPC response')); return }
        const pending = this.pending.get(message.id)
        if (!pending) continue
        this.pending.delete(message.id)
        pending.finish()
        if (message.error) pending.reject(new Error(message.error.message))
        else pending.resolve(message.result)
      }
    })
    // Drain diagnostics, but never copy user memory or credentials into host logs.
    child.stderr.on('data', () => {})
    child.stdin.on('error', () => this.stop(new Error('Memory input channel closed')))
    child.once('error', () => this.stop(new Error('Python could not start; configure DAISY_PYTHON')))
    child.once('exit', () => { if (this.child === child) this.stop(new Error('Memory service exited; next call can restart it')) })
    this.starting = this.send('initialize', { protocolVersion: '2024-11-05', capabilities: {}, clientInfo: { name: 'dsh-portable-memory', version: '1.1.0' } })
      .then(() => { child.stdin.write(JSON.stringify({ jsonrpc: '2.0', method: 'notifications/initialized' }) + '\n') })
      .finally(() => { this.starting = null })
    return this.starting
  }

  send(method, params, signal) {
    if (signal?.aborted) return Promise.reject(Object.assign(new Error('Memory operation cancelled'), { name: 'AbortError' }))
    if (this.pending.size >= 32) return Promise.reject(new Error('Memory queue is full; retry later'))
    return new Promise((resolve, reject) => {
      const id = ++this.sequence
      const abort = () => this.stop(Object.assign(new Error('Memory operation cancelled'), { name: 'AbortError' }))
      const timer = setTimeout(() => this.stop(new Error('Memory operation timed out')), this.timeout)
      const finish = () => { clearTimeout(timer); signal?.removeEventListener('abort', abort) }
      this.pending.set(id, { resolve, reject, finish })
      signal?.addEventListener('abort', abort, { once: true })
      if (!this.child?.stdin.writable) { this.stop(new Error('Memory input unavailable')); return }
      this.child.stdin.write(JSON.stringify({ jsonrpc: '2.0', id, method, params }) + '\n')
    })
  }

  async request(method, params, signal) {
    if (signal?.aborted) throw Object.assign(new Error('Memory operation cancelled'), { name: 'AbortError' })
    await this.ensure()
    return this.send(method, params, signal)
  }

  stop(error = new Error('Memory bridge stopped')) {
    const child = this.child
    this.child = null
    this.starting = null
    for (const pending of this.pending.values()) { pending.finish(); pending.reject(error) }
    this.pending.clear()
    if (child && !child.killed) child.kill()
  }

  dispose() { this.closed = true; this.stop() }
}

async function systemIdleSeconds() {
  if (process.platform === 'darwin') {
    try {
      const { stdout } = await run('/usr/sbin/ioreg', ['-c', 'IOHIDSystem'], { timeout: 5000, windowsHide: true })
      const match = stdout.match(/"HIDIdleTime"\s*=\s*(\d+)/)
      return match ? Number(match[1]) / 1e9 : null
    } catch { return null }
  }
  if (process.platform !== 'win32') return null
  const code = 'using System;using System.Runtime.InteropServices;public class PMIdle{[StructLayout(LayoutKind.Sequential)]public struct Info{public uint size,time;}[DllImport("user32.dll")]public static extern bool GetLastInputInfo(ref Info i);public static uint Seconds(){Info i=new Info();i.size=8;if(!GetLastInputInfo(ref i))throw new Exception();return ((uint)Environment.TickCount-i.time)/1000;}}'
  const command = Buffer.from(`Add-Type -TypeDefinition '${code}';[PMIdle]::Seconds()`, 'utf16le').toString('base64')
  try {
    const executable = join(process.env.SystemRoot || 'C:\\Windows', 'System32/WindowsPowerShell/v1.0/powershell.exe')
    const { stdout } = await run(executable, ['-NoProfile', '-NonInteractive', '-WindowStyle', 'Hidden', '-EncodedCommand', command], { timeout: 8000, windowsHide: true })
    const seconds = Number(stdout.trim())
    return Number.isFinite(seconds) && seconds >= 0 ? seconds : null
  } catch { return null }
}

export const name = 'portable-memory'

export function apply(ctx, config = {}) {
  const packageRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..')
  const bundled = join(packageRoot, 'runtime/mcp_server.py')
  const script = existsSync(bundled) ? bundled : resolve(packageRoot, '../mcp_server.py')
  const directory = process.env.DAISY_MEMORY_DIR || join(homedir(), 'Documents/portable-memory')
  const bridge = new MemoryBridge({ script,
    python: process.env.DAISY_PYTHON || (process.platform === 'win32' ? 'python' : 'python3'),
    store: process.env.MEMORY_STORE || join(directory, 'store/memory.json'),
  })
  let lastActive = Date.now(), checking = false
  const idleMs = Math.max(1, Number(process.env.DAISY_MEMORY_IDLE_MIN || 10)) * 60000
  const quiet = String(process.env.DAISY_MEMORY_QUIET || '23-7').split('-').map(Number)
  const timer = setInterval(async () => {
    const hour = new Date().getHours()
    const inQuiet = quiet[0] !== quiet[1] && (quiet[0] < quiet[1] ? hour >= quiet[0] && hour < quiet[1] : hour >= quiet[0] || hour < quiet[1])
    if (config.enableIdlePrune === false || checking || bridge.pending.size || Date.now() - lastActive < idleMs || inQuiet) return
    checking = true
    try {
      const seconds = await systemIdleSeconds()
      if (seconds === null || seconds * 1000 < idleMs) return
      await bridge.request('tools/call', { name: 'memory_prune', arguments: {} })
    } catch { /* A housekeeping failure must not interrupt conversation. */ }
    finally { checking = false }
  }, 30000)
  timer.unref?.()
  const dispose = () => { clearInterval(timer); bridge.dispose() }
  if (typeof ctx.effect === 'function') ctx.effect(() => dispose, 'portable-memory.bridge')
  else ctx.on('dispose', dispose)
  ctx.inject(['tools'], async scoped => {
    const catalog = await bridge.request('tools/list', {})
    for (const tool of catalog.tools) {
      scoped.tools.register({ name: tool.name, description: tool.description, parameters: tool.inputSchema,
        output: { schema: { type: 'object', properties: { text: { type: 'string' } }, required: ['text'], additionalProperties: false },
          render: (_args, value) => [{ type: 'text', text: value.text }] },
        async execute(args, options = {}) {
          lastActive = Date.now()
          const result = await bridge.request('tools/call', { name: tool.name, arguments: args || {} }, options.signal)
          const payload = result.structuredContent || JSON.parse(result.content[0].text)
          if (result.isError || payload.ok === false) {
            const error = new Error(payload.error?.message || 'Memory tool failed')
            error.code = payload.error?.code
            error.correction = payload.error?.correction
            throw error
          }
          return { text: JSON.stringify(payload) }
        },
      })
    }
  })
}
