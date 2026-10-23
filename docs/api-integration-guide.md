# Linchpin API 接入指南与参数参考

> 适用版本：Linchpin v0.7.0（API v1 / v2）｜ 文档整理日期：2026-10-22
>
> 本文由两部分构成：
>
> - **第 1–8 章（手写）**：接入流程、认证模型、密钥分配、通用约定、SSE 事件流与安全要点；
> - **第 9–26 章（自动生成）**：从本构建的 OpenAPI 快照生成的逐端点参数参考，覆盖全部 **101 个端点**与 **92 个 schema**，与代码严格同步。
>
> 所有请求/响应示例均以 `http://localhost:8000` 为例，生产环境请替换为实际域名。

## 目录

- [第 1 章 概述](#1-概述)
- [第 2 章 核心概念：多租户模型与 Key 即身份](#2-核心概念多租户模型与-key-即身份)
- [第 3 章 认证方式](#3-认证方式)
- [第 4 章 密钥分配模式（新老用户接入）](#4-密钥分配模式新老用户接入)
- [第 5 章 完整接入流程（Step 0–7）](#5-完整接入流程step-07)
- [第 6 章 通用约定：错误信封、分页、幂等、API 版本](#6-通用约定错误信封分页幂等api-版本)
- [第 7 章 SSE 事件流](#7-sse-事件流)
- [第 8 章 安全要点](#8-安全要点)
- 第 9 章 管理与项目（`admin`）
- 第 10 章 Agent 配置（`agents`）
- 第 11 章 产物（`artifacts`）
- 第 12 章 审计日志（`audit`）
- 第 13 章 部署（`deployments`）
- 第 14 章 Dreams（`dreams`）
- 第 15 章 环境（`environments`）
- 第 16 章 执行节点（`executors`）
- 第 17 章 文件（`files`）
- 第 18 章 记忆库（`memory`）
- 第 19 章 会话资源（`session_resources`）
- 第 20 章 会话（`sessions`）★ 核心
- 第 21 章 技能（`skills`）
- 第 22 章 用量（`usage`）
- 第 23 章 凭据保险库（`vaults`）
- 第 24 章 Webhook 端点（`webhooks`）
- 第 25 章 其他（`other`）
- [第 26 章 附录：Schema 速查](#26-附录schema-速查)

---

## 1. 概述

### 1.1 Linchpin 是什么

Linchpin 是一个**多租户 Agent 会话控制面（control plane）**，由两部分组成：

- **linchpin-api**：FastAPI 后端，负责 Agent 配置、会话编排、事件流、记忆库、凭据保险库等；
- **linchpin-console**：浏览器控制台，用于本地运维（查看项目、会话、事件等）。

接入方的业务系统通过 REST API 创建项目、签发密钥、创建会话，并通过 SSE 实时消费 Agent 的执行事件。

### 1.2 基础信息

| 项 | 值 |
|---|---|
| Base URL（本机） | `http://localhost:8000` |
| 交互式文档 | `http://localhost:8000/docs`（Swagger UI，以运行中服务为准） |
| OpenAPI 快照 | `http://localhost:8000/openapi.json` |
| 健康检查 | `GET /health`（无认证、无 `/v1` 前缀） |
| 认证方式 | `Authorization: Bearer <key>`（SSE 端点另接受 `?token=`，见第 3 章） |
| 请求/响应格式 | `application/json`（SSE 为 `text/event-stream`） |
| API 版本协商 | `Linchpin-API-Version` 请求头（见 6.5 节） |

### 1.3 配套文档

- [docs/error-codes.md](error-codes.md) — 错误码完整定义；
- [docs/limits.md](limits.md) — 限流与配额的详细语义；
- [docs/model-gateway.md](model-gateway.md) — 模型网关（Provider 接入）说明；
- [docs/vault-credential-guide.md](vault-credential-guide.md) — 凭据保险库专题接入文档（行为导向）；
- [docs/api-vault-credential-test-cases.md](api-vault-credential-test-cases.md) / [docs/api-vault-credential-test-report.md](api-vault-credential-test-report.md) — vault 凭据链路专项测试用例与执行报告。

### 1.4 端点分组索引

第 9–25 章按 OpenAPI tag 分组列出全部端点；下表是 tag 与章节的对照。

| Tag | 章节 | 说明 | 端点数 |
|---|---|---|---|
| `admin` | 9 | 项目与 API Key 管理 | 6 |
| `agents` | 10 | Agent 配置（模型/工具/MCP/技能） | 6 |
| `artifacts` | 11 | 会话产物 | 3 |
| `audit` | 12 | 审计日志 | 1 |
| `deployments` | 13 | 定时/触发部署 | 7 |
| `dreams` | 14 | Dreams | 4 |
| `environments` | 15 | 沙箱运行环境 | 5 |
| `executors` | 16 | 执行节点 | 5 |
| `files` | 17 | 会话文件 | 5 |
| `memory` | 18 | 记忆库 | 14 |
| `session_resources` | 19 | 会话资源挂载 | 4 |
| `sessions` | 20 | 会话与事件（★ 核心接入面） | 17 |
| `skills` | 21 | 技能 | 5 |
| `usage` | 22 | 用量统计 | 1 |
| `vaults` | 23 | 凭据保险库 | 11 |
| `webhooks` | 24 | Webhook 端点 | 6 |
| `other` | 25 | 健康检查 | 1 |
| **合计** | | | **101** |

---

## 2. 核心概念：多租户模型与 Key 即身份

### 2.1 多租户三层模型

```text
organization（组织）
└── project（项目）★ 租户边界 + 配额边界
    ├── agents / environments / sessions
    ├── api-keys / vaults / webhooks
    └── files / memory / artifacts ...
```

- **organization**：最顶层，一个部署内通常只对应一个组织；
- **project**：租户与配额的边界单位（`max_concurrent_sessions`、`monthly_token_budget` 都挂在 project 上）；
- **资源**：Agent、环境、会话等全部隶属于唯一的 project。

隔离在数据库层由 **PostgreSQL 行级安全（RLS）**强制：每个请求的租户上下文被钉定到连接上，任何跨 project / 跨 org 的访问都表现为「资源不存在」（404），而不是报错泄露存在性。

### 2.2 Key 即身份（AuthContext）

Linchpin **没有独立的「用户」对象**——API Key 就是身份。每个请求经过统一的认证依赖后解析出 `AuthContext`：

| 字段 | 含义 |
|---|---|
| `org_id` | 所属组织 |
| `project_id` | 当前项目（租户边界） |
| `api_key_id` | DB 签发 key 的记录 ID；bootstrap key 为 `None` |
| `scopes` | 权限范围列表 |
| `rate_limit_rpm` | 每 key 每分钟请求上限（可选） |

认证成功后，中间件将 project 上下文钉定到数据库连接（`app.current_project` GUC），后续所有查询自动按租户过滤——**不需要（也不应该）在业务代码里手写租户过滤**。

### 2.3 两类 API Key

| | Bootstrap Key | 业务 Key（DB 签发） |
|---|---|---|
| 来源 | 服务端环境变量 `LINCHPIN_API_KEY` | `POST /v1/api-keys`（9.5 节） |
| 归属 | 固定 default 租户（`00000000-0000-0000-0000-000000000001`） | 绑定单个 project |
| scopes | 全权（`*`） | 创建时指定 |
| 限流 | 不限流 | `rate_limit_rpm`（可选） |
| 服务端存储 | 仅环境变量 | 仅存 SHA-256 哈希 + 12 字符前缀 |
| 可调用端点 | 全部（含两个 bootstrap-only 端点） | 除 bootstrap-only 外全部 |
| 用途 | 引导：建项目、签 key、调配额 | 日常业务调用 |
| 吊销 | 更换环境变量并重启服务 | `DELETE /v1/api-keys/{id}`，≤60s 生效 |

**Bootstrap-only 端点仅两个**（业务 key 调用会被拒绝，防止租户自我扩权）：

- `POST /v1/projects` — 创建项目（9.2 节）；
- `PATCH /v1/projects/{project_id}` — 修改项目配额（9.3 节）。

### 2.4 授权模型（scopes）

授权在**集中依赖**中执行，路由代码零散检查：

- **读开放**：`GET` / `HEAD` / `OPTIONS` 请求，任何有效 key 均可读取；
- **写受控**：变更类方法（`POST` / `PATCH` / `PUT` / `DELETE`）要求 scope **`"{资源段}:write"`**，资源段即 `/v1/` 后第一段路径，例如：
  - `POST /v1/sessions/...` → 需要 `sessions:write`
  - `POST /v1/agents` → 需要 `agents:write`
  - `DELETE /v1/vaults/{id}` → 需要 `vaults:write`
- **通配**：`*` 表示全部权限；
- `/v1/` 之外的路径（如 `/health`）不做 scope 检查。

scope 不足返回 `403`（见 3.3 节）。

### 2.5 审计

敏感操作（签发/吊销 key、修改配额、创建项目等）自动写入审计日志，通过 `GET /v1/audit`（第 12 章）查询，用于合规与事后追查。

---

## 3. 认证方式

### 3.1 Authorization Header（标准方式）

```bash
curl -H "Authorization: Bearer <你的 API Key>" http://localhost:8000/v1/projects
```

- 格式必须为 `Bearer <key>`（大小写不敏感的 scheme、一个空格）；
- 缺失、格式错误、无效或已吊销的 key → `401`；
- 任何有效 key 都可通过 `GET /v1/api-keys` 查看本项目的 key 列表及其 `last_used_at`。

### 3.2 SSE 端点的 `?token=` 查询参数

浏览器原生 `EventSource` 无法自定义请求头，因此 **SSE 流端点额外接受 `?token=` 查询参数**：

```js
const es = new EventSource(
  `/v1/sessions/${sessionId}/stream?token=${encodeURIComponent(apiKey)}`
);
```

注意：URL 查询参数可能进入访问日志，生产环境请做好日志脱敏或由网关注入（见第 8 章）。

### 3.3 认证失败与权限不足

所有错误响应使用统一信封（顶层 `{"error", "message", "detail"}`，见 6.1 节）：

`401` 未认证（key 缺失 / 格式错误 / 无效 / 已吊销）：

```json
{
  "error": "unauthorized",
  "message": "Invalid API key",
  "detail": null
}
```

`403` 已认证但 scope 不足：

```json
{
  "error": "forbidden",
  "message": "missing scope sessions:write",
  "detail": null
}
```

### 3.4 吊销生效窗口（≤60s）

DB 签发的 key 查询结果有 **60 秒进程内缓存**。因此：

- 吊销一个 key 后，**最迟 60 秒**内全网生效（单副本部署内）；
- 新签发的 key 同样存在最长 60 秒的「预热窗口」，签发后立即调用可能偶发 401，重试即可；
- 高可用多副本部署时，各副本独立缓存，生效时间以各自缓存到期为准。

---

## 4. 密钥分配模式（新老用户接入）

### 4.1 「新老用户判定」由调用方完成

Linchpin 不维护终端用户账号体系，**新老用户的判定发生在你自己的业务系统**中（例如用户注册/开通流程）：

- **新用户** → 走一遍第 5 章的 Step 2–3：创建 project、签发 key；
- **老用户** → 复用既有 project（可按需签发新 key 或沿用旧 key）。

判定完成后，由你持有 bootstrap key 的开通服务调用 API 完成供给（provisioning）。

### 4.2 模式 A：一用户一项目一密钥（推荐）

```text
用户 U1 → project P1 → key K1
用户 U2 → project P2 → key K2
```

优点：

- 数据天然隔离（RLS 按 project 隔离）；
- 配额独立（并发会话数、月 token 预算按用户分别限制）；
- 可单独吊销单个用户的访问，不影响他人；
- 审计日志归属清晰。

适用：SaaS 多租户、按客户计费、需要独立配额/独立注销的场景。

### 4.3 模式 B：共享项目 + 共享密钥

```text
用户 U1 ┐
用户 U2 ┼→ project P1 → key K1（共享）
用户 U3 ┘
```

优点：接入最简单、无需动态供给。

代价：

- 配额全体共享（一个用户可耗尽所有人的额度）；
- 审计日志无法区分到人；
- 单个用户退出只能通过**整 key 轮换**（签新 key → 各端切换 → 删旧 key）实现。

适用：内部工具、原型验证、单一信任域。

### 4.4 密钥生命周期

```text
签发（明文仅一次） → 使用（last_used_at 持续更新） → 监控 → 轮换 → 吊销
```

1. **签发**：`POST /v1/api-keys`；响应中的 `key` 明文**仅此一次**，立即安全保存；
2. **使用**：业务侧以 `Authorization: Bearer` 携带；
3. **监控**：`GET /v1/api-keys` 查看 `last_used_at` 与限流配置，发现长期未用或异常调用可提前处理；
4. **轮换**：签发新 key → 调用方切换 → 吊销旧 key（无 key 更新端点，修改 scopes/限流 = 签新 key）；
5. **吊销**：`DELETE /v1/api-keys/{key_id}` 软删除，返回 204，最迟 60s 生效。

---

## 5. 完整接入流程（Step 0–7）

以下流程覆盖「从零启动到前端收到 Agent 流式响应」的完整链路。示例统一使用环境变量：

```bash
export LINCHPIN_URL="http://localhost:8000"
```

### Step 0：部署前置（服务端）

服务端启动前需要配置以下环境变量：

| 环境变量 | 必需 | 说明 |
|---|---|---|
| `DATABASE_URL` | 是 | PostgreSQL 连接串（ migrations 会自动建表） |
| `LINCHPIN_API_KEY` | 是 | **Bootstrap key**：自定义随机串（建议 ≥32 字符），仅保存在服务端 |
| `VAULT_ENCRYPTION_KEY` | 条件必需 | 启用凭据保险库（vault）前必须配置；未配置时 vault 端点不可用 |
| `LINCHPIN_SKIP_DOCKER` | 否 | 本机无 Docker 时设为 `true`，跳过沙箱容器检查 |

启动并验证：

```bash
export LINCHPIN_SKIP_DOCKER=true
uvicorn app.main:app --host 0.0.0.0 --port 8000

# 健康检查（无需认证）
curl -s http://localhost:8000/health
```

### Step 1：验证 Bootstrap Key

```bash
export BOOTSTRAP_KEY="lpk_xxxxxxxxxxxxxxxxxxxxxxxx"   # 即 LINCHPIN_API_KEY 的值

curl -s -H "Authorization: Bearer $BOOTSTRAP_KEY" \
  "$LINCHPIN_URL/v1/projects"
# 200 → 返回项目数组（初始为空 []）
# 401 → 服务端环境变量与传入 key 不一致，检查启动配置
```

### Step 2：创建项目（新用户开通）

```bash
curl -s -X POST "$LINCHPIN_URL/v1/projects" \
  -H "Authorization: Bearer $BOOTSTRAP_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "客户A工作区",
    "slug": "customer-a",
    "max_concurrent_sessions": 5,
    "monthly_token_budget": 1000000
  }'
```

- `name` 必填；`slug` 可选（省略时从 name 推导），小写 kebab-case、组织内唯一，冲突返回 **409**；
- 两个配额字段可省略（= 不限）；
- 成功返回 **201** 与 `ProjectResponse`，记下 `id`：

```bash
export PROJECT_ID="<返回的 id>"
```

> 该端点为 **bootstrap-only**：业务 key 调用会被拒绝。

### Step 3：为项目签发业务 Key

```bash
curl -s -X POST "$LINCHPIN_URL/v1/api-keys" \
  -H "Authorization: Bearer $BOOTSTRAP_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "customer-a-app",
    "project_id": "'$PROJECT_ID'",
    "scopes": ["agents:write", "environments:write", "sessions:write"],
    "rate_limit_rpm": 120
  }'
```

- `project_id` 省略时绑定到调用者所属项目；不属于调用者组织的项目 → **404**；
- `scopes` 建议按最小权限授予（`*` 仅在可信内部场景使用）；
- 成功返回 **201**，其中的 `key` 字段是**唯一一次**出现明文：

```bash
export BIZ_KEY="<立即保存的 key 明文>"
```

### Step 4：用业务 Key 准备资源并开启会话

**4a. 创建环境与 Agent**（字段详见第 15、10 章）：

```bash
curl -s -X POST "$LINCHPIN_URL/v1/environments" \
  -H "Authorization: Bearer $BIZ_KEY" -H "Content-Type: application/json" \
  -d '{"name": "python-sandbox", "config": {"networking": {"type": "none"}}}'
# → 记下 id 为 ENV_ID

curl -s -X POST "$LINCHPIN_URL/v1/agents" \
  -H "Authorization: Bearer $BIZ_KEY" -H "Content-Type: application/json" \
  -d '{
    "name": "客服助手",
    "system": "你是一个严谨的中文客服助手。",
    "model": {"provider": "openrouter", "id": "anthropic/claude-3.5-sonnet"}
  }'
# → 记下 id 为 AGENT_ID
```

**4b. 创建会话**（端点详解见 20.9 节）：

```bash
export IDEMPOTENCY_KEY="$(uuidgen)"

curl -s -X POST "$LINCHPIN_URL/v1/sessions" \
  -H "Authorization: Bearer $BIZ_KEY" \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: $IDEMPOTENCY_KEY" \
  -d '{
    "agent_id": "'$AGENT_ID'",
    "environment_id": "'$ENV_ID'",
    "title": "首单咨询",
    "metadata": {"channel": "web", "user_ref": "u-1001"}
  }'
# 201 → 记下 id 为 SESSION_ID
```

- 最小必填 body：`agent_id` + `environment_id`；
- 可选：`title`、`metadata`（自由对象）、`ttl_seconds`、`vault_ids`、`resources`（判别式联合：File / MemoryStore / GitRepository / GithubRepository / Vault，字段见 20.9 节）、`outcome`；
- 建议始终携带 `Idempotency-Key`（见 6.4 节），网络超时后安全重试。

**4c. 发送初始用户事件**（A4 状态门控，见 7.7 节）：

```bash
curl -s -X POST "$LINCHPIN_URL/v1/sessions/$SESSION_ID/events" \
  -H "Authorization: Bearer $BIZ_KEY" -H "Content-Type: application/json" \
  -d '{"events": [{"type": "user.message", "payload": {"text": "帮我查询订单状态"}}]}'
# 201 → 返回 EventResponse 数组
```

- `user.message` 的 payload 约定为 `{"text": "..."}`（`content` 字段兼容，可传结构化列表）；
- 未知事件类型 → 422；已归档会话 → 409；当前状态不接受的类型 → 409。

**4d. 拉取历史事件**（cursor 分页，见 6.3 节）：

```bash
curl -s -H "Authorization: Bearer $BIZ_KEY" \
  "$LINCHPIN_URL/v1/sessions/$SESSION_ID/events?limit=50&types[]=agent.message&types[]=session.status"
```

### Step 5：前端订阅 SSE 事件流

详见第 7 章。最简浏览器接入：

```js
const es = new EventSource(
  `/v1/sessions/${sessionId}/stream?token=${encodeURIComponent(apiKey)}`
);
es.addEventListener("agent.message_delta", (e) => {
  const frame = JSON.parse(e.data);
  appendToChat(frame.payload);          // 流式增量
});
es.onerror = () => {
  // EventSource 自动重连并回传 Last-Event-ID，服务端从断点续传
};
```

服务端会先重放该会话的历史事件，再切换为实时推送，前端无需自行补齐历史。

### Step 6：调整配额与限流

```bash
curl -s -X PATCH "$LINCHPIN_URL/v1/projects/$PROJECT_ID" \
  -H "Authorization: Bearer $BOOTSTRAP_KEY" -H "Content-Type: application/json" \
  -d '{"max_concurrent_sessions": 10, "monthly_token_budget": null}'
```

PATCH 为**三态语义**（详见 9.3 节与 docs/limits.md）：

| 请求写法 | 含义 |
|---|---|
| 字段省略 | 保持现值不变 |
| 字段 = `null` | 清除上限（不限量） |
| 字段 = 数值 | 设置为该值 |

超限时的两种 `429`（见 6.2 节）：

```json
{"error": "rate_limited", "message": "Rate limit exceeded: 120 requests per minute", "detail": {"retry_after": 17}}
```
（响应头另含 `Retry-After: 17`）

```json
{"error": "quota_exceeded", "message": "…", "detail": {"limit_type": "monthly_tokens"}}
```
（`limit_type` 取值：`concurrent_sessions` / `monthly_tokens`）

### Step 7：轮换与吊销

```bash
# 1) 签发新 key（Step 3 同款请求）
# 2) 调用方切换到新 key
# 3) 吊销旧 key
curl -s -X DELETE "$LINCHPIN_URL/v1/api-keys/<OLD_KEY_ID>" \
  -H "Authorization: Bearer $BOOTSTRAP_KEY"
# 204 No Content；旧 key 最迟 60s 后失效
```

`GET /v1/api-keys` 返回的 key 记录中拿 `id`（不是明文——明文已不可再得）。

---

## 6. 通用约定

### 6.1 错误响应信封

**所有错误**（含参数校验失败与未处理异常）统一为顶层三字段信封：

```json
{
  "error": "错误码字符串",
  "message": "人类可读描述",
  "detail": "结构化补充信息，无则为 null"
}
```

### 6.2 常见错误码

| HTTP | `error` | 触发场景 | `detail` |
|---|---|---|---|
| 401 | `unauthorized` | key 缺失 / 格式错误 / 无效 / 已吊销 | `null` |
| 403 | `forbidden` | 已认证但 scope 不足（`missing scope {资源段}:write`） | `null` |
| 404 | `not_found` | 资源不存在，或跨租户不可见（RLS） | 视端点而定 |
| 409 | 视场景 | slug 冲突；幂等键冲突；归档会话；A4 状态门控拒绝 | 视场景而定 |
| 422 | `invalid_request` | 请求体/参数校验失败 | `{"errors": [...]}`（Pydantic 错误数组，含 `loc`/`msg`/`type`/`input`） |
| 429 | `rate_limited` | 超 key 限流（滑动 60s 窗口） | `{"retry_after": 秒数}`；响应头含 `Retry-After` |
| 429 | `quota_exceeded` | 超项目配额 | `{"limit_type": "concurrent_sessions" \| "monthly_tokens"}` |
| 500 | （内部错误） | 未处理异常；细节仅写服务端日志 | `null` |

完整错误码定义见 [docs/error-codes.md](error-codes.md)。

### 6.3 分页

**绝大多数列表端点**（agents、sessions、api-keys、vaults 等）使用 offset 分页：

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `limit` | integer 1–100 | 20 | 每页数量 |
| `offset` | integer | 0 | 起始偏移 |
| `include_archived` | boolean | false | 是否包含已归档资源 |

**事件端点例外**（`GET /v1/sessions/{id}/events`）使用 cursor 分页（事件按 `seq` 单调追加，offset 分页在并发追加下会漂移）：

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `after_cursor` | string \| null | — | 从该游标之后开始取 |
| `limit` | integer 1–100 | 50 | 每页数量 |
| `types[]` | string[] | — | 按事件类型过滤（未知类型 → 422） |

响应含 `next_cursor`：非 `null` 表示还有更多，将其作为下一次请求的 `after_cursor`。

### 6.4 幂等（Idempotency-Key）

仅两个端点支持：`POST /v1/sessions` 与 `POST /v1/sessions/{id}/events`。

- 请求头携带任意唯一字符串（推荐 UUID）；
- 同 key 重复请求返回**首次缓存的响应**（仅记录 2xx 结果）；
- 同 key 但请求体不同 → **409**；
- 缓存 TTL 24 小时；
- 网络超时后可安全重试——这是推荐始终携带该头的核心原因。

### 6.5 API 版本协商（Linchpin-API-Version）

- 请求头 `Linchpin-API-Version` 指定 API 语义版本：
  - **缺省** = legacy（v1 语义）；
  - **`2026-05-13`** = 当前唯一的 dated 版本（v2 语义）；
  - 未知值 → **400**，响应含支持的版本列表；
- 服务端在响应头回显解析后的版本；legacy 输入的响应附带 `Linchpin-Deprecation` 头提示迁移；
- 版本在**会话创建时钉定**（`sessions.linchpin_api_version`），会话生命周期内不受后续请求头影响。

### 6.6 限流与配额

- **限流**：`api_keys.rate_limit_rpm`，per-key 滑动 60 秒窗口（按副本计），bootstrap key 不受限；超限 → `429 rate_limited` + `Retry-After`；
- **配额**：`projects.max_concurrent_sessions`（并发会话数）与 `projects.monthly_token_budget`（月 token 预算）；超限 → `429 quota_exceeded`；
- 配额检查为 **fail-open**：依赖异常时放行（限流仍生效），容量规划请以限流为主要防线。

详见 [docs/limits.md](limits.md)。

---

## 7. SSE 事件流

### 7.1 端点

| 端点 | 说明 |
|---|---|
| `GET /v1/sessions/{session_id}/stream` | **主端点**（20.15 节） |
| `GET /v1/sessions/{session_id}/events/stream` | 等价别名（20.14 节） |
| `GET /v1/sessions/{id}/events` | 备选：REST 拉取（cursor 分页） |
| `GET /v1/sessions/{id}/streaming` | 备选：轮询当前正在生成的累积文本（无流则 `null`） |

认证：`Authorization: Bearer` 或 `?token=`（EventSource 场景，见 3.2 节）。

### 7.2 帧格式

```text
id: 42
event: agent.message_delta
data: {"session_id":"4f0b…","type":"agent.message_delta","cursor":"42","seq":42,"event_id":"ev_…","payload":{…},"processed_at":"2026-10-22T09:30:12.481Z"}

```

- `id:` — 该事件的 `seq`（会话内单调递增）；
- `event:` — 事件类型（与 `data.type` 一致）；
- `data:` — 单行 JSON，结构见 7.3。

### 7.3 `data` 负载字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `session_id` | string | 会话 UUID |
| `type` | string | 事件类型 |
| `cursor` | string | 游标（= `seq`，用于续传） |
| `seq` | integer | 会话内单调递增序号 |
| `event_id` | string \| null | 事件 ID |
| `payload` | object | 事件负载，结构随事件类型而定 |
| `processed_at` | string \| null | 处理时间（ISO 8601） |

### 7.4 断线续传

重连时向服务端声明「我最后处理到哪」：

1. **`Last-Event-ID` 请求头**（优先）——浏览器 `EventSource` 自动携带；
2. **`?cursor=` 查询参数**——手动控制时使用。

服务端从该游标之后继续推送。**非法的续传点返回 422**（不会静默从头重放）。

### 7.5 连接行为：历史重放 + 实时推送

- **Phase 1**：连接建立后先按 `seq` 顺序重放该会话的历史事件（从游标/起点到当前）；
- **Phase 2**：重放完成后切换为实时推送（PostgreSQL `LISTEN/NOTIFY`，每会话独立 channel）。

前端无需在订阅前另行拉取历史。

### 7.6 事件类型总表（5 族 37 型）

**user.\***（用户侧输入）：

| 类型 | 说明 |
|---|---|
| `user.message` | 用户消息（触发/继续对话） |
| `user.steer` | 运行中转向指令（会被注入为带标记的用户消息） |
| `user.interrupt` | 打断当前执行 |
| `user.tool_confirmation` | 工具执行确认（配合 `session.requires_action`） |
| `user.custom_tool_result` | 自定义工具执行结果回传 |

**agent.\***（Agent 侧输出）：

| 类型 | 说明 |
|---|---|
| `agent.message` | 完整 Agent 消息 |
| `agent.message_delta` | 流式增量文本（打字机效果用这个） |
| `agent.thinking` | 思考过程 |
| `agent.tool_use` / `agent.tool_result` | 内置工具调用与结果 |
| `agent.mcp_tool_use` / `agent.mcp_tool_result` | MCP 工具调用与结果 |
| `agent.custom_tool_use` | 自定义工具调用 |
| `agent.deliverable` / `agent.deliverable_dropped` | 产物交付 / 丢弃 |
| `agent.thread_create` | 创建子线程请求 |

**session.\***（会话生命周期与状态）：

| 类型 | 说明 |
|---|---|
| `session.status_running` / `status_idle` / `status_rescheduled` / `status_terminated` | 状态迁移 |
| `session.status` | 状态快照 |
| `session.error` | 会话错误 |
| `session.requires_action` | 需要用户操作（如工具确认） |
| `session.resource_mount_failed` / `session.memory_mount_failed` | 资源 / 记忆挂载失败 |
| `session.deprecation_used` | 会话使用了弃用输入 |
| `session.compacted` | 上下文压缩完成 |
| `session.outcome_evaluation_started` / `outcome_evaluation_ended` | 结果评估开始 / 结束 |
| `session.thread_created` / `thread_idled` / `thread_terminated` | 子线程生命周期 |

**memory.\***（记忆库）：

| 类型 | 说明 |
|---|---|
| `memory.write` | 记忆写入 |
| `memory.write_rejected` | 记忆写入被拒 |
| `memory.gc` | 记忆回收 |

**span.\***（遥测）：

| 类型 | 说明 |
|---|---|
| `span.model_request_start` / `span.model_request_end` | 模型请求遥测开始 / 结束 |

> `payload` 的具体结构随事件类型与 Agent 前端约定而定；建议接入时先在 `/docs` 试发或抓取真实事件确认字段。

### 7.7 发送事件与状态门控（A4）

`POST /v1/sessions/{id}/events` 按顺序执行校验：

1. 事件类型合法 → 否则 **422**；
2. 会话存在 → 否则 **404**；
3. 会话未归档 → 否则 **409**；
4. **A4 状态门控**：按会话当前状态决定可接收的事件类型，被拒类型在 **409** 响应中返回。示例：运行中（running）状态接受 `user.steer`。

部署（deployments）场景的 `initial_events` 仅允许 `user.message` / `user.steer`——防止通过部署预置伪造 `agent.*` / `session.*` 事件。

### 7.8 事件可见性

`GET /v1/sessions/{id}/events`（REST 拉取）与 SSE 推送的事件完全同源（同一事件日志 + `seq`），可混用于「冷启动补历史 + 热路径实时推送」架构。

---

## 8. 安全要点

1. **明文仅一次**：`POST /v1/api-keys` 响应中的 `key` 只出现一次；服务端只存 SHA-256 哈希与 12 字符前缀，**任何人都无法找回明文**，丢失即重新签发。
2. **Bootstrap key 只在服务端**：仅通过环境变量注入；绝不写入代码仓库、构建产物或下发到浏览器/移动端。（控制台为本地运维工具，key 存于本机浏览器 `localStorage`，仅限受信任的单机环境使用。）
3. **最小 scope**：业务 key 按需授予（如 `["sessions:write"]`），避免 `*`；scope 只控制写操作，读对所有有效 key 开放——**不要把敏感数据放进能被任意本项目 key 读取的资源里**。
4. **吊销窗口**：60 秒进程内缓存意味着吊销/换 key 最迟 60s 生效；对安全事件应急时，同时考虑项目配额清零、网络层封禁等辅助手段。
5. **RLS 硬隔离**：租户上下文由中间件钉定，跨 project 访问一律 404；不要绕过 API 直接拼 SQL。
6. **传输安全**：生产必须 HTTPS；`?token=` 会进入访问日志——日志脱敏或由网关注入认证。
7. **VAULT_ENCRYPTION_KEY**：未配置时凭据保险库整体不可用；生产启用 vault 前必须配置并妥善保管该密钥（丢失 = 凭据不可解密）。
8. **限流是主防线**：配额检查 fail-open，限流始终生效；对外暴露的业务 key 务必设置 `rate_limit_rpm`。
9. **审计与监控**：敏感操作自动记录审计日志（第 12 章）；结合 `GET /v1/api-keys` 的 `last_used_at` 与 `GET /v1/audit` 排查异常。
10. **日志卫生**：展示 key 只用 12 字符前缀；应用日志、异常栈中绝不打印完整明文。

---

<!-- 本部分由生成器脚本从本构建的 OpenAPI 快照自动生成，请勿手改。 -->

## 9. 管理与项目（`admin`）

### 9.1 GET /v1/projects

**List Projects**

List projects in the caller's organization.

**响应 200**：Successful Response

返回 JSON 数组，元素为 `ProjectResponse`，字段如下：

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `id` | string | 是 | — | — |
| `org_id` | string | 是 | — | — |
| `name` | string | 是 | — | — |
| `slug` | string | 是 | — | — |
| `max_concurrent_sessions` | integer \| null | 否 | — | — |
| `monthly_token_budget` | integer \| null | 否 | — | — |
| `created_at` | string(date-time) | 是 | — | — |

**错误**：401 未认证 · 429 限流/配额超限 · 500 服务器内部错误（完整定义见第 6 章「通用约定」与 [error-codes.md](error-codes.md)）。

### 9.2 POST /v1/projects

**Create Project**

Create a project in the caller's organization (bootstrap key only).<br><br>Completes the pure-API provisioning flow: create a project, then bind keys to it via ``POST /v1/api-keys``. Bootstrap-only for the same reason as the PATCH quotas route — a tenant key must not grow its own tenant tree.

> 注意：仅 bootstrap key 可调用。

**请求体**（必填）

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `name` | string | 是 | 长度 1..∞ | — |
| `slug` | string \| null | 否 | 长度 1..64; 正则 `^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$` | — |
| `max_concurrent_sessions` | integer \| null | 否 | ≥ 1.0 | — |
| `monthly_token_budget` | integer \| null | 否 | ≥ 1.0 | — |

**响应 201**：Successful Response

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `id` | string | 是 | — | — |
| `org_id` | string | 是 | — | — |
| `name` | string | 是 | — | — |
| `slug` | string | 是 | — | — |
| `max_concurrent_sessions` | integer \| null | 否 | — | — |
| `monthly_token_budget` | integer \| null | 否 | — | — |
| `created_at` | string(date-time) | 是 | — | — |

**错误**：422 参数校验失败 · 401 未认证 · 429 限流/配额超限 · 500 服务器内部错误（完整定义见第 6 章「通用约定」与 [error-codes.md](error-codes.md)）。

### 9.3 PATCH /v1/projects/{project_id}

**Update Project**

Update project quota limits (Atlas M5, FR-11.2/11.3).<br><br>Bootstrap key only: a project-scoped DB key must not be able to lift its own tenant's limits. Omitted fields keep their current value; explicit ``null`` clears a limit (unlimited).

> 注意：仅 bootstrap key 可调用。

**路径参数**

| 参数 | 类型 | 说明 |
|---|---|---|
| `project_id` | string | — |

**请求体**（必填）

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `max_concurrent_sessions` | integer \| null | 否 | ≥ 1.0 | — |
| `monthly_token_budget` | integer \| null | 否 | ≥ 1.0 | — |

**响应 200**：Successful Response

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `id` | string | 是 | — | — |
| `org_id` | string | 是 | — | — |
| `name` | string | 是 | — | — |
| `slug` | string | 是 | — | — |
| `max_concurrent_sessions` | integer \| null | 否 | — | — |
| `monthly_token_budget` | integer \| null | 否 | — | — |
| `created_at` | string(date-time) | 是 | — | — |

**错误**：422 参数校验失败 · 401 未认证 · 429 限流/配额超限 · 500 服务器内部错误（完整定义见第 6 章「通用约定」与 [error-codes.md](error-codes.md)）。

### 9.4 GET /v1/api-keys

**List Api Keys**

List API keys for the caller's project (metadata only).

**响应 200**：Successful Response

返回 JSON 数组，元素为 `ApiKeyResponse`，字段如下：

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `id` | string | 是 | — | — |
| `project_id` | string | 是 | — | — |
| `name` | string | 是 | — | — |
| `prefix` | string | 是 | — | — |
| `scopes` | string[] | 是 | — | — |
| `status` | string | 是 | — | — |
| `rate_limit_rpm` | integer \| null | 否 | — | — |
| `created_at` | string(date-time) | 是 | — | — |
| `last_used_at` | string(date-time) \| null | 否 | — | — |

**错误**：401 未认证 · 429 限流/配额超限 · 500 服务器内部错误（完整定义见第 6 章「通用约定」与 [error-codes.md](error-codes.md)）。

### 9.5 POST /v1/api-keys

**Create Api Key**

Issue a new API key bound to a project.<br><br>The plaintext key is returned exactly once in this response — only its SHA-256 hash and a 12-char display prefix are stored.

**请求体**（必填）

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `name` | string | 否 | 默认 `""` | — |
| `project_id` | string \| null | 否 | — | — |
| `scopes` | string[] \| null | 否 | — | — |
| `rate_limit_rpm` | integer \| null | 否 | ≥ 1.0 | — |

**响应 201**：Successful Response

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `id` | string | 是 | — | — |
| `project_id` | string | 是 | — | — |
| `name` | string | 是 | — | — |
| `prefix` | string | 是 | — | — |
| `scopes` | string[] | 是 | — | — |
| `status` | string | 是 | — | — |
| `rate_limit_rpm` | integer \| null | 否 | — | — |
| `created_at` | string(date-time) | 是 | — | — |
| `last_used_at` | string(date-time) \| null | 否 | — | — |
| `key` | string | 是 | — | — |

**错误**：422 参数校验失败 · 401 未认证 · 429 限流/配额超限 · 500 服务器内部错误（完整定义见第 6 章「通用约定」与 [error-codes.md](error-codes.md)）。

### 9.6 DELETE /v1/api-keys/{key_id}

**Revoke Api Key**

Revoke an API key (soft delete: ``status='revoked'``).<br><br>Only keys belonging to the caller's project can be revoked. Revocation takes effect immediately for new requests; the short-lived auth cache (60s TTL) is the upper bound on staleness.

**路径参数**

| 参数 | 类型 | 说明 |
|---|---|---|
| `key_id` | string | — |

**响应 204**：Successful Response

无响应体。

**错误**：422 参数校验失败 · 401 未认证 · 429 限流/配额超限 · 500 服务器内部错误（完整定义见第 6 章「通用约定」与 [error-codes.md](error-codes.md)）。

## 10. Agent 配置（`agents`）

### 10.1 GET /v1/agents

**List Agents**

Return a paginated list of agents. Archived agents are excluded by default; opt back in with ``?include_archived=true``.

**Query 参数**

| 参数 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `limit` | integer | 否 | ≥ 1; ≤ 100; 默认 `20` | — |
| `offset` | integer | 否 | ≥ 0; 默认 `0` | — |
| `include_archived` | boolean | 否 | 默认 `false` | — |

**响应 200**：Successful Response

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `data` | any[] | 是 | — | — |
| `next_cursor` | string \| null | 否 | — | — |
| `has_more` | boolean | 否 | 默认 `false` | — |

**错误**：422 参数校验失败 · 401 未认证 · 429 限流/配额超限 · 500 服务器内部错误（完整定义见第 6 章「通用约定」与 [error-codes.md](error-codes.md)）。

### 10.2 POST /v1/agents

**Create Agent**

Create a new agent resource.

**请求体**（必填）

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `name` | string | 是 | — | — |
| `model` | `ModelConfig` | 是 | — | — |
| `system` | string | 是 | — | — |
| `tools` | `BuiltinToolConfig` \| `CustomToolConfig`[] | 否 | — | — |
| `mcp_servers` | `StdioMCPServerConfig` \| `UrlMCPServerConfig`[] | 否 | — | — |
| `skills` | string[] | 否 | — | — |
| `multi_agents` | `SubagentConfig`[] | 否 | — | — |
| `description` | string \| null | 否 | — | — |
| `metadata` | object | 否 | — | — |

**响应 201**：Successful Response

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `id` | string | 是 | — | — |
| `name` | string | 是 | — | — |
| `version` | integer | 是 | — | — |
| `model` | `ModelConfig` | 是 | — | — |
| `system` | string | 是 | — | — |
| `tools` | `BuiltinToolConfig` \| `CustomToolConfig`[] | 否 | — | — |
| `mcp_servers` | `StdioMCPServerConfig` \| `UrlMCPServerConfig`[] | 否 | — | — |
| `skills` | string[] | 否 | — | — |
| `multi_agents` | `SubagentConfig`[] | 否 | — | — |
| `created_at` | string(date-time) | 是 | — | — |
| `description` | string \| null | 否 | — | — |
| `metadata` | object | 否 | — | — |
| `archived_at` | string(date-time) \| null | 否 | — | — |

**错误**：422 参数校验失败 · 401 未认证 · 429 限流/配额超限 · 500 服务器内部错误（完整定义见第 6 章「通用约定」与 [error-codes.md](error-codes.md)）。

### 10.3 GET /v1/agents/{agent_id}

**Get Agent**

Retrieve an agent by id. Returns archived agents too — callers can filter on ``archived_at`` if needed.

**路径参数**

| 参数 | 类型 | 说明 |
|---|---|---|
| `agent_id` | string | — |

**响应 200**：Successful Response

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `id` | string | 是 | — | — |
| `name` | string | 是 | — | — |
| `version` | integer | 是 | — | — |
| `model` | `ModelConfig` | 是 | — | — |
| `system` | string | 是 | — | — |
| `tools` | `BuiltinToolConfig` \| `CustomToolConfig`[] | 否 | — | — |
| `mcp_servers` | `StdioMCPServerConfig` \| `UrlMCPServerConfig`[] | 否 | — | — |
| `skills` | string[] | 否 | — | — |
| `multi_agents` | `SubagentConfig`[] | 否 | — | — |
| `created_at` | string(date-time) | 是 | — | — |
| `description` | string \| null | 否 | — | — |
| `metadata` | object | 否 | — | — |
| `archived_at` | string(date-time) \| null | 否 | — | — |

**错误**：422 参数校验失败 · 401 未认证 · 429 限流/配额超限 · 500 服务器内部错误（完整定义见第 6 章「通用约定」与 [error-codes.md](error-codes.md)）。

### 10.4 PATCH /v1/agents/{agent_id}

**Update Agent**

Partially update an agent and snapshot the prior config into agent_versions.<br><br>v0.2.0 item #5 — the snapshot is taken BEFORE the UPDATE applies, so GET /v1/agents/{id}/versions can return the exact agent config any past session pinned via its agent_version field.

**路径参数**

| 参数 | 类型 | 说明 |
|---|---|---|
| `agent_id` | string | — |

**请求体**（必填）

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `name` | string \| null | 否 | — | — |
| `model` | `ModelConfig` \| null | 否 | — | — |
| `system` | string \| null | 否 | — | — |
| `tools` | `BuiltinToolConfig` \| `CustomToolConfig`[] \| null | 否 | — | — |
| `mcp_servers` | `StdioMCPServerConfig` \| `UrlMCPServerConfig`[] \| null | 否 | — | — |
| `skills` | string[] \| null | 否 | — | — |
| `multi_agents` | `SubagentConfig`[] \| null | 否 | — | — |
| `description` | string \| null | 否 | — | — |
| `metadata` | object \| null | 否 | — | — |

**响应 200**：Successful Response

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `id` | string | 是 | — | — |
| `name` | string | 是 | — | — |
| `version` | integer | 是 | — | — |
| `model` | `ModelConfig` | 是 | — | — |
| `system` | string | 是 | — | — |
| `tools` | `BuiltinToolConfig` \| `CustomToolConfig`[] | 否 | — | — |
| `mcp_servers` | `StdioMCPServerConfig` \| `UrlMCPServerConfig`[] | 否 | — | — |
| `skills` | string[] | 否 | — | — |
| `multi_agents` | `SubagentConfig`[] | 否 | — | — |
| `created_at` | string(date-time) | 是 | — | — |
| `description` | string \| null | 否 | — | — |
| `metadata` | object | 否 | — | — |
| `archived_at` | string(date-time) \| null | 否 | — | — |

**错误**：422 参数校验失败 · 401 未认证 · 429 限流/配额超限 · 500 服务器内部错误（完整定义见第 6 章「通用约定」与 [error-codes.md](error-codes.md)）。

### 10.5 GET /v1/agents/{agent_id}/versions

**List Agent Versions**

List historical versions of an agent, newest first.<br><br>v0.2.0 item #5 — Sessions pin agent_version on creation; this endpoint lets callers fetch the exact agent config a session was running under. Returns 404 if the agent itself never existed; returns an empty list if the agent exists but has never been PATCH'd (no snapshots taken yet).

**路径参数**

| 参数 | 类型 | 说明 |
|---|---|---|
| `agent_id` | string | — |

**Query 参数**

| 参数 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `limit` | integer | 否 | ≥ 1; ≤ 100; 默认 `20` | — |
| `offset` | integer | 否 | ≥ 0; 默认 `0` | — |

**响应 200**：Successful Response

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `data` | any[] | 是 | — | — |
| `next_cursor` | string \| null | 否 | — | — |
| `has_more` | boolean | 否 | 默认 `false` | — |

**错误**：422 参数校验失败 · 401 未认证 · 429 限流/配额超限 · 500 服务器内部错误（完整定义见第 6 章「通用约定」与 [error-codes.md](error-codes.md)）。

### 10.6 POST /v1/agents/{agent_id}/archive

**Archive Agent**

Soft-delete an agent (v0.2.0 item #5). Idempotent — archiving an already-archived agent returns the same row without re-stamping.

**路径参数**

| 参数 | 类型 | 说明 |
|---|---|---|
| `agent_id` | string | — |

**响应 200**：Successful Response

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `id` | string | 是 | — | — |
| `name` | string | 是 | — | — |
| `version` | integer | 是 | — | — |
| `model` | `ModelConfig` | 是 | — | — |
| `system` | string | 是 | — | — |
| `tools` | `BuiltinToolConfig` \| `CustomToolConfig`[] | 否 | — | — |
| `mcp_servers` | `StdioMCPServerConfig` \| `UrlMCPServerConfig`[] | 否 | — | — |
| `skills` | string[] | 否 | — | — |
| `multi_agents` | `SubagentConfig`[] | 否 | — | — |
| `created_at` | string(date-time) | 是 | — | — |
| `description` | string \| null | 否 | — | — |
| `metadata` | object | 否 | — | — |
| `archived_at` | string(date-time) \| null | 否 | — | — |

**错误**：422 参数校验失败 · 401 未认证 · 429 限流/配额超限 · 500 服务器内部错误（完整定义见第 6 章「通用约定」与 [error-codes.md](error-codes.md)）。

## 11. 产物（Artifacts）（`artifacts`）

### 11.1 GET /v1/artifacts

**List Artifacts**

List artifacts saved by agents. ``session_id`` narrows to one session; omit it to list every artifact in the project.

**Query 参数**

| 参数 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `session_id` | string \| null | 否 | — | — |
| `limit` | integer | 否 | ≥ 1; ≤ 100; 默认 `20` | — |
| `offset` | integer | 否 | ≥ 0; 默认 `0` | — |

**响应 200**：Successful Response

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `data` | `FileResponse`[] | 是 | — | — |
| `next_cursor` | string \| null | 否 | — | — |
| `has_more` | boolean | 否 | 默认 `false` | — |

**错误**：422 参数校验失败 · 401 未认证 · 429 限流/配额超限 · 500 服务器内部错误（完整定义见第 6 章「通用约定」与 [error-codes.md](error-codes.md)）。

### 11.2 GET /v1/artifacts/{artifact_id}

**Get Artifact**

Get artifact metadata.

**路径参数**

| 参数 | 类型 | 说明 |
|---|---|---|
| `artifact_id` | string | — |

**响应 200**：Successful Response

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `id` | string | 是 | — | — |
| `filename` | string | 是 | — | — |
| `content_type` | string | 是 | — | — |
| `size_bytes` | integer | 是 | — | — |
| `sha256` | string | 是 | — | — |
| `source` | string | 是 | 枚举 `"upload"` / `"deliverable"` / `"artifact"` | — |
| `downloadable` | boolean | 是 | — | — |
| `scope_type` | string \| null | 否 | 固定值 `"session"` | — |
| `scope_id` | string \| null | 否 | — | — |
| `created_at` | string(date-time) | 是 | — | — |
| `archived_at` | string(date-time) \| null | 否 | — | — |

**错误**：422 参数校验失败 · 401 未认证 · 429 限流/配额超限 · 500 服务器内部错误（完整定义见第 6 章「通用约定」与 [error-codes.md](error-codes.md)）。

### 11.3 GET /v1/artifacts/{artifact_id}/content

**Get Artifact Content**

Stream artifact bytes as an attachment (RFC 5987 filename encoding).

**路径参数**

| 参数 | 类型 | 说明 |
|---|---|---|
| `artifact_id` | string | — |

**响应 200**：Successful Response

内容类型 `application/json`。

**错误**：422 参数校验失败 · 401 未认证 · 429 限流/配额超限 · 500 服务器内部错误（完整定义见第 6 章「通用约定」与 [error-codes.md](error-codes.md)）。

## 12. 审计日志（`audit`）

### 12.1 GET /v1/audit

**Export Audit**

Stream audit log entries as NDJSON (newest first).

**Query 参数**

| 参数 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `from` | string(date-time) \| null | 否 | — | — |
| `to` | string(date-time) \| null | 否 | — | — |
| `resource_type` | string \| null | 否 | — | — |
| `action` | string \| null | 否 | — | — |
| `limit` | integer | 否 | ≥ 1; ≤ 50000; 默认 `5000` | — |

**响应 200**：Successful Response

内容类型 `application/json`。

**错误**：422 参数校验失败 · 401 未认证 · 429 限流/配额超限 · 500 服务器内部错误（完整定义见第 6 章「通用约定」与 [error-codes.md](error-codes.md)）。

## 13. 部署（`deployments`）

### 13.1 GET /v1/deployments

**List Deployments**

Return a paginated list of deployments, optionally by agent_id.

**Query 参数**

| 参数 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `limit` | integer | 否 | ≥ 1; ≤ 100; 默认 `20` | — |
| `offset` | integer | 否 | ≥ 0; 默认 `0` | — |
| `agent_id` | string \| null | 否 | — | — |

**响应 200**：Successful Response

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `data` | any[] | 是 | — | — |
| `next_cursor` | string \| null | 否 | — | — |
| `has_more` | boolean | 否 | 默认 `false` | — |

**错误**：422 参数校验失败 · 401 未认证 · 429 限流/配额超限 · 500 服务器内部错误（完整定义见第 6 章「通用约定」与 [error-codes.md](error-codes.md)）。

### 13.2 POST /v1/deployments

**Create Deployment**

Create a deployment. ``next_run_at`` is computed from now in the given timezone; the first scheduled run fires at the next cron match.

**请求体**（必填）

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `agent_id` | string | 是 | — | — |
| `environment_id` | string | 是 | — | — |
| `cron` | string | 是 | — | — |
| `timezone` | string | 否 | 默认 `"UTC"` | — |
| `initial_events` | `InitialEvent`[] | 否 | — | — |

**响应 201**：Successful Response

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `id` | string | 是 | — | — |
| `agent_id` | string | 是 | — | — |
| `environment_id` | string | 是 | — | — |
| `cron` | string | 是 | — | — |
| `timezone` | string | 是 | — | — |
| `initial_events` | `InitialEvent`[] | 否 | — | — |
| `status` | string | 是 | 枚举 `"paused"` / `"active"` | — |
| `next_run_at` | string(date-time) \| null | 是 | — | — |
| `created_at` | string(date-time) | 是 | — | — |
| `archived_at` | string(date-time) \| null | 是 | — | — |

**错误**：422 参数校验失败 · 401 未认证 · 429 限流/配额超限 · 500 服务器内部错误（完整定义见第 6 章「通用约定」与 [error-codes.md](error-codes.md)）。

### 13.3 GET /v1/deployments/{deployment_id}

**Get Deployment**

**路径参数**

| 参数 | 类型 | 说明 |
|---|---|---|
| `deployment_id` | string | — |

**响应 200**：Successful Response

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `id` | string | 是 | — | — |
| `agent_id` | string | 是 | — | — |
| `environment_id` | string | 是 | — | — |
| `cron` | string | 是 | — | — |
| `timezone` | string | 是 | — | — |
| `initial_events` | `InitialEvent`[] | 否 | — | — |
| `status` | string | 是 | 枚举 `"paused"` / `"active"` | — |
| `next_run_at` | string(date-time) \| null | 是 | — | — |
| `created_at` | string(date-time) | 是 | — | — |
| `archived_at` | string(date-time) \| null | 是 | — | — |

**错误**：422 参数校验失败 · 401 未认证 · 429 限流/配额超限 · 500 服务器内部错误（完整定义见第 6 章「通用约定」与 [error-codes.md](error-codes.md)）。

### 13.4 PATCH /v1/deployments/{deployment_id}

**Update Deployment**

Update cron/timezone/initial_events. A cron or timezone change recomputes ``next_run_at`` from now — the new schedule starts from the edit, not the old plan.

**路径参数**

| 参数 | 类型 | 说明 |
|---|---|---|
| `deployment_id` | string | — |

**请求体**（必填）

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `cron` | string \| null | 否 | — | — |
| `timezone` | string \| null | 否 | — | — |
| `initial_events` | `InitialEvent`[] \| null | 否 | — | — |

**响应 200**：Successful Response

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `id` | string | 是 | — | — |
| `agent_id` | string | 是 | — | — |
| `environment_id` | string | 是 | — | — |
| `cron` | string | 是 | — | — |
| `timezone` | string | 是 | — | — |
| `initial_events` | `InitialEvent`[] | 否 | — | — |
| `status` | string | 是 | 枚举 `"paused"` / `"active"` | — |
| `next_run_at` | string(date-time) \| null | 是 | — | — |
| `created_at` | string(date-time) | 是 | — | — |
| `archived_at` | string(date-time) \| null | 是 | — | — |

**错误**：422 参数校验失败 · 401 未认证 · 429 限流/配额超限 · 500 服务器内部错误（完整定义见第 6 章「通用约定」与 [error-codes.md](error-codes.md)）。

### 13.5 DELETE /v1/deployments/{deployment_id}

**Delete Deployment**

Archive a deployment (soft-delete). The scheduler only scans ``archived_at IS NULL``; historical sessions keep their ``deployment_id`` so the run history remains queryable.

**路径参数**

| 参数 | 类型 | 说明 |
|---|---|---|
| `deployment_id` | string | — |

**响应 200**：Successful Response

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `id` | string | 是 | — | — |
| `agent_id` | string | 是 | — | — |
| `environment_id` | string | 是 | — | — |
| `cron` | string | 是 | — | — |
| `timezone` | string | 是 | — | — |
| `initial_events` | `InitialEvent`[] | 否 | — | — |
| `status` | string | 是 | 枚举 `"paused"` / `"active"` | — |
| `next_run_at` | string(date-time) \| null | 是 | — | — |
| `created_at` | string(date-time) | 是 | — | — |
| `archived_at` | string(date-time) \| null | 是 | — | — |

**错误**：422 参数校验失败 · 401 未认证 · 429 限流/配额超限 · 500 服务器内部错误（完整定义见第 6 章「通用约定」与 [error-codes.md](error-codes.md)）。

### 13.6 POST /v1/deployments/{deployment_id}/pause

**Pause Deployment**

Stop scheduling. Idempotent — pausing a paused deployment is a no-op.

**路径参数**

| 参数 | 类型 | 说明 |
|---|---|---|
| `deployment_id` | string | — |

**响应 200**：Successful Response

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `id` | string | 是 | — | — |
| `agent_id` | string | 是 | — | — |
| `environment_id` | string | 是 | — | — |
| `cron` | string | 是 | — | — |
| `timezone` | string | 是 | — | — |
| `initial_events` | `InitialEvent`[] | 否 | — | — |
| `status` | string | 是 | 枚举 `"paused"` / `"active"` | — |
| `next_run_at` | string(date-time) \| null | 是 | — | — |
| `created_at` | string(date-time) | 是 | — | — |
| `archived_at` | string(date-time) \| null | 是 | — | — |

**错误**：422 参数校验失败 · 401 未认证 · 429 限流/配额超限 · 500 服务器内部错误（完整定义见第 6 章「通用约定」与 [error-codes.md](error-codes.md)）。

### 13.7 POST /v1/deployments/{deployment_id}/resume

**Resume Deployment**

Resume scheduling. ``next_run_at`` is recomputed from now so a long-paused deployment doesn't fire its backlog immediately.

**路径参数**

| 参数 | 类型 | 说明 |
|---|---|---|
| `deployment_id` | string | — |

**响应 200**：Successful Response

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `id` | string | 是 | — | — |
| `agent_id` | string | 是 | — | — |
| `environment_id` | string | 是 | — | — |
| `cron` | string | 是 | — | — |
| `timezone` | string | 是 | — | — |
| `initial_events` | `InitialEvent`[] | 否 | — | — |
| `status` | string | 是 | 枚举 `"paused"` / `"active"` | — |
| `next_run_at` | string(date-time) \| null | 是 | — | — |
| `created_at` | string(date-time) | 是 | — | — |
| `archived_at` | string(date-time) \| null | 是 | — | — |

**错误**：422 参数校验失败 · 401 未认证 · 429 限流/配额超限 · 500 服务器内部错误（完整定义见第 6 章「通用约定」与 [error-codes.md](error-codes.md)）。

## 14. Dreams（`dreams`）

### 14.1 GET /v1/dreams

**List Dreams**

List Dreams in the workspace, newest first. Optional ``status`` filter.

**Query 参数**

| 参数 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `status` | string \| null | 否 | 枚举 `"pending"` / `"running"` / `"completed"` / `"failed"` / `"canceled"` | — |
| `limit` | integer | 否 | ≥ 1; ≤ 100; 默认 `20` | — |
| `offset` | integer | 否 | ≥ 0; 默认 `0` | — |

**响应 200**：Successful Response

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `data` | `Dream`[] | 是 | — | — |
| `has_more` | boolean | 是 | — | — |
| `next_cursor` | string \| null | 否 | — | — |

**错误**：422 参数校验失败 · 401 未认证 · 429 限流/配额超限 · 500 服务器内部错误（完整定义见第 6 章「通用约定」与 [error-codes.md](error-codes.md)）。

### 14.2 POST /v1/dreams

**Start Dream**

Kick off a Dream.<br><br>Validates the input memory store exists + isn't archived, that the dreamer agent is configured (per-request override or platform default), and that the output store name isn't already in use by a live store in the workspace. Inserts the row with ``status='pending'`` — a background runner picks it up. (For v0.7 the actual runner is the same orchestrator loop sessions use; this PR ships the API + status surface, the runner-fanout is operator-driven.)

**请求体**（必填）

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `input_memory_store_id` | string | 是 | — | — |
| `output_store_name` | string | 是 | — | — |
| `session_filter` | `DreamSessionFilter` | 否 | — | — |
| `dreamer_agent_id` | string \| null | 否 | — | — |

**响应 201**：Successful Response

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `id` | string | 是 | — | — |
| `input_memory_store_id` | string | 是 | — | — |
| `output_memory_store_id` | string \| null | 否 | — | — |
| `output_store_name` | string | 是 | — | — |
| `dreamer_session_id` | string \| null | 否 | — | — |
| `dreamer_agent_id` | string \| null | 否 | — | — |
| `session_filter` | `DreamSessionFilter` | 是 | — | — |
| `status` | string | 是 | 枚举 `"pending"` / `"running"` / `"completed"` / `"failed"` / `"canceled"` | — |
| `error` | string \| null | 否 | — | — |
| `created_at` | string(date-time) | 是 | — | — |
| `started_at` | string(date-time) \| null | 否 | — | — |
| `ended_at` | string(date-time) \| null | 否 | — | — |

**错误**：422 参数校验失败 · 401 未认证 · 429 限流/配额超限 · 500 服务器内部错误（完整定义见第 6 章「通用约定」与 [error-codes.md](error-codes.md)）。

### 14.3 GET /v1/dreams/{dream_id}

**Get Dream**

**路径参数**

| 参数 | 类型 | 说明 |
|---|---|---|
| `dream_id` | string | — |

**响应 200**：Successful Response

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `id` | string | 是 | — | — |
| `input_memory_store_id` | string | 是 | — | — |
| `output_memory_store_id` | string \| null | 否 | — | — |
| `output_store_name` | string | 是 | — | — |
| `dreamer_session_id` | string \| null | 否 | — | — |
| `dreamer_agent_id` | string \| null | 否 | — | — |
| `session_filter` | `DreamSessionFilter` | 是 | — | — |
| `status` | string | 是 | 枚举 `"pending"` / `"running"` / `"completed"` / `"failed"` / `"canceled"` | — |
| `error` | string \| null | 否 | — | — |
| `created_at` | string(date-time) | 是 | — | — |
| `started_at` | string(date-time) \| null | 否 | — | — |
| `ended_at` | string(date-time) \| null | 否 | — | — |

**错误**：422 参数校验失败 · 401 未认证 · 429 限流/配额超限 · 500 服务器内部错误（完整定义见第 6 章「通用约定」与 [error-codes.md](error-codes.md)）。

### 14.4 POST /v1/dreams/{dream_id}/cancel

**Cancel Dream**

Cancel a pending or running Dream. Terminal Dreams (``completed`` / ``failed`` / ``canceled``) return 409 — there's nothing to cancel.<br><br>Also terminates the dreamer's underlying session if one exists, so the orchestrator loop stops and the container is reclaimed. Best- effort: a failure to terminate the session doesn't block the Dream-row cancel.

**路径参数**

| 参数 | 类型 | 说明 |
|---|---|---|
| `dream_id` | string | — |

**响应 200**：Successful Response

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `id` | string | 是 | — | — |
| `input_memory_store_id` | string | 是 | — | — |
| `output_memory_store_id` | string \| null | 否 | — | — |
| `output_store_name` | string | 是 | — | — |
| `dreamer_session_id` | string \| null | 否 | — | — |
| `dreamer_agent_id` | string \| null | 否 | — | — |
| `session_filter` | `DreamSessionFilter` | 是 | — | — |
| `status` | string | 是 | 枚举 `"pending"` / `"running"` / `"completed"` / `"failed"` / `"canceled"` | — |
| `error` | string \| null | 否 | — | — |
| `created_at` | string(date-time) | 是 | — | — |
| `started_at` | string(date-time) \| null | 否 | — | — |
| `ended_at` | string(date-time) \| null | 否 | — | — |

**错误**：422 参数校验失败 · 401 未认证 · 429 限流/配额超限 · 500 服务器内部错误（完整定义见第 6 章「通用约定」与 [error-codes.md](error-codes.md)）。

## 15. 环境（`environments`）

### 15.1 GET /v1/environments

**List Environments**

Return a paginated list of environments. Archived envs are excluded by default — opt in with ``?include_archived=true``.

**Query 参数**

| 参数 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `limit` | integer | 否 | ≥ 1; ≤ 100; 默认 `20` | — |
| `offset` | integer | 否 | ≥ 0; 默认 `0` | — |
| `include_archived` | boolean | 否 | 默认 `false` | — |

**响应 200**：Successful Response

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `data` | any[] | 是 | — | — |
| `next_cursor` | string \| null | 否 | — | — |
| `has_more` | boolean | 否 | 默认 `false` | — |

**错误**：422 参数校验失败 · 401 未认证 · 429 限流/配额超限 · 500 服务器内部错误（完整定义见第 6 章「通用约定」与 [error-codes.md](error-codes.md)）。

### 15.2 POST /v1/environments

**Create Environment**

Create a new environment resource.

**请求体**（必填）

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `name` | string | 是 | — | — |
| `config` | `EnvironmentConfig` | 是 | — | — |

**响应 201**：Successful Response

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `id` | string | 是 | — | — |
| `name` | string | 是 | — | — |
| `config` | `EnvironmentConfig` | 是 | — | — |
| `created_at` | string(date-time) | 是 | — | — |
| `archived_at` | string(date-time) \| null | 否 | — | — |

**错误**：422 参数校验失败 · 401 未认证 · 429 限流/配额超限 · 500 服务器内部错误（完整定义见第 6 章「通用约定」与 [error-codes.md](error-codes.md)）。

### 15.3 GET /v1/environments/{environment_id}

**Get Environment**

Retrieve an environment by id. Returns archived envs too — callers that need to filter can check ``archived_at`` on the response.

**路径参数**

| 参数 | 类型 | 说明 |
|---|---|---|
| `environment_id` | string | — |

**响应 200**：Successful Response

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `id` | string | 是 | — | — |
| `name` | string | 是 | — | — |
| `config` | `EnvironmentConfig` | 是 | — | — |
| `created_at` | string(date-time) | 是 | — | — |
| `archived_at` | string(date-time) \| null | 否 | — | — |

**错误**：422 参数校验失败 · 401 未认证 · 429 限流/配额超限 · 500 服务器内部错误（完整定义见第 6 章「通用约定」与 [error-codes.md](error-codes.md)）。

### 15.4 DELETE /v1/environments/{environment_id}

**Delete Environment**

Hard-delete an environment (v0.2.0 item #6).<br><br>Rejects 409 if any non-terminated session still references this env — forcing the caller to terminate dependent sessions first. Archive is the soft-delete path; this endpoint is for permanent removal.

**路径参数**

| 参数 | 类型 | 说明 |
|---|---|---|
| `environment_id` | string | — |

**响应 204**：Successful Response

无响应体。

**错误**：422 参数校验失败 · 401 未认证 · 429 限流/配额超限 · 500 服务器内部错误（完整定义见第 6 章「通用约定」与 [error-codes.md](error-codes.md)）。

### 15.5 POST /v1/environments/{environment_id}/archive

**Archive Environment**

Soft-delete an environment (v0.2.0 item #6).<br><br>Idempotent — archiving an already-archived env returns the same row without bumping ``archived_at`` again. Existing sessions retain their ``environment_id`` FK; the env stays reachable via GET for forensic and replay purposes.

**路径参数**

| 参数 | 类型 | 说明 |
|---|---|---|
| `environment_id` | string | — |

**响应 200**：Successful Response

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `id` | string | 是 | — | — |
| `name` | string | 是 | — | — |
| `config` | `EnvironmentConfig` | 是 | — | — |
| `created_at` | string(date-time) | 是 | — | — |
| `archived_at` | string(date-time) \| null | 否 | — | — |

**错误**：422 参数校验失败 · 401 未认证 · 429 限流/配额超限 · 500 服务器内部错误（完整定义见第 6 章「通用约定」与 [error-codes.md](error-codes.md)）。

## 16. 执行节点（`executors`）

### 16.1 POST /v1/executor-nodes/{node_id}/heartbeat

**Executor Node Heartbeat**

Daemon self-auth heartbeat: bearer token must hash to the node's stored token_hash. Updates last_heartbeat_at (readiness signal for the status derivation in list/detail responses).

**路径参数**

| 参数 | 类型 | 说明 |
|---|---|---|
| `node_id` | string | — |

**响应 200**：Successful Response

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `id` | string | 是 | — | — |
| `status` | string | 否 | 默认 `"online"` | — |

**错误**：422 参数校验失败（完整定义见第 6 章「通用约定」与 [error-codes.md](error-codes.md)）。

### 16.2 GET /v1/executor-nodes

**List Executor Nodes**

List live executor nodes in the caller's organization.

**响应 200**：Successful Response

返回 JSON 数组，元素为 `ExecutorNodeResponse`，字段如下：

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `id` | string | 是 | — | — |
| `org_id` | string | 是 | — | — |
| `name` | string | 是 | — | — |
| `url` | string | 是 | — | — |
| `prefix` | string | 是 | — | — |
| `max_sessions` | integer | 是 | — | — |
| `status` | string | 是 | — | — |
| `last_heartbeat_at` | string(date-time) \| null | 否 | — | — |
| `created_at` | string(date-time) | 是 | — | — |

**错误**：401 未认证 · 429 限流/配额超限 · 500 服务器内部错误（完整定义见第 6 章「通用约定」与 [error-codes.md](error-codes.md)）。

### 16.3 POST /v1/executor-nodes

**Register Executor Node**

Register an executor daemon for the caller's org.<br><br>The daemon token is returned exactly once in this response — configure it into the daemon's ``LINCHPIN_EXECUTOR_TOKEN``.

**请求体**（必填）

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `name` | string | 是 | — | — |
| `url` | string | 是 | — | — |
| `max_sessions` | integer | 否 | ≥ 1.0; 默认 `5` | — |

**响应 201**：Successful Response

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `id` | string | 是 | — | — |
| `org_id` | string | 是 | — | — |
| `name` | string | 是 | — | — |
| `url` | string | 是 | — | — |
| `prefix` | string | 是 | — | — |
| `max_sessions` | integer | 是 | — | — |
| `status` | string | 是 | — | — |
| `last_heartbeat_at` | string(date-time) \| null | 否 | — | — |
| `created_at` | string(date-time) | 是 | — | — |
| `token` | string | 是 | — | — |

**错误**：422 参数校验失败 · 401 未认证 · 429 限流/配额超限 · 500 服务器内部错误（完整定义见第 6 章「通用约定」与 [error-codes.md](error-codes.md)）。

### 16.4 GET /v1/executor-nodes/{node_id}

**Get Executor Node**

Fetch one executor node (org-scoped).

**路径参数**

| 参数 | 类型 | 说明 |
|---|---|---|
| `node_id` | string | — |

**响应 200**：Successful Response

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `id` | string | 是 | — | — |
| `org_id` | string | 是 | — | — |
| `name` | string | 是 | — | — |
| `url` | string | 是 | — | — |
| `prefix` | string | 是 | — | — |
| `max_sessions` | integer | 是 | — | — |
| `status` | string | 是 | — | — |
| `last_heartbeat_at` | string(date-time) \| null | 否 | — | — |
| `created_at` | string(date-time) | 是 | — | — |

**错误**：422 参数校验失败 · 401 未认证 · 429 限流/配额超限 · 500 服务器内部错误（完整定义见第 6 章「通用约定」与 [error-codes.md](error-codes.md)）。

### 16.5 DELETE /v1/executor-nodes/{node_id}

**Delete Executor Node**

Soft-delete an executor node (archived_at = now()).

**路径参数**

| 参数 | 类型 | 说明 |
|---|---|---|
| `node_id` | string | — |

**响应 204**：Successful Response

无响应体。

**错误**：422 参数校验失败 · 401 未认证 · 429 限流/配额超限 · 500 服务器内部错误（完整定义见第 6 章「通用约定」与 [error-codes.md](error-codes.md)）。

## 17. 文件（`files`）

### 17.1 GET /v1/files

**List Files**

List files. ``scope_id`` filters by scope; omit it for unscoped uploads only.<br><br>No prefix match — scope filtering is exact, so a session cannot list another session's deliverables.

**Query 参数**

| 参数 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `scope_id` | string \| null | 否 | — | — |
| `limit` | integer | 否 | ≥ 1; ≤ 100; 默认 `20` | — |
| `offset` | integer | 否 | ≥ 0; 默认 `0` | — |

**响应 200**：Successful Response

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `data` | `FileResponse`[] | 是 | — | — |
| `next_cursor` | string \| null | 否 | — | — |
| `has_more` | boolean | 否 | 默认 `false` | — |

**错误**：422 参数校验失败 · 401 未认证 · 429 限流/配额超限 · 500 服务器内部错误（完整定义见第 6 章「通用约定」与 [error-codes.md](error-codes.md)）。

### 17.2 POST /v1/files

**Upload File**

Upload a file. Persisted with ``source=upload`` and ``downloadable=false.

**请求体**（必填）

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `file` | string | 是 | — | — |

**响应 201**：Successful Response

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `id` | string | 是 | — | — |
| `filename` | string | 是 | — | — |
| `content_type` | string | 是 | — | — |
| `size_bytes` | integer | 是 | — | — |
| `sha256` | string | 是 | — | — |
| `source` | string | 是 | 枚举 `"upload"` / `"deliverable"` / `"artifact"` | — |
| `downloadable` | boolean | 是 | — | — |
| `scope_type` | string \| null | 否 | 固定值 `"session"` | — |
| `scope_id` | string \| null | 否 | — | — |
| `created_at` | string(date-time) | 是 | — | — |
| `archived_at` | string(date-time) \| null | 否 | — | — |

**错误**：422 参数校验失败 · 401 未认证 · 429 限流/配额超限 · 500 服务器内部错误（完整定义见第 6 章「通用约定」与 [error-codes.md](error-codes.md)）。

### 17.3 GET /v1/files/{file_id}

**Get File**

Get file metadata.

**路径参数**

| 参数 | 类型 | 说明 |
|---|---|---|
| `file_id` | string | — |

**响应 200**：Successful Response

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `id` | string | 是 | — | — |
| `filename` | string | 是 | — | — |
| `content_type` | string | 是 | — | — |
| `size_bytes` | integer | 是 | — | — |
| `sha256` | string | 是 | — | — |
| `source` | string | 是 | 枚举 `"upload"` / `"deliverable"` / `"artifact"` | — |
| `downloadable` | boolean | 是 | — | — |
| `scope_type` | string \| null | 否 | 固定值 `"session"` | — |
| `scope_id` | string \| null | 否 | — | — |
| `created_at` | string(date-time) | 是 | — | — |
| `archived_at` | string(date-time) \| null | 否 | — | — |

**错误**：422 参数校验失败 · 401 未认证 · 429 限流/配额超限 · 500 服务器内部错误（完整定义见第 6 章「通用约定」与 [error-codes.md](error-codes.md)）。

### 17.4 DELETE /v1/files/{file_id}

**Delete File**

Hard-delete a file: removes the DB row and the stored bytes.

**路径参数**

| 参数 | 类型 | 说明 |
|---|---|---|
| `file_id` | string | — |

**响应 204**：Successful Response

无响应体。

**错误**：422 参数校验失败 · 401 未认证 · 429 限流/配额超限 · 500 服务器内部错误（完整定义见第 6 章「通用约定」与 [error-codes.md](error-codes.md)）。

### 17.5 GET /v1/files/{file_id}/content

**Get File Content**

Stream file bytes. Returns 403 if the file is not marked downloadable.

**路径参数**

| 参数 | 类型 | 说明 |
|---|---|---|
| `file_id` | string | — |

**响应 200**：Successful Response

内容类型 `application/json`。

**错误**：422 参数校验失败 · 401 未认证 · 429 限流/配额超限 · 500 服务器内部错误（完整定义见第 6 章「通用约定」与 [error-codes.md](error-codes.md)）。

## 18. 记忆库（`memory`）

### 18.1 GET /v1/memory_stores

**List Memory Stores**

**Query 参数**

| 参数 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `limit` | integer | 否 | ≥ 1; ≤ 100; 默认 `20` | — |
| `include_archived` | boolean | 否 | 默认 `false` | — |

**响应 200**：Successful Response

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `data` | any[] | 是 | — | — |
| `next_cursor` | string \| null | 否 | — | — |
| `has_more` | boolean | 否 | 默认 `false` | — |

**错误**：422 参数校验失败 · 401 未认证 · 429 限流/配额超限 · 500 服务器内部错误（完整定义见第 6 章「通用约定」与 [error-codes.md](error-codes.md)）。

### 18.2 POST /v1/memory_stores

**Create Memory Store**

Create a memory store. ``name`` must be unique (live) within the workspace — soft-deleted stores don't count toward uniqueness.

**请求体**（必填）

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `name` | string | 是 | 长度 1..64 | — |
| `description` | string \| null | 否 | 长度 0..1024 | — |

**响应 201**：Successful Response

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `id` | string | 是 | — | — |
| `name` | string | 是 | — | — |
| `description` | string \| null | 否 | — | — |
| `created_at` | string(date-time) | 是 | — | — |
| `updated_at` | string(date-time) | 是 | — | — |
| `archived_at` | string(date-time) \| null | 否 | — | — |

**错误**：422 参数校验失败 · 401 未认证 · 429 限流/配额超限 · 500 服务器内部错误（完整定义见第 6 章「通用约定」与 [error-codes.md](error-codes.md)）。

### 18.3 GET /v1/memory_stores/{store_id}

**Get Memory Store**

**路径参数**

| 参数 | 类型 | 说明 |
|---|---|---|
| `store_id` | string | — |

**响应 200**：Successful Response

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `id` | string | 是 | — | — |
| `name` | string | 是 | — | — |
| `description` | string \| null | 否 | — | — |
| `created_at` | string(date-time) | 是 | — | — |
| `updated_at` | string(date-time) | 是 | — | — |
| `archived_at` | string(date-time) \| null | 否 | — | — |

**错误**：422 参数校验失败 · 401 未认证 · 429 限流/配额超限 · 500 服务器内部错误（完整定义见第 6 章「通用约定」与 [error-codes.md](error-codes.md)）。

### 18.4 PATCH /v1/memory_stores/{store_id}

**Update Memory Store**

**路径参数**

| 参数 | 类型 | 说明 |
|---|---|---|
| `store_id` | string | — |

**请求体**（必填）

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `description` | string \| null | 否 | 长度 0..1024 | — |

**响应 200**：Successful Response

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `id` | string | 是 | — | — |
| `name` | string | 是 | — | — |
| `description` | string \| null | 否 | — | — |
| `created_at` | string(date-time) | 是 | — | — |
| `updated_at` | string(date-time) | 是 | — | — |
| `archived_at` | string(date-time) \| null | 否 | — | — |

**错误**：422 参数校验失败 · 401 未认证 · 429 限流/配额超限 · 500 服务器内部错误（完整定义见第 6 章「通用约定」与 [error-codes.md](error-codes.md)）。

### 18.5 DELETE /v1/memory_stores/{store_id}

**Delete Memory Store**

Hard delete: cascades to memories + versions. Refuses (409) if any active session references this store via ``session_resources``.

**路径参数**

| 参数 | 类型 | 说明 |
|---|---|---|
| `store_id` | string | — |

**响应 204**：Successful Response

无响应体。

**错误**：422 参数校验失败 · 401 未认证 · 429 限流/配额超限 · 500 服务器内部错误（完整定义见第 6 章「通用约定」与 [error-codes.md](error-codes.md)）。

### 18.6 POST /v1/memory_stores/{store_id}/archive

**Archive Memory Store**

**路径参数**

| 参数 | 类型 | 说明 |
|---|---|---|
| `store_id` | string | — |

**响应 200**：Successful Response

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `id` | string | 是 | — | — |
| `name` | string | 是 | — | — |
| `description` | string \| null | 否 | — | — |
| `created_at` | string(date-time) | 是 | — | — |
| `updated_at` | string(date-time) | 是 | — | — |
| `archived_at` | string(date-time) \| null | 否 | — | — |

**错误**：422 参数校验失败 · 401 未认证 · 429 限流/配额超限 · 500 服务器内部错误（完整定义见第 6 章「通用约定」与 [error-codes.md](error-codes.md)）。

### 18.7 GET /v1/memory_stores/{store_id}/memories

**List Or Get Memory**

If ``?path=…`` is given, returns the single memory at that path wrapped in a one-row PaginatedListResponse (or 404). Otherwise lists memories, optionally filtered by ``?path_prefix=/preferences/``.

**路径参数**

| 参数 | 类型 | 说明 |
|---|---|---|
| `store_id` | string | — |

**Query 参数**

| 参数 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `path` | string \| null | 否 | — | — |
| `path_prefix` | string \| null | 否 | — | — |
| `limit` | integer | 否 | ≥ 1; ≤ 200; 默认 `50` | — |

**响应 200**：Successful Response

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `data` | any[] | 是 | — | — |
| `next_cursor` | string \| null | 否 | — | — |
| `has_more` | boolean | 否 | 默认 `false` | — |

**错误**：422 参数校验失败 · 401 未认证 · 429 限流/配额超限 · 500 服务器内部错误（完整定义见第 6 章「通用约定」与 [error-codes.md](error-codes.md)）。

### 18.8 POST /v1/memory_stores/{store_id}/memories

**Create Memory**

Create or upsert a memory. Optional precondition matches HTTP ETag semantics: ``sha256=None`` requires the path to not yet exist; ``sha256=<hex>`` requires that to be the current head sha. Either direction's mismatch returns 412.

**路径参数**

| 参数 | 类型 | 说明 |
|---|---|---|
| `store_id` | string | — |

**请求体**（必填）

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `path` | string | 是 | — | — |
| `content` | string | 是 | — | — |
| `precondition` | `ContentShaPrecondition` \| null | 否 | — | — |

**响应 201**：Successful Response

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `id` | string | 是 | — | — |
| `memory_store_id` | string | 是 | — | — |
| `path` | string | 是 | — | — |
| `content_sha256` | string | 是 | — | — |
| `size_bytes` | integer | 是 | — | — |
| `created_at` | string(date-time) | 是 | — | — |
| `updated_at` | string(date-time) | 是 | — | — |

**错误**：422 参数校验失败 · 401 未认证 · 429 限流/配额超限 · 500 服务器内部错误（完整定义见第 6 章「通用约定」与 [error-codes.md](error-codes.md)）。

### 18.9 PATCH /v1/memory_stores/{store_id}/memories

**Patch Memory**

Same as POST. Kept as a separate verb so callers that want PUT-ish semantics can pick PATCH; both endpoints share one canonical writer.

**路径参数**

| 参数 | 类型 | 说明 |
|---|---|---|
| `store_id` | string | — |

**请求体**（必填）

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `path` | string | 是 | — | — |
| `content` | string | 是 | — | — |
| `precondition` | `ContentShaPrecondition` \| null | 否 | — | — |

**响应 200**：Successful Response

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `id` | string | 是 | — | — |
| `memory_store_id` | string | 是 | — | — |
| `path` | string | 是 | — | — |
| `content_sha256` | string | 是 | — | — |
| `size_bytes` | integer | 是 | — | — |
| `created_at` | string(date-time) | 是 | — | — |
| `updated_at` | string(date-time) | 是 | — | — |

**错误**：422 参数校验失败 · 401 未认证 · 429 限流/配额超限 · 500 服务器内部错误（完整定义见第 6 章「通用约定」与 [error-codes.md](error-codes.md)）。

### 18.10 DELETE /v1/memory_stores/{store_id}/memories

**Delete Memory**

Soft-delete a memory by path. Writes an ``action='delete'`` version so the version chain stays intact for the 30-day retention window.

**路径参数**

| 参数 | 类型 | 说明 |
|---|---|---|
| `store_id` | string | — |

**Query 参数**

| 参数 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `path` | string | 是 | — | — |

**响应 204**：Successful Response

无响应体。

**错误**：422 参数校验失败 · 401 未认证 · 429 限流/配额超限 · 500 服务器内部错误（完整定义见第 6 章「通用约定」与 [error-codes.md](error-codes.md)）。

### 18.11 GET /v1/memory_stores/{store_id}/memory_versions

**List Memory Versions**

List versions in this store. Optionally filter to one memory's chain via ``?memory_id=<id>`` — typical usage when an operator is reconstructing the history of a single path.

**路径参数**

| 参数 | 类型 | 说明 |
|---|---|---|
| `store_id` | string | — |

**Query 参数**

| 参数 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `memory_id` | string \| null | 否 | — | — |
| `limit` | integer | 否 | ≥ 1; ≤ 200; 默认 `50` | — |

**响应 200**：Successful Response

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `data` | any[] | 是 | — | — |
| `next_cursor` | string \| null | 否 | — | — |
| `has_more` | boolean | 否 | 默认 `false` | — |

**错误**：422 参数校验失败 · 401 未认证 · 429 限流/配额超限 · 500 服务器内部错误（完整定义见第 6 章「通用约定」与 [error-codes.md](error-codes.md)）。

### 18.12 GET /v1/memory_stores/{store_id}/memory_versions/{version_id}

**Get Memory Version**

**路径参数**

| 参数 | 类型 | 说明 |
|---|---|---|
| `store_id` | string | — |
| `version_id` | string | — |

**响应 200**：Successful Response

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `id` | string | 是 | — | — |
| `memory_id` | string | 是 | — | — |
| `memory_store_id` | string | 是 | — | — |
| `seq` | integer | 是 | — | — |
| `content_sha256` | string | 是 | — | — |
| `size_bytes` | integer | 是 | — | — |
| `author` | string | 是 | — | — |
| `action` | string | 是 | 枚举 `"create"` / `"update"` / `"delete"` / `"redact"` | — |
| `redacted_at` | string(date-time) \| null | 否 | — | — |
| `created_at` | string(date-time) | 是 | — | — |
| `expires_at` | string(date-time) | 是 | — | — |

**错误**：422 参数校验失败 · 401 未认证 · 429 限流/配额超限 · 500 服务器内部错误（完整定义见第 6 章「通用约定」与 [error-codes.md](error-codes.md)）。

### 18.13 GET /v1/memory_stores/{store_id}/memory_versions/{version_id}/content

**Get Memory Version Content**

Stream the raw bytes for this version. Returns 404 once the row is redacted (``redacted_at IS NOT NULL``). Delete and create-empty versions (zero-byte ``action='delete'`` rows) return an empty body with 200 so callers can distinguish "no content was written" from "the content was redacted".

**路径参数**

| 参数 | 类型 | 说明 |
|---|---|---|
| `store_id` | string | — |
| `version_id` | string | — |

**响应 200**：Successful Response

内容类型 `application/json`。

**错误**：422 参数校验失败 · 401 未认证 · 429 限流/配额超限 · 500 服务器内部错误（完整定义见第 6 章「通用约定」与 [error-codes.md](error-codes.md)）。

### 18.14 POST /v1/memory_stores/{store_id}/memory_versions/{version_id}/redact

**Redact Memory Version**

Tombstone a specific version: unlink its bytes from the FileStore, zero its ``content_sha256``, and set ``redacted_at``. If the version is the current head of its memory, also write a NEW ``action='redact'`` version so the agent doesn't read previously- redacted content on next mount.

**路径参数**

| 参数 | 类型 | 说明 |
|---|---|---|
| `store_id` | string | — |
| `version_id` | string | — |

**请求体**（必填）

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `reason` | string | 是 | 长度 1..1024 | — |

**响应 200**：Successful Response

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `id` | string | 是 | — | — |
| `memory_id` | string | 是 | — | — |
| `memory_store_id` | string | 是 | — | — |
| `seq` | integer | 是 | — | — |
| `content_sha256` | string | 是 | — | — |
| `size_bytes` | integer | 是 | — | — |
| `author` | string | 是 | — | — |
| `action` | string | 是 | 枚举 `"create"` / `"update"` / `"delete"` / `"redact"` | — |
| `redacted_at` | string(date-time) \| null | 否 | — | — |
| `created_at` | string(date-time) | 是 | — | — |
| `expires_at` | string(date-time) | 是 | — | — |

**错误**：422 参数校验失败 · 401 未认证 · 429 限流/配额超限 · 500 服务器内部错误（完整定义见第 6 章「通用约定」与 [error-codes.md](error-codes.md)）。

## 19. 会话资源（`session_resources`）

### 19.1 GET /v1/sessions/{session_id}/resources

**List Session Resources**

List all resources persisted for ``session_id``.<br><br>Includes every state ('mounted', 'failed', 'unmounted', 'unmounting'). Callers wanting only active resources can filter on `state` client-side — server-side filtering lands with live mount/unmount in v0.2.x.

**路径参数**

| 参数 | 类型 | 说明 |
|---|---|---|
| `session_id` | string | — |

**响应 200**：Successful Response

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `data` | any[] | 是 | — | — |
| `next_cursor` | string \| null | 否 | — | — |
| `has_more` | boolean | 否 | 默认 `false` | — |

**错误**：422 参数校验失败 · 401 未认证 · 429 限流/配额超限 · 500 服务器内部错误（完整定义见第 6 章「通用约定」与 [error-codes.md](error-codes.md)）。

### 19.2 POST /v1/sessions/{session_id}/resources

**Post Session Resource**

Live resource mount — deferred to v0.2.x per eng-review D1.<br><br>Returns 501 with a structured body identifying when the surface is expected. Body is parsed (and validated) so SDKs get clean 422 on malformed requests, then 501 if the request would otherwise be valid.

**路径参数**

| 参数 | 类型 | 说明 |
|---|---|---|
| `session_id` | string | — |

**请求体**（必填）

结构 `Body`（无展开字段，见附录）。

**错误**：501 · 422 参数校验失败 · 401 未认证 · 429 限流/配额超限 · 500 服务器内部错误（完整定义见第 6 章「通用约定」与 [error-codes.md](error-codes.md)）。

### 19.3 GET /v1/sessions/{session_id}/resources/{resource_id}

**Get Session Resource**

Get a single session resource by id.

**路径参数**

| 参数 | 类型 | 说明 |
|---|---|---|
| `session_id` | string | — |
| `resource_id` | string | — |

**响应 200**：Successful Response

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `id` | string | 是 | — | — |
| `session_id` | string | 是 | — | — |
| `type` | string | 是 | 枚举 `"file"` / `"memory_store"` / `"github_repository"` / `"git_repository"` / `"vault"` | — |
| `mount_path` | string | 是 | — | — |
| `config` | object | 否 | — | — |
| `state` | string | 否 | 枚举 `"mounted"` / `"unmounting"` / `"unmounted"` / `"failed"`; 默认 `"mounted"` | — |
| `error` | string \| null | 否 | — | — |
| `created_at` | string(date-time) | 是 | — | — |
| `unmounted_at` | string(date-time) \| null | 否 | — | — |

**错误**：422 参数校验失败 · 401 未认证 · 429 限流/配额超限 · 500 服务器内部错误（完整定义见第 6 章「通用约定」与 [error-codes.md](error-codes.md)）。

### 19.4 DELETE /v1/sessions/{session_id}/resources/{resource_id}

**Delete Session Resource**

Live resource unmount — deferred to v0.2.x per eng-review D1.

**路径参数**

| 参数 | 类型 | 说明 |
|---|---|---|
| `session_id` | string | — |
| `resource_id` | string | — |

**错误**：501 · 422 参数校验失败 · 401 未认证 · 429 限流/配额超限 · 500 服务器内部错误（完整定义见第 6 章「通用约定」与 [error-codes.md](error-codes.md)）。

## 20. 会话（`sessions`）

### 20.1 GET /v1/sessions

**List Sessions**

Return a paginated list of sessions, optionally filtered by agent_id and/or deployment_id (B7 — a deployment's run history is simply its sessions).

**Query 参数**

| 参数 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `limit` | integer | 否 | ≥ 1; ≤ 100; 默认 `20` | — |
| `offset` | integer | 否 | ≥ 0; 默认 `0` | — |
| `agent_id` | string \| null | 否 | — | — |
| `deployment_id` | string \| null | 否 | — | — |

**响应 200**：Successful Response

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `data` | any[] | 是 | — | — |
| `next_cursor` | string \| null | 否 | — | — |
| `has_more` | boolean | 否 | 默认 `false` | — |

**错误**：422 参数校验失败 · 401 未认证 · 429 限流/配额超限 · 500 服务器内部错误（完整定义见第 6 章「通用约定」与 [error-codes.md](error-codes.md)）。

### 20.2 POST /v1/sessions

**Create Session**

Create a new session: validate refs, provision container, insert record.<br><br>Atlas A8 — an ``Idempotency-Key`` header makes the create exactly-once: a retry with the same key + same body replays the stored response. Atlas B8 — ``x-events-encrypted: true`` enables envelope encryption of business-content event payloads for this session (FR-10.3). Atlas M6 BYOK — ``x-kek-credential-id`` names a ``byok_kek`` vault credential (one of the session's ``vault_ids``); when present together with ``x-events-encrypted: true`` the per-session data key is wrapped under the customer's KEK instead of the platform master key.

**Header 参数**

| 参数 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `Idempotency-Key` | string \| null | 否 | — | — |
| `x-events-encrypted` | boolean | 否 | 默认 `false` | — |
| `x-kek-credential-id` | string \| null | 否 | — | — |

**请求体**（必填）

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `agent_id` | string | 是 | — | — |
| `environment_id` | string | 是 | — | — |
| `title` | string \| null | 否 | — | — |
| `metadata` | object | 否 | — | — |
| `ttl_seconds` | integer \| null | 否 | — | — |
| `vault_ids` | string[] | 否 | — | 绑定凭据保险库；运行时按智能体模型供应商从其中解析 `api_key` 凭据（见第 23 章） |
| `resources` | `FileResource` \| `MemoryStoreResource` \| `GitRepositoryResource` \| `GithubRepositoryResource` \| `VaultResource`[] | 否 | — | 资源挂载列表；其中 `type="vault"` 条目等效于 `vault_ids` 绑定 |
| `outcome` | `OutcomeDefinition` \| null | 否 | — | — |

**响应 201**：Successful Response

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `id` | string | 是 | — | — |
| `agent_id` | string | 是 | — | — |
| `agent_version` | integer | 是 | — | — |
| `environment_id` | string | 是 | — | — |
| `status` | string | 是 | 枚举 `"provisioning"` / `"rescheduling"` / `"running"` / `"idle"` / `"error"` / `"terminated"` / `"failed"` | — |
| `stop_reason` | string \| null | 否 | — | — |
| `retry_count` | integer | 否 | 默认 `0` | — |
| `container_id` | string \| null | 否 | — | — |
| `title` | string \| null | 否 | — | — |
| `metadata` | object | 否 | — | — |
| `created_at` | string(date-time) | 是 | — | — |
| `updated_at` | string(date-time) | 是 | — | — |
| `archived_at` | string(date-time) \| null | 否 | — | — |
| `last_event_cursor` | string \| null | 否 | — | — |
| `ttl_seconds` | integer \| null | 否 | — | — |
| `stats` | `SessionStats` | 否 | — | — |
| `usage` | `SessionUsage` | 否 | — | — |
| `vault_ids` | string[] | 否 | — | 绑定的凭据保险库 ID（运行时用于解析模型 API Key 与 MCP 凭据，见第 23 章） |
| `resources` | `SessionResource`[] | 否 | — | — |
| `outcome` | `OutcomeDefinition` \| null | 否 | — | — |
| `parent_session_id` | string \| null | 否 | — | — |
| `deployment_id` | string \| null | 否 | — | — |
| `events_encrypted` | boolean | 否 | 默认 `false` | — |

**错误**：422 参数校验失败 · 401 未认证 · 429 限流/配额超限 · 500 服务器内部错误（完整定义见第 6 章「通用约定」与 [error-codes.md](error-codes.md)）。

### 20.3 GET /v1/sessions/{session_id}

**Get Session**

Retrieve a session by id with stats and usage.

**路径参数**

| 参数 | 类型 | 说明 |
|---|---|---|
| `session_id` | string | — |

**响应 200**：Successful Response

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `id` | string | 是 | — | — |
| `agent_id` | string | 是 | — | — |
| `agent_version` | integer | 是 | — | — |
| `environment_id` | string | 是 | — | — |
| `status` | string | 是 | 枚举 `"provisioning"` / `"rescheduling"` / `"running"` / `"idle"` / `"error"` / `"terminated"` / `"failed"` | — |
| `stop_reason` | string \| null | 否 | — | — |
| `retry_count` | integer | 否 | 默认 `0` | — |
| `container_id` | string \| null | 否 | — | — |
| `title` | string \| null | 否 | — | — |
| `metadata` | object | 否 | — | — |
| `created_at` | string(date-time) | 是 | — | — |
| `updated_at` | string(date-time) | 是 | — | — |
| `archived_at` | string(date-time) \| null | 否 | — | — |
| `last_event_cursor` | string \| null | 否 | — | — |
| `ttl_seconds` | integer \| null | 否 | — | — |
| `stats` | `SessionStats` | 否 | — | — |
| `usage` | `SessionUsage` | 否 | — | — |
| `vault_ids` | string[] | 否 | — | 绑定的凭据保险库 ID（运行时用于解析模型 API Key 与 MCP 凭据，见第 23 章） |
| `resources` | `SessionResource`[] | 否 | — | — |
| `outcome` | `OutcomeDefinition` \| null | 否 | — | — |
| `parent_session_id` | string \| null | 否 | — | — |
| `deployment_id` | string \| null | 否 | — | — |
| `events_encrypted` | boolean | 否 | 默认 `false` | — |

**错误**：422 参数校验失败 · 401 未认证 · 429 限流/配额超限 · 500 服务器内部错误（完整定义见第 6 章「通用约定」与 [error-codes.md](error-codes.md)）。

### 20.4 POST /v1/sessions/{session_id}

**Update Session**

Update session title and/or metadata.

**路径参数**

| 参数 | 类型 | 说明 |
|---|---|---|
| `session_id` | string | — |

**请求体**（必填）

结构 `Body`（无展开字段，见附录）。

**响应 200**：Successful Response

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `id` | string | 是 | — | — |
| `agent_id` | string | 是 | — | — |
| `agent_version` | integer | 是 | — | — |
| `environment_id` | string | 是 | — | — |
| `status` | string | 是 | 枚举 `"provisioning"` / `"rescheduling"` / `"running"` / `"idle"` / `"error"` / `"terminated"` / `"failed"` | — |
| `stop_reason` | string \| null | 否 | — | — |
| `retry_count` | integer | 否 | 默认 `0` | — |
| `container_id` | string \| null | 否 | — | — |
| `title` | string \| null | 否 | — | — |
| `metadata` | object | 否 | — | — |
| `created_at` | string(date-time) | 是 | — | — |
| `updated_at` | string(date-time) | 是 | — | — |
| `archived_at` | string(date-time) \| null | 否 | — | — |
| `last_event_cursor` | string \| null | 否 | — | — |
| `ttl_seconds` | integer \| null | 否 | — | — |
| `stats` | `SessionStats` | 否 | — | — |
| `usage` | `SessionUsage` | 否 | — | — |
| `vault_ids` | string[] | 否 | — | 绑定的凭据保险库 ID（运行时用于解析模型 API Key 与 MCP 凭据，见第 23 章） |
| `resources` | `SessionResource`[] | 否 | — | — |
| `outcome` | `OutcomeDefinition` \| null | 否 | — | — |
| `parent_session_id` | string \| null | 否 | — | — |
| `deployment_id` | string \| null | 否 | — | — |
| `events_encrypted` | boolean | 否 | 默认 `false` | — |

**错误**：422 参数校验失败 · 401 未认证 · 429 限流/配额超限 · 500 服务器内部错误（完整定义见第 6 章「通用约定」与 [error-codes.md](error-codes.md)）。

### 20.5 DELETE /v1/sessions/{session_id}

**Terminate Session**

Terminate a session: set status to terminated, destroy container.

**路径参数**

| 参数 | 类型 | 说明 |
|---|---|---|
| `session_id` | string | — |

**响应 200**：Successful Response

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `id` | string | 是 | — | — |
| `agent_id` | string | 是 | — | — |
| `agent_version` | integer | 是 | — | — |
| `environment_id` | string | 是 | — | — |
| `status` | string | 是 | 枚举 `"provisioning"` / `"rescheduling"` / `"running"` / `"idle"` / `"error"` / `"terminated"` / `"failed"` | — |
| `stop_reason` | string \| null | 否 | — | — |
| `retry_count` | integer | 否 | 默认 `0` | — |
| `container_id` | string \| null | 否 | — | — |
| `title` | string \| null | 否 | — | — |
| `metadata` | object | 否 | — | — |
| `created_at` | string(date-time) | 是 | — | — |
| `updated_at` | string(date-time) | 是 | — | — |
| `archived_at` | string(date-time) \| null | 否 | — | — |
| `last_event_cursor` | string \| null | 否 | — | — |
| `ttl_seconds` | integer \| null | 否 | — | — |
| `stats` | `SessionStats` | 否 | — | — |
| `usage` | `SessionUsage` | 否 | — | — |
| `vault_ids` | string[] | 否 | — | 绑定的凭据保险库 ID（运行时用于解析模型 API Key 与 MCP 凭据，见第 23 章） |
| `resources` | `SessionResource`[] | 否 | — | — |
| `outcome` | `OutcomeDefinition` \| null | 否 | — | — |
| `parent_session_id` | string \| null | 否 | — | — |
| `deployment_id` | string \| null | 否 | — | — |
| `events_encrypted` | boolean | 否 | 默认 `false` | — |

**错误**：422 参数校验失败 · 401 未认证 · 429 限流/配额超限 · 500 服务器内部错误（完整定义见第 6 章「通用约定」与 [error-codes.md](error-codes.md)）。

### 20.6 GET /v1/sessions/{session_id}/outcome_evaluations

**List Outcome Evaluations**

Append-only history of grader evaluations for a session, newest first. Empty list when no outcome was declared or no grader has run yet.

**路径参数**

| 参数 | 类型 | 说明 |
|---|---|---|
| `session_id` | string | — |

**响应 200**：Successful Response

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `data` | `OutcomeEvaluation`[] | 是 | — | — |

**错误**：422 参数校验失败 · 401 未认证 · 429 限流/配额超限 · 500 服务器内部错误（完整定义见第 6 章「通用约定」与 [error-codes.md](error-codes.md)）。

### 20.7 POST /v1/sessions/{session_id}/outcome_evaluations

**Post Outcome Evaluation**

Record a grader evaluation.<br><br>Auto-terminates the session when ``score >= outcome.success_threshold`` and the outcome declared ``auto_terminate: true`` (the default). Emits ``session.outcome_evaluation_ended`` regardless of threshold.

**路径参数**

| 参数 | 类型 | 说明 |
|---|---|---|
| `session_id` | string | — |

**请求体**（必填）

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `score` | number | 是 | ≥ 0.0; ≤ 1.0 | — |
| `rationale` | string \| null | 否 | — | — |
| `criteria` | object[] | 否 | — | — |
| `grader_id` | string \| null | 否 | — | — |

**响应 201**：Successful Response

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `id` | string | 是 | — | — |
| `session_id` | string | 是 | — | — |
| `grader_id` | string \| null | 否 | — | — |
| `score` | number | 是 | — | — |
| `rationale` | string \| null | 否 | — | — |
| `criteria` | object[] | 否 | — | — |
| `created_at` | string(date-time) | 是 | — | — |

**错误**：422 参数校验失败 · 401 未认证 · 429 限流/配额超限 · 500 服务器内部错误（完整定义见第 6 章「通用约定」与 [error-codes.md](error-codes.md)）。

### 20.8 GET /v1/sessions/{parent_id}/threads

**List Threads**

Return every thread (including terminated) for a parent session.

**路径参数**

| 参数 | 类型 | 说明 |
|---|---|---|
| `parent_id` | string | — |

**响应 200**：Successful Response

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `data` | `SessionResponse`[] | 是 | — | — |

**错误**：422 参数校验失败 · 401 未认证 · 429 限流/配额超限 · 500 服务器内部错误（完整定义见第 6 章「通用约定」与 [error-codes.md](error-codes.md)）。

### 20.9 POST /v1/sessions/{parent_id}/threads

**Spawn Thread**

Spawn a worker thread off ``parent_id``.<br><br>The new session is created via the same machinery as a normal ``POST /v1/sessions`` (validation, container provisioning, resources, …) but with two differences:<br><br>1. ``parent_session_id`` is set to ``parent_id``. 2. The nesting cap (``LINCHPIN_MAX_THREAD_DEPTH``, default 3) is enforced — a coordinator-of-coordinator-of-coordinator chain rejects rather than billing the user for runaway loops.<br><br>On success, ``session.thread_created`` lands on the parent's event log so a coordinator watching its own stream learns about its worker without polling.

**路径参数**

| 参数 | 类型 | 说明 |
|---|---|---|
| `parent_id` | string | — |

**请求体**（必填）

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `agent_id` | string | 是 | — | — |
| `environment_id` | string | 是 | — | — |
| `title` | string \| null | 否 | — | — |
| `metadata` | object | 否 | — | — |
| `ttl_seconds` | integer \| null | 否 | — | — |
| `vault_ids` | string[] | 否 | — | 绑定凭据保险库；运行时按智能体模型供应商从其中解析 `api_key` 凭据（见第 23 章） |
| `resources` | any[] | 否 | — | — |
| `input_message` | string \| object \| any[] \| null | 否 | — | — |
| `outcome` | `OutcomeDefinition` \| null | 否 | — | — |

**响应 201**：Successful Response

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `id` | string | 是 | — | — |
| `agent_id` | string | 是 | — | — |
| `agent_version` | integer | 是 | — | — |
| `environment_id` | string | 是 | — | — |
| `status` | string | 是 | 枚举 `"provisioning"` / `"rescheduling"` / `"running"` / `"idle"` / `"error"` / `"terminated"` / `"failed"` | — |
| `stop_reason` | string \| null | 否 | — | — |
| `retry_count` | integer | 否 | 默认 `0` | — |
| `container_id` | string \| null | 否 | — | — |
| `title` | string \| null | 否 | — | — |
| `metadata` | object | 否 | — | — |
| `created_at` | string(date-time) | 是 | — | — |
| `updated_at` | string(date-time) | 是 | — | — |
| `archived_at` | string(date-time) \| null | 否 | — | — |
| `last_event_cursor` | string \| null | 否 | — | — |
| `ttl_seconds` | integer \| null | 否 | — | — |
| `stats` | `SessionStats` | 否 | — | — |
| `usage` | `SessionUsage` | 否 | — | — |
| `vault_ids` | string[] | 否 | — | 绑定的凭据保险库 ID（运行时用于解析模型 API Key 与 MCP 凭据，见第 23 章） |
| `resources` | `SessionResource`[] | 否 | — | — |
| `outcome` | `OutcomeDefinition` \| null | 否 | — | — |
| `parent_session_id` | string \| null | 否 | — | — |
| `deployment_id` | string \| null | 否 | — | — |
| `events_encrypted` | boolean | 否 | 默认 `false` | — |

**错误**：422 参数校验失败 · 401 未认证 · 429 限流/配额超限 · 500 服务器内部错误（完整定义见第 6 章「通用约定」与 [error-codes.md](error-codes.md)）。

### 20.10 POST /v1/sessions/{session_id}/archive

**Archive Session**

Archive a session: set archived_at.<br><br>Atlas A6 — idempotent: archiving an already-archived session returns its current state (200) instead of 409, matching agents/environments.

**路径参数**

| 参数 | 类型 | 说明 |
|---|---|---|
| `session_id` | string | — |

**响应 200**：Successful Response

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `id` | string | 是 | — | — |
| `agent_id` | string | 是 | — | — |
| `agent_version` | integer | 是 | — | — |
| `environment_id` | string | 是 | — | — |
| `status` | string | 是 | 枚举 `"provisioning"` / `"rescheduling"` / `"running"` / `"idle"` / `"error"` / `"terminated"` / `"failed"` | — |
| `stop_reason` | string \| null | 否 | — | — |
| `retry_count` | integer | 否 | 默认 `0` | — |
| `container_id` | string \| null | 否 | — | — |
| `title` | string \| null | 否 | — | — |
| `metadata` | object | 否 | — | — |
| `created_at` | string(date-time) | 是 | — | — |
| `updated_at` | string(date-time) | 是 | — | — |
| `archived_at` | string(date-time) \| null | 否 | — | — |
| `last_event_cursor` | string \| null | 否 | — | — |
| `ttl_seconds` | integer \| null | 否 | — | — |
| `stats` | `SessionStats` | 否 | — | — |
| `usage` | `SessionUsage` | 否 | — | — |
| `vault_ids` | string[] | 否 | — | 绑定的凭据保险库 ID（运行时用于解析模型 API Key 与 MCP 凭据，见第 23 章） |
| `resources` | `SessionResource`[] | 否 | — | — |
| `outcome` | `OutcomeDefinition` \| null | 否 | — | — |
| `parent_session_id` | string \| null | 否 | — | — |
| `deployment_id` | string \| null | 否 | — | — |
| `events_encrypted` | boolean | 否 | 默认 `false` | — |

**错误**：422 参数校验失败 · 401 未认证 · 429 限流/配额超限 · 500 服务器内部错误（完整定义见第 6 章「通用约定」与 [error-codes.md](error-codes.md)）。

### 20.11 GET /v1/sessions/{session_id}/streaming

**Get Session Streaming**

Get the in-flight streaming state for a session.<br><br>Returns the accumulated text so far if the model is currently generating, or null if no active stream.

**路径参数**

| 参数 | 类型 | 说明 |
|---|---|---|
| `session_id` | string | — |

**响应 200**：Successful Response

返回 JSON：若模型正在生成则返回当前已累积的文本，否则返回 `null`。

**错误**：422 参数校验失败 · 401 未认证 · 429 限流/配额超限 · 500 服务器内部错误（完整定义见第 6 章「通用约定」与 [error-codes.md](error-codes.md)）。

### 20.12 GET /v1/sessions/{session_id}/events

**Get Session Events**

Get events for a session with cursor-based pagination.<br><br>v0.2.0 item #12 — optional ``?types[]=X&types[]=Y`` filter restricts the response to events whose ``type`` is in the list. Unknown type strings return 422 with the offender, rather than a silent empty result set.

**路径参数**

| 参数 | 类型 | 说明 |
|---|---|---|
| `session_id` | string | — |

**Query 参数**

| 参数 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `after_cursor` | string \| null | 否 | — | — |
| `limit` | integer | 否 | ≥ 1; ≤ 100; 默认 `50` | — |
| `types[]` | string[] \| null | 否 | — | — |

**响应 200**：Successful Response

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `events` | `EventResponse`[] | 是 | — | — |
| `next_cursor` | string \| null | 否 | — | — |

**错误**：422 参数校验失败 · 401 未认证 · 429 限流/配额超限 · 500 服务器内部错误（完整定义见第 6 章「通用约定」与 [error-codes.md](error-codes.md)）。

### 20.13 POST /v1/sessions/{session_id}/events

**Post Events**

Post events to a session.<br><br>Validates event types (422 via Pydantic), checks session exists (404), checks session is not archived (409), enforces the A4 state-gating matrix (409 with the blocked types when the current state won't accept them), then appends the whole batch and triggers LISTEN/NOTIFY on the session channel.<br><br>Validates: Requirements 9.1, 9.2, 9.3, 9.4, 9.5, 10.4

**路径参数**

| 参数 | 类型 | 说明 |
|---|---|---|
| `session_id` | string | — |

**Header 参数**

| 参数 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `Idempotency-Key` | string \| null | 否 | — | — |

**请求体**（必填）

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `events` | `EventPayload`[] | 是 | — | — |

**响应 201**：Successful Response

返回 JSON 数组，元素为 `EventResponse`，字段如下：

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `session_id` | string | 是 | — | — |
| `cursor` | string | 是 | — | — |
| `seq` | integer | 是 | — | — |
| `type` | string | 是 | 枚举 `"user.message"` / `"user.steer"` / `"user.interrupt"` / `"user.tool_confirmation"` / `"user.custom_tool_result"` / `"agent.message"` / `"agent.message_delta"` / `"agent.thinking"` / `"agent.tool_use"` / `"agent.tool_result"` / `"agent.mcp_tool_use"` / `"agent.mcp_tool_result"` / `"agent.custom_tool_use"` / `"agent.deliverable"` / `"agent.deliverable_dropped"` / `"session.status_running"` / `"session.status_idle"` / `"session.status_rescheduled"` / `"session.status_terminated"` / `"session.status"` / `"session.error"` / `"session.requires_action"` / `"session.resource_mount_failed"` / `"session.memory_mount_failed"` / `"session.deprecation_used"` / `"session.compacted"` / `"memory.write"` / `"memory.write_rejected"` / `"memory.gc"` / `"session.outcome_evaluation_started"` / `"session.outcome_evaluation_ended"` / `"agent.thread_create"` / `"session.thread_created"` / `"session.thread_idled"` / `"session.thread_terminated"` / `"span.model_request_start"` / `"span.model_request_end"` | — |
| `payload` | object | 否 | — | — |
| `processed_at` | string(date-time) \| null | 否 | — | — |
| `event_id` | string \| null | 否 | — | — |

**错误**：422 参数校验失败 · 401 未认证 · 429 限流/配额超限 · 500 服务器内部错误（完整定义见第 6 章「通用约定」与 [error-codes.md](error-codes.md)）。

### 20.14 GET /v1/sessions/{session_id}/events/stream

**Stream Session**

SSE stream endpoint: replay existing events then live-stream new ones.<br><br>- Validates session exists (404 if not). - Resume point: ``Last-Event-ID`` header (Atlas A3) takes priority over ``?cursor=``; its value is the ``id:`` of the last frame seen — the per-session ``seq``. A raw cursor value is also tolerated. - If a resume point is provided, validates it (422 if invalid). - Phase 1: Replay all events after the resume point (or from beginning). - Phase 2: Subscribe to PG LISTEN/NOTIFY and yield new events as they arrive. - On client disconnect, cleans up the LISTEN subscription.<br><br>Validates: Requirements 7.1, 7.2, 7.4

**路径参数**

| 参数 | 类型 | 说明 |
|---|---|---|
| `session_id` | string | — |

**Header 参数**

| 参数 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `Last-Event-ID` | string \| null | 否 | — | — |

**Query 参数**

| 参数 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `cursor` | string \| null | 否 | — | — |

**响应 200**：Successful Response

SSE 事件流（事件类型与帧格式见第 7 章「SSE 事件流」）。

**错误**：422 参数校验失败 · 401 未认证 · 429 限流/配额超限 · 500 服务器内部错误（完整定义见第 6 章「通用约定」与 [error-codes.md](error-codes.md)）。

### 20.15 GET /v1/sessions/{session_id}/stream

**Stream Session**

SSE stream endpoint: replay existing events then live-stream new ones.<br><br>- Validates session exists (404 if not). - Resume point: ``Last-Event-ID`` header (Atlas A3) takes priority over ``?cursor=``; its value is the ``id:`` of the last frame seen — the per-session ``seq``. A raw cursor value is also tolerated. - If a resume point is provided, validates it (422 if invalid). - Phase 1: Replay all events after the resume point (or from beginning). - Phase 2: Subscribe to PG LISTEN/NOTIFY and yield new events as they arrive. - On client disconnect, cleans up the LISTEN subscription.<br><br>Validates: Requirements 7.1, 7.2, 7.4

**路径参数**

| 参数 | 类型 | 说明 |
|---|---|---|
| `session_id` | string | — |

**Header 参数**

| 参数 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `Last-Event-ID` | string \| null | 否 | — | — |

**Query 参数**

| 参数 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `cursor` | string \| null | 否 | — | — |

**响应 200**：Successful Response

SSE 事件流（事件类型与帧格式见第 7 章「SSE 事件流」）。

**错误**：422 参数校验失败 · 401 未认证 · 429 限流/配额超限 · 500 服务器内部错误（完整定义见第 6 章「通用约定」与 [error-codes.md](error-codes.md)）。

### 20.16 GET /v1/sessions/{session_id}/filesystem

**List Session Filesystem**

List a directory inside the sandbox (Atlas B5, FR-9.2).<br><br>Each entry carries name/type/size/permissions/owner/group/modified (and link_target for symlinks), as parsed from ``ls -la`` output by ``sandbox.list_dir``.

**路径参数**

| 参数 | 类型 | 说明 |
|---|---|---|
| `session_id` | string | — |

**Query 参数**

| 参数 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `path` | string | 否 | 默认 `"/"` | — |

**响应 200**：Successful Response

内容类型 `application/json`。

**错误**：422 参数校验失败 · 401 未认证 · 429 限流/配额超限 · 500 服务器内部错误（完整定义见第 6 章「通用约定」与 [error-codes.md](error-codes.md)）。

### 20.17 GET /v1/sessions/{session_id}/filesystem/raw

**Read Session Filesystem Raw**

Download a file from the sandbox as raw bytes (Atlas B5, FR-9.2).<br><br>Binary-safe ``docker cp``-style read via ``sandbox.copy_from_container``; served as an attachment with an RFC 5987-encoded filename.

**路径参数**

| 参数 | 类型 | 说明 |
|---|---|---|
| `session_id` | string | — |

**Query 参数**

| 参数 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `path` | string | 是 | — | — |

**响应 200**：Successful Response

内容类型 `application/json`。

**错误**：422 参数校验失败 · 401 未认证 · 429 限流/配额超限 · 500 服务器内部错误（完整定义见第 6 章「通用约定」与 [error-codes.md](error-codes.md)）。

## 21. 技能（`skills`）

### 21.1 POST /v1/skills/preflight

**Preflight Skill**

Parse a bundle and report what an upload would do — without persisting anything. The console calls this before an upload proceeds so it can warn the developer when a same-name skill (``replaces``) will be overwritten by the upsert.

**请求体**（必填）

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `bundle` | string | 是 | — | — |

**响应 200**：Successful Response

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `name` | string | 是 | — | — |
| `description` | string | 是 | — | — |
| `version` | string \| null | 否 | — | — |
| `replaces` | `Skill` \| null | 否 | — | — |

**错误**：422 参数校验失败 · 401 未认证 · 429 限流/配额超限 · 500 服务器内部错误（完整定义见第 6 章「通用约定」与 [error-codes.md](error-codes.md)）。

### 21.2 GET /v1/skills

**List Skills**

List skills in the workspace. Live-only by default.

**Query 参数**

| 参数 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `include_archived` | boolean | 否 | 默认 `false` | — |
| `limit` | integer | 否 | ≥ 1; ≤ 100; 默认 `20` | — |
| `offset` | integer | 否 | ≥ 0; 默认 `0` | — |

**响应 200**：Successful Response

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `data` | `Skill`[] | 是 | — | — |
| `has_more` | boolean | 是 | — | — |
| `next_cursor` | string \| null | 否 | — | — |

**错误**：422 参数校验失败 · 401 未认证 · 429 限流/配额超限 · 500 服务器内部错误（完整定义见第 6 章「通用约定」与 [error-codes.md](error-codes.md)）。

### 21.3 POST /v1/skills

**Upload Skill**

Upload a skill bundle. Parses SKILL.md, validates the frontmatter, persists the archive on disk, and either inserts a new row or updates the existing one with the same ``name`` (the update path is how operators ship a new revision of a skill).

**请求体**（必填）

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `bundle` | string | 是 | — | — |

**响应 201**：Successful Response

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `id` | string | 是 | — | — |
| `name` | string | 是 | — | — |
| `description` | string | 是 | — | — |
| `version` | string \| null | 否 | — | — |
| `bundle_sha256` | string | 是 | — | — |
| `bundle_size` | integer | 是 | — | — |
| `created_at` | string(date-time) | 是 | — | — |
| `updated_at` | string(date-time) | 是 | — | — |
| `archived_at` | string(date-time) \| null | 否 | — | — |

**错误**：422 参数校验失败 · 401 未认证 · 429 限流/配额超限 · 500 服务器内部错误（完整定义见第 6 章「通用约定」与 [error-codes.md](error-codes.md)）。

### 21.4 GET /v1/skills/{skill_id}

**Get Skill**

**路径参数**

| 参数 | 类型 | 说明 |
|---|---|---|
| `skill_id` | string | — |

**响应 200**：Successful Response

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `id` | string | 是 | — | — |
| `name` | string | 是 | — | — |
| `description` | string | 是 | — | — |
| `version` | string \| null | 否 | — | — |
| `bundle_sha256` | string | 是 | — | — |
| `bundle_size` | integer | 是 | — | — |
| `created_at` | string(date-time) | 是 | — | — |
| `updated_at` | string(date-time) | 是 | — | — |
| `archived_at` | string(date-time) \| null | 否 | — | — |

**错误**：422 参数校验失败 · 401 未认证 · 429 限流/配额超限 · 500 服务器内部错误（完整定义见第 6 章「通用约定」与 [error-codes.md](error-codes.md)）。

### 21.5 DELETE /v1/skills/{skill_id}

**Delete Skill**

Soft-delete a skill (sets ``archived_at``). The bundle stays on disk in case it's still referenced by an agent's ``skills[]`` from a prior version; GC of orphaned bundles is a future concern.

**路径参数**

| 参数 | 类型 | 说明 |
|---|---|---|
| `skill_id` | string | — |

**响应 200**：Successful Response

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `id` | string | 是 | — | — |
| `name` | string | 是 | — | — |
| `description` | string | 是 | — | — |
| `version` | string \| null | 否 | — | — |
| `bundle_sha256` | string | 是 | — | — |
| `bundle_size` | integer | 是 | — | — |
| `created_at` | string(date-time) | 是 | — | — |
| `updated_at` | string(date-time) | 是 | — | — |
| `archived_at` | string(date-time) \| null | 否 | — | — |

**错误**：422 参数校验失败 · 401 未认证 · 429 限流/配额超限 · 500 服务器内部错误（完整定义见第 6 章「通用约定」与 [error-codes.md](error-codes.md)）。

## 22. 用量（`usage`）

### 22.1 GET /v1/usage

**Get Usage**

Aggregate token usage for the caller's project.<br><br>Buckets are UTC days by default; ``group_by=project`` folds the whole window into a single project row (kept for symmetry with the platform-level aggregation in the upgrade plan).

**Query 参数**

| 参数 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `from` | string(date-time) \| null | 否 | — | — |
| `to` | string(date-time) \| null | 否 | — | — |
| `group_by` | string | 否 | 枚举 `"day"` / `"project"`; 默认 `"day"` | — |

**响应 200**：Successful Response

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `group_by` | string | 是 | 枚举 `"day"` / `"project"` | — |
| `from` | string(date-time) \| null | 否 | — | — |
| `to` | string(date-time) \| null | 否 | — | — |
| `total` | `UsageBucket` | 是 | — | — |
| `buckets` | `UsageBucket`[] | 否 | — | — |

**错误**：422 参数校验失败 · 401 未认证 · 429 限流/配额超限 · 500 服务器内部错误（完整定义见第 6 章「通用约定」与 [error-codes.md](error-codes.md)）。

## 23. 凭据保险库（`vaults`）

凭据分三类用途：

| 用途 | credential_type | 目标字段 | 说明 |
|---|---|---|---|
| MCP 服务鉴权 | `bearer_token` / `oauth` | `mcp_server_url` | 会话中 MCP 工具调用时按服务地址匹配使用 |
| 智能体模型调用 | `api_key` | `provider` | 会话创建时绑定保险库后，运行时按 `(vault_id, provider)` 与智能体模型供应商匹配，解密后作为 LLM API Key 注入（详见 [model-gateway.md](model-gateway.md)） |
| BYOK 加密 | `byok_kek` | — | 客户托管密钥加密密钥（KEK） |

`api_key` 凭据的解析优先级：绑定保险库中的 Vault 凭据 → 环境变量（`OPENROUTER_API_KEY` / `OPENAI_API_KEY`）→ `ollama` 无需密钥；均已缺失且供应商非 `ollama` 时，会话以 `credential_missing` 失败。同一保险库内 `mcp_server_url`（MCP 凭据）或 `provider`（`api_key` 凭据）各自唯一。

> **行为导向专题文档**：[docs/vault-credential-guide.md](vault-credential-guide.md) — 面向接入方的凭据保险库说明（快速上手、BYOK 流程、生命周期语义与实测错误速查表）。本章各端点下标注"实测注记"的行为均已按 0.7.0 专项测试核验（[用例](api-vault-credential-test-cases.md) / [执行报告](api-vault-credential-test-report.md)）。

### 23.1 GET /v1/vaults

**List Vaults**

Return a paginated list of vaults.

**Query 参数**

| 参数 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `limit` | integer | 否 | ≥ 1; ≤ 100; 默认 `20` | — |
| `offset` | integer | 否 | ≥ 0; 默认 `0` | — |

**响应 200**：Successful Response

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `data` | any[] | 是 | — | — |
| `next_cursor` | string \| null | 否 | — | — |
| `has_more` | boolean | 否 | 默认 `false` | — |

> **实测注记（0.7.0）**：`next_cursor` 恒为 `null`，游标翻页不可用；实际请用 `limit` / `offset` 翻页，排序为 `created_at` 降序。`offset` 越界返回 200 且 `data == []`、`has_more == false`。

**错误**：422 参数校验失败 · 401 未认证 · 429 限流/配额超限 · 500 服务器内部错误（完整定义见第 6 章「通用约定」与 [error-codes.md](error-codes.md)）。

### 23.2 POST /v1/vaults

**Create Vault**

Create a new vault resource.

**请求体**（必填）

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `display_name` | string | 是 | — | — |
| `metadata` | object | 否 | — | — |

**响应 201**：Successful Response

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `id` | string | 是 | — | — |
| `display_name` | string | 是 | — | — |
| `metadata` | object | 否 | — | — |
| `created_at` | string(date-time) | 是 | — | — |
| `updated_at` | string(date-time) | 是 | — | — |
| `archived_at` | string(date-time) \| null | 否 | — | — |

**错误**：422 参数校验失败 · 401 未认证 · 429 限流/配额超限 · 500 服务器内部错误（完整定义见第 6 章「通用约定」与 [error-codes.md](error-codes.md)）。

### 23.3 GET /v1/vaults/{vault_id}

**Get Vault**

Retrieve a vault by id.

**路径参数**

| 参数 | 类型 | 说明 |
|---|---|---|
| `vault_id` | string | — |

**响应 200**：Successful Response

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `id` | string | 是 | — | — |
| `display_name` | string | 是 | — | — |
| `metadata` | object | 否 | — | — |
| `created_at` | string(date-time) | 是 | — | — |
| `updated_at` | string(date-time) | 是 | — | — |
| `archived_at` | string(date-time) \| null | 否 | — | — |

**错误**：422 参数校验失败 · 401 未认证 · 429 限流/配额超限 · 500 服务器内部错误（完整定义见第 6 章「通用约定」与 [error-codes.md](error-codes.md)）。

> **实测注记（0.7.0）**：路径 `vault_id` 非法 UUID 返回 **404**（非 422），message 形如 `"Vault not-a-uuid not found"`；跨项目访问同样 404（与"不存在"同文案，不泄露存在性）。对照：会话绑定 `vault_ids` 中的非法 UUID 返回 422（见 20.2）。

### 23.4 DELETE /v1/vaults/{vault_id}

**Delete Vault**

Delete a vault and all its credentials (cascade).

**路径参数**

| 参数 | 类型 | 说明 |
|---|---|---|
| `vault_id` | string | — |

**响应 204**：Successful Response

无响应体。

> **实测注记（0.7.0）**：删除会级联**物理删除**其下全部凭据；**已归档的 vault 也可直接删除**（同样 204）；重复删除返回 404 `"Vault {id} not found"`。

**错误**：422 参数校验失败 · 401 未认证 · 429 限流/配额超限 · 500 服务器内部错误（完整定义见第 6 章「通用约定」与 [error-codes.md](error-codes.md)）。

### 23.5 POST /v1/vaults/{vault_id}/archive

**Archive Vault**

Archive a vault: set archived_at, purge credential secrets.

**路径参数**

| 参数 | 类型 | 说明 |
|---|---|---|
| `vault_id` | string | — |

**响应 200**：Successful Response

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `id` | string | 是 | — | — |
| `display_name` | string | 是 | — | — |
| `metadata` | object | 否 | — | — |
| `created_at` | string(date-time) | 是 | — | — |
| `updated_at` | string(date-time) | 是 | — | — |
| `archived_at` | string(date-time) \| null | 否 | — | — |

> **实测注记（0.7.0）**：归档事务同步**级联归档其下全部凭据并清空其密文**；重复归档返回 409 `"Vault {id} is already archived"`；归档后不再接受新建凭据（409 `"Vault {id} is archived"`），但仍可直接 DELETE（204，见 23.4）。

**错误**：422 参数校验失败 · 401 未认证 · 429 限流/配额超限 · 500 服务器内部错误（完整定义见第 6 章「通用约定」与 [error-codes.md](error-codes.md)）。

### 23.6 GET /v1/vaults/{vault_id}/credentials

**List Credentials**

Return a paginated list of credentials in a vault (secrets omitted).

**路径参数**

| 参数 | 类型 | 说明 |
|---|---|---|
| `vault_id` | string | — |

**Query 参数**

| 参数 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `limit` | integer | 否 | ≥ 1; ≤ 100; 默认 `20` | — |
| `offset` | integer | 否 | ≥ 0; 默认 `0` | — |

**响应 200**：Successful Response

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `data` | any[] | 是 | — | — |
| `next_cursor` | string \| null | 否 | — | — |
| `has_more` | boolean | 否 | 默认 `false` | — |

**错误**：422 参数校验失败 · 401 未认证 · 429 限流/配额超限 · 500 服务器内部错误（完整定义见第 6 章「通用约定」与 [error-codes.md](error-codes.md)）。

### 23.7 POST /v1/vaults/{vault_id}/credentials

**Create Credential**

Create a credential within a vault.

**路径参数**

| 参数 | 类型 | 说明 |
|---|---|---|
| `vault_id` | string | — |

**请求体**（必填，按 `credential_type` 判别，4 种变体）

**`credential_type="bearer_token"`**（MCP 服务令牌）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `credential_type` | string | 是 | 固定 `"bearer_token"` |
| `mcp_server_url` | string | 是 | MCP 服务地址（同一保险库内唯一） |
| `token` | string | 是 | Bearer 令牌 |

**`credential_type="api_key"`**（智能体模型供应商密钥）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `credential_type` | string | 是 | 固定 `"api_key"` |
| `provider` | string | 是 | 枚举 `"openrouter"` / `"openai"` / `"ollama"`（同一保险库内唯一） |
| `api_key` | string | 是 | 供应商 API Key，Fernet 加密存储 |

**`credential_type="oauth"`**（MCP 服务 OAuth）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `credential_type` | string | 是 | 固定 `"oauth"` |
| `mcp_server_url` | string | 是 | MCP 服务地址（同一保险库内唯一） |
| `access_token` | string | 是 | 访问令牌 |
| `refresh_token` | string | 否 | 刷新令牌 |
| `client_id` | string | 否 | 客户端 ID |
| `client_secret` | string | 否 | 客户端密钥 |

**`credential_type="byok_kek"`**（客户托管 KEK）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `credential_type` | string | 是 | 固定 `"byok_kek"` |
| `kek` | string | 是 | Fernet 密钥（base64 编码的 32 字节），用于包裹会话数据密钥 |

**响应 201**：Successful Response

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `id` | string | 是 | — | — |
| `vault_id` | string | 是 | — | — |
| `credential_type` | string | 是 | 枚举 `"bearer_token"` / `"api_key"` / `"oauth"` / `"byok_kek"` | — |
| `mcp_server_url` | string \| null | 否 | — | — |
| `provider` | string \| null | 否 | 枚举 `"openrouter"` / `"openai"` / `"ollama"` | — |
| `client_id` | string \| null | 否 | — | — |
| `created_at` | string(date-time) | 是 | — | — |
| `updated_at` | string(date-time) | 是 | — | — |
| `archived_at` | string(date-time) \| null | 否 | — | — |

**实测错误行为（0.7.0）**：

- **422（请求体校验）**：判别联合的 `loc` 带 discriminator 段——非法 `provider` 报 `loc == ["body", "api_key", "provider"]`、`type == "literal_error"`、`msg == "Input should be 'openrouter', 'openai' or 'ollama'"`；非法 `kek` 报 `loc == ["body", "byok_kek", "kek"]`、`msg == "Value error, kek is not a valid Fernet key: Fernet key must be 32 url-safe base64-encoded bytes."`（校验先于入库，DB 无残留行）。
- **409（唯一性冲突）**：同 vault 内重复 `provider` / `mcp_server_url` 返回 `{"error":"conflict","message":"A credential for '{target}' already exists in vault {vault_id}","detail":null}`（`target` 取 `mcp_server_url or provider`）；唯一性作用域为同 vault 内，跨 vault 同值允许。
- **409（数量上限）**：单 vault 未归档凭据达 20 个后再建返回 409 `"Vault {vault_id} already has 20 active credentials"`（上限按未归档计数，归档可腾出名额）。
- **409（vault 已归档）**：向已归档 vault 创建凭据返回 409 `"Vault {vault_id} is archived"`。

**错误**：422 参数校验失败 · 401 未认证 · 429 限流/配额超限 · 500 服务器内部错误（完整定义见第 6 章「通用约定」与 [error-codes.md](error-codes.md)）。

### 23.8 GET /v1/vaults/{vault_id}/credentials/{credential_id}

**Get Credential**

Retrieve a credential by id (secrets omitted).

**路径参数**

| 参数 | 类型 | 说明 |
|---|---|---|
| `vault_id` | string | — |
| `credential_id` | string | — |

**响应 200**：Successful Response

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `id` | string | 是 | — | — |
| `vault_id` | string | 是 | — | — |
| `credential_type` | string | 是 | 枚举 `"bearer_token"` / `"api_key"` / `"oauth"` / `"byok_kek"` | — |
| `mcp_server_url` | string \| null | 否 | — | — |
| `provider` | string \| null | 否 | 枚举 `"openrouter"` / `"openai"` / `"ollama"` | — |
| `client_id` | string \| null | 否 | — | — |
| `created_at` | string(date-time) | 是 | — | — |
| `updated_at` | string(date-time) | 是 | — | — |
| `archived_at` | string(date-time) \| null | 否 | — | — |

**错误**：422 参数校验失败 · 401 未认证 · 429 限流/配额超限 · 500 服务器内部错误（完整定义见第 6 章「通用约定」与 [error-codes.md](error-codes.md)）。

### 23.9 PATCH /v1/vaults/{vault_id}/credentials/{credential_id}

**Update Credential**

Update secret fields on a credential.

**路径参数**

| 参数 | 类型 | 说明 |
|---|---|---|
| `vault_id` | string | — |
| `credential_id` | string | — |

**请求体**（必填）

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `token` | string \| null | 否 | — | — |
| `api_key` | string \| null | 否 | — | — |
| `access_token` | string \| null | 否 | — | — |
| `refresh_token` | string \| null | 否 | — | — |
| `client_secret` | string \| null | 否 | — | — |
| `kek` | string \| null | 否 | — | — |

> ⚠️ **实测行为警示（0.7.0）**：PATCH 是对 `encrypted_secrets` 的**整体替换而非字段合并**——请求中未带的密文字段会被**静默清空**（例如仅 PATCH `access_token` 会使原 `refresh_token` 丢失，DB 解密复核已证实）。轮换密钥时请在单次请求中带上**全部**需保留的密文字段。空体 `{}` 原样返回且 `updated_at` 不变；已归档凭据返回 409 `"Credential {id} is archived"`。

**响应 200**：Successful Response

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `id` | string | 是 | — | — |
| `vault_id` | string | 是 | — | — |
| `credential_type` | string | 是 | 枚举 `"bearer_token"` / `"api_key"` / `"oauth"` / `"byok_kek"` | — |
| `mcp_server_url` | string \| null | 否 | — | — |
| `provider` | string \| null | 否 | 枚举 `"openrouter"` / `"openai"` / `"ollama"` | — |
| `client_id` | string \| null | 否 | — | — |
| `created_at` | string(date-time) | 是 | — | — |
| `updated_at` | string(date-time) | 是 | — | — |
| `archived_at` | string(date-time) \| null | 否 | — | — |

**错误**：422 参数校验失败 · 401 未认证 · 429 限流/配额超限 · 500 服务器内部错误（完整定义见第 6 章「通用约定」与 [error-codes.md](error-codes.md)）。

### 23.10 DELETE /v1/vaults/{vault_id}/credentials/{credential_id}

**Delete Credential**

Delete a credential.

**路径参数**

| 参数 | 类型 | 说明 |
|---|---|---|
| `vault_id` | string | — |
| `credential_id` | string | — |

**响应 204**：Successful Response

无响应体。

> **实测注记（0.7.0）**：DELETE 不过滤已归档凭据——已归档凭据同样可物理删除（204）。

**错误**：422 参数校验失败 · 401 未认证 · 429 限流/配额超限 · 500 服务器内部错误（完整定义见第 6 章「通用约定」与 [error-codes.md](error-codes.md)）。

### 23.11 POST /v1/vaults/{vault_id}/credentials/{credential_id}/archive

**Archive Credential**

Archive a credential: set archived_at, purge encrypted secrets.

**路径参数**

| 参数 | 类型 | 说明 |
|---|---|---|
| `vault_id` | string | — |
| `credential_id` | string | — |

**响应 200**：Successful Response

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `id` | string | 是 | — | — |
| `vault_id` | string | 是 | — | — |
| `credential_type` | string | 是 | 枚举 `"bearer_token"` / `"api_key"` / `"oauth"` / `"byok_kek"` | — |
| `mcp_server_url` | string \| null | 否 | — | — |
| `provider` | string \| null | 否 | 枚举 `"openrouter"` / `"openai"` / `"ollama"` | — |
| `client_id` | string \| null | 否 | — | — |
| `created_at` | string(date-time) | 是 | — | — |
| `updated_at` | string(date-time) | 是 | — | — |
| `archived_at` | string(date-time) \| null | 否 | — | — |

> **实测注记（0.7.0）**：归档置 `archived_at` 并清空密文；重复归档返回 409 `"Credential {id} is already archived"`；归档后 PATCH 返回 409 `"Credential {id} is archived"`；GET 与 DELETE 不过滤已归档（GET 仍 200、DELETE 仍 204）。vault 归档会级联归档其下全部凭据（见 23.5）。

**错误**：422 参数校验失败 · 401 未认证 · 429 限流/配额超限 · 500 服务器内部错误（完整定义见第 6 章「通用约定」与 [error-codes.md](error-codes.md)）。

## 24. Webhook 端点（`webhooks`）

### 24.1 GET /v1/webhook_endpoints

**List Webhook Endpoints**

**Query 参数**

| 参数 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `limit` | integer | 否 | ≥ 1; ≤ 100; 默认 `20` | — |
| `include_archived` | boolean | 否 | 默认 `false` | — |

**响应 200**：Successful Response

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `data` | any[] | 是 | — | — |
| `next_cursor` | string \| null | 否 | — | — |
| `has_more` | boolean | 否 | 默认 `false` | — |

**错误**：422 参数校验失败 · 401 未认证 · 429 限流/配额超限 · 500 服务器内部错误（完整定义见第 6 章「通用约定」与 [error-codes.md](error-codes.md)）。

### 24.2 POST /v1/webhook_endpoints

**Create Webhook Endpoint**

Create a new webhook endpoint. The HMAC secret is returned ONCE in this response — subsequent reads omit it. Use ``PATCH ... rotate_secret`` to roll a forgotten one (invalidates the old).

**请求体**（必填）

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `url` | string | 是 | 长度 1..2048 | — |
| `enabled_events` | string[] | 否 | — | — |
| `description` | string \| null | 否 | 长度 0..1024 | — |

**响应 201**：Successful Response

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `id` | string | 是 | — | — |
| `url` | string | 是 | — | — |
| `enabled_events` | string[] | 否 | — | — |
| `description` | string \| null | 否 | — | — |
| `enabled` | boolean | 否 | 默认 `true` | — |
| `created_at` | string(date-time) | 是 | — | — |
| `updated_at` | string(date-time) | 是 | — | — |
| `archived_at` | string(date-time) \| null | 否 | — | — |
| `secret` | string \| null | 否 | — | — |

**错误**：422 参数校验失败 · 401 未认证 · 429 限流/配额超限 · 500 服务器内部错误（完整定义见第 6 章「通用约定」与 [error-codes.md](error-codes.md)）。

### 24.3 GET /v1/webhook_endpoints/{endpoint_id}

**Get Webhook Endpoint**

**路径参数**

| 参数 | 类型 | 说明 |
|---|---|---|
| `endpoint_id` | string | — |

**响应 200**：Successful Response

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `id` | string | 是 | — | — |
| `url` | string | 是 | — | — |
| `enabled_events` | string[] | 否 | — | — |
| `description` | string \| null | 否 | — | — |
| `enabled` | boolean | 否 | 默认 `true` | — |
| `created_at` | string(date-time) | 是 | — | — |
| `updated_at` | string(date-time) | 是 | — | — |
| `archived_at` | string(date-time) \| null | 否 | — | — |
| `secret` | string \| null | 否 | — | — |

**错误**：422 参数校验失败 · 401 未认证 · 429 限流/配额超限 · 500 服务器内部错误（完整定义见第 6 章「通用约定」与 [error-codes.md](error-codes.md)）。

### 24.4 PATCH /v1/webhook_endpoints/{endpoint_id}

**Update Webhook Endpoint**

**路径参数**

| 参数 | 类型 | 说明 |
|---|---|---|
| `endpoint_id` | string | — |

**请求体**（必填）

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `url` | string \| null | 否 | — | — |
| `enabled_events` | string[] \| null | 否 | — | — |
| `description` | string \| null | 否 | — | — |
| `enabled` | boolean \| null | 否 | — | — |
| `rotate_secret` | boolean | 否 | 默认 `false` | — |

**响应 200**：Successful Response

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `id` | string | 是 | — | — |
| `url` | string | 是 | — | — |
| `enabled_events` | string[] | 否 | — | — |
| `description` | string \| null | 否 | — | — |
| `enabled` | boolean | 否 | 默认 `true` | — |
| `created_at` | string(date-time) | 是 | — | — |
| `updated_at` | string(date-time) | 是 | — | — |
| `archived_at` | string(date-time) \| null | 否 | — | — |
| `secret` | string \| null | 否 | — | — |

**错误**：422 参数校验失败 · 401 未认证 · 429 限流/配额超限 · 500 服务器内部错误（完整定义见第 6 章「通用约定」与 [error-codes.md](error-codes.md)）。

### 24.5 DELETE /v1/webhook_endpoints/{endpoint_id}

**Delete Webhook Endpoint**

Soft-delete (sets ``archived_at``). The endpoint stops receiving new deliveries but historical ``webhook_deliveries`` rows are preserved for audit. Idempotent.

**路径参数**

| 参数 | 类型 | 说明 |
|---|---|---|
| `endpoint_id` | string | — |

**响应 204**：Successful Response

无响应体。

**错误**：422 参数校验失败 · 401 未认证 · 429 限流/配额超限 · 500 服务器内部错误（完整定义见第 6 章「通用约定」与 [error-codes.md](error-codes.md)）。

### 24.6 GET /v1/webhook_endpoints/{endpoint_id}/deliveries

**List Webhook Deliveries**

**路径参数**

| 参数 | 类型 | 说明 |
|---|---|---|
| `endpoint_id` | string | — |

**Query 参数**

| 参数 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `limit` | integer | 否 | ≥ 1; ≤ 200; 默认 `50` | — |

**响应 200**：Successful Response

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `data` | any[] | 是 | — | — |
| `next_cursor` | string \| null | 否 | — | — |
| `has_more` | boolean | 否 | 默认 `false` | — |

**错误**：422 参数校验失败 · 401 未认证 · 429 限流/配额超限 · 500 服务器内部错误（完整定义见第 6 章「通用约定」与 [error-codes.md](error-codes.md)）。

## 25. 其他（`other`）

### 25.1 GET /health

**Health**

Health check endpoint.

**响应 200**：Successful Response

内容类型 `application/json`。

## 26. 附录：Schema 速查

正文中以反引号引用（如 `SessionResponse`）而未就地展开的结构，在此列出顶层字段（仅一层，嵌套引用以类型名标注）。

### 26.1 `AgentResponse`

Full agent resource returned by the API.

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `id` | string | 是 | — | — |
| `name` | string | 是 | — | — |
| `version` | integer | 是 | — | — |
| `model` | `ModelConfig` | 是 | — | — |
| `system` | string | 是 | — | — |
| `tools` | `BuiltinToolConfig` \| `CustomToolConfig`[] | 否 | — | — |
| `mcp_servers` | `StdioMCPServerConfig` \| `UrlMCPServerConfig`[] | 否 | — | — |
| `skills` | string[] | 否 | — | — |
| `multi_agents` | `SubagentConfig`[] | 否 | — | — |
| `created_at` | string(date-time) | 是 | — | — |
| `description` | string \| null | 否 | — | — |
| `metadata` | object | 否 | — | — |
| `archived_at` | string(date-time) \| null | 否 | — | — |

### 26.2 `ApiKeyCreatedResponse`

``POST /v1/api-keys`` response — ``key`` is the plaintext secret, returned exactly once and never stored (only its SHA-256 hash is).

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `id` | string | 是 | — | — |
| `project_id` | string | 是 | — | — |
| `name` | string | 是 | — | — |
| `prefix` | string | 是 | — | — |
| `scopes` | string[] | 是 | — | — |
| `status` | string | 是 | — | — |
| `rate_limit_rpm` | integer \| null | 否 | — | — |
| `created_at` | string(date-time) | 是 | — | — |
| `last_used_at` | string(date-time) \| null | 否 | — | — |
| `key` | string | 是 | — | — |

### 26.3 `ApiKeyResponse`

API key metadata (never includes the secret material).

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `id` | string | 是 | — | — |
| `project_id` | string | 是 | — | — |
| `name` | string | 是 | — | — |
| `prefix` | string | 是 | — | — |
| `scopes` | string[] | 是 | — | — |
| `status` | string | 是 | — | — |
| `rate_limit_rpm` | integer \| null | 否 | — | — |
| `created_at` | string(date-time) | 是 | — | — |
| `last_used_at` | string(date-time) \| null | 否 | — | — |

### 26.4 `Body_preflight_skill_v1_skills_preflight_post`

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `bundle` | string | 是 | — | — |

### 26.5 `Body_upload_file_v1_files_post`

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `file` | string | 是 | — | — |

### 26.6 `Body_upload_skill_v1_skills_post`

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `bundle` | string | 是 | — | — |

### 26.7 `BuiltinToolConfig`

Built-in toolset entry (e.g. bash, read, write …).

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `type` | string | 是 | 固定值 `"builtin"` | — |
| `default_config` | object | 否 | — | — |
| `configs` | `BuiltinToolItemConfig`[] | 否 | — | — |

### 26.8 `ContentShaPrecondition`

Optimistic-concurrency precondition for memory writes.<br><br>Semantics mirror HTTP ETag: ``sha256=None`` matches "must not exist" (create-only); ``sha256=<hex>`` matches "current head sha must equal". A mismatch returns 412 ``precondition_failed`` so callers can re-read and retry their merge.

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `type` | string | 否 | 固定值 `"content_sha256"`; 默认 `"content_sha256"` | — |
| `sha256` | string \| null | 否 | — | — |

### 26.9 `CreateAgentRequest`

POST /v1/agents request body.

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `name` | string | 是 | — | — |
| `model` | `ModelConfig` | 是 | — | — |
| `system` | string | 是 | — | — |
| `tools` | `BuiltinToolConfig` \| `CustomToolConfig`[] | 否 | — | — |
| `mcp_servers` | `StdioMCPServerConfig` \| `UrlMCPServerConfig`[] | 否 | — | — |
| `skills` | string[] | 否 | — | — |
| `multi_agents` | `SubagentConfig`[] | 否 | — | — |
| `description` | string \| null | 否 | — | — |
| `metadata` | object | 否 | — | — |

### 26.10 `CreateApiKeyRequest`

Body for ``POST /v1/api-keys``.<br><br>``project_id`` is optional — defaults to the caller's project. Passing a project from another organization is rejected with 404. ``rate_limit_rpm`` (Atlas M5, FR-11.2) sets an optional requests-per- minute limit enforced as an in-process sliding window.

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `name` | string | 否 | 默认 `""` | — |
| `project_id` | string \| null | 否 | — | — |
| `scopes` | string[] \| null | 否 | — | — |
| `rate_limit_rpm` | integer \| null | 否 | ≥ 1.0 | — |

### 26.11 `CreateDeploymentRequest`

POST /v1/deployments request body.

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `agent_id` | string | 是 | — | — |
| `environment_id` | string | 是 | — | — |
| `cron` | string | 是 | — | — |
| `timezone` | string | 否 | 默认 `"UTC"` | — |
| `initial_events` | `InitialEvent`[] | 否 | — | — |

### 26.12 `CreateDreamRequest`

``POST /v1/dreams`` request body.

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `input_memory_store_id` | string | 是 | — | — |
| `output_store_name` | string | 是 | — | — |
| `session_filter` | `DreamSessionFilter` | 否 | — | — |
| `dreamer_agent_id` | string \| null | 否 | — | — |

### 26.13 `CreateEnvironmentRequest`

POST /v1/environments request body.

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `name` | string | 是 | — | — |
| `config` | `EnvironmentConfig` | 是 | — | — |

### 26.14 `CreateMemoryStoreRequest`

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `name` | string | 是 | 长度 1..64 | — |
| `description` | string \| null | 否 | 长度 0..1024 | — |

### 26.15 `CreateProjectRequest`

Body for ``POST /v1/projects`` (bootstrap key only).<br><br>``slug`` is optional — derived from ``name`` when omitted; must be lowercase kebab-case and unique within the organization (409 on conflict). Quota fields mirror ``UpdateProjectRequest`` (``None`` = unlimited).

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `name` | string | 是 | 长度 1..∞ | — |
| `slug` | string \| null | 否 | 长度 1..64; 正则 `^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$` | — |
| `max_concurrent_sessions` | integer \| null | 否 | ≥ 1.0 | — |
| `monthly_token_budget` | integer \| null | 否 | ≥ 1.0 | — |

### 26.16 `CreateSessionRequest`

POST /v1/sessions request body.

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `agent_id` | string | 是 | — | — |
| `environment_id` | string | 是 | — | — |
| `title` | string \| null | 否 | — | — |
| `metadata` | object | 否 | — | — |
| `ttl_seconds` | integer \| null | 否 | — | — |
| `vault_ids` | string[] | 否 | — | 绑定凭据保险库；运行时按智能体模型供应商从其中解析 `api_key` 凭据（见第 23 章） |
| `resources` | `FileResource` \| `MemoryStoreResource` \| `GitRepositoryResource` \| `GithubRepositoryResource` \| `VaultResource`[] | 否 | — | 资源挂载列表；其中 `type="vault"` 条目等效于 `vault_ids` 绑定 |
| `outcome` | `OutcomeDefinition` \| null | 否 | — | — |

### 26.17 `CreateVaultRequest`

POST /v1/vaults request body.

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `display_name` | string | 是 | — | — |
| `metadata` | object | 否 | — | — |

### 26.18 `CreateWebhookEndpointRequest`

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `url` | string | 是 | 长度 1..2048 | — |
| `enabled_events` | string[] | 否 | — | — |
| `description` | string \| null | 否 | 长度 0..1024 | — |

### 26.19 `CredentialResponse`

Credential resource returned by the API (all secret fields omitted).

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `id` | string | 是 | — | — |
| `vault_id` | string | 是 | — | — |
| `credential_type` | string | 是 | 枚举 `"bearer_token"` / `"api_key"` / `"oauth"` / `"byok_kek"` | — |
| `mcp_server_url` | string \| null | 否 | — | — |
| `provider` | string \| null | 否 | 枚举 `"openrouter"` / `"openai"` / `"ollama"` | — |
| `client_id` | string \| null | 否 | — | — |
| `created_at` | string(date-time) | 是 | — | — |
| `updated_at` | string(date-time) | 是 | — | — |
| `archived_at` | string(date-time) \| null | 否 | — | — |

### 26.20 `CustomToolConfig`

Custom tool entry with an HTTP endpoint.

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `type` | string | 是 | 固定值 `"custom"` | — |
| `name` | string | 是 | — | — |
| `description` | string | 否 | 默认 `""` | — |
| `input_schema` | object | 否 | — | — |
| `permission_policy` | string | 否 | 枚举 `"always_allow"` / `"always_ask"`; 默认 `"always_ask"` | — |
| `endpoint` | string \| null | 否 | — | — |

### 26.21 `DeploymentResponse`

Deployment resource returned by the API.

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `id` | string | 是 | — | — |
| `agent_id` | string | 是 | — | — |
| `environment_id` | string | 是 | — | — |
| `cron` | string | 是 | — | — |
| `timezone` | string | 是 | — | — |
| `initial_events` | `InitialEvent`[] | 否 | — | — |
| `status` | string | 是 | 枚举 `"paused"` / `"active"` | — |
| `next_run_at` | string(date-time) \| null | 是 | — | — |
| `created_at` | string(date-time) | 是 | — | — |
| `archived_at` | string(date-time) \| null | 是 | — | — |

### 26.22 `Dream`

A single Dream record.

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `id` | string | 是 | — | — |
| `input_memory_store_id` | string | 是 | — | — |
| `output_memory_store_id` | string \| null | 否 | — | — |
| `output_store_name` | string | 是 | — | — |
| `dreamer_session_id` | string \| null | 否 | — | — |
| `dreamer_agent_id` | string \| null | 否 | — | — |
| `session_filter` | `DreamSessionFilter` | 是 | — | — |
| `status` | string | 是 | 枚举 `"pending"` / `"running"` / `"completed"` / `"failed"` / `"canceled"` | — |
| `error` | string \| null | 否 | — | — |
| `created_at` | string(date-time) | 是 | — | — |
| `started_at` | string(date-time) \| null | 否 | — | — |
| `ended_at` | string(date-time) \| null | 否 | — | — |

### 26.23 `DreamListResponse`

Payload for ``GET /v1/dreams``.

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `data` | `Dream`[] | 是 | — | — |
| `has_more` | boolean | 是 | — | — |
| `next_cursor` | string \| null | 否 | — | — |

### 26.24 `DreamSessionFilter`

Filter the past sessions a Dream curates over.<br><br>All fields are AND'd together. Empty (default) means "every session in the workspace"; the runtime still caps at ``MAX_SESSIONS_PER_DREAM`` so a wide-open filter doesn't drag in the whole history.

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `agent_id` | string \| null | 否 | — | — |
| `since` | string(date-time) \| null | 否 | — | — |
| `until` | string(date-time) \| null | 否 | — | — |
| `limit` | integer | 否 | ≥ 1.0; ≤ 100.0; 默认 `100` | — |

### 26.25 `EnvironmentConfig`

Environment configuration wrapper.

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `networking` | `NoneNetworking` \| `UnrestrictedNetworking` \| `LimitedNetworking` | 是 | — | — |
| `packages` | `EnvironmentPackages` | 否 | — | — |
| `resources` | `SandboxResources` | 否 | — | — |
| `isolation` | string | 否 | 枚举 `"docker"` / `"microvm"`; 默认 `"docker"` | — |
| `tool_search` | `ToolSearchConfig` \| null | 否 | — | — |
| `executor_node` | string \| null | 否 | — | — |

### 26.26 `EnvironmentResponse`

Full environment resource returned by the API.

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `id` | string | 是 | — | — |
| `name` | string | 是 | — | — |
| `config` | `EnvironmentConfig` | 是 | — | — |
| `created_at` | string(date-time) | 是 | — | — |
| `archived_at` | string(date-time) \| null | 否 | — | — |

### 26.27 `EventPayload`

A single event inside a PostEventsRequest.

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `type` | string | 是 | 枚举 `"user.message"` / `"user.steer"` / `"user.interrupt"` / `"user.tool_confirmation"` / `"user.custom_tool_result"` / `"agent.message"` / `"agent.message_delta"` / `"agent.thinking"` / `"agent.tool_use"` / `"agent.tool_result"` / `"agent.mcp_tool_use"` / `"agent.mcp_tool_result"` / `"agent.custom_tool_use"` / `"agent.deliverable"` / `"agent.deliverable_dropped"` / `"session.status_running"` / `"session.status_idle"` / `"session.status_rescheduled"` / `"session.status_terminated"` / `"session.status"` / `"session.error"` / `"session.requires_action"` / `"session.resource_mount_failed"` / `"session.memory_mount_failed"` / `"session.deprecation_used"` / `"session.compacted"` / `"memory.write"` / `"memory.write_rejected"` / `"memory.gc"` / `"session.outcome_evaluation_started"` / `"session.outcome_evaluation_ended"` / `"agent.thread_create"` / `"session.thread_created"` / `"session.thread_idled"` / `"session.thread_terminated"` / `"span.model_request_start"` / `"span.model_request_end"` | — |
| `payload` | object | 否 | — | — |

### 26.28 `EventResponse`

Single event in API responses.

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `session_id` | string | 是 | — | — |
| `cursor` | string | 是 | — | — |
| `seq` | integer | 是 | — | — |
| `type` | string | 是 | 枚举 `"user.message"` / `"user.steer"` / `"user.interrupt"` / `"user.tool_confirmation"` / `"user.custom_tool_result"` / `"agent.message"` / `"agent.message_delta"` / `"agent.thinking"` / `"agent.tool_use"` / `"agent.tool_result"` / `"agent.mcp_tool_use"` / `"agent.mcp_tool_result"` / `"agent.custom_tool_use"` / `"agent.deliverable"` / `"agent.deliverable_dropped"` / `"session.status_running"` / `"session.status_idle"` / `"session.status_rescheduled"` / `"session.status_terminated"` / `"session.status"` / `"session.error"` / `"session.requires_action"` / `"session.resource_mount_failed"` / `"session.memory_mount_failed"` / `"session.deprecation_used"` / `"session.compacted"` / `"memory.write"` / `"memory.write_rejected"` / `"memory.gc"` / `"session.outcome_evaluation_started"` / `"session.outcome_evaluation_ended"` / `"agent.thread_create"` / `"session.thread_created"` / `"session.thread_idled"` / `"session.thread_terminated"` / `"span.model_request_start"` / `"span.model_request_end"` | — |
| `payload` | object | 否 | — | — |
| `processed_at` | string(date-time) \| null | 否 | — | — |
| `event_id` | string \| null | 否 | — | — |

### 26.29 `ExecutorNodeCreateRequest`

Body for ``POST /v1/executor-nodes``.<br><br>``url`` is the base URL of the executor daemon (``uvicorn app.executor_daemon:app``) reachable from the API.

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `name` | string | 是 | — | — |
| `url` | string | 是 | — | — |
| `max_sessions` | integer | 否 | ≥ 1.0; 默认 `5` | — |

### 26.30 `ExecutorNodeCreatedResponse`

``POST /v1/executor-nodes`` response — ``token`` is the plaintext daemon credential, returned exactly once. It is stored Fernet-encrypted (the API must present it to the daemon on every call).

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `id` | string | 是 | — | — |
| `org_id` | string | 是 | — | — |
| `name` | string | 是 | — | — |
| `url` | string | 是 | — | — |
| `prefix` | string | 是 | — | — |
| `max_sessions` | integer | 是 | — | — |
| `status` | string | 是 | — | — |
| `last_heartbeat_at` | string(date-time) \| null | 否 | — | — |
| `created_at` | string(date-time) | 是 | — | — |
| `token` | string | 是 | — | — |

### 26.31 `ExecutorNodeHeartbeatResponse`

``POST /v1/executor-nodes/{id}/heartbeat`` response.

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `id` | string | 是 | — | — |
| `status` | string | 否 | 默认 `"online"` | — |

### 26.32 `ExecutorNodeResponse`

Executor node metadata (never includes the secret material).<br><br>``status`` is derived from ``last_heartbeat_at`` at read time — "online" when a heartbeat arrived within the freshness window, "offline" otherwise (including nodes that never heartbeated).

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `id` | string | 是 | — | — |
| `org_id` | string | 是 | — | — |
| `name` | string | 是 | — | — |
| `url` | string | 是 | — | — |
| `prefix` | string | 是 | — | — |
| `max_sessions` | integer | 是 | — | — |
| `status` | string | 是 | — | — |
| `last_heartbeat_at` | string(date-time) \| null | 否 | — | — |
| `created_at` | string(date-time) | 是 | — | — |

### 26.33 `FileResource`

Mount a previously-uploaded file into the sandbox read-only.<br><br>The referenced file must have source='upload' (deliverables cannot be re-mounted into a different session — that would be a covert channel).<br><br>``mount_path`` is optional since Atlas B5: when omitted, create_session assigns ``/mnt/session/uploads/<basename>`` automatically (see routes/sessions.py). Explicit values still go through the shared validator, so platform-reserved prefixes are rejected.

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `type` | string | 是 | 固定值 `"file"` | — |
| `file_id` | string | 是 | — | — |
| `mount_path` | string \| null | 否 | — | — |

### 26.34 `FileResponse`

File resource returned by the API. Metadata only; bytes stream via /content.

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `id` | string | 是 | — | — |
| `filename` | string | 是 | — | — |
| `content_type` | string | 是 | — | — |
| `size_bytes` | integer | 是 | — | — |
| `sha256` | string | 是 | — | — |
| `source` | string | 是 | 枚举 `"upload"` / `"deliverable"` / `"artifact"` | — |
| `downloadable` | boolean | 是 | — | — |
| `scope_type` | string \| null | 否 | 固定值 `"session"` | — |
| `scope_id` | string \| null | 否 | — | — |
| `created_at` | string(date-time) | 是 | — | — |
| `archived_at` | string(date-time) \| null | 否 | — | — |

### 26.35 `GitRepositoryResource`

Clone any HTTPS git remote into the sandbox (v0.5.0).<br><br>Generic by design — works with GitHub, GitLab, Bitbucket, Gitea, any HTTPS git server — even though the most common pairing is the GitHub MCP server. Tokens flow either inline via ``authorization_token`` or, post-v0.5, through ``vault_ids`` (same pattern as URL MCP servers).

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `type` | string | 是 | 固定值 `"git_repository"` | — |
| `url` | string | 是 | — | — |
| `mount_path` | string | 是 | — | — |
| `authorization_token` | string \| null | 否 | — | — |
| `branch` | string \| null | 否 | — | — |
| `shallow` | boolean | 否 | 默认 `true` | — |

### 26.36 `GithubRepositoryResource`

Legacy alias for ``GitRepositoryResource`` (v0.2-v0.4 reserved the ``github_repository`` type; v0.5 generalizes to ``git_repository``). Accepted on input for backwards compat and normalized to the generic form before dispatch.

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `type` | string | 是 | 固定值 `"github_repository"` | — |
| `url` | string | 是 | — | — |
| `mount_path` | string | 是 | — | — |
| `authorization_token` | string \| null | 否 | — | — |
| `branch` | string \| null | 否 | — | — |
| `shallow` | boolean | 否 | 默认 `true` | — |

### 26.37 `InitialEvent`

One event seeded into every session a deployment triggers.<br><br>Only user-side inputs are accepted — a deployment must not be able to forge agent.*/session.* events into its own session log.

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `type` | string | 是 | 枚举 `"user.message"` / `"user.steer"` | — |
| `payload` | object | 否 | — | — |

### 26.38 `Memory`

A single path-addressed memory entry inside a store.

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `id` | string | 是 | — | — |
| `memory_store_id` | string | 是 | — | — |
| `path` | string | 是 | — | — |
| `content_sha256` | string | 是 | — | — |
| `size_bytes` | integer | 是 | — | — |
| `created_at` | string(date-time) | 是 | — | — |
| `updated_at` | string(date-time) | 是 | — | — |

### 26.39 `MemoryStore`

Workspace-scoped persistent memory container.<br><br>A session attaches up to ``LINCHPIN_MEMORY_MAX_STORES_PER_SESSION`` memory stores via the ``resources[]`` framework. Mount path is derived from ``name`` (``/mnt/memory/<name>/``), not caller-supplied.

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `id` | string | 是 | — | — |
| `name` | string | 是 | — | — |
| `description` | string \| null | 否 | — | — |
| `created_at` | string(date-time) | 是 | — | — |
| `updated_at` | string(date-time) | 是 | — | — |
| `archived_at` | string(date-time) \| null | 否 | — | — |

### 26.40 `MemoryStoreResource`

Attach a memory store. Reserved for v0.3 — rejected by v0.2 handlers.

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `type` | string | 是 | 固定值 `"memory_store"` | — |
| `memory_store_id` | string | 是 | — | — |
| `access` | string | 否 | 枚举 `"read_only"` / `"read_write"`; 默认 `"read_only"` | — |
| `instructions` | string \| null | 否 | — | — |

### 26.41 `MemoryVersion`

Immutable snapshot of a memory at a point in time.<br><br>Versions retain for ``LINCHPIN_MEMORY_VERSION_RETENTION_DAYS`` (30 by default), then the GC pass tombstones them — the row remains for audit but ``content_sha256`` becomes zeros and the bytes are unlinked from the FileStore.

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `id` | string | 是 | — | — |
| `memory_id` | string | 是 | — | — |
| `memory_store_id` | string | 是 | — | — |
| `seq` | integer | 是 | — | — |
| `content_sha256` | string | 是 | — | — |
| `size_bytes` | integer | 是 | — | — |
| `author` | string | 是 | — | — |
| `action` | string | 是 | 枚举 `"create"` / `"update"` / `"delete"` / `"redact"` | — |
| `redacted_at` | string(date-time) \| null | 否 | — | — |
| `created_at` | string(date-time) | 是 | — | — |
| `expires_at` | string(date-time) | 是 | — | — |

### 26.42 `ModelConfig`

LLM provider configuration.

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `provider` | string | 是 | 枚举 `"openrouter"` / `"openai"` / `"ollama"` | — |
| `id` | string | 是 | — | — |
| `base_url` | string \| null | 否 | — | — |
| `context_window` | integer \| null | 否 | — | — |

### 26.43 `OutcomeDefinition`

The ``session.outcome`` declaration (v0.6.0).<br><br>Validates: definition non-empty + bounded, rubric non-empty + bounded, criterion weights sum to 1.0, grader present.

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `definition` | string | 是 | 长度 1..4096 | — |
| `rubric` | `RubricCriterion`[] | 是 | — | — |
| `grader` | `OutcomeAgentGrader` | 是 | — | — |
| `success_threshold` | number | 否 | ≥ 0.0; ≤ 1.0; 默认 `0.8` | — |
| `auto_terminate` | boolean | 否 | 默认 `true` | — |

### 26.44 `OutcomeEvaluation`

One row from the ``outcome_evaluations`` history table.

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `id` | string | 是 | — | — |
| `session_id` | string | 是 | — | — |
| `grader_id` | string \| null | 否 | — | — |
| `score` | number | 是 | — | — |
| `rationale` | string \| null | 否 | — | — |
| `criteria` | object[] | 否 | — | — |
| `created_at` | string(date-time) | 是 | — | — |

### 26.45 `OutcomeEvaluationsResponse`

Payload for ``GET /v1/sessions/{id}/outcome_evaluations``.

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `data` | `OutcomeEvaluation`[] | 是 | — | — |

### 26.46 `PaginatedEventsResponse`

Cursor-paginated event list.

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `events` | `EventResponse`[] | 是 | — | — |
| `next_cursor` | string \| null | 否 | — | — |

### 26.47 `PaginatedFilesResponse`

Cursor-paginated file list.

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `data` | `FileResponse`[] | 是 | — | — |
| `next_cursor` | string \| null | 否 | — | — |
| `has_more` | boolean | 否 | 默认 `false` | — |

### 26.48 `PaginatedListResponse`

Generic paginated list for agents, environments, sessions.

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `data` | any[] | 是 | — | — |
| `next_cursor` | string \| null | 否 | — | — |
| `has_more` | boolean | 否 | 默认 `false` | — |

### 26.49 `PostEventsRequest`

POST /v1/sessions/{id}/events — batch envelope.

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `events` | `EventPayload`[] | 是 | — | — |

### 26.50 `ProjectResponse`

A project (tenant) under the caller's organization.<br><br>``max_concurrent_sessions`` / ``monthly_token_budget`` are the Atlas M5 (FR-11.2/11.3) quota settings; ``None`` = unlimited.

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `id` | string | 是 | — | — |
| `org_id` | string | 是 | — | — |
| `name` | string | 是 | — | — |
| `slug` | string | 是 | — | — |
| `max_concurrent_sessions` | integer \| null | 否 | — | — |
| `monthly_token_budget` | integer \| null | 否 | — | — |
| `created_at` | string(date-time) | 是 | — | — |

### 26.51 `RedactRequest`

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `reason` | string | 是 | 长度 1..1024 | — |

### 26.52 `SessionResource`

Persisted session_resources row exposed via the API.<br><br>PR2 inserts these with ``state='mounted'`` at session-create time but does not actually mount anything — sandbox machinery lands in PR3.

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `id` | string | 是 | — | — |
| `session_id` | string | 是 | — | — |
| `type` | string | 是 | 枚举 `"file"` / `"memory_store"` / `"github_repository"` / `"git_repository"` / `"vault"` | — |
| `mount_path` | string | 是 | — | — |
| `config` | object | 否 | — | — |
| `state` | string | 否 | 枚举 `"mounted"` / `"unmounting"` / `"unmounted"` / `"failed"`; 默认 `"mounted"` | — |
| `error` | string \| null | 否 | — | — |
| `created_at` | string(date-time) | 是 | — | — |
| `unmounted_at` | string(date-time) \| null | 否 | — | — |

### 26.53 `SessionResponse`

Full session resource returned by the API.

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `id` | string | 是 | — | — |
| `agent_id` | string | 是 | — | — |
| `agent_version` | integer | 是 | — | — |
| `environment_id` | string | 是 | — | — |
| `status` | string | 是 | 枚举 `"provisioning"` / `"rescheduling"` / `"running"` / `"idle"` / `"error"` / `"terminated"` / `"failed"` | — |
| `stop_reason` | string \| null | 否 | — | — |
| `retry_count` | integer | 否 | 默认 `0` | — |
| `container_id` | string \| null | 否 | — | — |
| `title` | string \| null | 否 | — | — |
| `metadata` | object | 否 | — | — |
| `created_at` | string(date-time) | 是 | — | — |
| `updated_at` | string(date-time) | 是 | — | — |
| `archived_at` | string(date-time) \| null | 否 | — | — |
| `last_event_cursor` | string \| null | 否 | — | — |
| `ttl_seconds` | integer \| null | 否 | — | — |
| `stats` | `SessionStats` | 否 | — | — |
| `usage` | `SessionUsage` | 否 | — | — |
| `vault_ids` | string[] | 否 | — | 绑定的凭据保险库 ID（运行时用于解析模型 API Key 与 MCP 凭据，见第 23 章） |
| `resources` | `SessionResource`[] | 否 | — | — |
| `outcome` | `OutcomeDefinition` \| null | 否 | — | — |
| `parent_session_id` | string \| null | 否 | — | — |
| `deployment_id` | string \| null | 否 | — | — |
| `events_encrypted` | boolean | 否 | 默认 `false` | — |

### 26.54 `SessionStats`

Aggregate counters for a session.

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `total_events` | integer | 否 | 默认 `0` | — |
| `tool_calls` | integer | 否 | 默认 `0` | — |
| `model_turns` | integer | 否 | 默认 `0` | — |

### 26.55 `SessionUsage`

Cumulative token usage for a session (v0.2.0 item #10).<br><br>Cache fields default to 0 so providers that don't report prompt-caching metrics (Ollama, OpenAI Chat Completions today) yield sensible numbers rather than nulls. Counters are monotonic for the session lifetime; update_usage() in orchestrator.py increments all four atomically.

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `input_tokens` | integer | 否 | 默认 `0` | — |
| `output_tokens` | integer | 否 | 默认 `0` | — |
| `cache_creation_input_tokens` | integer | 否 | 默认 `0` | — |
| `cache_read_input_tokens` | integer | 否 | 默认 `0` | — |

### 26.56 `Skill`

A skill bundle exposed via the API.<br><br>The bundle bytes themselves (SKILL.md + scripts/resources) live on disk under ``LINCHPIN_SKILLS_ROOT`` — the row records only the SKILL.md frontmatter (``name``, ``description``, optional ``version``) plus the bundle's content-addressable storage info.

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `id` | string | 是 | — | — |
| `name` | string | 是 | — | — |
| `description` | string | 是 | — | — |
| `version` | string \| null | 否 | — | — |
| `bundle_sha256` | string | 是 | — | — |
| `bundle_size` | integer | 是 | — | — |
| `created_at` | string(date-time) | 是 | — | — |
| `updated_at` | string(date-time) | 是 | — | — |
| `archived_at` | string(date-time) \| null | 否 | — | — |

### 26.57 `SkillListResponse`

Paginated list payload for ``GET /v1/skills``.

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `data` | `Skill`[] | 是 | — | — |
| `has_more` | boolean | 是 | — | — |
| `next_cursor` | string \| null | 否 | — | — |

### 26.58 `SkillPreflightResponse`

Metadata extracted from a bundle *without* persisting it.<br><br>Used by the console before an upload proceeds: ``replaces`` is the live skill that a same-name upload would overwrite (upsert), or ``None`` when the upload would be a fresh insert.

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `name` | string | 是 | — | — |
| `description` | string | 是 | — | — |
| `version` | string \| null | 否 | — | — |
| `replaces` | `Skill` \| null | 否 | — | — |

### 26.59 `StdioMCPServerConfig`

MCP server reached via a local subprocess over stdio.<br><br>The legacy v0.4 shape — ``command`` / ``args`` / ``env`` — continues to validate as this variant. The ``type`` discriminator is optional on the wire: a config without ``type`` is interpreted as stdio for backwards compat with rows persisted before v0.5.0.

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `type` | string | 否 | 固定值 `"stdio"`; 默认 `"stdio"` | — |
| `name` | string | 是 | — | — |
| `command` | string | 是 | — | — |
| `args` | string[] | 否 | — | — |
| `env` | object | 否 | — | — |

### 26.60 `SubagentConfig`

FR-6.1 — one declared worker an agent may spawn via ``spawn_subagent``.<br><br>Declaration is the enablement gate: the orchestrator only exposes the ``spawn_subagent`` tool when ``multi_agents`` is non-empty, the policy layer fixes it at ``always_ask``, and the handler refuses any name not declared here (fail closed).<br><br>``environment_id`` unset inherits the parent session's environment at spawn time; ``agent_id`` must reference a live agent in the same project (enforced at the routes layer, mirroring skills validation).

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `name` | string | 是 | — | — |
| `agent_id` | string | 是 | — | — |
| `environment_id` | string \| null | 否 | — | — |
| `description` | string | 否 | 默认 `""` | — |

### 26.61 `ThreadListResponse`

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `data` | `SessionResponse`[] | 是 | — | — |

### 26.62 `UpdateAgentRequest`

PATCH /v1/agents/{id} request body — all fields optional.

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `name` | string \| null | 否 | — | — |
| `model` | `ModelConfig` \| null | 否 | — | — |
| `system` | string \| null | 否 | — | — |
| `tools` | `BuiltinToolConfig` \| `CustomToolConfig`[] \| null | 否 | — | — |
| `mcp_servers` | `StdioMCPServerConfig` \| `UrlMCPServerConfig`[] \| null | 否 | — | — |
| `skills` | string[] \| null | 否 | — | — |
| `multi_agents` | `SubagentConfig`[] \| null | 否 | — | — |
| `description` | string \| null | 否 | — | — |
| `metadata` | object \| null | 否 | — | — |

### 26.63 `UpdateCredentialRequest`

PATCH body — only secret fields, all optional.

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `token` | string \| null | 否 | — | — |
| `api_key` | string \| null | 否 | — | — |
| `access_token` | string \| null | 否 | — | — |
| `refresh_token` | string \| null | 否 | — | — |
| `client_secret` | string \| null | 否 | — | — |
| `kek` | string \| null | 否 | — | — |

### 26.64 `UpdateDeploymentRequest`

PATCH /v1/deployments/{id} request body.<br><br>Changing ``cron`` or ``timezone`` recomputes ``next_run_at`` from now (the new schedule starts from the edit, not the old plan).

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `cron` | string \| null | 否 | — | — |
| `timezone` | string \| null | 否 | — | — |
| `initial_events` | `InitialEvent`[] \| null | 否 | — | — |

### 26.65 `UpdateMemoryStoreRequest`

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `description` | string \| null | 否 | 长度 0..1024 | — |

### 26.66 `UpdateProjectRequest`

Body for ``PATCH /v1/projects/{id}`` (bootstrap key only).<br><br>Omitted fields keep their current value; explicit ``null`` clears a limit (unlimited). ``ge=1`` guards against zero/negative no-op limits.

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `max_concurrent_sessions` | integer \| null | 否 | ≥ 1.0 | — |
| `monthly_token_budget` | integer \| null | 否 | ≥ 1.0 | — |

### 26.67 `UpdateWebhookEndpointRequest`

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `url` | string \| null | 否 | — | — |
| `enabled_events` | string[] \| null | 否 | — | — |
| `description` | string \| null | 否 | — | — |
| `enabled` | boolean \| null | 否 | — | — |
| `rotate_secret` | boolean | 否 | 默认 `false` | — |

### 26.68 `UrlMCPServerConfig`

MCP server reached over HTTP-streamable transport (v0.5.0).<br><br>Auth flows through the session's ``vault_ids[]`` — each vault id listed in ``vault_ids`` resolves to a credential the connector injects as a Bearer token on the upstream request. OAuth tokens refresh automatically using the existing vault credential machinery.

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `type` | string | 是 | 固定值 `"url"` | — |
| `name` | string | 是 | — | — |
| `url` | string | 是 | — | — |
| `vault_ids` | string[] | 否 | — | 绑定的凭据保险库 ID（运行时用于解析模型 API Key 与 MCP 凭据，见第 23 章） |

### 26.69 `UsageBucket`

Aggregated ``sessions.usage`` counters for one grouping bucket.<br><br>A bucket is either a UTC day (``group_by=day``, default) or a project id (``group_by=project``). ``sessions`` counts the sessions folded into the bucket; the four token counters mirror ``SessionUsage``.

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `bucket` | string | 是 | — | — |
| `sessions` | integer | 否 | 默认 `0` | — |
| `input_tokens` | integer | 否 | 默认 `0` | — |
| `output_tokens` | integer | 否 | 默认 `0` | — |
| `cache_creation_input_tokens` | integer | 否 | 默认 `0` | — |
| `cache_read_input_tokens` | integer | 否 | 默认 `0` | — |

### 26.70 `UsageResponse`

``GET /v1/usage`` response — overall totals plus per-bucket rows.

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `group_by` | string | 是 | 枚举 `"day"` / `"project"` | — |
| `from` | string(date-time) \| null | 否 | — | — |
| `to` | string(date-time) \| null | 否 | — | — |
| `total` | `UsageBucket` | 是 | — | — |
| `buckets` | `UsageBucket`[] | 否 | — | — |

### 26.71 `VaultResource`

Attach a credential vault to the session.<br><br>Folded-in from the legacy ``vault_ids`` field — sending vault_ids on the request continues to parse for one more minor but emits a ``Linchpin-Deprecation: vault_ids`` header + a ``session.deprecation_used`` event. Internal code normalizes both inputs into ``resources[]`` of ``type='vault'`` so the rest of the pipeline sees a single homogeneous list.<br><br>No ``mount_path`` — vaults inject credentials into the agent runtime through ``credentials.py``, not via a sandbox bind mount.

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `type` | string | 是 | 固定值 `"vault"` | — |
| `vault_id` | string | 是 | — | — |

### 26.72 `VaultResponse`

Full vault resource returned by the API.

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `id` | string | 是 | — | — |
| `display_name` | string | 是 | — | — |
| `metadata` | object | 否 | — | — |
| `created_at` | string(date-time) | 是 | — | — |
| `updated_at` | string(date-time) | 是 | — | — |
| `archived_at` | string(date-time) \| null | 否 | — | — |

### 26.73 `WebhookEndpoint`

Caller-registered HTTP destination for outbound events.<br><br>``secret`` is returned in the POST /v1/webhook_endpoints response only — subsequent reads omit it. Stripe's "you must save this now" model; we don't store any way to retrieve it later. If lost, the caller rotates by PATCH'ing with ``rotate_secret: true``.

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `id` | string | 是 | — | — |
| `url` | string | 是 | — | — |
| `enabled_events` | string[] | 否 | — | — |
| `description` | string \| null | 否 | — | — |
| `enabled` | boolean | 否 | 默认 `true` | — |
| `created_at` | string(date-time) | 是 | — | — |
| `updated_at` | string(date-time) | 是 | — | — |
| `archived_at` | string(date-time) \| null | 否 | — | — |
| `secret` | string \| null | 否 | — | — |

### 26.74 `WriteMemoryRequest`

POST / PATCH ``/v1/memory_stores/{id}/memories`` body.<br><br>``content`` is base64-encoded bytes on the wire (Pydantic ``bytes`` field type decodes it). 100 KB hard cap is enforced at validation time, before the bytes ever touch storage.

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `path` | string | 是 | — | — |
| `content` | string | 是 | — | — |
| `precondition` | `ContentShaPrecondition` \| null | 否 | — | — |

### 26.75 `_PostOutcomeEvaluation`

Body shape for ``POST /v1/sessions/{id}/outcome_evaluations``.<br><br>Either the grader agent itself or an external caller can submit a result. The route is the single canonical write path for outcome evaluations — auto-termination + event emission both fan out from here so a deterministic grader (future) and an agent grader (v0.6) converge on identical session-side behavior.

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `score` | number | 是 | ≥ 0.0; ≤ 1.0 | — |
| `rationale` | string \| null | 否 | — | — |
| `criteria` | object[] | 否 | — | — |
| `grader_id` | string \| null | 否 | — | — |

### 26.76 `_SpawnThreadRequest`

Body shape for ``POST /v1/sessions/{parent_id}/threads``.<br><br>Most fields shadow ``CreateSessionRequest`` so a coordinator can spawn a worker with any agent + environment + resources combo — the spawn is just a normal session-create with the parent link overlaid.

| 字段 | 类型 | 必填 | 约束 / 默认 | 说明 |
|---|---|---|---|---|
| `agent_id` | string | 是 | — | — |
| `environment_id` | string | 是 | — | — |
| `title` | string \| null | 否 | — | — |
| `metadata` | object | 否 | — | — |
| `ttl_seconds` | integer \| null | 否 | — | — |
| `vault_ids` | string[] | 否 | — | 绑定的凭据保险库 ID（运行时用于解析模型 API Key 与 MCP 凭据，见第 23 章） |
| `resources` | any[] | 否 | — | — |
| `input_message` | string \| object \| any[] \| null | 否 | — | — |
| `outcome` | `OutcomeDefinition` \| null | 否 | — | — |

