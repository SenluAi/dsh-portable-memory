# 便携记忆（dsh-portable-memory）

把「便携记忆」直接内置进 DeepSeek Harness 的分层记忆插件：数据落在本地
`Documents/portable-memory/store/memory.json`，与 `memory.py`、`mcp_server.py`
共用同一个库，双向可见。插件在宿主进程内加载，不需要额外 MCP 进程。

## 六层记忆

| 层 | 名称 | 说明 | 过期 |
| --- | --- | --- | --- |
| rules | 规则 | 硬性约定，优先级最高 | 不过期 |
| profile | 画像 | 用户事实与背景 | 不过期 |
| preferences | 偏好 | 沟通与操作习惯 | 不过期 |
| tasks | 任务 | 带时间的结果与结论 | 默认 14 天 |
| recent | 近期上下文 | 文件、应用、网页 | 默认 7 天 |
| fragments | 碎片流水 | 零散一句话线索 | 不过期，超过上限丢最旧 |

淘汰的条目只归档不硬删，可用 `restore` 恢复。

## 提供的工具

- `memory_context`：读取注入用的完整记忆块（任务开始时先调用）
- `memory_save`：写入规则、画像、偏好、任务、碎片
- `memory_search`：按关键词召回，命中的条目会刷新最后访问时间
- `memory_forget` / `memory_restore`：归档与恢复
- `memory_stats`：查看各层条数与 token 占用
- `memory_prune`：手动清理

## 空闲自动清理

- 软件启动即开始计时，键鼠与记忆活动都算「在用」。
- 连续空闲满 10 分钟（`IDLE_MIN` 可调）才触发一次清理。
- 每天最多清理一次（`PRUNE_GAP_HOURS` 可调，默认 24 小时），状态记录在
  `state/prune-state.json`，重启后同样生效。
- 清理内容：任务与近期上下文超过 TTL 的条目移入归档，流水超过上限丢最旧。

## 安装

```bash
npm install dsh-portable-memory
```

或在宿主件的 `cordis.patch.yml` 中挂载本包的 bundle 补丁。

## 配置

| 环境变量 | 默认 | 说明 |
| --- | --- | --- |
| `DAISY_MEMORY_DIR` | `~/Documents/portable-memory` | 记忆库目录 |
| `IDLE_MIN` | `10` | 触发清理所需的空闲分钟数 |
| `PRUNE_GAP_HOURS` | `24` | 两次清理的最小间隔 |

## 许可

MIT
