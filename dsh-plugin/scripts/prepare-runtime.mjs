import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..')
const target = path.join(root, 'dsh-plugin/runtime')
fs.mkdirSync(target, { recursive: true })
for (const name of ['memory.py', 'mcp_server.py', 'service.py', 'semantic.py', 'facts.py', 'LICENSE']) {
  fs.copyFileSync(path.join(root, name), path.join(target, name))
}
