/**
 * dsh-portable-memory —— 把「便携记忆」直接内置进 DeepSeek Harness。
 *
 * 宿主进程内加载（cordis 插件），7 个工具与内置工具同级出现在工具列表里，
 * 不再依赖额外 MCP 进程。数据仍落在 Documents/portable-memory/store/memory.json，
 * 与 memory.py / mcp_server.py 共用同一个库，双向可见。
 *
 * @module dsh-portable-memory
 */
import { execFile } from 'node:child_process'
import { promisify } from 'node:util'
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { homedir } from 'node:os'
import { dirname, join } from 'node:path'

const run = promisify(execFile)

/**
 * 空闲自动清理：软件启动即开始计时，只有「系统真正空闲」——键鼠连续 IDLE_MIN 分钟
 * （默认 10）没有动静、也没有记忆活动——才跑一次 prune（归档过期任务/近期上下文、截断超长流水）。
 * 正在做任务（键鼠或对话有活动）时绝不清理；夜间 QUIET 时段（默认 23 点到次日 7 点）也跳过。
 * 两次清理之间至少间隔 PRUNE_GAP_MIN 分钟（默认 1440，即每天最多清理一次）；
 * 上次清理时间会落盘，重启软件也认这份记录，不会同一天反复清理。
 * 可用环境变量 DAISY_MEMORY_IDLE_MIN / DAISY_MEMORY_PRUNE_GAP_MIN 调整。
 */
const IDLE_MS = Math.max(1, Number(process.env.DAISY_MEMORY_IDLE_MIN || 10)) * 60 * 1000
const PRUNE_GAP_MS = Math.max(1, Number(process.env.DAISY_MEMORY_PRUNE_GAP_MIN || 1440)) * 60 * 1000

const MEMORY_DIR = process.env.DAISY_MEMORY_DIR || join(homedir(), 'Documents', 'portable-memory')
const MEMORY_PY = join(MEMORY_DIR, 'memory.py')
const PYTHON = process.env.DAISY_PYTHON || 'python'
/** 自动清理状态（上次真正清理的时间、结果），让“每天一次”跨重启生效。 */
const PRUNE_STATE = join(MEMORY_DIR, 'store', 'prune-state.json')

/** 夜间免打扰时段，格式「起始-结束」，默认 23-7；改这里或设 DAISY_MEMORY_QUIET 环境变量。 */
const QUIET_RANGE = String(process.env.DAISY_MEMORY_QUIET || '23-7').split('-').map(Number)
const QUIET_START = Number.isFinite(QUIET_RANGE[0]) ? QUIET_RANGE[0] : 23
const QUIET_END = Number.isFinite(QUIET_RANGE[1]) ? QUIET_RANGE[1] : 7

/** 判断当前是否落在夜间免打扰时段。 */
function inQuietHours(now) {
  if (QUIET_START === QUIET_END) return false
  const h = new Date(now).getHours()
  return QUIET_START < QUIET_END
    ? h >= QUIET_START && h < QUIET_END
    : h >= QUIET_START || h < QUIET_END
}

/** 系统级空闲秒数（键盘鼠标多久没动）；Windows 走 GetLastInputInfo，取不到返回 null。 */
const IDLE_PS = (() => {
  const cs = 'using System;using System.Runtime.InteropServices;'
    + 'public struct LASTINPUTINFO{public uint cbSize;public uint dwTime;}'
    + 'public class DaisyIdle{[DllImport("user32.dll")]public static extern bool GetLastInputInfo(ref LASTINPUTINFO plii);'
    + 'public static uint Seconds(){LASTINPUTINFO li=new LASTINPUTINFO();li.cbSize=(uint)Marshal.SizeOf(li);'
    + 'if(!GetLastInputInfo(ref li)){return 0;}return ((uint)Environment.TickCount-li.dwTime)/1000;}}'
  const ps = `Add-Type -TypeDefinition '${cs}' -ErrorAction Stop; [DaisyIdle]::Seconds()`
  return Buffer.from(ps, 'utf16le').toString('base64')
})()

const POWERSHELL = process.env.SystemRoot ? String(process.env.SystemRoot) + '\\System32\\WindowsPowerShell\\v1.0\\powershell.exe' : 'powershell'
let idleCache = { at: 0, value: null }

async function systemIdleSeconds() {
  if (process.platform !== 'win32') return null
  const now = Date.now()
  if (now - idleCache.at < 15000) return idleCache.value
  let value = null
  try {
    const { stdout } = await run(
      POWERSHELL,
      ['-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-EncodedCommand', IDLE_PS],
      { timeout: 8000, windowsHide: true },
    )
    const n = Number(String(stdout).trim().split(/\r?\n/).pop())
    if (Number.isFinite(n) && n >= 0) value = n
  } catch {
    /* 拿不到系统空闲时间时，退回只看记忆活动 */
  }
  idleCache = { at: now, value }
  return value
}

let lastActiveAt = Date.now()
let lastPruneAt = 0

/** 读回上次清理时间；文件缺失或损坏都当作从未清理过。 */
function loadPruneState() {
  try {
    const data = JSON.parse(readFileSync(PRUNE_STATE, 'utf8'))
    const at = Number(data && data.lastPruneAt)
    if (Number.isFinite(at) && at > 0 && at <= Date.now()) lastPruneAt = at
  } catch {
    /* 没有状态文件或内容损坏，按首次启动处理 */
  }
}

/** 把本次清理结果写入状态文件，失败静默，不影响正常使用。 */
function savePruneState(info) {
  try {
    mkdirSync(dirname(PRUNE_STATE), { recursive: true })
    writeFileSync(PRUNE_STATE, JSON.stringify(info, null, 2), 'utf8')
  } catch {
    /* 状态文件写不进去时只影响跨重启的间隔判断 */
  }
}

loadPruneState()

/** 调 memory.py 的 CLI，返回 stdout 文本。 */
async function memory(args, opts = {}) {
  if (opts && opts.touch !== false) lastActiveAt = Date.now()
  if (!existsSync(MEMORY_PY)) {
    return `找不到记忆脚本：${MEMORY_PY}（可用环境变量 DAISY_MEMORY_DIR 指定目录）`
  }
  try {
    const { stdout } = await run(PYTHON, ['-X', 'utf8', MEMORY_PY, ...args], {
      cwd: MEMORY_DIR,
      env: { ...process.env, PYTHONIOENCODING: 'utf-8', PYTHONUTF8: '1' },
      windowsHide: true,
      maxBuffer: 8 * 1024 * 1024,
      timeout: 30000,
    })
    return String(stdout || '').trim() || '(无输出)'
  } catch (error) {
    const out = String((error && (error.stdout || error.stderr)) || '').trim()
    return `记忆命令失败：${(error && error.message) || error}${out ? `\n${out}` : ''}`
  }
}

const textOutput = {
  schema: { type: 'object', properties: { text: { type: 'string' } }, required: ['text'], additionalProperties: false },
  render(_args, value) {
    return [{ type: 'text', text: String((value && value.text) || '') }]
  },
}

const str = (description) => ({ type: 'string', description })
const num = (description) => ({ type: 'number', description })
const bool = (description) => ({ type: 'boolean', description })

const LAYER_NOTE = 'rules/profile/preferences 为永久层，tasks 按 ttl 天过期，recent 记文件·应用·网页，fragments 为碎片。'

export const name = 'portable-memory'

/** Host plugin body. */
export function apply(ctx /*, config */) {
  lastActiveAt = Date.now()

  /** 只有「空闲满 10 分钟且距上次清理够久（默认一天）」才真正执行；失败静默。 */
  async function pruneWhenIdle() {
    const now = Date.now()
    if (now - lastActiveAt < IDLE_MS) return
    if (now - lastPruneAt < PRUNE_GAP_MS) return
    // 正在用软件（键鼠或任务有活动）一律不清理。
    const sysIdle = await systemIdleSeconds()
    if (sysIdle !== null && sysIdle * 1000 < IDLE_MS) return
    lastPruneAt = now
    let ok = false
    let output = ''
    try {
      output = String(await memory(['prune'], { touch: false }))
      ok = !output.startsWith('记忆命令失败')
    } catch (error) {
      output = String((error && error.message) || error)
    }
    // memory.py 自己也有“每天最多一次”的兜底；状态文件记录本次尝试，避免重启后同一天再跑。
    savePruneState({
      lastPruneAt: now,
      lastPruneAtText: new Date(now).toISOString(),
      ok,
      output: output.slice(0, 500),
    })
  }

  const idleTimer = setInterval(() => {
    void pruneWhenIdle()
  }, 30 * 1000)
  if (typeof idleTimer.unref === 'function') idleTimer.unref()
  try {
    ctx.on('dispose', () => clearInterval(idleTimer))
  } catch {
    /* 宿主不支持 dispose 钩子时忽略 */
  }

  ctx.inject(['tools'], (scoped) => {
    const tools = scoped.tools

    tools.register({
      name: 'memory_context',
      description:
        '读取全部永久规则、画像、偏好、未过期任务和最近上下文，返回可直接注入提示词的 Markdown 记忆块。任务开始时先调用它。',
      parameters: {
        type: 'object',
        properties: {
          max_recent: num('近期上下文条数，默认 10。'),
          touch: bool('true 则给命中的任务/近期项续期。'),
        },
        additionalProperties: false,
      },
      output: textOutput,
      presentCall: () => ({ card: 'terminal', title: 'memory_context' }),
      async execute(args) {
        const argv = ['context']
        if (args && args.max_recent) argv.push('--max-recent', String(Math.trunc(Number(args.max_recent))))
        if (args && args.touch) argv.push('--touch')
        return { text: await memory(argv) }
      },
    })

    tools.register({
      name: 'memory_recall',
      description:
        `按关键词检索记忆（中文按字与双字滑窗，英文按词匹配，同义词靠 tags）。${LAYER_NOTE}`,
      parameters: {
        type: 'object',
        properties: {
          query: str('检索关键词；不给则按层列出。'),
          layer: str('只查某一层：rules/profile/preferences/tasks/recent/fragments。'),
          limit: num('最多返回几条，默认 8。'),
          touch: bool('true 则命中项续期（默认会续期，传 false 关闭）。'),
        },
        additionalProperties: false,
      },
      output: textOutput,
      presentCall: (args) => ({ card: 'terminal', title: `memory_recall ${String((args && args.query) || '').slice(0, 24)}`.trim() }),
      async execute(args) {
        const argv = ['recall']
        if (args && args.query) argv.push(String(args.query))
        if (args && args.layer) argv.push('--layer', String(args.layer))
        if (args && args.limit) argv.push('--limit', String(Math.trunc(Number(args.limit))))
        if (args && args.touch === false) argv.push('--no-touch')
        return { text: await memory(argv) }
      },
    })

    tools.register({
      name: 'memory_remember',
      description:
        '写入一条记忆。layer=rules/profile/preferences 为永久；tasks 按 ttl 天过期（默认 14，0 表示不过期）；recent 记文件/应用/网页，自动去重并累加命中次数；fragments 为碎片。',
      parameters: {
        type: 'object',
        properties: {
          layer: { ...str('目标层。'), enum: ['rules', 'profile', 'preferences', 'tasks', 'recent', 'fragments'] },
          text: str('记忆正文（一句话）。'),
          kind: str('可选分类标签，例如 preference/task/file/app/page/fact。'),
          value: str('recent 层的实际值，如文件路径或网址。'),
          why: str('为什么记这条（便于日后判断是否过时）。'),
          tags: str('逗号分隔的标签，用于同义词召回。'),
          ttl: num('tasks 存活天数，默认 14，0 表示不过期。'),
          status: str('任务状态，如 pending/done。'),
        },
        required: ['layer'],
        additionalProperties: false,
      },
      output: textOutput,
      presentCall: (args) => ({ card: 'terminal', title: `memory_remember ${String((args && args.layer) || '')}`.trim() }),
      async execute(args) {
        const a = args || {}
        const argv = ['remember', '--layer', String(a.layer)]
        if (a.text) argv.push('--text', String(a.text))
        if (a.kind) argv.push('--kind', String(a.kind))
        if (a.value) argv.push('--value', String(a.value))
        if (a.why) argv.push('--why', String(a.why))
        if (a.tags) argv.push('--tags', String(a.tags))
        if (a.ttl !== undefined && a.ttl !== null && a.ttl !== '') argv.push('--ttl', String(Math.trunc(Number(a.ttl))))
        if (a.status) argv.push('--status', String(a.status))
        return { text: await memory(argv) }
      },
    })

    tools.register({
      name: 'memory_forget',
      description: '删除记忆，可按 id 或关键词匹配。删除前会记入 archive 归档，可再用 memory_restore 捞回。',
      parameters: {
        type: 'object',
        properties: {
          id: str('要删的记忆 id。'),
          query: str('按关键词删除。'),
        },
        additionalProperties: false,
      },
      output: textOutput,
      presentCall: (args) => ({ card: 'terminal', title: `memory_forget ${String((args && (args.id || args.query)) || '').slice(0, 24)}`.trim() }),
      async execute(args) {
        const a = args || {}
        const argv = ['forget']
        if (a.id) argv.push('--id', String(a.id))
        if (a.query) argv.push('--query', String(a.query))
        if (argv.length === 1) return { text: '需要 id 或 query 之一。' }
        return { text: await memory(argv) }
      },
    })

    tools.register({
      name: 'memory_prune',
      description: '归档过期任务与近期上下文、截断超长流水。dryRun=true 只报告不写入。',
      parameters: {
        type: 'object',
        properties: {
          dryRun: bool('true 只预演，不写盘。'),
        },
        additionalProperties: false,
      },
      output: textOutput,
      presentCall: () => ({ card: 'terminal', title: 'memory_prune' }),
      async execute(args) {
        const argv = ['prune']
        if (args && args.dryRun) argv.push('--dry-run')
        return { text: await memory(argv) }
      },
    })

    tools.register({
      name: 'memory_restore',
      description: '从归档捞回被遗忘/删除的记忆。可按 id、关键词，或 last=N 恢复最近归档的 N 条；listOnly=true 先看归档。',
      parameters: {
        type: 'object',
        properties: {
          id: str('按 id 恢复。'),
          query: str('按关键词恢复。'),
          last: num('恢复最近归档的 N 条。'),
          limit: num('按 id/关键词匹配时最多恢复几条，默认 8。'),
          listOnly: bool('true 只列出归档，不恢复。'),
          keepArchive: bool('true 则恢复后保留归档记录。'),
        },
        additionalProperties: false,
      },
      output: textOutput,
      presentCall: () => ({ card: 'terminal', title: 'memory_restore' }),
      async execute(args) {
        const a = args || {}
        const argv = ['restore']
        if (a.id) argv.push('--id', String(a.id))
        if (a.query) argv.push('--query', String(a.query))
        if (a.last) argv.push('--last', String(Math.trunc(Number(a.last))))
        if (a.limit) argv.push('--limit', String(Math.trunc(Number(a.limit))))
        if (a.listOnly) argv.push('--list')
        if (a.keepArchive) argv.push('--keep-archive')
        return { text: await memory(argv) }
      },
    })

    tools.register({
      name: 'memory_stats',
      description: '查看各层条数、有效条数、归档数量与库文件大小。',
      parameters: { type: 'object', properties: {}, additionalProperties: false },
      output: textOutput,
      presentCall: () => ({ card: 'terminal', title: 'memory_stats' }),
      async execute() {
        return { text: await memory(['stats']) }
      },
    })
  })
}
