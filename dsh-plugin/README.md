# dsh-portable-memory 1.1.0

Cordis 工具适配器，使用与 CLI/MCP 相同的十个工具和业务核心。
工具列表从服务的 schema 读取，不再维护缩水的副本。

## 运行要求

- 宿主提供官方 `tools.register()` 与 Cordis 生命周期。
- Python 3.10+。可通过 `DAISY_PYTHON` 指定解释器绝对路径。
- 向量检索需要在该解释器环境安装 `fastembed>=0.7,<1` 并预热本地多语言模型。
- 否则 hybrid 明确降级为词法；semantic 模式返回错误，不能伪装就绪。

插件包已包含 runtime 下的 memory.py、mcp_server.py、service.py、facts.py、semantic.py 和 LICENSE，
不再依赖用户 Documents 中预先放置脚本，但 **不捆绑 Python 解释器或模型权重**。
源码开发目录直接复用仓库根脚本；打包时 prepack 从唯一源复制运行文件。

```sh
npm pack
```

安装生成的 tgz 到兼容宿主；源码更新不等于 npm 公共 registry 已发布。

## 配置与生命周期

| 环境变量 | 说明 |
| --- | --- |
| `DAISY_PYTHON` | 解释器；Windows 默认 python，其他平台 python3 |
| `MEMORY_STORE` | 记忆文件绝对路径，优先于目录设置 |
| `DAISY_MEMORY_DIR` | 默认 `~/Documents/portable-memory`，库位于其 store/memory.json |
| `MEMORY_*` embedding 变量 | 与仓库根 README 一致 |
| `DAISY_MEMORY_IDLE_MIN` | 自动归档前的空闲分钟数，默认 10 |
| `DAISY_MEMORY_QUIET` | 夜间跳过时段，默认 23-7，首尾相同表示关闭 |

插件保持一个后台 Python MCP 子进程，不在每次工具调用时重新启动解释器。
没有可见控制台；有有界队列、超时和 AbortSignal。取消会停止该桥接进程并使等待请求报错，下一次调用可重新启动。
宿主 dispose 会清理计时器和子进程。参数、损坏库和依赖错误以可识别的工具异常报告，不返回普通成功文本。

空闲检查使用记忆活动、待执行请求和系统输入空闲时间；Windows/macOS 可获取系统空闲时间，
未知平台保守跳过。夜间设置真实参与判断。每日归档限制保存在核心库的 meta 中，跨重启生效。
这不是宿主完整任务状态监听；如宿主有后台任务但无输入/记忆活动，可配置 enableIdlePrune=false。
原先未可靠执行的 PRUNE_GAP_HOURS/DAISY_MEMORY_PRUNE_GAP_MIN 不作为独立调度保证，统一以核心日历日策略为准。

适配器不会自动替换 Daisy 内置记忆；宿主仍需在正确的对话/任务时机调用工具，
并对 scope、用户确认和数据上传策略实施自己的权限控制。

测试：`node --test dsh-plugin/test/bridge.test.mjs`。MIT 许可。
