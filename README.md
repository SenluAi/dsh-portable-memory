# dsh-portable-memory 1.1.0

本仓库同时提供完整 portable-memory 源码和可安装的 DSH/Cordis 插件。
下载插件包请到 [Releases](https://github.com/SenluAi/dsh-portable-memory/releases)。

## DSH 插件安装

将 Release 中的 `dsh-portable-memory-1.1.0.tgz` 安装到兼容 DSH bundle 的宿主。
根目录保留 `package.json`、`cordis.patch.yml` 和 `lib/index.js`，兼容现有仓库安装入口。
新版核心源码位于根目录，插件适配器位于 `dsh-plugin/`；根入口仅转发同一个适配器，不维护两套业务逻辑。

安装前需要 Python 3.10+，可用 `DAISY_PYTHON` 指定解释器。
语义检索还需在同一 Python 环境安装 `fastembed>=0.7,<1` 并预热模型；插件不捆绑 Python 或模型权重。
完整运行要求和环境变量见 [插件说明](dsh-plugin/README.md)。

源码构建插件：在仓库根目录运行 `npm pack`，自动复制唯一源核心到插件 runtime，再打包。
本仓库提供 GitHub 下载包，不表示 npm 公共 registry 已发布。

## 记忆核心

面向 CLI、MCP 和 DeepSeek Harness/Cordis 的本地分层记忆系统。
本版统一三条入口，加入真正的多语言向量检索、带证据的原子事实纠错和待确认冲突。
Python 3.10+；基本读写只用标准库。向量检索需要安装可选依赖和下载模型。

## 安装与向量检索

```sh
python -m venv .venv
# Windows: .venv\Scripts\activate；macOS/Linux: source .venv/bin/activate
python -m pip install -e ".[semantic]"
python memory.py init --owner "你的名字"
python memory.py remember --layer profile --text "I live in Shanghai." --source user --confirmed
python memory.py recall "我居住在哪个城市" --mode semantic
```

默认本地模型：`sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`，384 维，支持中文和英文。
第一次语义查询会下载模型，之后本地推理；不会把记忆发给模型下载站。
可先在不含私人数据的库里执行一次查询，预热模型。

- `hybrid`：默认，融合词法与向量排名。缺少模型/依赖时明确返回 `search.used=lexical` 和原因，不伪装成向量检索。
- `semantic`：严格向量模式，不可用就报 `SEMANTIC_UNAVAILABLE`，不静默降级。
- `lexical`：纯本地关键词、中文双字、同义词和 alias/tags 匹配，不需要额外依赖。
- 召回只给实际返回的条目续期；上下文默认不续期。
- 模型文件、分词器或文本变化会使缓存失效。SQLite 索引只存向量和标识，不存原文，可删除重建。
- 索引计算在记忆文件锁外进行；提交和返回前重新核对原文摘要，不能把删除或已修改的记忆重新带回来。

### 可选远程 embedding 接口

必须显式选择，默认不启用。使用兼容 OpenAI 的 `/embeddings` API：

| 环境变量 | 默认/说明 |
| --- | --- |
| `MEMORY_STORE` | 源码目录的 `store/memory.json`；建议指定用户可写的绝对路径 |
| `MEMORY_EMBEDDING_PROVIDER` | `local`；可显式改成 `openai` |
| `MEMORY_EMBEDDING_MODEL` | 本地默认使用上面的多语言模型；远程模式必须指定模型名 |
| `MEMORY_MODEL_CACHE` | `~/.cache/portable-memory` |
| `MEMORY_LOCAL_FILES_ONLY` | 设为 `1` 时仅使用缓存，不下载或检查远端模型；首次使用前须预热 |
| `MEMORY_EMBEDDING_BASE_URL` | 远程模式必填，例如自己服务的 `https://host/v1`；禁止 URL 内嵌凭据 |
| `MEMORY_EMBEDDING_API_KEY` | 仅从环境读取，不写入记忆/索引/日志 |
| `MEMORY_EMBEDDING_REVISION` | 远程模型部署变更时设置新版本，避免复用不兼容向量 |
| `MEMORY_SEMANTIC_THRESHOLD` | 默认 `0.45`，范围 0~1，需要按实际模型和语料评测 |

远程接口要求 HTTPS，仅本机回环地址允许 HTTP；不跟随重定向，以免转发鉴权头。
选择远程模式就意味着检索文本和记忆正文会发给该接口；把 context 交给云端对话模型时，也会发送该上下文。

## 数据分层

| 层 | 用途 | 策略 |
| --- | --- | --- |
| rules | 用户规则 | 默认不过期，上下文不能静默裁掉规则；不代表系统授权 |
| profile | 用户事实 | 默认不过期 |
| preferences | 稳定偏好 | 默认不过期 |
| tasks | 任务、状态、结果 | 默认 14 天，可用稳定 id 更新 |
| recent | 文件、应用、网页 | 默认 lastSeen 后 7 天 |
| fragments | 原始流水 | 每个 scope 默认 120 条，超量先摘要、原文归档 |

旧 JSON schema `portable-memory/1` 继续可读。新条目用 UUID；加载旧库时会修复缺失/重复 ID，并保留旧值和迁移记录。
旧库升级会生成 `.bak`；升级前仍建议自行保存离线备份。请同时升级全部写入端，不要让 1.0.x 写入器与本版并用。

## 自动事实纠错与矛盾消解

事实以 `(scope, subject, predicate)` 定位，不能只根据向量相似就认定互相矛盾。

```sh
python memory.py remember --layer profile --text "我住在北京" --source user --confirmed
python memory.py remember --layer profile --text "我搬到上海" --source user --confirmed
# 新值生效，旧值作为 superseded 归档，保留来源和修正链，不在当前上下文同时注入两种住址。

python memory.py remember --layer profile --text "当前数据库已迁移" --source observation --confidence 0.99 --fact '{"subject":"project-a","predicate":"database","value":"postgresql"}'
```

CLI 的 JSON 参数引号需要按使用的 shell 转义；MCP 直接传对象，无此问题。

- 常见中英文住址、姓名、回复语言、中文喜好否定和“X 的 Y 是 Z”等模式自动提取原子事实。
- 更一般的自然语言请传 `fact` 对象。没有对应原子事实的任意文本，不承诺自动语义纠错。
- 同一事实的相同值可以去重；新确认的用户修正可替换旧值。
- 高置信度现场观测可修正未确认的 agent 推测，不能自动覆盖已确认事实或用户规则。
- 不足以自动判定时保存待确认冲突，不修改活跃事实；可用 `memory_conflicts` 查看和确认处理。
- 来源、确认状态和置信度都是调用方的证据声明，不是身份认证。宿主必须限制不可信代理冒充 `source=user` 或伪造用户确认。
- 历史上被替代的事实不能通过 restore 自动恢复成当前事实，需要提交新的确认修正。
- 旧版纯文本中的已知事实模式会补充原子字段，但不伪造用户确认；切换分类不能绕过事实矛盾检查，恢复归档也不能重新引入与当前事实相反的旧值。

```sh
python memory.py tool memory_conflicts --arguments '{"action":"list"}'
python memory.py tool memory_conflicts --arguments '{"id":"冲突ID","action":"accept_new","confirmed":true}'
```

## 统一工具接口

MCP 和 DSH 插件均注册同一份 10 个工具，CLI 也可以通过 `tool` 子命令调用同一服务：

`memory_context`、`memory_recall`、`memory_remember`、`memory_forget`、`memory_prune`、
`memory_restore`、`memory_summarize`、`memory_synonyms`、`memory_stats`、`memory_conflicts`。

工具支持 `scope` 命名空间，默认 `default`。CLI 在子命令前传 `--scope project-a`。
这能隔离检索/修改目标，但不是多用户访问控制：拥有同一文件读写权的程序仍能读取整库。

```sh
python memory.py remember --layer tasks --id task-1 --text "迁移数据库" --status doing
python memory.py remember --layer tasks --id task-1 --text "迁移数据库完成" --status done --result "已验证"
python memory.py context --query "当前项目使用什么数据库" --budget 2000
python memory.py recall 数据库 --mode hybrid --json
python memory.py synonyms --add "桌面=桌面目录,desktop"
python memory.py summary --file chat.txt --dry-run
python memory.py prune --dry-run
python memory.py restore --list
python memory.py stats --json
```

`context` 的预算是估算 token 上限，包括服务响应元数据；不是所有模型 tokenizer 的精确计数。
`--budget 0` 不限量。规则装不下就返回 `BUDGET_EXCEEDED`，需要增加预算，不能拿缺规则的片段继续执行。
不再声称未经 tokenizer 基准验证的 2%~3% 误差。

## 删除、归档和隐私

默认 forget 是归档，可以 restore；只有 `--purge` 才执行隐私删除。
建议按 ID 删除，过短的关键词会被拒绝。

```sh
python memory.py forget --id "记忆ID"
python memory.py forget --id "记忆ID" --purge
```

purge 会覆盖活跃条目、同文副本、相关归档/冲突、可追踪派生摘要，并清理受控 `.bak`、临时文件和向量索引。
不具备来源信息的旧摘要采取保守清理。库中保留无正文的遗忘标记，阻止相同内容被自动重新导入。
清理失败会报错，不会返回成功。外部复制、云同步、操作系统备份、磁盘取证恢复不在保证范围。
本地 JSON **不加密**；有基础凭据识别、拒绝写入和输出脱敏，但不能保证识别所有类型的秘密。

## 并发、自动维护和测试

`mutate(path, fn)` 把读改写放在一个锁区；线程重入锁与跨进程 OS 文件锁组合使用。
写盘先 fsync 再原子替换，旧版保留为 `.bak`；损坏备份也不可用时返回明确错误，不结束 MCP 服务。
CLI/MCP 工具调用按活动时间补做空闲归档，每个日历日最多一次；`prune --force` 可显式再清。
自动归档不是可靠的任务恢复执行器，不会代替宿主继续运行历史任务。

```sh
python -X utf8 tests/test_memory.py
python -X utf8 -m unittest discover -s tests -p test_service.py -v
node --test dsh-plugin/test/bridge.test.mjs
```

真实本地向量验收：安装 `[semantic]` 并预热模型后，设 `MEMORY_TEST_LOCAL_MODEL=1` 再运行第二条命令。
默认测试使用隔离合成数据，不读取开发者真实记忆。CI 覆盖 Windows、macOS、Linux 的无模型测试。

MCP 配置见 `examples/`；DSH 打包和 Python 运行时要求见 `dsh-plugin/README.md`。
修复对照与可重复验收流程见 `TESTING.md`。
本项目不会自动改造 Daisy 本体、替换其现用记忆或接管其对话队列。MIT 许可见 LICENSE。
