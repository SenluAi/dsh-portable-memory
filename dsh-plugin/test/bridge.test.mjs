import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { MemoryBridge, apply } from '../lib/index.js'

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..')
const python = process.env.DAISY_PYTHON || (process.platform === 'win32' ? 'python' : 'python3')
function fixture(t, options = {}) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'portable-memory-node-'))
  const bridge = new MemoryBridge({ python, script: path.join(root, 'mcp_server.py'), store: path.join(directory, 'store.json'), ...options })
  t.after(async () => {
    bridge.dispose()
    assert.equal(path.dirname(directory), os.tmpdir())
    await fs.promises.rm(directory, { recursive: true, force: true, maxRetries: 10, retryDelay: 100 })
  })
  return { bridge, directory }
}

test('real persistent Python, shared schema and tasks', async t => {
  const { bridge } = fixture(t)
  const catalog = await bridge.request('tools/list', {})
  assert.equal(catalog.tools.length, 10)
  assert.ok(catalog.tools.find(tool => tool.name === 'memory_remember').inputSchema.properties.fact)
  const pid = bridge.child.pid
  const remembered = await bridge.request('tools/call', { name: 'memory_remember', arguments: { layer: 'tasks', id: 'fixed-task', text: 'fixture task', status: 'done', result: 'verified' } })
  assert.equal(remembered.structuredContent.item.id, 'fixed-task')
  assert.equal(remembered.structuredContent.item.status, 'done')
  await bridge.request('tools/call', { name: 'memory_recall', arguments: { query: 'fixture', mode: 'lexical' } })
  assert.equal(bridge.child.pid, pid)
})

test('bad arguments and corrupt store return structured errors without disconnecting', async t => {
  const { bridge } = fixture(t)
  const response = await bridge.request('tools/call', { name: 'memory_remember', arguments: { layer: 'rules' } })
  assert.equal(response.isError, true)
  assert.equal(response.structuredContent.error.code, 'INVALID_ARGUMENT')
  assert.deepEqual(await bridge.request('ping', {}), {})
  fs.writeFileSync(bridge.store, '{broken')
  const corrupt = await bridge.request('tools/call', { name: 'memory_stats', arguments: {} })
  assert.equal(corrupt.isError, true)
  assert.deepEqual(await bridge.request('ping', {}), {})
})

test('abort stops the background process and next call restarts cleanly', async t => {
  const { bridge } = fixture(t, { script: path.join(root, 'tests/fixtures/slow_rpc.py'), timeout: 5000 })
  await bridge.ensure()
  const controller = new AbortController()
  const promise = bridge.request('tools/call', {}, controller.signal)
  setTimeout(() => controller.abort(), 50)
  await assert.rejects(promise, { name: 'AbortError' })
  assert.equal(bridge.child, null)
  assert.deepEqual(await bridge.request('ping', {}), {})
})

test('bounded timeout rejects instead of hanging', async t => {
  const { bridge } = fixture(t, { script: path.join(root, 'tests/fixtures/slow_rpc.py'), timeout: 700 })
  await assert.rejects(bridge.request('tools/call', {}), /timed out/)
  assert.equal(bridge.child, null)
})

test('missing Python runtime is an error, not a successful text response', async t => {
  const { bridge } = fixture(t, { python: path.join(os.tmpdir(), 'nonexistent-portable-python.exe') })
  await assert.rejects(bridge.request('tools/list', {}), /could not start/)
})

test('Cordis adapter registers the same tools and throws tool errors', async t => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'portable-memory-plugin-'))
  const previous = process.env.MEMORY_STORE
  process.env.MEMORY_STORE = path.join(directory, 'memory.json')
  const definitions = [], disposers = []
  let injected
  t.after(async () => {
    for (const dispose of disposers) dispose()
    if (previous === undefined) delete process.env.MEMORY_STORE
    else process.env.MEMORY_STORE = previous
    await fs.promises.rm(directory, { recursive: true, force: true, maxRetries: 10, retryDelay: 100 })
  })
  await apply({ on(_event, dispose) { disposers.push(dispose) },
    inject(_services, callback) { injected = callback({ tools: { register(definition) { definitions.push(definition) } } }); return injected } }, { enableIdlePrune: false })
  await injected
  assert.equal(definitions.length, 10)
  await assert.rejects(definitions.find(tool => tool.name === 'memory_remember').execute({ layer: 'rules' }), error => error.code === 'INVALID_ARGUMENT')
})

test('Cordis effect cleanup stops the persistent Python service', async t => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'portable-memory-effect-'))
  const previous = process.env.MEMORY_STORE
  process.env.MEMORY_STORE = path.join(directory, 'memory.json')
  const effects = []
  let injected
  t.after(async () => {
    for (const dispose of effects) dispose()
    if (previous === undefined) delete process.env.MEMORY_STORE
    else process.env.MEMORY_STORE = previous
    await fs.promises.rm(directory, { recursive: true, force: true, maxRetries: 10, retryDelay: 100 })
  })
  apply({ effect(create) { effects.push(create()) }, on() { throw new Error('Use Cordis effects, not an undocumented dispose event') },
    inject(_services, callback) { injected = callback({ tools: { register() {} } }); return injected } }, { enableIdlePrune: false })
  await injected
  assert.equal(effects.length, 1)
  effects[0]()
})
