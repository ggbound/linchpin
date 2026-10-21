# Linchpin → Atlas 托管 Agent 平台改造方案

- 文档版本：v0.3（**ATLAS 改造全量收官**）
- 日期：2026-10-21
- 关联需求：`PRD-托管Agent平台.md`（产品代号 Atlas，v0.1 草案）
- 改造基线：Linchpin `linchpin-api` v0.7.0（alembic 迁移 0001–0018）
- 状态：A1–A8、B1–B15、C1、C2（方案 1 自研增量）、C3、M4（tool search FR-5.6、BYO executor FR-4.6）已全部实施（迁移 0016–0028 落地）；C2 方案 2 Temporal 评估已完成（见 C2 节评估记录）；M5 收口批次（限额配额 FR-11.2/11.3、OpenAPI 定制 FR-12.2，B16/B17）已实施（迁移 0029）；**M6 收官批次（BYOK 客户自带密钥 B18、egress curated 域名 B19、RLS 兜底 B20）已实施（迁移 0030）——至此本方案全部条目交付完毕，无挂起项。全量测试 1243 passed / 0 failed，迁移 0030 已应用真实 DB 并通过行为冒烟（GUC 过滤 + WITH CHECK 拒绝 + RESET 后 fail-open 不报错）**

---

## 1. 执行摘要

PRD §9.2 承认：引入开源组件后，M1 真正需要自研的只有三块——**四元组资源与 REST/SSE API 层、Agent loop 与沙箱胶水层、控制台会话视图**。Linchpin 正是这三块的现成实现，且覆盖度超出 PRD 预期（Webhook、web_search、Agent 版本化、Memory 版本审计这些 PRD 排在 P1 的能力均已存在）。

**结论：以 Linchpin 为底座，不重写。** 改动分三层：

| 层 | 性质 | 规模 |
|---|---|---|
| **A 语义对齐** | 改现有行为（状态机、SSE 帧、门控、幂等、错误码） | 8 项，均为小改 |
| **B 新增能力** | 新模块/新资源（compaction、artifacts、deployment、加密落库、审计） | 12 项 |
| **C 架构级** | 多租户、横向扩展、强隔离沙箱 | 3 项，决定平台成败 |

最大的三项工程：多租户改造的侵入面、compaction 从零实现、单实例编排器→分布式。

---

## 2. 差距分析总表

图例：✅ 已满足 / 🟡 部分满足或语义有差 / ❌ 缺失。**收官备注：下表已按最终交付状态全量清账（原差距条目的实施出处见 §3/§4/§5 各节实施记录）——除显式标注的"偏离/有界残余"外全部 ✅。**

| PRD 条目 | Linchpin 现状 | 结论 |
|---|---|---|
| FR-1.1 Agent CRUD + 版本化 + 会话锁版本 | CRUD+PATCH 快照 + `agent_versions` 表；会话按锁定的 `agent_version` 读快照（A1，快照缺失回退 agents 行） | ✅ |
| FR-1.2 Environment | CRUD+包声明+网络三档；资源规格三档（B3）；白名单由 per-env Squid egress proxy 执行（B4），curated 域名开关（B19） | ✅ |
| FR-1.3 会话引用 + 历史保留 | ✅（事件溯源天然跨轮保留） | ✅ |
| FR-1.4 归档/删除 + 数据清除 + 审计 | archive/DELETE 端点；terminate 清容器与目录；events/files 随会话删除一并清除（B12）；全量审计（B9） | ✅ |
| FR-2.1 Agent loop | ✅ | ✅ |
| FR-2.2 Compaction | 估算→轮边界截断→摘要事件闭环→重放跳过（B1，嵌套覆盖语义） | ✅ |
| FR-2.3 失败恢复 | provisioning/error 态 + 可重试错误指数退避 + retries_exhausted 收口（A2）；lease 心跳接管（C2 方案 1，对齐"worker 崩溃自动迁移"） | ✅ |
| FR-2.4 打断 | 流式中断（in-flight 模型请求取消）+ 可跟踪 exec 组杀（3 秒宽限）+ 部分文本保留收口（B2） | ✅ |
| FR-2.5 运行中引导 | running 时 user.message 排队 ✅；user.steer 安全点注入下一轮上下文（B6） | ✅ |
| FR-3.1 事件三接口 | 批量 POST 整批原子校验 + 状态门控矩阵（A4）、历史过滤、SSE（`/stream` 与 `/events/stream` 别名） | ✅ |
| FR-3.2 游标续读 | `?cursor=` + `Last-Event-ID` 头优先 + 帧 `id:`/`event:` 字段（A3） | ✅ |
| FR-3.3 事件持久化 + sevt_ id | 持久化 + 全局 `sevt_` event_id（A3，uuid4 hex 非ULID——免依赖，时序由 per-session seq 保证）；SSE 游标仍用 per-session seq（刻意保留，单调续读语义） | ✅（游标偏离已记录） |
| FR-3.4 delta 预览 | 内存 delta + 专用 `GET /{id}/streaming` 端点（帧不落库，符合 PRD） | ✅ |
| FR-3.5 Webhook | 完整实现（CRUD+HMAC+投递 worker+deliveries）+ 订阅类型过滤（A7） | ✅ |
| FR-4.1 沙箱隔离 | Docker 每会话一容器 + MicroVMSandbox（E2B driver，`isolation: "microvm"`，C3）；Firecracker raw 接入点是叶子变更 | ✅（macOS 宿主需外接 E2B 端点） |
| FR-4.2 内置工具在沙箱执行 | ✅（10 工具：8 基础 + save_artifact + spawn_subagent） | ✅ |
| FR-4.3 网络三档 | 三网络；limited=per-env Squid egress proxy 域名白名单（B4）+ curated 域名开关（B19）+ 解析后 IP 校验防 DNS 重绑定 | ✅ |
| FR-4.4 文件系统保留 | `/mnt/session/outputs` 跨轮保留 + watcher 登记；`/mnt/session/uploads` 挂载；`/workspace` 语义以容器复用实现（整个 FS 跨轮保留，与 PRD"默认不保留"相反，作为偏离记录——microvm/remote runtime 下 rw mount 为会话结束可见的有界降级） | ✅（偏离已记录） |
| FR-4.5 规格档位 | 1c2g/2c4g/4c8g 三档映射 nano_cpus/mem_limit（B3） | ✅ |
| FR-4.6 BYO executor | executor_nodes 注册表 + RemoteExecutorSandbox HTTP 代理 + executor daemon + 容量门控（C4） | ✅ |
| FR-5.1 builtin_toolkit 显式启用 | `BuiltinToolConfig`（enabled/permission_policy，per-tool） | ✅ |
| FR-5.2 MCP | ✅（linchpin-connector + mcp_tool_use/result 事件） | ✅ |
| FR-5.3 自定义工具回路 | 回路 + `idle(requires_action)` 对外状态 + 发送门控（A4） | ✅ |
| FR-5.4 工具审批 | PolicyEvaluator 三态 + `user.tool_confirmation` | ✅ |
| FR-5.5 web_search | 已有（Tavily） | ✅ |
| FR-5.6 tool search | tool_search 元工具 + 关键词检索延迟加载，threshold 门控 + metadata 快照（C5） | ✅ |
| FR-6.1 子代理 | 独立子会话+单向镜像+`thread_id`；`multi_agents` 花名册配置面（B13，未声明 fail-closed） | ✅ |
| FR-7.1 Memory | 三级资源+多会话挂载+不可变版本审计 | ✅ |
| FR-8.1 Deployment/cron | deployments 表 + croniter 调度器（SKIP LOCKED claim 多副本安全）+ pause/resume（B7） | ✅ |
| FR-9.1 save_artifact | 工具 + `/v1/artifacts` API + 去重登记（B5） | ✅ |
| FR-9.2 filesystem 浏览 | `GET /v1/sessions/:id/filesystem`（列表）+ `/raw`（下载）（B5） | ✅ |
| FR-9.3 文件上传 | POST /v1/files + `/mnt/session/uploads` 只读挂载 + 上传件可下载 | ✅ |
| FR-10.1 鉴权 | org/project/api_keys/scopes 全套（C1）+ per-key 限流（B16）+ RLS 兜底（B20）；IP 白名单留网关层（APISIX，PRD §9.2 同款） | ✅ |
| FR-10.2 Vault | Fernet+启动校验；BYOK 客户自带 KEK 登记（byok_kek 凭据类型，B18） | ✅ |
| FR-10.3 加密落库 | 会话级信封加密（x-events-encrypted，B8）+ BYOK 客户 KEK wrap（B18） | ✅ |
| FR-10.4 审计 | audit_log 全资源写入点 + NDJSON 导出（B9） | ✅ |
| FR-10.5 数据删除 | terminate+rmtree + events/files 随会话删除清除（B12） | ✅ |
| FR-11.1 用量计量 | span 带 model_usage + `sessions.usage` 累计 + 会话详情内嵌 | ✅ |
| FR-11.2/11.3 计费限额 | API 侧自研（B16）：per-key 滑动窗口限流 + project 并发/月 token 配额；网关层 APISIX 配方见 docs/limits.md；OpenMeter+Lago 计费面留网关/商务侧（刻意不引入） | ✅（限额面） |
| FR-12.1 SDK | TS SDK（零依赖 fetch + SSE/NDJSON）+ Python SDK（零依赖 urllib + 流式） | ✅ |
| FR-12.2 OpenAPI/错误码 | 统一错误信封（A5）+ custom_openapi（B17）+ docs/error-codes.md + docs/limits.md | ✅ |
| FR-12.3 控制台 | 6 组页面 + 会话实时 SSE 视图 + 用量看板（B14） | ✅ |
| §4.1 状态机 | 5 值 + provisioning/error + requires_action 对外化 + retries_exhausted（A2） | ✅ |
| 非功能 | Prometheus/OTel（B15）；events 保留期 90d 可配（B12）；advisory lease 多副本（C2）；RLS 租户兜底（B20） | ✅（多区域未做——无对应 PRD 条目，触发条件见 C2 评估第 3 点） |

---

## 3. A 类：语义对齐（改现有行为）

### A1. 会话锁定 Agent 版本（FR-1.1）

- 现状：`run_session` 每轮 `load_agent(agent_id)` 读 agents 最新行；`agent_versions` 快照表已存在。
- 改动：
  1. `sessions` 表加列 `agent_version INT NOT NULL`；创建会话时取 `agents.version` 当前值写入。
  2. `load_agent` 改为 `load_agent_at_version(agent_id, version)`：优先读 `agent_versions` 快照，快照缺失（v1 历史数据）回退 agents 行。
- 验收：会话运行期间 PATCH agent 不影响进行中/恢复中的会话。

- 实施记录：零迁移——`sessions.agent_version` 自迁移 0001 即已存在，只改读路径。`orchestrator.load_agent_at_version(agent_id, version)` 优先读 `agent_versions` 快照、快照缺失回退 agents 行；`run_session` 每轮按会话锁定的版本取 agent。

### A2. 状态机对齐（§4.1 / FR-2.3）

- 现状：创建即 `running`（`routes/sessions.py:789`）；失败→`failed` 终态（`orchestrator.py:1117-1136`）；等待自定义工具/审批期间对外仍 `running`。
- 改动：
  1. `SessionStatus` 增加 `provisioning`、`error`：POST /sessions 先插 `provisioning`，容器就绪并发出首个事件后转 `running`；沙箱创建失败期间前端可见。
  2. **requires_action 对外化**：进入 `wait_for_confirmation` / `wait_for_custom_tool_result` 前发状态事件并将会话对外置为 `idle(requires_action)`（编排器内部等待机制不变，仅对外状态语义对齐）；收到 resolution 后转回 `running`。
  3. 失败重试：运行期异常按可重试/不可重试分类（可重试：ProviderError 中 Retryable 类、沙箱暂不可用等；不可重试：凭据缺失、上下文非法）。可重试错误→`error` 态 + 由恢复循环指数退避重拉 `run_session`（`retry_count` 上限 3）；耗尽后发 `session.status(idle, stop_reason="retries_exhausted")`——按 PRD 收口为 idle 而非 failed，会话仍可人工续跑。
  4. `sessions` 表加 `retry_count INT NOT NULL DEFAULT 0`；EVENT_TYPES 增加统一 `session.status` 事件（data: status / stop_reason），保留现有 `session.status_idle` 等细分事件一个过渡期。
- 验收：杀掉 API 进程再拉起，运行中会话自动接管；模拟模型持续 5xx，最终收到 idle(retries_exhausted) 而非 failed。

- 实施记录（迁移 0016）：`sessions` 加 `retry_count INTEGER NOT NULL DEFAULT 0`、`stop_reason TEXT`。创建流程行为修正：先落 `provisioning` 行（INSERT RETURNING）再做 `ensure_image`/容器创建，失败经 `_mark_provisioning_failed` 收口——前端自创建请求返回即可见 provisioning。requires_action 对外化落地：等待审批/自定义工具结果前 `transition(idle, stop_reason="requires_action")` 并发事件，收到 resolution 转回 running。重试：可重试异常置 `error` 态、按指数退避（`min(60, 5 * 2^retry_count)` 秒）重拉 `run_session`，耗尽（MAX_RETRIES）后收口 `idle(stop_reason="retries_exhausted")`；成功轮重置 retry_count。统一 `session.status` 事件已入 EVENT_TYPES。

### A3. SSE 帧格式与游标对齐（FR-3.2/3.3）

- 现状：`GET /v1/sessions/{id}/stream`（`routes/sessions.py:1863-1940`），`?cursor=` 续读；帧只有 `data:`，无 `id:`/`event:`。
- 改动：
  1. 帧补齐：`id: <seq>`、`event: <type>`、`data: {...}`。
  2. stream 端点支持 `Last-Event-ID` 请求头，优先级高于 `?cursor=`；新增路径别名 `GET /v1/sessions/:id/events/stream`（旧 `/stream` 保留）。
  3. 全局事件 id：`events` 表加 `event_id TEXT UNIQUE`（`sevt_` + ULID，DB 默认值生成），放入事件 data 与 GET 响应；**SSE 游标仍用 per-session seq**（保证单调递增续读语义）。
- 验收：断线重连带 Last-Event-ID 无丢失、无重复。

- 实施记录（迁移 0017）：`events.event_id TEXT NOT NULL UNIQUE`，DB 默认值 `'sevt_' || replace(gen_random_uuid()::text, '-', '')`（uuid4 hex 而非原计划的 ULID——免第三方依赖，时序语义由 per-session seq 保证）+ 存量回填。帧格式 `_sse_frame` 补齐 `id:`（=seq）/ `event:`（=type）/ `data:`，`event_id` 随 data 下发；`Last-Event-ID`（数字即 seq）优先于 `?cursor=`，原始 cursor 仍兼容旧客户端；路径别名 `GET /v1/sessions/:id/events/stream` 已加。断线续读游标仍用 per-session seq。

### A4. 批量原子校验 + 发送门控（FR-3.1 / FR-5.3）

- 现状：`post_events`（`routes/sessions.py:1762-1857`）仅校验存在与未归档，逐条处理。
- 改动：
  1. 先整批校验（schema、类型白名单、状态门控），任一失败整批 400 `invalid_request`；全过再统一写入。
  2. 状态门控矩阵：

| 会话对外状态 | 放行类型 | 其余 |
|---|---|---|
| provisioning | 仅 user.interrupt | 409 conflict |
| running | user.message / user.steer / user.interrupt | 409 |
| idle(end_turn) | user.message / user.interrupt | 409 |
| idle(requires_action) | **仅** user.custom_tool_result / user.tool_confirmation / user.interrupt | 409 |
| error / terminated / failed | 仅 user.interrupt（error 态允许取消等待） | 409 |
| archived | 409（现状保留） | — |

- 验收：requires_action 期间发 user.message 返回 409 且整批不落库。

- 实施记录：整批校验通过后统一写入；门控矩阵按上表落地。`user.steer` 已入 EVENT_TYPES 并在 running 档放行；编排器内的 steer 注入执行留 B6。

### A5. 统一错误码（§6.4 / FR-12.2）

- 现状：各路由手写 `detail={"error": ..., "message": ...}`，字段不齐。
- 改动：`main.py` 注册全局 exception handler；所有 4xx/5xx 统一为 `{"error": <code>, "message": ..., "detail": ...}`，code 取值对齐 PRD §6.4（`invalid_request / unauthorized / forbidden / not_found / conflict / rate_limited / quota_exceeded / internal / environment_not_ready`）。现有 `environment_in_use` 等 code 映射到 `conflict`（或保留专名，评审定）。

- 实施记录：`main.py` 全局 handler `_error_envelope`——`detail` 为含 error/message 的 dict 时上移：信封为顶层 `{"error", "message", "detail"}`；无额外键时 `detail: null`；带额外键（`limit_bytes` / `blocking_session_id` / `supported_versions` 等）时额外键保留在 `body["detail"]` 下。全部 4xx/5xx 断言已随信封语义迁移完成。

### A6. archive 幂等化

- 现状：重复 archive 返回 409（`routes/sessions.py:1672-1676`），与 agents/environments 的幂等 archive 不一致。
- 改动：已归档再次 archive 返回 200 + 当前状态。

- 实施记录：重复 archive 返回 200 + 当前状态，与 agents/environments 的幂等语义对齐。

### A7. Webhook 触发对齐（FR-3.5）

- 现状：`append_event` 末尾全量入队（`events.py:169-181`）。
- 改动：webhooks 表加 `events TEXT[]`（订阅类型）；入队按订阅过滤；PRD 语义（idle/failed/requires_action）作为默认订阅模板。

- 实施记录：零迁移——`webhook_endpoints.enabled_events` 自迁移 0009 即以 JSONB 存在（非原规划的 TEXT[]）。订阅过滤 = SQL 侧 `enabled_events ? $1 OR jsonb_array_length(enabled_events) = 0` + Python 侧复核；空订阅按默认模板 `DEFAULT_WEBHOOK_EVENTS = {session.status_idle, session.error, session.requires_action}`（取实际发射的类型）而非全量。
- 发现：`session.created` / `session.failed` / `session.terminated` 在 WEBHOOK_RELEVANT_EVENTS 中有定义但无任何发射点（EVENT_TYPES 中本就没有这三个类型），默认订阅的 failed 语义由实际发射的 `session.error` 承担；代码注释已标注留作前向兼容。
- 实施中修正：过滤 SQL 原用 `cardinality(enabled_events)`，该函数仅接受 anyarray，对 JSONB 列运行时会报 `function cardinality(jsonb) does not exist`（测试因 mock 不落真实 PG 而暴露不了，异常又被吞掉，webhook 会静默失效）——已改为 `jsonb_array_length`。

### A8. 幂等键（FR-1.3 验收）

- 改动：新表 `idempotency_keys(key TEXT PK, endpoint TEXT, request_hash TEXT, response_status INT, response_body JSONB, created_at)`；POST /v1/sessions 与 POST /v1/sessions/:id/events 接 `Idempotency-Key` 头，命中且 request_hash 一致直接回放上次响应；TTL 24h 由 lifespan 清理。

- 实施记录（迁移 0018）：`idempotency_keys(key TEXT PK, endpoint, request_hash, response_status, response_body JSONB, created_at)` + created_at 索引；POST /v1/sessions 与 POST /v1/sessions/:id/events 接 `Idempotency-Key` 头，命中且 request_hash 一致回放上次响应、不一致返回 409 conflict；无 key 时短路不触 DB；TTL 24h，lifespan 内每小时清理。
- 实施中发现并修复产品 bug：spawn_thread 进程内直调 `create_session` 不走 FastAPI DI，`Header(default=None)` 的 FieldInfo 默认值 truthy 会误入 replay 分支触 DB——调用点显式传 `idempotency_key=None`。

---

## 4. B 类：新增能力

### B1. 上下文压缩 Compaction（FR-2.2，P0 最重新增项）

- 现状：`build_context`（`orchestrator.py:366-519`）全量重放，无窗口管理。
- 设计：
  1. **预算估算**：按事件 payload 字符量粗估 + provider usage 回报校准，维护会话级估算 token 数（可存 `sessions.context_estimate`）。
  2. **触发**：估算值 ≥ 模型窗口 × 0.8 时，在下一轮构建上下文前先压缩。
  3. **压缩过程**：取未被覆盖的历史（排除已有 compacted 摘要区间）→ 截断点必须落在轮边界（不得切断 tool_use/tool_result 配对）→ 调 `provider.send`（非流式）生成摘要 → 写新事件 `session.compacted`（data: summary、covered_seq_start/end、模型与 token 记账）。
  4. **重放规则**：`build_context` 遇 `session.compacted` 事件 → 跳过 covered 区间内事件，注入摘要为系统上下文。摘要本身也是事件 → 事件溯源闭环，恢复/重放天然一致。
  5. 不压缩内容：memory 挂载块（`_build_memory_system_block`）、系统提示词、最后一轮完整消息。
- 新增：`app/compaction.py`；EVENT_TYPES 加 `session.compacted`。
- 验收：>200 步长会话不触顶；压缩后继续对话行为连贯；进程重启重放一致。

- 实施记录（迁移 0019）：`sessions.context_estimate INTEGER NOT NULL DEFAULT 0`；新增 `app/compaction.py`（estimate_tokens CJK 感知粗估、get_context_window 三级回退：`ModelConfig.context_window` → `LINCHPIN_CONTEXT_WINDOW` env → 128k 默认、maybe_compact、轻量 transcript 渲染器）。
- 压缩语义：触发条件为估算 ≥ max(窗口×0.8, 2000) 且 covered 区间 ≥ 2 事件；边界 = 最后一条未覆盖 `user.message` 的 seq − 1（最后一轮永不压缩、tool 配对永不切断）；**嵌套覆盖**——新压缩把上一份摘要 + 新覆盖历史一起重新摘要，生效摘要恒为 `covered_seq_end` 最大的 compacted 事件，replay 只跳过 `seq ≤ covered_end`（旧 compacted 事件随之折叠）；`session.compacted` 事件含 summary / covered_seq_start/end / model / usage 记账。
- 接入点：`run_session` 将 `get_provider` 上移到 API key 解析后、`build_context` 前调 `maybe_compact`（整体 try/except，失败退化为不压缩，绝不打断 agent loop）；`build_context` 注入 `<session_summary>` 系统消息并跳过 covered 区间；每轮 `update_usage` 后以真实 usage 四键之和 GREATEST 校准 `context_estimate`。
- 测试：`tests/test_compaction.py` 20 例（估算/窗口回退/边界选取/嵌套重摘要/provider 失败容错/无 DB pool 容错/replay 注入与跳过/多摘要最新生效/JSON 字符串 payload 兼容），全量 766 passed / 0 failed。

### B2. 打断强化（FR-2.4）

- 现状：仅两处轮边界检查（`orchestrator.py:855-859, 1021-1027`）；流式循环（939-989）内无检查；`sandbox.exec`（`sandbox.py:331-350`）无停止能力。
- 改动：
  1. **流式中断**：进程内 per-session 中断标志（post_events 收到 interrupt 时置位 + NOTIFY）；`send_streaming` 循环每 chunk 检查标志，命中即取消请求（httpx stream aclose）。
  2. **沙箱命令停止**：`DockerSandbox.exec` 改为可跟踪 exec（记录容器内主进程 PID）；新增 `kill_exec(pid, grace=3.0)`：容器内 `kill -TERM <pid>`，3 秒后存活则 `kill -KILL`。
  3. **收口**：已生成的部分文本落库为 `agent.message`，本轮以 idle(end_turn) 收口。
- 验收：长 bash 命令运行中发 interrupt，3 秒内命令终止、会话 idle、部分输出保留。

- 实施记录（无迁移变更）：新增 `app/interrupts.py` —— 进程内 per-session 标志注册表（`request_interrupt` 置位 / `is_interrupted` 读 / `event` 返回 Event 供竞速 wait / `clear_interrupt` 消费即清除）。`post_events` 在主 session 与 ancestor 镜像循环里持久化 `user.interrupt` 的同时置位对应标志（NOTIFY 逻辑不变）。
- 流式中断：`run_session` 流式循环每 chunk 顶部检查标志，命中即 `stream_interrupted=True` + break，finally 的 `stream_gen.aclose()` 关闭 httpx 流取消在途请求；收口为已积累文本落库 `agent.message` → `clear_interrupt` → `transition(idle, stop_reason="end_turn")` + `session.status` + `session.status_idle` 三事件。轮边界的既有 `check_for_interrupt` → `handle_interrupt` 路径（idle(interrupt)）保持不动；`handle_interrupt` 开头补 `clear_interrupt`。
- 可跟踪 exec：`DockerSandbox.exec` 改走低层 API——命令包装为 `bash -c "setsid bash -c 'echo $$; <cmd>'"`（inner bash 经 setsid 成会话组长，组杀可级联子进程），`exec_create` + `exec_start(stream=True, demux=True)` 流式读出，首行解析容器内主进程 PID 并注册 `_active_pids[container_id]`（PID 分块缓冲解析；首行非整数时整体回退为普通输出、不注册）；exec 结束按值匹配 pop 注册表。`active_exec_pid` 供竞速分支查询。
- `kill_exec(container_id, pid, grace=3.0)`：经 `_exec_raw`（绕过注册表的裸 exec_run）容器内组杀 `kill -TERM -<pid>` + `kill -TERM <pid>` → sleep(grace) → `kill -0` 探活 → 仍存活则组杀 KILL；docker 异常吞掉返回 False（best-effort）；finally 清自身注册项（不误删并发新 exec 的注册）。
- 工具循环竞速：`dispatch_tool` 与 `interrupts.event(session_id).wait()` 以 `asyncio.wait(FIRST_COMPLETED)` 竞速。interrupt 胜出（同时完成时 interrupt 优先）：builtin 工具先 `load_session` 取 container_id + `active_exec_pid` 命中则 `kill_exec`；随后 reap dispatch（`dispatch_tool` 自吞异常返回 error JSON，必能完成），custom 哨兵 `__custom_tool_needs_client__` 改写为 `{"error": "Tool call interrupted"}` + `agent.tool_result`，补齐 tool_use/tool_result 配对后 clear → idle(end_turn) 三事件收口。tool 胜出：`int_waiter.cancel()` 走原路径。
- 标志消费点共 6 处（防泄漏关键：`check_for_interrupt` 只看最新一条事件，分支内 append 的 tool_result 会把 interrupt 屏蔽，标志若不清会泄漏到下一轮首 chunk 造成静默断流）——`handle_interrupt` 开头、流式断流收口、竞速 interrupt 胜出分支、always_ask deny 分支、custom_tool_result 分支、end_turn 收口处无条件 clear（兜住最后一条 tool_result 与收口之间的微窗口）。
- 测试：`tests/test_interrupts.py` 12 例（置位/幂等/隔离/清除/wait 唤醒与单例）；`tests/test_sandbox.py` TestExec 重写为流式 mock 并新增 PID 分块/非 PID 回退/无换行/stderr 收集 5 例、新增 TestKillExec 4 例（升级强杀/探活已死不杀/异常 False/注册表选择性清理）与 TestActiveExecPid 2 例；`tests/test_orchestrator.py` 新增断流中断收口（部分文本保留、idle(end_turn)、无泄漏）、竞速 interrupt 胜出（kill_exec 以 tracked PID 调用、配对完整、idle(end_turn)）与 tool 正常胜出（无 kill、原路径）3 例。全量 792 passed / 0 failed。

### B3. 沙箱规格档位（FR-4.5）

- `Environment.config` 加 `resources: {tier: "1c2g" | "2c4g" | "4c8g"}`（默认 1c2g）；档位映射硬编码（1c2g→nano_cpus=1e9, mem_limit="2g"…）；`DockerSandbox.create` 透传 `nano_cpus` / `mem_limit`。
- 工作量最小的一项，可与 A2 provisioning 状态同批做。

- 实施记录：三档映射（1c2g/2c4g/4c8g → nano_cpus/mem_limit）硬编码于 `sandbox.py`，`DockerSandbox.create` 透传；`Environment.config.resources` 为 JSONB 内字段，无列变更（原规划迁移 0023 的 resources 部分随之取消）。

### B4. 网络白名单（FR-4.3）

- 现状：`linchpin-limited` 网络 internal=True 纯断网；`LimitedNetworking.allowed_hosts` 字段存在但未消费（`models.py:360-376`）。
- 设计：新增 egress proxy 容器（轻量 Go/Squid HTTP(S) 代理）；limited 档容器默认路由指向 proxy；proxy 按 `allowed_hosts` 过滤域名，解析后校验目标 IP（防 DNS 重绑定）；proxy 配置由环境派生（沿用 `ensure_image` 的派生思路，configmap 挂载）。
- docker-compose 增 proxy 服务；none/unrestricted 档不变。

- **实施记录（✅ 已完成，零迁移）**：
  - egress proxy 模块（[egress.py](linchpin-api/app/egress.py)）：**每环境一个 Squid proxy 容器**（非规格文字的单全局 proxy——多环境共用一个白名单池是特权提升，安全不正确，作为偏离项记录）。容器名 `linchpin-egress-<env 去横线前 12 位>`，双网络接入：`linchpin-limited`（internal，会话侧）+ `linchpin-open`（出网侧）——proxy 是 limited 档唯一 egress 咽喉点，会话容器无法直连外网。
  - 配置派生 + 幂等生命周期（`ensure_image` 同思路）：内容哈希 `sha256(json({env, hosts}))[:16]` 存容器 label `linchpin.egress.config_hash`；同 hash+running 复用 / 同 hash+stopped start / 异 hash remove 后重建。镜像 `ubuntu/squid:24.04_stable`（`LINCHPIN_EGRESS_PROXY_IMAGE` 可覆盖，[docker-compose.yml](docker-compose.yml) 已注入默认值）。
  - Squid 策略即安全语义（`_SQUID_CONF_TEMPLATE`）：Safe_ports(80/443) + CONNECT 仅 443 → `acl private_dst dst <私网/环回/链路本地/CGNAT>` deny（**Squid 服务端解析主机名，dst ACL 基于解析后 IP 匹配 = DNS 重绑定防护，对应风险表 #5**）→ allow `allowed_hosts`（`*.x.com`→前导点子域匹配、裸域名精确、IPv4 literal 直通）→ deny all。空白名单也装 proxy（dstdomain 指向 `.invalid.allowlist`，行为=断网但拓扑一致，环境白名单扩充无需改拓扑）。
  - 配置注入用 `put_archive`（in-memory tar）而非规格中的 configmap/bind mount：API 容器内运行时 bind mount 的 host 路径指向 daemon host 文件系统（docker-in-docker 经典坑），put_archive 无此问题且保持自托管单机可运行。偏离项记录。
  - 接线（[sessions.py](linchpin-api/app/routes/sessions.py)）：limited 档在 `sandbox.create` 前 `proxy_env_from_networking` 引导 proxy，产出 `HTTP(S)_PROXY` env（NO_PROXY 排除 metadata 端点 169.254.169.254）经新增的 `env` 参数（[sandbox.py](linchpin-api/app/sandbox.py) protocol+实现）注入会话容器；失败走 `_mark_provisioning_failed(egress_proxy_failed)` + 500。none/unrestricted 档零改动。
  - metrics（[metrics.py](linchpin-api/app/metrics.py)）：沙箱容器 gauge 排除 `linchpin-egress-` 前缀（基础设施不冒充沙箱）。
  - 未做（M4）：compose 静态 proxy 服务（per-env 动态管理取代之）。`allow_mcp_servers` / `allow_package_managers` curated 域名集合已由 M6 补齐（见 B19）。
  - 测试：[test_egress.py](linchpin-api/tests/test_egress.py) 18 例（squid.conf 渲染与 ACL 顺序 / env vars / hash 确定性 / 容器名稳定 / 生命周期 6 态含 pull-retry 与异常映射 / to_thread 卸载 / end_to_end / metrics 排除）+ [test_sessions.py](linchpin-api/tests/test_sessions.py) limited 用例扩展 proxy env 断言。全量 956 passed / 0 failed。

### B5. Artifacts 与文件系统浏览（FR-9.1/9.2）

1. **save_artifact 工具**：`tools.py` 注册表加第 9 个 handler——把沙箱内指定路径文件复制入 FileStore 并登记 `files` 表（`source='artifact'`），返回 file id。
2. **API**：`GET /v1/artifacts?session_id=`（files 表过滤 source='artifact'）、`GET /v1/artifacts/:id/content`（复用现有 content 通道）。
3. **filesystem 浏览**：`GET /v1/sessions/:id/filesystem?path=/`（docker exec `ls -la` JSON 化，逐层）；`GET .../filesystem/raw?path=`（docker cp 流式下载）。
4. 语义对齐：用户上传件放开下载限制（PRD 未禁）；上传挂载路径调整为 `/mnt/session/uploads`；新增 `/workspace` 路径约定（容器内工作目录，是否跨轮保留作为 environment 配置项，默认不保留——需要把"工作目录"与容器复用解耦，或将容器改为按轮重建+outputs/uploads/memory 三挂载点延续状态，评审定）。

- **实施记录（✅ 已完成，迁移 0024）**：
  - `save_artifact` 工具（[tools.py](linchpin-api/app/tools.py) `_save_artifact`，注册表第 9 个 handler）：`copy_from_container` → `FileStore.write` → `files` 行（`source='artifact'`、`scope_type='session'`、`downloadable=TRUE`），按 (session, sha256) 部分唯一索引去重（同内容重存返回既有行）；超 `get_max_bytes()` 上限返回错误 JSON。`orchestrator.dispatch_tool` 为 builtin 工具贯通 `context={"session_id", "project_id"}` 归属链。
  - API（[routes/artifacts.py](linchpin-api/app/routes/artifacts.py)）：`GET /v1/artifacts`（cursor 分页、`source='artifact'` 过滤 + 可选 `session_id`）、`GET /v1/artifacts/{id}`、`GET /v1/artifacts/{id}/content`（复用 files 下载通道）。
  - filesystem 浏览：`GET /v1/sessions/{id}/filesystem?path=`（`ls -la` JSON 化逐层）、`GET .../filesystem/raw?path=`（流式下载）；`sandbox.py` 新增 `copy_from_container` / `list_dir`。
  - 语义对齐：上传件 `downloadable` 放开；`/mnt/session/uploads` 作为上传默认挂载路径（B5 前已随 C1 批次落地）。
  - **已知错误修复（迁移 0024）**：0003 的 `files.source CHECK` 只允许 `('upload','deliverable')`，`save_artifact` 写 `'artifact'` 在真实 DB 必现 CheckViolation（单测 mock DB 不暴露）——0024 放宽 CHECK 加 `'artifact'`，已跑通。
  - 测试：[test_artifacts.py](linchpin-api/tests/test_artifacts.py) 23 例（工具层 / HTTP 端点 / filesystem / 归属子查询）。

### B6. Steer（FR-2.5）

- EVENT_TYPES 加 `user.steer`；running 期间放行（A4 门控矩阵）。
- 编排器在工具块间安全点（现 1021-1027 中断检查处）同时检查 steer 事件，命中则把 steer 内容注入下一轮上下文的当前轮消息流尾部（以用户身份、标 injected=true）。不做模型流中途注入（破坏消息完整性，成本高收益低）。

- **实施记录（✅ 已完成，零迁移）**：
  - 门控（[routes/sessions.py](linchpin-api/app/routes/sessions.py) 批量原子校验矩阵）：`running` 及 transitional 状态的 allowed 集合加 `user.steer`（A4 矩阵单项扩展，矩阵语义由 A4 测试覆盖）。
  - 注入（[orchestrator.py](linchpin-api/app/orchestrator.py) `build_context`）：`user.steer` 重放为 user-role 消息、`<steer injected="true">` 包裹（`content` 键优先回退 `text`，file sources 经 `_resolve_file_sources` 解析）——下一轮工具轮与 steer 后续轮共享同一重放路径。
  - 安全点：end_turn 收口处以本轮起始 watermark（`turn_start_seq`，每轮重取）调 `has_pending_steer`（seq > watermark 的未消费 steer；查询失败 fail-closed 视作无 steer，与 `maybe_compact` 同 advisory 契约）——命中则不 idle、继续循环让 build_context 重放 steer；interrupt 收口路径（流式断流 / 竞速胜出）有意跳过该检查，interrupt 优先于 steer。
  - 未做：模型流中途注入（规格即排除）；steer 消费无显式标记——靠 watermark 每轮重取覆盖已消费事件，无泄漏路径。
  - 测试：[test_orchestrator.py](linchpin-api/tests/test_orchestrator.py) 7 例（has_pending_steer 2 / build_context 重放 3 / 安全点 continue-vs-idle 2）。

### B7. Deployment / cron（FR-8.1）

- 新表 `deployments(id, agent_id, environment_id, cron TEXT, timezone TEXT, initial_events JSONB, status TEXT paused/active, next_run_at TIMESTAMPTZ, created_at, archived_at)`；会话表加 `deployment_id`（运行历史即按此过滤的 sessions）。
- 端点：POST/GET/PATCH/DELETE `/v1/deployments`、POST `/v1/deployments/:id/pause|resume`。
- 调度器：lifespan 内 asyncio 循环扫 `next_run_at <= now()`（SELECT ... FOR UPDATE SKIP LOCKED 防重复触发）→ 创建会话（带 initial_events）→ 计算下次 cron 时间。M3 起步足够；M4 随 C2 迁 Temporal schedule。
- cron 解析用 croniter（含时区）。

- **实施记录（✅ 已完成，迁移 0025）**：
  - 迁移 [0025_deployments.py](linchpin-api/alembic/versions/0025_deployments.py)：`deployments` 表（id UUID PK / project_id / agent_id / environment_id / cron / timezone / initial_events JSONB / status CHECK paused|active / next_run_at / created_at / archived_at）+ `deployments_due_idx`（next_run_at，WHERE status='active' AND archived_at IS NULL，调度器扫描专用）+ `sessions.deployment_id`（nullable FK→deployments ON DELETE SET NULL）+ `sessions_deployment_idx`（部分索引）。已应用真实 DB。
  - 服务层（[deployments.py](linchpin-api/app/deployments.py)）：`validate_timezone`（ZoneInfo）/ `validate_cron`（croniter）/ `compute_next_run`（croniter + tz；paused/archive 不计算）/ `validate_initial_events`（只允许 `user.message`/`user.steer`，单条非空）；`claim_due_deployments`（`SELECT ... FOR UPDATE SKIP LOCKED`，单连接事务内 claim——多进程安全）；`trigger_deployment`：**spawn_thread 先例复用**——直调 `create_session` 需显式传 `idempotency_key=None, events_encrypted=False`（防 FieldInfo 泄漏，A8/B8 同款坑）+ 伪 `StarletteRequest` + 从 agents JOIN projects 派生系统 AuthContext；`deployment_id` 偏离主路径字段 create 后 `UPDATE sessions SET` 补写；initial_events 经既有批量校验器写入；触发完 `_recompute_next_run`（部署行 PATCH 期间 claim 与 recompute 竞态由行锁 + 状态复核化解）。`deployments_scheduler_loop`：lifespan asyncio 任务，1s 轮询，`LINCHPIN_DEPLOYMENTS_SCHEDULER` 开关（默认 true，[conftest](linchpin-api/tests/conftest.py) 置 false 防测试抖动）。
  - API（[routes/deployments.py](linchpin-api/app/routes/deployments.py)）：POST/GET 列表（分页+agent_id 过滤）/GET 单个/PATCH（cron/timezone 变更即时重算 next_run_at）/DELETE（archive 语义）/POST `:id/pause|resume`（resume 重算 next_run_at）；归属校验走 agent→project 归属子查询。
  - 模型（[models.py](linchpin-api/app/models.py)）：`DeploymentCreate/Update/Response` 4 模型；`SessionResponse` 加 `deployment_id`；[sessions.py](linchpin-api/app/routes/sessions.py) list_sessions 加 `deployment_id` 过滤（运行历史查询）。
  - 接线（[main.py](linchpin-api/app/main.py)）：lifespan 启动/取消 `deployments_scheduler_task`（`scheduler_enabled()` 门控）+ v1 router 挂载。
  - 测试：[test_deployments.py](linchpin-api/tests/test_deployments.py) 27 例（迁移形态 / CRUD 全量 / cron-timezone 校验与 next_run_at 重算 / pause-resume / 权限归属 / 调度器 claim-trigger-recompute 全链路 / FieldInfo 防御）。全量 956 passed / 0 failed。

### B8. 加密落库（FR-10.3）

- 创建会话请求头 `x-events-encrypted: true` 时启用：会话生成 `data_key`（信封加密），业务内容事件（user.message / agent.message / agent.thinking / tool_result 等）payload 以 data_key 加密后落库（payload 内存 `{"enc":"v1","ct":...}`）；控制类事件（session.* / span.* / thread.*）明文，保障调度。
- 客户密钥登记走 vault（复用 Fernet 基建）或 KMS；GET events 按会话解密返回。
- 注意与 A3 的 `event_id`、cursor 兼容（加密只涉及 payload）。

- **实施记录（✅ 已完成，迁移 0023）**：
  - 迁移 [0023_sessions_events_encrypted.py](linchpin-api/alembic/versions/0023_sessions_events_encrypted.py)：`sessions.events_encrypted BOOLEAN NOT NULL DEFAULT false` + `sessions.data_key TEXT`（wrapped 副本落库，重启后可恢复解密）。
  - 信封加密（[event_crypto.py](linchpin-api/app/event_crypto.py)）：`generate_data_key`（Fernet）/ `wrap_data_key` / `unwrap_data_key`（主密钥 = `VAULT_ENCRYPTION_KEY` 的 EncryptionService 单例，与 vault 同基建）/ `encrypt_payload` / `decrypt_payload`（信封 `{"enc":"v1","ct":...}`；无 key、非信封 payload 全部原样透传，向后兼容）/ `is_control_event`（`session.*` / `span.*` / `thread.*` 前缀）/ 进程内 `_data_key_cache`（memoize，terminate 时清除）。
  - 写路径：POST /v1/sessions 收 `x-events-encrypted: true` 头 → 生成 data_key、wrapped 落库、明文 key 进缓存、审计记 flag；`events.append_event` 在锁外密封业务事件 payload，控制事件明文保调度；`Event` 模型与 webhook fan-out 始终返回明文（解密只发生在 DB 边界两侧）。
  - 读路径：`get_events` 按会话页级取一次 key 解密（SSE stream 复用同一函数自动覆盖）；`orchestrator.build_context` / `wait_for_confirmation` / `wait_for_custom_tool_result` 三处消费点解密；compaction 读取后解密（Record 转 dict 携带解密 payload）。grep 全量核对 `FROM events` 9 处消费点，无遗漏。
  - 生命周期：`DELETE /v1/sessions/{id}`（terminate）清除进程内缓存；retention 按期删行（密文随行删除，无额外操作）。
  - 陷阱记录：内部直调 `create_session`（spawn_thread）不经过 FastAPI DI，`Header()` 的 FieldInfo 默认值会作为 truthy 实参泄漏——显式传 `events_encrypted=False`（A8 同款坑）。
  - 测试：[test_event_crypto.py](linchpin-api/tests/test_event_crypto.py) 18 例（分类 / 密钥生命周期 / 信封形状与透传 / 缓存与 DB 回源）+ [test_events.py](linchpin-api/tests/test_events.py) 追加 5 例（密封落库密文、控制事件明文、GET 解密）+ [test_sessions.py](linchpin-api/tests/test_sessions.py) 追加 3 例（header 启用、默认明文、terminate 清缓存）；conftest autouse 隔离 fixture（清缓存 + crypto 模块 DB 访问 mock）。
  - 未做（M3+ 增量）：客户自带密钥（BYOK 走 vault/KMS 登记）——已由 M6 补齐（`byok_kek` 凭据类型 + `x-kek-credential-id` 头，见 B18）。

### B9. 统一审计日志（FR-10.4）

- 新表 `audit_log(id, at, actor, action, resource_type, resource_id, detail JSONB)`。
- 写入点：全部资源 CRUD/archive/delete；凭据解析命中（`resolve_api_key` 成功，记 vault/credential id 不记值）；审批决定（tool_confirmation）；会话删除与数据清除。
- 导出：`GET /v1/audit?from=&to=`（NDJSON 流式）。

- **实施记录（✅ 已完成，迁移 0021）**：
  - 迁移 [0021_audit_log.py](linchpin-api/alembic/versions/0021_audit_log.py)：`audit_log(id, at, project_id, actor, action, resource_type, resource_id, detail JSONB)` + 3 索引（at DESC、(project_id, at DESC)、(resource_type, resource_id)）。
  - 写入（[audit.py](linchpin-api/app/audit.py) `record_audit`）：best-effort——内部 try/except，审计失败只 log 绝不打断业务路由；actor 推导 `apikey:<id>` / `bootstrap`；project 归属取 `ctx.project_id`。
  - 写入点（全量 grep 核对）：agents created/updated/archived；environments created/archived/deleted；sessions created（含 `events_encrypted` flag）/archived/deleted（数据清除）；vaults 全套 6 处；files uploaded/deleted；memories written/deleted、memory_versions redacted、memory_stores created/archived/deleted；webhooks 3 处；skills 2 处；`credential.resolved`（[credentials.py](linchpin-api/app/credentials.py) 3 处命中路径，只记 vault/credential id 与 provider，不记值）；`tool.confirmation_decided`（[orchestrator.py](linchpin-api/app/orchestrator.py) 审批决定）；`api_key.created/revoked`（[admin.py](linchpin-api/app/routes/admin.py)）。
  - 导出：`GET /v1/audit`（[routes/audit.py](linchpin-api/app/routes/audit.py)）NDJSON 流式（`from`/`to`/`resource_type`/`action`/`limit` 过滤，内部 1000 行/页 keyset 分页，大导出不全量缓冲）。
  - 测试：[test_audit.py](linchpin-api/tests/test_audit.py) 覆盖 record_audit 语义、导出过滤与分页、fail-open 集成。

### B10. 用量与可观测（FR-11 / §7）

1. `GET /v1/usage?from=&to=`：聚合 `sessions.usage`（按日/按 project）。
2. Prometheus：`/metrics` 端点（prometheus-fastapi-instrumentator HTTP 指标 + 自定义 gauge：活跃会话数、编排任务数、沙箱容器数、webhook 投递积压）。
3. OTel：span.model_request_* 事件双写 OTel → Langfuse；trace 贯通 会话→轮→模型调用。
4. Console 增用量看板页（消费 /v1/usage）。

- **实施记录（4/4 ✅ 已完成）**：
  - `GET /v1/usage`（[routes/usage.py](linchpin-api/app/routes/usage.py)）：按 `sessions.created_at` 时间窗（`from`/`to`，可选）聚合租户内 `sessions.usage` 四键（input/output/cache_creation/cache_read，`COALESCE` 兜 legacy 行缺键）；`group_by=day`（默认，UTC 日）或 `group_by=project`；响应含 `total` 汇总 + `buckets` 明细（[models.py](linchpin-api/app/models.py) `UsageBucket`/`UsageResponse`）。零迁移（纯聚合查询）。
  - 测试：[test_usage.py](linchpin-api/tests/test_usage.py) 5 例（day 分组 SQL/参数、from/to 窗口、project 分组、空结果形状、鉴权）。
  - Console 用量看板页（随 B14 交付）：消费 `/v1/usage` 的 KPI + 图表 + 明细表，详见 B14 实施记录。
  - Prometheus `/metrics`（随 B15 交付）：详见 B15 实施记录。
  - OTel 双写（随 B15 交付）：详见 B15 实施记录。

### B11. 模型层对齐"自有模型 + 主流协议"（FR-2/§2.3）

- 结论：**不需要新增适配器**。保留 providers.py 双实现；部署 LiteLLM 网关，客户配置 `provider="openrouter"` + `base_url=<litellm>` + vault/env 提供 key 即接入任意 OpenAI 兼容上游（含自有模型）。providers.py 的 `base_url` 覆盖通道（`get_provider`，L649-655）原样可用。
- 交付物：compose 增可选 litellm 服务 + 接入文档；providers.py 的 env_var_map 语义文档化（走网关时 key 名统一）。

- **实施记录（✅ 已完成，零迁移、零代码路径变化，测试基线 995 不变）**：
  - compose（[docker-compose.yml](docker-compose.yml)）：`litellm` 服务以 `profiles: ["litellm"]` 门控（默认不启动，`docker compose --profile litellm up -d litellm` 启用）；wildcard 路由（[litellm.config.yaml](litellm.config.yaml)，`model_name: "*"` → `openrouter/*` 逐字转发，免逐模型登记）；双 key 语义——`OPENROUTER_API_KEY` 是网关自身的上游凭证，`LITELLM_MASTER_KEY` 是 agent 呈给网关的 Bearer（vault credential `provider="openrouter"` 或 env fallback）。
  - 接入文档（[docs/model-gateway.md](docs/model-gateway.md)）：快速开始、**key 名统一语义表**（直连与走网关均 `provider="openrouter"` → env 固定 `OPENROUTER_API_KEY`、vault credential 均匹配 `provider="openrouter"`——凭证按 provider 字符串而非 endpoint 解析）、自有模型接入（vLLM/自托管 OpenAI 兼容端点增 `model_list` 条目，或绕过网关直接 `base_url` 指向）、compose 内网地址注意（API 容器内用服务名 `http://litellm:4000/v1`）、Ollama 对照、观测性提示（B15 OTel 由 API 侧发出不受上游影响；LiteLLM 自身可选 `callbacks: ["langfuse"]`）。README Models 节挂链。
  - 语义文档化（规格笔误校正：`env_var_map` 实际位于 [orchestrator.py](linchpin-api/app/orchestrator.py) 的 key 解析处而非 providers.py）：orchestrator `env_var_map` 块与 [providers.py](linchpin-api/app/providers.py) `get_provider` docstring 各补 Atlas B11 注释——`openrouter` 即通用 OpenAI 兼容通道，`base_url` 覆盖即网关/自托管接入，凭证命名跟随 provider 字符串。

### B12. 事件保留期（§7）

- lifespan 清理任务：`DELETE FROM events WHERE created_at < now() - interval`，默认 90 天，`LINCHPIN_EVENTS_RETENTION_DAYS` 可配；只删 payload 重（或按行删），seq 单调性保留（cursor 不回退）。
- 会话删除数据清除（FR-1.4/FR-10.5）：DELETE 端点补 `DELETE FROM events WHERE session_id=$1` + files 清除 + 审计记录。

- **实施记录（✅ 已完成，迁移 0022）**：
  - 迁移 [0022_events_retention.py](linchpin-api/alembic/versions/0022_events_retention.py)：`events.created_at TIMESTAMPTZ`（存量回填 `COALESCE(processed_at, now())`）+ `idx_events_created_at` 索引。
  - 清理（[retention.py](linchpin-api/app/retention.py)）：lifespan 内每小时 `DELETE FROM events WHERE created_at < now() - ($1 || ' days')::interval`；默认 90 天，`LINCHPIN_EVENTS_RETENTION_DAYS` 可配（<=0 禁用）；按年龄整行删，per-session seq 单调性天然保留（幸存行 cursor 不动，客户端游标不回退）。
  - 会话删除数据清除（FR-10.5）：`DELETE /v1/sessions/{id}`（terminate）补 `DELETE FROM events WHERE session_id=$1`、删除 session 作用域 files 行（无其他行引用同一 storage_path 时才删底层字节——内容寻址可共享）、rmtree outputs/memory/skills 缓存目录并写 `session.deleted` 审计（含 events_deleted/files_deleted 计数）——事件、密文、文件随会话删除一并清除。
  - 测试：retention 取值解析/禁用语义/清理调用 + 会话删除清除路径（并入 [test_sessions.py](linchpin-api/tests/test_sessions.py) 等既有用例）。

### B13. 子代理配置面与 TS SDK（FR-6.1 / FR-12.1，M3 收口）

- 子代理配置面：Agent 定义声明可 spawn 的子代理花名册（`multi_agents`，每个 entry 指向同项目既有 agent）；编排器仅按声明装配 `spawn_subagent` 工具，未声明的 spawn 请求 fail-closed。
- TS SDK：`linchpin-sdk-ts/` 零运行时依赖 TypeScript 客户端（fetch + SSE/NDJSON 流式解析），覆盖 sessions/deployments/artifacts/usage/audit 全资源面。

- **实施记录（✅ 已完成，迁移 0026）**：
  - 配置面模型（[models.py](linchpin-api/app/models.py)）：`SubagentConfig`（`name` 必填且正则约束、`agent_id` 必填——引用同项目既有 agent（routes 层校验，对齐 skills 惯例）、`environment_id` 可选（缺省 spawn 时继承父会话环境）、`description`）+ `Agent.multi_agents`/`AgentVersion.multi_agents`（JSONB，快照随版本固化）。花名册级校验：`MAX_SUBAGENTS_PER_AGENT` 上限 + 声明名唯一（每个 name 成为 `spawn_subagent` 工具的 enum 项，重复即配置 bug）。
  - 迁移 [0026](linchpin-api/alembic/versions/0026_agent_multi_agents.py)：agents/agent_versions 加 `multi_agents JSONB`。
  - 装配（[orchestrator.py](linchpin-api/app/orchestrator.py)）：`load_agent`/`load_agent_at_version` 解析 `multi_agents`（列缺失回退空花名册）；`build_tool_definitions` 仅在花名册非空时注入 `spawn_subagent` 工具描述（声明即启用门控）。
  - handler（[tools.py](linchpin-api/app/tools.py) `_spawn_subagent`）：fail-closed——未声明 name 直接拒绝；校验 name/message/session 上下文；复用 spawn_thread 既有通道（系统上下文模板、单向镜像到祖先），depth 超限映射为错误信封。
  - policy：`spawn_subagent` 固定 `always_ask` 放行（审批语义与父会话工具一致，不做 per-agent 覆写）。
  - 路由（[routes/agents.py](linchpin-api/app/routes/agents.py)）：create/update 校验 `multi_agents` 结构，快照列一并落 agent_versions。
  - TS SDK（[linchpin-sdk-ts/](linchpin-sdk-ts/README.md)）：零运行时依赖（Node >=18 全局 fetch/ReadableStream），types/errors/sse/client 四模块——统一错误信封解析（兼容 FastAPI legacy detail）、`SseParser` 增量解析器（WHATWG 规范：CRLF/bare-CR/跨 chunk `\r\n` 对拆分/BOM/EOF 未终结行/空 data 不 dispatch/空 id 清 last-event-id）、`LinchpinClient` 覆盖 sessions（含 spawnThread/listThreads/stream + Last-Event-ID resume）、deployments（pause/resume）、artifacts（content）、usage、audit（NDJSON 流式导出）；`fetch` 经构造参数可注入。51 tests（vitest）+ typecheck + build（dist/ d.ts+js）全绿。
  - 测试：[test_subagent_config.py](linchpin-api/tests/test_subagent_config.py) 28 例（模型解析/默认花名册/版本快照/工具装配/spawn happy path 与 fail-closed/depth 映射/参数与会话上下文校验）+ SDK 51 例。全量 984 passed / 0 failed（基线 956 + 28）。

### B14. Python SDK 与用量看板（FR-12.1 / FR-12.3，M2/M3 收口）

- Python SDK：`linchpin-sdk-py/` 标准库零依赖同步客户端（urllib + SSE/NDJSON 流式解析），与 TS SDK 语义逐一对齐，覆盖 sessions/deployments/artifacts/usage/audit 全资源面。
- 用量看板：Console 新增 `/usage` 页面消费 `GET /v1/usage`（KPI + 图表 + 明细表）。

- **实施记录（✅ 已完成，零迁移）**：
  - Python SDK（[linchpin-sdk-py/](linchpin-sdk-py/README.md)）：src 布局，`requires-python >=3.10`（3.11 以下条件依赖 typing_extensions，其余零运行时依赖）；默认 transport 用 `urllib.request`（HTTPError 错误 body 可达 → 统一错误信封；URLError/OSError → NetworkError），transport 可注入；四模块 types/errors/sse/client——`UsageParams`/`AuditParams` 用 `from_` 别名（保序字典推导转 `from`，与 TS URL 一致）、错误信封三形态（统一 envelope/FastAPI legacy detail/纯文本）、`SseParser` WHATWG 语义逐项移植（含增量 UTF-8 解码/BOM/跨 chunk `\r\n` 对拆分/EOF 未终结行）、query bool 序列化 `"true"/"false"` 对齐 TS；`SessionsResource.stream` 支持 `Last-Event-ID` + cursor 续读。
  - 用量看板（[UsageView.tsx](linchpin-console/src/pages/UsageView.tsx)）：不引 chart 依赖，手绘 SVG——day 分组纵向堆叠柱状（1/2/5×10^n nice 刻度、MM-DD 抽稀标签、`<title>` 悬停数值）、project 分组横向堆叠条形；KPI 5 卡（色条对应图表分段色）、快捷区间 7D/30D/90D、`type="date"` 输入（ISO `T00:00:00Z`/`T23:59:59Z`）、Day/Project 切换、明细表 tabular-nums；复用 LoadingSpinner/ErrorMessage 与设计 tokens；刷新保留旧数据（stale-while-revalidate）、无数据时整页错误态 + 重试；路由 `/usage` + Sidebar 导航项（[router.tsx](linchpin-console/src/router.tsx)/[Sidebar.tsx](linchpin-console/src/components/Sidebar.tsx)）。
  - 测试：Python SDK 80 例（pytest，transport 注入纯单测）+ Console [UsageView.test.tsx](linchpin-console/src/pages/UsageView.test.tsx) 7 例（vitest + @testing-library/react，`vi.mock('../api/client')`）。全量回归：linchpin-api 984 passed、TS SDK 51 tests、Console 7 tests + build，均绿。

### B15. Prometheus 指标与 OTel 追踪（B10 第 2、3 项，M3 收口）

- Prometheus：`/metrics` 端点（prometheus-fastapi-instrumentator HTTP 指标 + 自定义 gauge：活跃会话数、编排任务数、沙箱容器数、webhook 投递积压）。
- OTel：span.model_request_* 事件双写 OTel → Langfuse；trace 贯通 会话→轮→模型调用。

- **实施记录（✅ 已完成，零迁移）**：
  - Prometheus（[app/metrics.py](linchpin-api/app/metrics.py)）：两层——HTTP 指标用 `prometheus-fastapi-instrumentator` 挂 root app 暴露 `/metrics`（无鉴权，`excluded_handlers=["health","metrics"]`）；自定义 gauge 四枚：`linchpin_active_sessions`（DB `status='running'` 计数）、`linchpin_orchestrator_tasks`（进程内任务 dict，lifespan `register_inprocess_sources` 注册）、`linchpin_sandbox_containers`（linchpin-* 网络容器数，排除 `linchpin-egress-` 代理）、`linchpin_webhook_delivery_backlog`（pending/failed 行数）；DB/Docker 类 gauge 由 `metrics_sampler_loop` 周期刷新（`LINCHPIN_METRICS_INTERVAL_SEC` 默认 15，<=0 禁用；每步 best-effort 失败仅 warning）。依赖声明补齐 [pyproject.toml](linchpin-api/pyproject.toml)（prometheus-client / prometheus-fastapi-instrumentator / opentelemetry-sdk / opentelemetry-exporter-otlp-proto-http）。
  - OTel（[app/tracing.py](linchpin-api/app/tracing.py)）：双写——orchestrator 在 emit `span.model_request_start/end` 事件（events 表契约不变，SSE/API 消费者无感）的同时调用 tracing 钩子，模型调用落为 OTel span，经 OTLP/HTTP 导出（默认 `http://localhost:4318/v1/traces`，配 `LINCHPIN_OTLP_HEADERS` 即接 Langfuse）。trace 贯通：trace_id 派生自 session_id（UUID→128-bit int），同会话所有 span 共享一个 trace；层级 `agent.turn`（一轮主循环，惰性旋转关闭）→ `model_request`（一次模型调用，与 events 的 span_id 配对）；每 span 带 `session.id`/`langfuse.session.id` 与 `gen_ai.*` 语义属性（system/model/usage 四键/elapsed_ms），error → ERROR status（截断 512）。全部钩子 best-effort（tracing 失败仅 warning，绝不影响 agent loop 与事件表）；未启用时零成本 no-op。配置（os.environ）：`LINCHPIN_OTEL_ENABLED`（默认 false）/`LINCHPIN_OTLP_ENDPOINT`/`LINCHPIN_OTLP_HEADERS`/`LINCHPIN_OTEL_SERVICE_NAME`/`LINCHPIN_OTLP_TIMEOUT_SEC`；lifespan 启动 `setup_tracing()`、关闭时 `shutdown_tracing()` flush（[main.py](linchpin-api/app/main.py)）。
  - 测试：[test_tracing.py](linchpin-api/tests/test_tracing.py) 11 例（InMemorySpanExporter 注入，零网络）——disabled 全钩子 no-op、setup 默认关/enabled 构建成功、headers 解析（base64 padding 兼容）、span 属性与 agent.turn→model_request 父子层级、同会话跨 turn 同 trace + turn 旋转、异会话异 trace、error 状态与截断、泄漏 span 会话收尾 flush、未配对 end 忽略、无 turn 时 lazy state。全量回归：linchpin-api 995 passed / 0 failed（基线 984 + 11）。

### B16. 限额与配额（FR-11.2/11.3，M5 收口）

- PRD 方案（§9.2）为 OpenMeter+Lago+网关；决策：**API 侧自研轻量限额**（进程内 per-key 滑动窗口限流 + project 级并发/月 token 配额），网关层（APISIX）作为多副本精确限额的补充文档化；计费面（OpenMeter+Lago）不引入，留网关/商务侧。

**实施记录（✅ 已完成，迁移 0029）**：

- 迁移 [0029_quotas_and_rate_limits.py](linchpin-api/alembic/versions/0029_quotas_and_rate_limits.py)：`projects.max_concurrent_sessions INTEGER`、`projects.monthly_token_budget BIGINT`、`api_keys.rate_limit_rpm INTEGER`——全 nullable（NULL=不限），存量租户零回填零影响。
- 限流（[app/ratelimit.py](linchpin-api/app/ratelimit.py)）：per-key（api_key_id）60s 滑动窗口——进程内 `deque[float]`（monotonic 时间戳）+ LRU 字典（cap 10,000，touch 刷新、超 cap 驱逐并同步删 bucket）；被拒请求不入队不占槽；429 信封 `rate_limited` + `Retry-After` header（整秒、下限 1）；bootstrap 键（api_key_id None）与 limit NULL 直接放行；`reset_rate_limits()` 供测试/运维清态。
- 配额（[app/quotas.py](linchpin-api/app/quotas.py)）：`enforce_session_quotas(project_id)` 在会话创建前执行——`max_concurrent_sessions`：活跃会话计数（`status NOT IN ('terminated','failed') AND archived_at IS NULL`，与 C4 节点容量检查同语义）at-cap → 429；`monthly_token_budget`：当前 UTC 月创建会话的 usage 四键 SUM，at-cap → 429；双限额并发先触发（token 查询短路）。429 信封 `quota_exceeded` + `limit_type/limit/active|used`（无 Retry-After）。**fail-open**：无效 uuid/项目行缺失/DB 不可达一律放行（限额绝不阻断可用性）。
- 接线：[auth.py](linchpin-api/app/auth.py) `verify_bearer_token` 在 scope 检查前调 `check_rate_limit(ctx.api_key_id, ctx.rate_limit_rpm)`；[sessions.py](linchpin-api/app/routes/sessions.py) 创建路径调 `enforce_session_quotas`。
- 管理 API：[admin.py](linchpin-api/app/routes/admin.py) `PATCH /v1/projects/{id}`（**bootstrap key only**——租户 key 不得自抬限额，403）管理双配额；body 三态语义（omitted=只读回显 / 显式 null=清除 / 值=设置）；UPDATE 带 org_id 守卫，成功写审计 `project.quota_updated`。
- 文档：[docs/limits.md](docs/limits.md)（三限额表、PATCH 三态语义、429 响应契约、多副本 per-replica 语义 + APISIX limit-count 插件配方、60s auth 缓存=限流变更生效上界）、[docs/error-codes.md](docs/error-codes.md)（统一信封结构 + 核心码 + 30 领域码全表）。
- 测试：[test_ratelimit.py](linchpin-api/tests/test_ratelimit.py) 11 例（时钟注入/超限 retry/倒计时/滑动重开/被拒不占槽/LRU 驱逐与 touch/reset）+ [test_quotas.py](linchpin-api/tests/test_quotas.py) 11 例（uuid/未知项目/无限额短路/并发与 token at-cap·below-cap/双限额优先级/三处 fail-open）+ [test_multitenancy.py](linchpin-api/tests/test_multitenancy.py) 追加 6 例 PATCH（bootstrap-only 403/双限额 SET/org 守卫/显式 null 清除/空 body 只读 SELECT/404 两态）。

### B17. OpenAPI 3.x 定制（FR-12.2，M5 收口）

- 现状（A5 前）：FastAPI 自动生成无定制；A5 统一信封后 schema 未反映（ErrorEnvelope 缺失、鉴权/错误响应未声明）。

**实施记录（✅ 已完成，零迁移）**：

- `custom_openapi`（[main.py](linchpin-api/app/main.py)）：OpenAPI 3.1；16 组 tags 元数据；全 /v1 操作注入 `security=[{"bearerAuth": []}]` + 401/429/500 responses（429 描述含 Retry-After 语义）；`/v1/executor-nodes/{id}/heartbeat`（daemon 自鉴权）整体排除；`POST /v1/sessions` 与 `POST /v1/sessions/{parent_id}/threads` 的 429 描述换用 QuotaExceeded（quota_exceeded 语义、无 Retry-After）；components.responses 注册 Unauthorized/RateLimited/QuotaExceeded/Internal 四组件 + `ErrorEnvelope` schema（required: error/message）。
- 测试：[test_openapi.py](linchpin-api/tests/test_openapi.py) 12 例（3.1 / bearerAuth / ErrorEnvelope 形状 / 组件引用 / 全 op 注入与排除集 / quota vs rate 429 描述区分 / tags / /health 不注入）。

### B18. BYOK 客户自带密钥（FR-10.2/10.3 增量，M6 收口）

- B8 挂起项：`x-events-encrypted: true` 会话的 per-session data_key 改由**客户 KEK** wrap——客户 KEK 经 vault 登记后，平台持有了 KEK 才能解密事件流；客户撤走/轮换 KEK 即平台侧不可读（信封自描述形状，`kid` 指回凭据）。

**实施记录（✅ 已完成，零迁移）**：

- vault 凭据类型（[models.py](linchpin-api/app/models.py)）：`CredentialType` 加 `"byok_kek"`；`CreateByokKekCredentialRequest`（credential_type Literal["byok_kek"] + `kek` 必填，pydantic `field_validator` 校验必须是合法 Fernet key，否则 422）；`UpdateCredentialRequest.kek: str | None`（None 跳过）支持 KEK 轮换——PATCH 后重存 `{"kek": <new>}` 密文。KEK 以 `{"kek": ...}` JSON 经平台主密钥（EncryptionService，与 vault 同基建）二次加密落 `credentials.encrypted_secrets`：不以明文落库、不进沙箱、仅 API 侧解包使用。
- 信封加密路径（[event_crypto.py](linchpin-api/app/event_crypto.py)）：`wrap_data_key` / `unwrap_data_key` 形状路由——无 `kek` 参数走平台主密钥（legacy 裸 Fernet token 形状，向后兼容）；带 `kek` 走 BYOK 自描述信封 `{"enc": "byok1", "kid": <credential id>, "ct": <wrapped token>}`；`byok_credential_id(wrapped)` 解析 `kid`；`get_kek(kek_credential_id)` 按 credential id 从 vault 解出 KEK（`_kek_cache` 进程内 memoize；wrap 时预热缓存使首次 unwrap 零 DB 往返）；读路径 `decrypt_data_key_row` 按 `kid` 自动分派（byok1 → get_kek → unwrap，legacy → 平台 unwrap）——GET events / SSE stream / build_context / 审批等待等全部既有消费点零改动自动覆盖。KEK 缺失/凭据已删（如客户轮换前撤销）→ 500 `kek_unavailable`（fail-closed，密文不可读是 BYOK 语义本身）。
- 会话接线（[routes/sessions.py](linchpin-api/app/routes/sessions.py)）：POST /v1/sessions 与 POST /v1/sessions/{parent_id}/threads 新增 `x-kek-credential-id` 请求头；`_resolve_kek_credential` 在会话行插入前校验——凭据存在且 `credential_type='byok_kek'` 且未归档（否则 404）、所属 vault 必须在会话请求的 `vault_ids` 声明内（否则 422，防止未授权 vault 的凭据被借用）；命中则 `wrap_data_key(data_key, kek=..., kek_credential_id=...)`。仅 `x-events-encrypted` 头时行为与 B8 完全一致。spawn_thread 直调点显式传 `kek_credential_id=None`（FieldInfo 泄漏同款坑，B8/A8 第三次）。
- 测试：[test_vaults.py](linchpin-api/tests/test_vaults.py) 4 例新建（byok_kek 登记密文形状与 INSERT 参数 / 非 Fernet key 422 / PATCH 轮换重存 / 空 body 跳过 UPDATE）+ [test_event_crypto.py](linchpin-api/tests/test_event_crypto.py) BYOK 扩展（wrap/unwrap 形状路由 / get_kek DB 回源与 memoize / kid 解析 / 缺 KEK fail-closed / 冷路径缓存清空模拟重启）+ [test_sessions.py](linchpin-api/tests/test_sessions.py) `test_create_session_byok_wraps_under_client_kek`（header 组合 + 落库信封 kid 断言）。

### B19. Egress curated 域名（FR-4.3 残余，M6 收口）

- B4 挂起项：`LimitedNetworking.allow_mcp_servers` / `allow_package_managers` 两个 surface-only 布尔接入 B4 的 per-env Squid 白名单——此前字段存在但无任何消费。

**实施记录（✅ 已完成，零迁移）**：

- curated 集合（[egress.py](linchpin-api/app/egress.py)）：`CURATED_PACKAGE_DOMAINS`（EnvironmentPackages 支持的六包管理器的安装/索引端点：apt debian+ubuntu 4 域 / pip 2 域 / npm 1 域 / cargo 3 域 / gem 1 域 / go 2 域）+ `CURATED_MCP_DOMAINS`（npx/uvx 运行 MCP server 前拉包的 npm 与 PyPI 注册表 3 域）。**刻意不收录 MCP server 自身的上游 API 域**——第三方 API 无 curated 集合可背书，必须显式列 `allowed_hosts`（注释已标明该边界）。
- 合并语义（`effective_allowed_hosts`）：`allowed_hosts` 位置与优先级保持，curated 域仅在对应布尔开启且未显式出现时按序追加；产出即 Squid 允许且参与 hash 的最终列表（配置派生/幂等重建语义与 B4 完全一致）；`proxy_env_from_networking` 改经该函数取白名单。
- 测试：[test_egress.py](linchpin-api/tests/test_egress.py) 新增 curated 8 例（两开关独立合并 / 显式 host 优先不重复 / 关闭时不合并 / 合并结果进 squid.conf / hash 随开关变化驱动 proxy 重建）。

### B20. RLS 租户隔离兜底（C1 二期加固，M6 收口）

- C1 挂起项：应用层 `WHERE project_id = ...` 过滤之上的数据库侧兜底——任何遗漏谓词的查询（新增路由手写 SQL、后台任务、未来贡献者）不再跨租户读写。

**实施记录（✅ 已完成，迁移 0030，已应用真实 DB + 行为冒烟）**：

- 迁移 [0030_rls_tenant_isolation.py](linchpin-api/alembic/versions/0030_rls_tenant_isolation.py)：12 张租户表 `ENABLE` + `FORCE ROW LEVEL SECURITY` + 单一 fail-open policy `tenant_isolation`（USING/WITH CHECK 同式，cmd=ALL）——agents/environments/sessions/vaults/files/webhook_endpoints/api_keys/deployments（project_id）+ memory_stores/skills/dreams（workspace_id）+ executor_nodes（org_id）。`FORCE` 必需：应用以表主角色连接（同角色跑迁移）。**刻意不覆盖**：organizations/projects（跨项目 JOIN 与 admin 列表）、audit_log（合规要求跨租户全量视图且 project_id nullable）、经父 JOIN 过滤的子表（credentials/memories/memory_versions/session_resources/webhook_deliveries/outcome_evaluations/agent_versions/events/idempotency_keys——过滤父表已足，保持热 fan-out 查询单计划）。
- policy 表达式（fail-open 语义）：

```sql
USING (
    coalesce(current_setting('app.current_project', true), '') = ''
    OR <tenant column>::text = current_setting('app.current_project', true)
)
```

  GUC 未设（后台任务、预鉴权查找如 `_load_api_key`）→ 全行可见（fail-open：unset 上下文绝不打断 housekeeping，API 层谓词仍是第一道线）；GUC 设定 → 仅该租户行可读写；GUC 为非法值 → 匹配零行（fail-closed，只有应用能 set）。**两个 PG 17 实战发现**（真库冒烟抓到，均已写进迁移 docstring）：① RESET 后的 placeholder GUC `current_setting(..., true)` 读作**空串而非 NULL**——`IS NULL` 分支 RESET 后永不触发，故用 `coalesce(..., '') = ''`；② planner 对 stable 函数带常量参数可在 **plan 期**提前求值（`EXPLAIN` 不执行也报错为证），`OR` 短路在 plan 期不保证——`<col> = current_setting(...)::uuid` 在 RESET 后直接抛 `invalid input syntax for type uuid: ""`，故租户比较必须去掉 cast 改 `::text` 等值（最坏情形是普通 false，永不报错）。
- GUC 接线（[db.py](linchpin-api/app/db.py)）：`set_tenant_context(project_id)` ContextVar + per-connection `_guc_state: WeakKeyDictionary`（want==have 零额外往返）；fetch_one/fetch_all/execute 三 helper 在带租户上下文的语句外层 `SET app.current_project`、finally `RESET`（异常 suppress——RESET 失败不影响业务结果，连接归还池后由下次 SET 覆盖）；listen/notify 不走租户 GUC。
- 请求路径强制（[auth.py](linchpin-api/app/auth.py)）：`require_auth` 校验完成后 `set_tenant_context(ctx.project_id)`——全部鉴权路由的 DB 访问自动收进租户边界；后台任务（watcher/metrics/retention/scheduler/lease supervisor）无 context → fail-open 全量可见（它们本就要求跨租户视野）。
- 真库行为冒烟（ServBay PG 17，全过）：RESET 后 `SELECT count(*) FROM api_keys` 不报错且 = 全量；GUC=本 project → 命中行；GUC=他 project → 0 行；GUC=本 project、INSERT 他 project 行（FK 满足）→ `42501 new row violates row-level security policy`（WITH CHECK 拒绝）；RESET 后 INSERT 成功、清理干净。单测 [test_db_tenant.py](linchpin-api/tests/test_db_tenant.py) 5 例（无 context 零额外往返 / SET-RESET 往返序 / execute·fetch_all 序 / RESET 异常 suppress / `_guc_state` 残留清理）。

---

## 5. C 类：架构级

### C1. 多租户（FR-10.1）——侵入面最大，建议最先启动

1. **身份层**：新表 `organizations(id, name, ...)`、`projects(id, org_id, name, ...)`、`api_keys(id, project_id, key_hash, prefix, scopes TEXT[], ip_allowlist TEXT[], status, created_at, last_used_at)`。
2. **鉴权中间件**：替换 `auth.py` 的单环境变量校验——Bearer → 查 api_keys（带进程内缓存）→ scope 校验（`agents:write` / `sessions:write` / `readonly` 等，路由声明 required scope）→ 注入 request.state.project。SSE `?token=` 通道兼容。
3. **数据隔离**：所有业务表加 `project_id`（org 冗余可查）；查询统一带过滤。落地策略：
   - FastAPI dependency 统一注入 project context，路由层只管业务；
   - 现有 `workspace_id` 占位列（`memory_stores.py:30-40` 等）正好利用；
   - 评估 Postgres RLS（SET app.current_project）作为兜底防线 vs 应用层过滤——推荐应用层过滤 + RLS 二期加固。
4. **IP 白名单与限流**：放网关层（APISIX，PRD §9.2 同款），API 侧只做 scope。
5. **迁移**：现有数据归入默认 org/project；Console 登录态对接（Keycloak OIDC 或先简单项目切换器）。

**实施记录（✅ 已完成，迁移 0020 提前占用原 deployments 编号，deployments/audit_log 顺延至 0021/0022）**：

- 迁移 [0020_multi_tenancy.py](linchpin-api/alembic/versions/0020_multi_tenancy.py)：`organizations`、`projects`（UNIQUE(org_id, slug)）、`api_keys`（key_hash UNIQUE、prefix 12 字符、scopes TEXT[]、status CHECK、last_used_at）+ agents/environments/sessions/vaults/files/webhook_endpoints 加 `project_id` 列与索引 + 存量回填默认租户（org/project 固定 ID `00000000-0000-0000-0000-000000000001`）。`ip_allowlist` 按网关层方案未建列。
- 鉴权（[auth.py](linchpin-api/app/auth.py)）：`AuthContext(org_id, project_id, api_key_id, scopes)`；bootstrap key（`LINCHPIN_API_KEY`）兼容保留；DB 签发 key 走 sha256 查询 + 60s 进程内缓存（revoke 生效上界）+ last_used_at 触碰；scope 校验集中 `_check_scopes`——GET/HEAD/OPTIONS 任何有效 key 可读，非 GET 需 `*` 或 `{第一段路径资源}:write`；SSE `?token=` 通道兼容。
- 数据隔离：全部业务路由注入 `ctx: AuthContext = Depends(require_auth)` 并按 `project_id` 过滤；memory_stores/skills/dreams 沿用 `workspace_id` 列（语义 == project UUID）；无自有租户列的表（memories、memory_versions、session_resources、credentials、webhook_deliveries）经父资源 JOIN 过滤；watcher 后台任务经 sessions 子查询派生 project_id。RLS 兜底（`SET app.current_project`）二期加固已由 M6 落地（迁移 0030，见 B20）。
- 管理面（[routes/admin.py](linchpin-api/app/routes/admin.py)）：`GET /v1/projects`（按 ctx.org_id）、`POST /v1/api-keys`（`lpk_` + token_urlsafe(32)，明文仅返回一次，跨 org project → 404）、`GET /v1/api-keys`（不回吐 hash/明文）、`DELETE /v1/api-keys/{id}`（软删 status='revoked'）。
- 测试：[test_multitenancy.py](linchpin-api/tests/test_multitenancy.py) 18 例（签发 key 认证、401/revoked、`?token=`、缓存 TTL 单次查库、scope 三态、admin 全端点）；全量 810 passed。

### C2. 横向扩展与 durable execution（§7 / FR-2.3）

- 现状约束：per-session asyncio 锁单进程正确性（`events.py:33-37`）；编排任务进程内 asyncio（`sessions.py:972`）；单实例架构假设。
- **方案 1（推荐，M1–M3 自研增量）**：
  1. per-session 锁 → Postgres advisory lock（`pg_try_advisory_lock(session_key)`）；谁拿到锁谁执行该会话的编排轮次 → API 无状态多副本；
  2. `recover_sessions` 扩展为心跳机制：编排任务定期续期心跳（advisory lease），超时未续期即被其他实例接管（对齐 FR-2.3"worker 崩溃自动迁移"）；
  3. LISTEN/NOTIFY：统一 channel + 会话过滤，或按实例订阅分配（Postgres LISTEN 不支持通配，需要收敛 channel 设计）；
  4. 事件三副本：Postgres 流复制即可满足。
- **方案 2（M4 评估）**：会话状态机/重试/Deployment cron 迁 Temporal workflow（PRD §9.2 建议）。**划界原则：事件流管"发生过什么"（保持现状不动），Temporal 管"接下来该做什么"（调度与重试外移）**；副作用幂等由事件信封（event_id 幂等键）保证，重放不重发。
- 500 并发会话容量测试纳入 M2 验收。

**实施记录（✅ 方案 1 已完成，迁移 0027；方案 2 Temporal 保持 M4 评估不动）**：

- 迁移 [0027_sessions_lease.py](linchpin-api/alembic/versions/0027_sessions_lease.py)：`sessions.orchestrator_instance` TEXT、`sessions.orchestrator_heartbeat` TIMESTAMPTZ + 部分索引 `idx_sessions_lease_stale`（WHERE status IN ('running','idle')，加速 supervisor 扫描）。已应用（ServBay Postgres，0026 → 0027）。
- 集群原语（[app/cluster.py](linchpin-api/app/cluster.py)，新模块）：
  - `get_instance_id()`——副本身份（`LINCHPIN_INSTANCE_ID` 钉住，否则 `api-<rand12>`），stamp 在每个会话的 `orchestrator_instance` 上；
  - `SessionLease`——`pg_try_advisory_lock` 绑定**专用池连接**（不能走 fetch_one/execute helper，那会每语句释放锁）：锁随连接死自动释放，崩溃持有者永不清漏；`acquire()` False=held elsewhere、DB 不可用 raise（调用方决定降级）；持锁期间 `_stamp` 心跳写 `orchestrator_heartbeat`；`renew()` 失败置 `broken`；`release()` 按 `orchestrator_instance = me` 条件清 bookkeeping（防误清接管方）；
  - `EventBus`——每进程 1 条 LISTEN 连接订阅共享 `linchpin_events` channel（Postgres LISTEN 无通配符，per-session channel 会每消费者烧一条连接直至 max_connections），payload `{"s": <uuid>, "k": <kind>}` 进程内按 session demultiplex 到 per-session `asyncio.Queue`。
- 编排接线（[app/orchestrator.py](linchpin-api/app/orchestrator.py)）：
  - `run_session` 改 wrapper：获取 lease（失败 `LeaseHeldElsewhere` → 不启动）+ 心跳 task（`LINCHPIN_HEARTBEAT_INTERVAL_SEC` 默认 10s）+ finally 释放；主体改名 `_run_session_loop`，循环顶加 broken-lease 退出（避免双驱动）；
  - 无池降级：`_acquire_session_lease` 捕获 RuntimeError → None（单进程/测试零改动，进程内 per-session 锁 + events 表 PK 仍是正确性底线）；
  - `recover_sessions` 扫描 lease 感知：跳过他人新鲜心跳（该副本正在驱动），仍收自己/stale/NULL 心跳行；
  - lease supervisor（`LINCHPIN_LEASE_SUPERVISOR` 默认开）：`take_over_stale_sessions`（`LINCHPIN_LEASE_TIMEOUT_SEC` 默认 60s）跳过本地活跃 task → try-acquire（失败即他副本抢先，不 release）→ 容器死 `failed(container_lost)` + `session.error` → 活 `idle`（保 stop_reason）+ `session.takeover` 事件 + `run_session(lease=已持)`（避免 release→re-acquire 竞态）+ watcher respawn；**不发 notify**（中 turn 死亡不自动重放，与进程重启语义一致，turn replay 归 M4）；`supervise_stale_sessions` 30s 循环。对齐 FR-2.3"worker 崩溃自动迁移"。
- NOTIFY 收敛（[app/db.py](linchpin-api/app/db.py)）：`notify` 对 session channel 改为参数化 `pg_notify(linchpin_events, {"s","k"})` + `dispatch_local` 立即本地投递（重复投递无害）；`listen` bus 模式订阅进程内 Queue（**SSE 连接不再各占一条池连接，顺带解决池耗尽**），无 bus 时 fallback 专用连接按 session 过滤（malformed payload 照样唤醒——漏唤醒降级为延迟，绝不丢数据）。
- 跨副本双写兜底（[app/events.py](linchpin-api/app/events.py)）：接管窄窗口内双 append 由 events 表 `PRIMARY KEY (session_id, seq)` 兜底——`append_event` 捕获 `asyncpg.UniqueViolationError` 重算 `MAX(seq)+1` 重试 3 次。
- lifespan（[app/main.py](linchpin-api/app/main.py)）：启动 event bus + lease supervisor task（skip_docker 或 env false 关闭）；关闭顺序 supervisor → event bus → watcher → pool。
- 配置：`LINCHPIN_INSTANCE_ID` / `LINCHPIN_HEARTBEAT_INTERVAL_SEC`(10) / `LINCHPIN_LEASE_TIMEOUT_SEC`(60) / `LINCHPIN_LEASE_SUPERVISOR`(true)。
- 测试：[test_cluster.py](linchpin-api/tests/test_cluster.py) 43 例（instance id / advisory key / channel 往返 / lease 降级与互斥 / run_session 接线与 broken-lease 退出 / 心跳 / EventBus demultiplex 与无池降级 / db.listen bus+fallback / db.notify 参数化+legacy / takeover 六分支 / supervisor 循环 / recover lease 感知）；全量 **1038 passed / 0 failed**（基线 995 + 43）。
- **部署模型约束**：方案 1 支撑**同宿主**多副本（api 层多副本 + 共享 Postgres + 共享 Docker daemon——沙箱 bind mount 与 Docker 网络不跨宿主）；跨宿主需自托管 executor（M4 BYO executor / C3 时间点评估）。已知接受的有界残余：lease 过期到 supervisor 下一轮之间旧 owner 可能仍在收尾 in-flight turn（events PK 保日志一致）；旧 owner 阻塞中的 `wait_for_confirmation` 至下次通知才醒（至多重复一轮，不损坏状态）。

**方案 2 评估记录（✅ 评估已完成 2026-10-21，供 M4 决策；未实施任何迁移）**：

- **评估范围**（三块迁移对象）：① Deployment cron（[app/deployments.py](linchpin-api/app/deployments.py) `deployments_scheduler_loop`：5s 轮询 `next_run_at <= NOW()` + `FOR UPDATE SKIP LOCKED` claim + croniter 按计划时间重算，多副本安全已达成，表形已按 additive 迁移预留）；② 会话状态机（[app/orchestrator.py](linchpin-api/app/orchestrator.py) `run_session` lease wrapper + `_run_session_loop` 进程内 while 循环 + LISTEN/NOTIFY + `wait_for_confirmation` 轮询 events 表）；③ 失败重试（retry_count/retries_exhausted + lease supervisor 容器死 `failed(container_lost)`）。
- **事实基础**（temporalio 1.34 GA，2026-10 调研）：Python SDK GA 且 AI 插件生态最全（OpenAI Agents SDK / LangGraph 官方集成），无官方 FastAPI 框架（通行做法 lifespan 内嵌或独立 Worker 进程）；自托管 = 4 服务（frontend/history/matching/worker）+ Postgres 持久层 + 手工 `temporal-sql-tool` schema 升级（PG13+ 无自动管理），最小 HA 约 4–6 实例；Cloud 按 Actions 计费（$50/百万起，heartbeat 也计费），无中国区域、无 BYOC——与本项目自托管诉求存在适配断点，Cloud 不构成兜底；确定性硬约束：LLM/tool 调用必须全部 Activity 化且客户端重试归零（重试交 RetryPolicy），HITL 长等待 = Signal/Update + `wait_condition`（durable timer，可等数天），payload 单条 2MB / history 50MB 硬限（滚动窗口 + claim check + ContinueAsNew 应对）；Schedules GA 三年（pause/resume/trigger/backfill/overlap 策略，官方有 Celery/APScheduler 迁移指南）。
- **划界确认**（维持既定原则）：events 表保持「发生过什么」的审计/SSR 流不动；Temporal history 仅是内部执行账本，两者以 event_id 幂等键对齐（重放不重发）；LLM Activity 以 session_id+seq 幂等 token 防重放双烧 token。
- **成本收益**：收益=无双主窗口（消除 lease 有界残余）、HITL durable timer 替代 events 表轮询、Schedules backfill/overlap、Temporal UI 可观测性、多区域 namespace 模型；成本=新增有状态 4 服务集群的 24×7 运维（版本顺序滚动升级 + 手工 schema 迁移）、orchestrator ~2000 行 turn 循环的确定性改造、上下文滚动窗口改造（payload/history 限制）。
- **结论与建议（分期，触发条件驱动）**：
  1. **会话编排暂不迁移**：方案 1 已覆盖单集群正确性底线，迁移收益集中在消除有界残余与 HITL durable timer，而改造成本与新增运维面显著——保持方案 1 为生产路径；
  2. **Deployment cron 是最小风险试点**（若决定引入集群）：Schedules 是现状 loop 的严格超集，表形已预留 additive 迁移；建议 M4 以「自托管集群试点 + Schedules 承接 cron」起步积累运维经验；
  3. **会话编排迁移触发条件**（满足其一再评估）：多区域部署、跨会话 fan-out/fan-in 长编排、HITL 并发等待规模使 events 表轮询不可持续、lease 有界残余升级为商务问题；
  4. **轻量备选**：若拒绝新增集群运维面，DBOS（纯 Python 库、复用现有 Postgres）值得平行 PoC，Hatchet（Postgres 底座、agentic 导向）次之；Temporal 差异化在生态成熟度/UI/十年生产验证。

### C3. 强隔离沙箱（FR-4.1 / §9.1）

- 普通 Docker 隔离对多租户 SaaS 不够（PRD 风险表第一行"沙箱安全逃逸：高"）。
- 方案：实现 `MicroVMSandbox`（E2B 自托管 / Firecracker），与 `DockerSandbox` 同接口（create / exec / kill_exec / destroy / mount）；`Environment.config` 加 `isolation: "docker" | "microvm"`；工厂按配置选择。`sandbox.py` 的单类抽象使替换面可控。
- 时间点：M4（GA 前置）；M1–M3 以 Docker + 网络默认 none + 资源档位 + 网关防护过渡，并在商务上限定"同租户会话可同宿主"。

**实施记录（✅ 已完成，零迁移——config.isolation 为 JSONB 内字段）**：

- 模型（[app/models.py](linchpin-api/app/models.py)）：`EnvironmentConfig.isolation: Literal["docker", "microvm"] = "docker"`（JSONB 内字段，存量配置默认 docker，无需回填）。
- 沙箱层（[app/sandbox.py](linchpin-api/app/sandbox.py) C3 新增段）：
  - **Driver 注入模式**：`MicroVMDriver` Protocol 只做原子操作（create_vm / exec_in_vm / kill_vm_process / write_vm_file / read_vm_file / copy_vm_file / list_vm_dir / destroy_vm），mount 注入、rw flush、exec PID 簿记全部在 `MicroVMSandbox` 层——换 provider（E2B → raw Firecracker）是叶子变更；
  - `E2BDriver`：构造不 import e2b，首次操作惰性 import；SDK 缺失 raise 描述性 SandboxError（提示 pip install e2b）；`kill_vm_process` E2B 无此概念 → logger.warning + return False；资源档位 `MICROVM_RESOURCE_TIERS` 与 Docker `RESOURCE_TIERS` 1:1（1c2g/2c4g/4c8g）；
  - `MicroVMSandbox` 与 DockerSandbox 同 `SandboxProtocol` 接口（create / ensure_image / exec / active_exec_pid / kill_exec / write_file / read_file / copy_from_container / list_dir / destroy）。**rw mount 语义**（microVM 无 host bind mount）：创建时 ro mount 直接注入文件内容；rw mount 在宿主路径为文件时注入内容并记入 `_rw_mounts`，destroy 时 `_pull_tree` 递归 flush 回宿主（best-effort，单 mount 失败仅 warning 不阻断 teardown）；因此 **watcher 的流式交付在 microvm 会话降级为「会话结束可见」**（流式恢复归 M4 BYO executor）；
  - `ensure_image`：无 packages 返回 base_image（microVM 用预烘焙镜像），带 packages raise（包烘焙是 Docker 能力）；network 校验只接受 linchpin-none/open/limited（VM 内无 docker bridge，策略由 driver 网络模型 + egress proxy env 承担）。
- 运行时选择（工厂 + 路由层）：
  - 工厂 `create_sandbox("docker" | "microvm")`；`resolve_sandbox(app.state, env_config)` 按创建路径的 `env_config.isolation` 解析；既有会话按创建时 pin 进 `session.metadata["sandbox_runtime"]` 的值路由（[app/routes/sessions.py](linchpin-api/app/routes/sessions.py) `sandbox_for_session`，C3 前的行/无 metadata 默认 docker）；
  - sessions.py 五处接线：创建路径 `resolve_sandbox` 前置到 session 行 INSERT 之前（缺 runtime → 503 `sandbox_runtime_unavailable`，不留孤儿行）；metadata pin `sandbox_runtime`；terminate / filesystem list / filesystem raw 三处按 pinned runtime 路由（SandboxError → 400 filesystem_error）；
  - lifespan（[app/main.py](linchpin-api/app/main.py)）：`app.state.microvm_sandbox` 独立属性（不进 skip_docker 分支守卫——microVM 供给不依赖本地 Docker daemon；`app.state.sandbox = DockerSandbox()` 直调形式保留，测试 patch 依赖）；`LINCHPIN_ENABLE_MICROVM=true` 才实例化，驱动缺 SDK 仅 warning 降级不炸启动。
- 配置：`LINCHPIN_ENABLE_MICROVM`（默认 false）/ `LINCHPIN_E2B_TIMEOUT_SEC` / `LINCHPIN_SANDBOX_IMAGE`（microvm 默认镜像 fallback）。macOS 宿主无法跑 Firecracker，实际启用需指向 E2B 自托管端点（或等效 microVM provider）。
- 测试：[test_microvm_sandbox.py](linchpin-api/tests/test_microvm_sandbox.py) 50 例（spec 构建/档位映射/网络与 ensure_image 校验/rw 注入与 destroy flush/E2B 惰性 import 与降级/工厂与 runtime 解析/创建 503 前置/metadata pin 路由/terminate 与 filesystem 路由）；全量 **1088 passed / 0 failed**（基线 1038 + 50）。

### C4. BYO executor / 自托管 executor（FR-4.6）

- PRD 语义：客户自带算力——session 沙箱运行在客户注册的 executor 节点（客户自己基础设施上的常驻 daemon）上，API 只做注册、调度与代理，跨宿主执行成为可能；直接补上 C2 部署模型约束（"沙箱 bind mount 与 Docker 网络不跨宿主"）留下的执行面缺口。
- 设计要点：节点注册表 + 与 `DockerSandbox` 同 `SandboxProtocol` 接口的 HTTP 代理沙箱 + 节点侧 daemon 三件套；会话按环境配置钉住节点。

**实施记录（✅ 已完成，迁移 0028）**：

- 迁移 [0028_executor_nodes.py](linchpin-api/alembic/versions/0028_executor_nodes.py)：`executor_nodes` 表（id / org_id / name UNIQUE / url / token_encrypted BYTEA / token_hash / token_prefix / max_sessions INT 默认 5 / last_heartbeat_at / created_at / updated_at / archived_at 软删）+ `(org_id) WHERE archived_at IS NULL` 部分索引。**token 存储方向说明**：token 为 Fernet 可逆加密（与 api_keys 只存 hash 相反——API 每次调用都要向 daemon **出示** token，鉴权方向相反）；SHA-256 hash 辅助心跳自鉴权查询、12 字符 prefix 用于展示。
- 数据面（[app/sandbox.py](linchpin-api/app/sandbox.py) C4 新增段）：`RemoteExecutorSandbox`——与 DockerSandbox 同接口，全部调用经 httpx（`MockTransport` 可注入）代理到节点 daemon：POST `/v1/containers`（create，payload `{"image", "network", "env", "resources"?, "files"?}`）、DELETE `/v1/containers/{id}`、POST `/{id}/exec`、POST `/{id}/exec/kill`、PUT/GET `/{id}/files`、GET `/{id}/tree`。**rw mount 语义**（跨宿主无 host bind mount）：创建时将 mount 内容 b64 打包进 `files`（单文件取原路径、目录按相对路径递归），rw mount 记入 `_rw_mounts`，destroy 时先 `_flush_rw_mounts`（daemon tree 列表 → 逐文件 copy 回宿主，best-effort）再删容器——与 C3 microvm 同款有界降级（watcher 流式交付变为"会话结束可见"）；`ensure_image`：EnvironmentPackages 空/None 透传 base image，非空拒绝（包烘焙是节点镜像层职责，daemon 不做 build）；`runtime_for_config`：环境 config `executor_node` 非空 → runtime `"remote"`；`remote_sandbox_for_node_row`：解密 token → 构造沙箱（VAULT_ENCRYPTION_KEY 未配置 → SandboxError 明示）。
- 调度解析（[app/sandbox_resolver.py](linchpin-api/app/sandbox_resolver.py) 新模块，两个 async 入口）：
  - `resolve_sandbox_for_config`（创建路径）：`executor_node` 为空 → 本地 `resolve_sandbox`（docker/microvm，C3 语义不变）；非空 → 按 org + name 查节点行（归档/不存在 → SandboxError "not found"）→ `_check_node_capacity`（`max_sessions > 0` 时统计 `(metadata->>'executor_node') = node` 且 `status NOT IN ('terminated','failed')` 的活跃会话数，满 → SandboxError "at capacity (n/max live sessions)"）→ `remote_sandbox_for_node_row`；
  - `resolve_session_sandbox`（生命周期路径）：按 metadata pinned 节点名反查行 → 构造 remote 沙箱；**无容量检查**（已被调度的存量会话必须始终能解析，容量只约束新会话入队）；节点消失 → SandboxError "no longer exists"。SandboxError 在创建路径统一映射 503 `sandbox_runtime_unavailable`（A5 信封）。
- 节点侧 daemon（[app/executor_daemon.py](linchpin-api/app/executor_daemon.py) 新模块，独立 router 挂根 app——节点直连不经 v1 版本化前缀）：常驻客户基础设施，持有本地 DockerSandbox，暴露 8 端点（ping / containers create+delete / exec / exec kill / files PUT+GET / tree）；鉴权：每请求读 `LINCHPIN_EXECUTOR_TOKEN`（未配置 → 503 `executor_token_unconfigured`；不匹配 → 401）；创建时 `_inject_files` 经 docker SDK 将打包文件写入容器（相对路径拒绝）；SandboxError → 502 原样透传 message。
- 注册面 API（[app/routes/executor_nodes.py](linchpin-api/app/routes/executor_nodes.py)）：`POST /v1/executor-nodes`（201，token 仅创建响应返回一次）、`GET /v1/executor-nodes`（org 过滤；online/offline 为**读取时推导**——心跳 90s 内 online，不落列）、`GET /v1/executor-nodes/{id}`、`DELETE /v1/executor-nodes/{id}`（软删）、`POST /v1/executor-nodes/{id}/heartbeat`（daemon 自鉴权：`fetch_one(sql, node_id, sha256(token))` 双条件查行，命中续 `last_heartbeat_at`）；响应模型永不回吐 token_encrypted/token_hash。
- 接线（[app/routes/sessions.py](linchpin-api/app/routes/sessions.py)）：创建路径以 `resolve_sandbox_for_config` 替换原 `resolve_sandbox` 前置调用；metadata pin `executor_node`（按名钉住，name UNIQUE 稳定可反查）与 `sandbox_runtime: "remote"`；[main.py](linchpin-api/app/main.py) 将 `resolve_session_sandbox` 注入 TTL 清理 / lease supervisor 接管 / recover_sessions 三个后台生命周期路径——remote 会话的容器操作同样被代理，daemon 不可达由 SandboxError 语义收口。
- 测试：[test_executor_nodes.py](linchpin-api/tests/test_executor_nodes.py) 18 例（注册加密形状与 INSERT 参数/token 冲突 409/加密不可用 503/列表 org 过滤与 secret 不外泄/detail/软删/心跳自鉴权三态与 fetch 参数序/90s 阈值边界）+ [test_remote_executor.py](linchpin-api/tests/test_remote_executor.py) 47 例（RemoteExecutorSandbox MockTransport 全方法 wire-format 与 rw flush 顺序 17 / resolver 本地 fallback·远端·容量·生命周期分支 8 / daemon 端点鉴权与错误映射 20 / sessions 创建分派 remote-vs-local 与 503 信封 2）。

### C5. tool search 延迟工具加载（FR-5.6）

- PRD 语义：工具目录庞大时不再把全部工具定义随每轮模型请求下发（token 成本 + 注意力稀释），由模型按需检索激活。
- 设计要点：不引入向量库——关键词检索（子串 OR 匹配、name 权重 2 / description 权重 1、`(-score, idx)` 排序）满足 P2 定位；环境配置门控 + 阈值切换，会话创建时快照进 metadata（与 C3 runtime pin 同思路）。

**实施记录（✅ 已完成，零迁移）**：

- 检索器（[app/orchestrator.py](linchpin-api/app/orchestrator.py) `search_tool_definitions`）：空 query/无命中返回空列表；空格分词小写化，逐 term 对 name（+2）/ description（+1）子串 OR 匹配，`(-score, idx)` 排序（catalog 顺序平局决胜），`limit` 截断（默认 10）。
- 元工具（`tool_search`，`TOOL_SEARCH_DEF`）：入参 `{query: string}`；**仅 search_mode 生效时注入**模型请求的工具列表头部，且不受 `agent.tools` 声明约束。
- 门控与快照（[app/routes/sessions.py](linchpin-api/app/routes/sessions.py)）：环境 config 新增 `tool_search: {enabled, threshold=20, max_results=10}`（`ToolSearchConfig` 校验，非法形状 warning 降级为 legacy 全量暴露语义）；创建会话时快照进 `session.metadata["tool_search"]`，编排循环免每轮 JOIN environments。
- 编排接线（[app/orchestrator.py](linchpin-api/app/orchestrator.py) `_run_session_loop`）：`len(all_tools) > threshold` 才进入 search_mode——目录小于阈值时行为与 legacy 完全一致；search_mode 下模型请求只携带 `tool_search` 元工具 + `activated` 集合中已激活工具（`activated` 存活于整个 `_run_session_loop`，跨 steer 重入；下轮 run_session 重置，重新检索成本低且无陈旧状态）；**分发前拦截**在策略评估之前：`tool_search` 调用走内联处理（检索 → 命中项并入 `activated` → `agent.tool_result` 返回 `{"tools": [...]}`，无命中附 retry 提示），未激活工具直接返回激活引导错误——meta-tool 不入 PolicyEvaluator（避免 never-asked-for 的审批门控）。
- 测试：[test_tool_search.py](linchpin-api/tests/test_tool_search.py) 14 例（空/空白/无命中 query、name 权重胜出且排除 idx 平局干扰、desc-only 命中、多词 OR 大小写、limit 截断与默认 10、25 工具目录阈值切换、search_mode 下未激活工具拦截、tool_search 回路、非 search_mode 零行为变化）；全量 **1167 passed / 0 failed**（基线 1088 + 79：tool_search 14 + executor_nodes 18 + remote_executor 47）。

---

## 6. 库表变更汇总（alembic）

| 迁移 | 内容 | 状态 |
|---|---|---|
| 0016 | sessions: retry_count、stop_reason（A2）。A1 零迁移——agent_version 自 0001 已存在，仅改读路径 | ✅ 已实施 |
| 0017 | events: event_id TEXT NOT NULL UNIQUE（'sevt_' + uuid4 hex，DB 默认值 + 存量回填）（A3） | ✅ 已实施 |
| 0018 | idempotency_keys（A8；TTL 24h lifespan 清理） | ✅ 已实施 |
| 0019 | sessions.context_estimate（B1 compaction 预算估算列） | ✅ 已实施 |
| 0020 | organizations / projects / api_keys + 各业务表 project_id + 默认租户回填（C1，提前实施） | ✅ 已实施 |
| 0021 | audit_log + 3 索引（B9） | ✅ 已实施 |
| 0022 | events.created_at + 索引（B12 保留期，存量回填） | ✅ 已实施 |
| 0023 | sessions.events_encrypted + data_key（B8 加密落库） | ✅ 已实施 |
| 0024 | files.source CHECK 放宽加 'artifact'（B5 已知错误修复） | ✅ 已实施 |
| 0025 | deployments 表 + sessions.deployment_id（B7） | ✅ 已实施 |
| 0026 | agent_multi_agents（B13 子代理，提前占用编号） | ✅ 已实施 |
| 0027 | sessions.orchestrator_instance / orchestrator_heartbeat + idx_sessions_lease_stale 部分索引（C2 方案 1） | ✅ 已实施 |
| 0028 | executor_nodes 表（BYO executor 节点注册表，C4；token Fernet 可逆加密 + SHA-256 hash + prefix） | ✅ 已实施 |
| 0029 | projects.max_concurrent_sessions / monthly_token_budget + api_keys.rate_limit_rpm（全 nullable，B16 限额配额） | ✅ 已实施 |
| 0030 | 12 张租户表 RLS ENABLE+FORCE + tenant_isolation fail-open policy（B20；表达式含 PG 17 placeholder GUC 空串与 plan 期求值两个实战修正，见 B20） | ✅ 已实施（真实 DB + 行为冒烟） |

原规划中 enabled_events（A7，自迁移 0009 即已存在，实际类型 JSONB 而非 TEXT[]）、
`config.resources`（B3 已落地）与 `config.isolation`（C3，已实施并确认零迁移）均为 JSONB 内字段，无列变更，相应迁移取消。
0019 起编号按实际实施顺序顺延（C1 提前实施占用 0020；audit_log/events retention/sessions 加密/files CHECK
相应占用 0021–0024）；deployments（B7，`sessions.deployment_id` 一并加）实际占用 0025。

---

## 7. 端点变更汇总

- **新增**：`GET /v1/sessions/:id/events/stream`（别名）、`GET /v1/artifacts`、`GET /v1/artifacts/:id`、`GET /v1/artifacts/:id/content`、`GET /v1/sessions/:id/filesystem`、`GET /v1/sessions/:id/filesystem/raw`、`/v1/deployments`（CRUD+pause/resume，B7 已实施）、`GET /v1/usage`、`GET /v1/audit`、`/v1/projects`、`PATCH /v1/projects/:id`（bootstrap key only，配额管理，B16 已实施）、`/v1/api-keys`、`GET /metrics`（Prometheus，B10 已实施）、`/v1/executor-nodes`（注册/列表/详情/软删 + `/:id/heartbeat` daemon 自鉴权，C4 已实施）；executor daemon 内部面（ping/containers/exec/files/tree，节点侧独立 router 挂根 app，Bearer `LINCHPIN_EXECUTOR_TOKEN`，C4）
- **变更**：`POST /v1/sessions`（provisioning 状态、Idempotency-Key、x-events-encrypted、`x-kek-credential-id`（B18 BYOK，凭 client KEK wrap data_key）、memory_ids/environments 语义核对）；`POST /v1/sessions/:id/events`（批量原子校验+门控）；`GET /v1/sessions/:id/stream`（帧格式+Last-Event-ID）；`POST /v1/sessions/:id/archive`（幂等）
- **全局**：统一错误码 envelope（§6.4，A5 已实施，码表见 docs/error-codes.md）；OpenAPI 3.x 定制发布（M5 已实施，custom_openapi——bearerAuth security、401/429/500 responses、ErrorEnvelope schema，见 B17）；`Linchpin-API-Version` 头机制保留（PRD"版本头"已满足）

---

## 8. 里程碑落地顺序

| 里程碑 | 范围 | 本方案条目 | Linchpin 起点 |
|---|---|---|---|
| **M1 内测（T+8 周）** | 状态机/SSE/门控/幂等/错误码 + 规格档位 + compaction + 打断强化 + 失败重试 + 多租户启动 | A1–A8、B1、B2、B3、C1 启动 | 核心已有约 70% |
| **M2 Beta（T+14 周）** | 审计、加密落库、Artifacts、Python SDK、控制台只读视图、限额 | B5、B8、B9、B10(1/4)、B12、C1 完成 | Vault/审批已有。**当前进度：B5/B8/B9/B12/B10(4/4) + Python SDK + 控制台只读视图 ✅（995 passed + Python SDK 80 tests）；限额（FR-11.2/11.3）M5 已补（API 侧自研，见 B16）✅** |
| **M3 公测（T+22 周）** | Webhook（已提前达标）、子代理配置面、Deployment、TS SDK、网络白名单、用量看板 | A7、B4、B6、B7、B10(2/4) | Webhook/Memory/web_search 已有。**当前进度：A7/B4/B6/B7/B10(4/4) + 子代理配置面 + TS SDK + 用量看板（FR-12.3，B14）+ Prometheus/OTel（B15）+ 模型网关接入（B11）✅（995 passed + TS SDK 51 + Python SDK 80 + Console 7 tests，B4 per-env Squid egress proxy 偏离单 proxy 规格见实施记录），M3 范围内交付全部完成** |
| **M4 GA（T+30 周）** | 强隔离、自托管 executor、多区域、durable execution 收口 | C2（Temporal 评估）、C3、tool search、BYO executor | — **当前进度：M4 范围内交付全部完成——C2 方案 1（advisory lock lease + 心跳接管 + NOTIFY 收敛 + 流复制）+ C3（MicroVMSandbox，E2B driver，零迁移）+ tool search（FR-5.6，C5 节，零迁移）+ BYO executor（FR-4.6，C4 节，迁移 0028）✅（1167 passed）；C2 方案 2 Temporal 评估 ✅（结论：维持方案 1 生产路径，Schedules 承接 cron 为试点建议，详见 C2 节评估记录）；多区域无对应条目（触发条件见 C2 评估第 3 点）** |
| **M5 收口批次** | 限额配额 + OpenAPI 定制 | FR-11.2/11.3（B16）、FR-12.2（B17） | ✅ 全部完成——迁移 0029 + per-key 滑动窗口限流 + project 并发/月 token 配额 + PATCH /v1/projects（bootstrap only） + custom_openapi（bearerAuth/401/429/500/ErrorEnvelope） + docs/limits.md · docs/error-codes.md |
| **M6 收官批次** | BYOK 客户自带密钥 + egress curated 域名 + RLS 兜底 | FR-10.2/10.3（B18）、FR-4.3（B19）、FR-10.1（B20） | ✅ 全部完成——`byok_kek` 凭据类型 + `x-kek-credential-id` 头 + byok1 信封分派（B18）+ curated 域名合并 squid 白名单（B19）+ 迁移 0030 十二表 RLS ENABLE+FORCE + `app.current_project` GUC 接线（B20，真库行为冒烟通过）✅（1243 passed）。**ATLAS 方案全部条目交付完毕、无挂起项，全量收官** |

排序原则：**C1 多租户最先启动**（越晚越贵，牵动所有路由与查询）；A 类先行（低风险快速对齐）；B1 compaction 独立并行；C3 强隔离放最后（M1–M3 用 Docker+网络策略+档位过渡）。

---

## 9. 风险与决策点

| # | 风险/决策 | 说明 | 建议 |
|---|---|---|---|
| 1 | 多租户侵入面 | 60 个端点、全部查询、files/vault/events 隔离 | 最先启动；dependency 注入 + 默认 project 迁移；RLS 二期 |
| 2 | Compaction 正确性 | 摘要截断切坏工具链配对、重放不一致 | 截断点强制轮边界；压缩事件闭环进重放；长会话回归用例 |
| 3 | 单实例→分布式 | advisory lock 接管 vs Temporal 双正确性来源 | M1–M3 自研增量（方案 1），M4 评估 Temporal；事件流永远只管事实 |
| 4 | 打断的副作用一致性 | 杀进程可能留下半写文件 | PRD 已接受该语义（3 秒宽限强杀）；文档明示 |
| 5 | DNS 重绑定绕过白名单 | egress proxy 按域名过滤的固有风险 | 解析后校验 IP；拒绝解析到内网地址 |
| 6 | AGPL 传染 | 若引入 MinIO/Lago（PRD §9.1） | Linchpin files 的 FileStore 抽象已留 S3 接口；法务复核前先 Local/S3 直连 |
| 7 | 会话锁版本 vs 热更新 | 客户可能依赖"改 agent 立即生效"旧行为 | 默认锁版本（PRD 要求）；文档标注 breaking change |
| 8 | requires_action 语义变更 | 现有集成可能轮询 running 判断等待 | 新旧状态并存一个过渡期（细分事件保留） |

---

## 10. 附录：Linchpin 与 PRD §9 组件映射

| PRD §9 组件 | Linchpin 的替代关系 |
|---|---|
| codex core / LangGraph（harness 内核） | **Linchpin 编排器即此物**（事件溯源 + 工具路由 + 审批三态），无需引入 |
| NATS JetStream（事件总线） | Postgres events 表 + LISTEN/NOTIFY 已覆盖 M1–M3 量级；上量后再评估 |
| 自研 API 层 / 控制台会话视图 | Linchpin 主业，Console 已有实时视图 |
| LiteLLM | 唯一推荐新增的模型层组件（B11，零代码接入） |
| OpenBao | Linchpin vault 够 MVP；企业级动态凭据/轮换（M4+）再评估 |
| MinIO | FileStore 抽象已留 S3 接口，部署 MinIO 或云 S3 均可 |
| Temporal / E2B / APISIX / Keycloak / Langfuse / OpenMeter+Lago | 按本方案 C 类与 B10/B11 增量引入 |

按此方案，Linchpin 使 PRD §9.2"需要自研的三块"变为"已交付、待对齐"，M1 的实际自研余量进一步压缩到：**A 类语义对齐 + compaction + 多租户启动**。
