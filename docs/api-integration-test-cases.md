# Linchpin API 接入测试用例

| 项目 | 内容 |
|---|---|
| 对应文档 | [docs/api-integration-guide.md](api-integration-guide.md) |
| 被测版本 | Linchpin API 0.7.0（wire 版本 `v1` / `2026-05-13`） |
| 用例总数 | 56（P0×23 · P1×26 · P2×7） |
| 用例来源 | 全部预期结果均以 linchpin-api 源码为依据逐条核实（auth.py / admin.py / sessions.py / quotas.py / main.py / api_version.py / audit.py），非猜测值 |

---

## 1. 使用说明

### 1.1 适用范围

覆盖接入主链路：健康检查 → 认证 → 项目 → API key → 环境/Agent → 会话 → 事件（状态门控）→ SSE 流 → 通用约定（分页/幂等/版本协商）→ 限流与配额 → 越权防护 → 审计。
不含：部署（deployments）、vault、webhooks、executor 节点等管理面端点（另行专项测试）。

### 1.2 执行环境要求

| 项 | 要求 |
|---|---|
| 服务 | linchpin-api 已启动（默认 `http://localhost:8000`），PostgreSQL 可达 |
| bootstrap key | 服务环境变量 `LINCHPIN_API_KEY` 的值，经 `export BOOTSTRAP_KEY=...` 注入；**任何测试记录/日志/截图中只允许出现占位符 `lpk_…`，禁止出现完整明文** |
| 沙箱/模型后端 | 仅 G-3、G-4、J-2 三个用例需要 Docker 沙箱与真实模型后端（provider `openrouter`/`ollama`）；其余用例在 `LINCHPIN_SKIP_DOCKER=true` 的无 Docker 环境即可执行 |
| 工具 | `curl`、`jq`、（SSE 用例）`curl -N` |
| 时钟 | 限流窗口按分钟计，J-1 执行前确认系统时钟正常 |

### 1.3 变量约定

```bash
export BASE_URL="http://localhost:8000"
export BOOTSTRAP_KEY="${LINCHPIN_API_KEY}"   # bootstrap key（全权、不限流）
export DB_KEY="lpk_..."                      # D-2 签发的默认全权 key（scopes=["*"]）
export RO_KEY="lpk_..."                      # D-1 签发的受限 key（scopes=["sessions:read"]）
export RPM_KEY="lpk_..."                     # J-1 签发的限流 key（rate_limit_rpm=3）
export K2_KEY="lpk_..."                      # K 组：项目 B 的 key（bootstrap 跨项目签发）
export PROJECT_ID="..."  export PROJECT2_ID="..."
export ENV_ID="..."      export AGENT_ID="..."
export SESSION_ID="..."  export KEY_ID="..."
```

### 1.4 优先级定义

| 级别 | 含义 |
|---|---|
| P0 | 接入主链路，任一失败即阻断上线 |
| P1 | 重要边界与错误语义，上线前应通过 |
| P2 | 补充覆盖，允许降级放行 |

### 1.5 建议执行顺序

`A → C → D → B → E → F → G → H → I → J → K → L`。
B 组的受限/全权 key 由 D 组签发；E→F→G→H 为链式依赖（前一组产出的 ID 是下一组的入参）；各分组开头已标明所需前置。

### 1.6 预期结果书写约定

- 所有错误响应均为统一信封 `{"error", "message", "detail"}`；信封规则（源码 `main.py _error_envelope`）：detail 中携带 `error`/`message` 之外键时，这些键归入 `detail` 字段；**无附加键时 `detail` 为字面量 `null`**（不是空对象）。
- 所有 2xx 响应与统一错误响应都携带 `Linchpin-API-Version` 回显头（例外见 I-3）。
- `…` 表示值不定的字段（UUID、时间戳等），断言只针对写出的部分。

### 1.7 安全注意事项

1. 明文 key 仅在 D-1/D-2 签发响应中出现一次，采集后立即写入受控变量文件（权限 600），不得回显到测试报告。
2. 测试结束后执行第 4 节清理步骤，吊销全部测试 key。
3. 禁止把 `BOOTSTRAP_KEY` 写进任何脚本仓库、CI 明文变量或 issue。

---

## 2. 用例总览

| 组 | 主题 | 用例数 | P0 |
|---|---|---|---|
| A | 健康检查 | 2 | 1 |
| B | 认证 | 6 | 4 |
| C | 项目管理（bootstrap-only） | 4 | 2 |
| D | API key 管理 | 6 | 4 |
| E | 环境与 Agent | 4 | 2 |
| F | 会话创建（幂等/校验） | 5 | 3 |
| G | 事件与 A4 状态门控 | 7 | 3 |
| H | SSE 流 | 7 | 2 |
| I | 通用约定（分页/类型过滤/版本协商） | 6 | 1 |
| J | 限流与配额 | 3 | 0 |
| K | 越权访问（租户隔离） | 3 | 1 |
| L | 审计日志 | 3 | 0 |

---

## 3. 分组用例

### A. 健康检查

#### A-1 健康检查可达（P0）· 文档 §1

**前置条件**：服务已启动。

**步骤**：

```bash
curl -sS -w "\nHTTP:%{http_code}\n" "${BASE_URL}/health"
```

**预期结果**：

- HTTP 200
- 响应体：`{"status":"ok"}`

#### A-2 健康检查不要求认证（P2）· 文档 §3.1

**前置条件**：同 A-1。

**步骤**：不带任何 Authorization 头请求 `/health`。

**预期结果**：HTTP 200（`/health` 挂载在应用根，不走 v1 认证依赖；对比 B-1 同样无头访问 `/v1/*` 得 401）。

---

### B. 认证

> **本组前置**：已按 D-1/D-2 签发 `RO_KEY`（scopes `["sessions:read"]`）与 `DB_KEY`（默认全权）。源码依据：`app/auth.py`。

#### B-1 缺失 Authorization 头 → 401（P0）· 文档 §3.2

**步骤**：

```bash
curl -sS -w "\nHTTP:%{http_code}\n" "${BASE_URL}/v1/agents"
```

**预期结果**：

- HTTP 401
- 响应体：

```json
{"error": "unauthorized", "message": "Missing Authorization header", "detail": null}
```

#### B-2 Authorization 格式错误 → 401（P1）· 文档 §3.2

**步骤**：

```bash
curl -sS -w "\nHTTP:%{http_code}\n" -H "Authorization: ${DB_KEY}" "${BASE_URL}/v1/agents"
```

**预期结果**：

- HTTP 401
- 响应体：

```json
{"error": "unauthorized", "message": "Invalid Authorization header format, expected 'Bearer <token>'", "detail": null}
```

#### B-3 无效 key → 401（P0）· 文档 §3.2

**步骤**：

```bash
curl -sS -w "\nHTTP:%{http_code}\n" -H "Authorization: Bearer lpk_invalid_invalid_invalid" "${BASE_URL}/v1/agents"
```

**预期结果**：

- HTTP 401
- 响应体：

```json
{"error": "unauthorized", "message": "Invalid API key", "detail": null}
```

#### B-4 query 参数 `?token=` 优先于 Authorization 头（P1）· 文档 §3.1

**步骤**：同时给出坏的 header 与好的 query token。

```bash
curl -sS -w "\nHTTP:%{http_code}\n" \
  -H "Authorization: Bearer lpk_bad_bad" \
  "${BASE_URL}/v1/api-keys?token=${DB_KEY}"
```

**预期结果**：

- HTTP 200（query token 生效，坏 header 被忽略；源码 `_extract_token` 先取 query 再回退 header）
- 响应体为 key 元数据数组。

#### B-5 受限 key：GET 放行、写操作 403（P0）· 文档 §3.3

**前置条件**：存在至少一个 Agent（E-3 已执行），持有 `RO_KEY`（scopes `["sessions:read"]`）。

**步骤**：

```bash
# ① GET：任何有效 key 均可读
curl -sS -o /dev/null -w "%{http_code}\n" -H "Authorization: Bearer ${RO_KEY}" \
  "${BASE_URL}/v1/agents"

# ② POST：非 GET 需要对应资源段的 :write scope
curl -sS -w "\nHTTP:%{http_code}\n" -X POST -H "Authorization: Bearer ${RO_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"name":"x","model":{"provider":"ollama","id":"m"},"system":"s"}' \
  "${BASE_URL}/v1/agents"
```

**预期结果**：

- ① HTTP 200
- ② HTTP 403，响应体：

```json
{"error": "forbidden", "message": "missing scope agents:write", "detail": null}
```

（required scope = `/v1/` 后第一资源段 + `:write`，此处资源段为 `agents`。）

#### B-6 全权 key（默认 scopes `["*"]`）写操作放行（P0）· 文档 §4.2

**前置条件**：持有 `DB_KEY`（D-2 签发时省略 scopes）。

**步骤**：同 B-5 ②，改用 `DB_KEY`。

**预期结果**：HTTP 201（`*` scope 满足任意 `:write` 要求）。

---

### C. 项目管理（bootstrap-only）

> **本组前置**：`BOOTSTRAP_KEY` 可用。源码依据：`app/routes/admin.py`（403 检查、UniqueViolationError 处理）。

#### C-1 非 bootstrap key 创建项目 → 403（P0）· 文档 §4.1 / §5 Step 2

**前置条件**：持有 `DB_KEY`（D-2 已执行）。

**步骤**：

```bash
curl -sS -w "\nHTTP:%{http_code}\n" -X POST -H "Authorization: Bearer ${DB_KEY}" \
  -H "Content-Type: application/json" -d '{"name":"proj-a"}' \
  "${BASE_URL}/v1/projects"
```

**预期结果**：

- HTTP 403
- 响应体：

```json
{"error": "forbidden", "message": "Project creation requires the bootstrap API key", "detail": null}
```

#### C-2 bootstrap key 创建项目 → 201（P0）· 文档 §5 Step 2

**步骤**：

```bash
curl -sS -w "\nHTTP:%{http_code}\n" -X POST -H "Authorization: Bearer ${BOOTSTRAP_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"name":"接入测试项目A","slug":"proj-test-a","max_concurrent_sessions":null,"monthly_token_budget":null}' \
  "${BASE_URL}/v1/projects"
```

**预期结果**：

- HTTP 201
- 响应体字段齐全：

```json
{
  "id": "…", "org_id": "…", "name": "接入测试项目A", "slug": "proj-test-a",
  "max_concurrent_sessions": null, "monthly_token_budget": null, "created_at": "…"
}
```

- 记录 `PROJECT_ID`（后续全部用例的租户边界基准）。

#### C-3 重复 slug → 409（P1）· 文档 §5 Step 2

**步骤**：同 C-2，再发一次相同 `slug`（`name` 可不同）。

**预期结果**：

- HTTP 409
- 响应体：

```json
{"error": "conflict", "message": "Slug 'proj-test-a' already exists in this organization", "detail": null}
```

#### C-4 非 bootstrap key 修改配额 → 403（P1）· 文档 §4.1

**前置条件**：持有 `DB_KEY`、`PROJECT_ID`。

**步骤**：

```bash
curl -sS -w "\nHTTP:%{http_code}\n" -X PATCH -H "Authorization: Bearer ${DB_KEY}" \
  -H "Content-Type: application/json" -d '{"max_concurrent_sessions": 5}' \
  "${BASE_URL}/v1/projects/${PROJECT_ID}"
```

**预期结果**：

- HTTP 403
- 响应体：

```json
{"error": "forbidden", "message": "Quota management requires the bootstrap API key", "detail": null}
```

---

### D. API key 管理

> **本组前置**：`BOOTSTRAP_KEY`、`PROJECT_ID`（C-2）。源码依据：`app/routes/admin.py` L232-344（签发/列表/吊销）。

#### D-1 签发受限 key：明文仅返回一次、prefix 为前 12 字符（P0）· 文档 §4.2 / §5 Step 2

**步骤**：

```bash
curl -sS -w "\nHTTP:%{http_code}\n" -X POST -H "Authorization: Bearer ${BOOTSTRAP_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"project_id": "'"${PROJECT_ID}"'", "name": "ro-key", "scopes": ["sessions:read"]}' \
  "${BASE_URL}/v1/api-keys"
```

**预期结果**：

- HTTP 201
- 响应体：

```json
{
  "id": "…", "project_id": "${PROJECT_ID}", "name": "ro-key",
  "prefix": "…", "scopes": ["sessions:read"], "status": "active",
  "rate_limit_rpm": null, "created_at": "…", "last_used_at": null,
  "key": "lpk_…"
}
```

- 断言：`key` 以 `lpk_` 开头；`prefix == key 前 12 个字符`；`len(key) > 12`。
- 断言：该明文**仅此一次**——后续 D-4 列表中不再出现任何明文字段。
- 采集 `RO_KEY=$key`、`KEY_ID_RO=$id`。

#### D-2 省略 scopes → 默认全权 `["*"]`（P0）· 文档 §4.2

**步骤**：

```bash
curl -sS -X POST -H "Authorization: Bearer ${BOOTSTRAP_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"project_id": "'"${PROJECT_ID}"'", "name": "full-key"}' \
  "${BASE_URL}/v1/api-keys"
```

**预期结果**：

- HTTP 201；响应中 `"scopes": ["*"]`（源码：`body.scopes is not None` 才采用请求值，省略即 `["*"]`）。
- 采集 `DB_KEY=$key`、`KEY_ID_FULL=$id`。

#### D-3 跨组织 project_id 签发 → 404（P1）· 文档 §4.2 / §8

**步骤**：将 `project_id` 替换为一个不存在（或其他租户）的 UUID。

```bash
curl -sS -w "\nHTTP:%{http_code}\n" -X POST -H "Authorization: Bearer ${BOOTSTRAP_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"project_id": "00000000-0000-0000-0000-0000000000ff", "name": "bad"}' \
  "${BASE_URL}/v1/api-keys"
```

**预期结果**：

- HTTP 404（组织归属不符按 404 处理，不泄露项目存在性）
- 响应体：

```json
{"error": "not_found", "message": "Project 00000000-0000-0000-0000-0000000000ff not found", "detail": null}
```

#### D-4 key 列表只含元数据、无明文（P0）· 文档 §4.2

**步骤**：

```bash
curl -sS -H "Authorization: Bearer ${BOOTSTRAP_KEY}" "${BASE_URL}/v1/api-keys" | jq '.[0] | keys'
```

**预期结果**：

- HTTP 200；数组元素键集合恰好为：
  `id, project_id, name, prefix, scopes, status, rate_limit_rpm, created_at, last_used_at`
- 断言：**不含** `key`（明文）字段；`prefix` 与 D-1 记录一致。

#### D-5 吊销 key → 204，再次使用 → 401（P0）· 文档 §4.2 / §8

**步骤**：

```bash
# ① 再签发一个一次性 key
ONE_KEY=$(curl -sS -X POST -H "Authorization: Bearer ${BOOTSTRAP_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"project_id": "'"${PROJECT_ID}"'", "name": "burn-key"}' \
  "${BASE_URL}/v1/api-keys" | jq -r .key)
ONE_ID=$(curl -sS -H "Authorization: Bearer ${BOOTSTRAP_KEY}" "${BASE_URL}/v1/api-keys" \
  | jq -r '.[] | select(.name=="burn-key") | .id')

# ② 吊销
curl -sS -o /dev/null -w "revoke:%{http_code}\n" \
  -X DELETE -H "Authorization: Bearer ${BOOTSTRAP_KEY}" \
  "${BASE_URL}/v1/api-keys/${ONE_ID}"

# ③ 等待认证缓存过期（≤60s）后复用
sleep 61
curl -sS -w "\nHTTP:%{http_code}\n" -H "Authorization: Bearer ${ONE_KEY}" "${BASE_URL}/v1/agents"
```

**预期结果**：

- ② HTTP 204（软删除：`status='revoked'`）
- ③ HTTP 401，响应体 `{"error":"unauthorized","message":"Invalid API key","detail":null}`
- 时效说明：吊销对新请求立即生效，但服务端 key 缓存 TTL 为 60 秒，故**最迟 60 秒后**必现 401；`sleep 61` 保证确定性。

#### D-6 吊销不存在的 key → 404（P2）· 文档 §4.2

**步骤**：

```bash
curl -sS -w "\nHTTP:%{http_code}\n" -X DELETE -H "Authorization: Bearer ${BOOTSTRAP_KEY}" \
  "${BASE_URL}/v1/api-keys/00000000-0000-0000-0000-0000000000aa"
```

**预期结果**：HTTP 404，响应体：

```json
{"error": "not_found", "message": "API key 00000000-0000-0000-0000-0000000000aa not found", "detail": null}
```

---

### E. 环境与 Agent

> **本组前置**：`DB_KEY`（B-6 验证其可写）。源码依据：OpenAPI 快照（ModelConfig.provider 枚举 `openrouter`/`ollama`；EnvironmentConfig 最小示例 `{"networking":{"type":"none"}}`）。

#### E-1 创建环境（最小 config）→ 201（P0）· 文档 §5 Step 1

**步骤**：

```bash
curl -sS -w "\nHTTP:%{http_code}\n" -X POST -H "Authorization: Bearer ${DB_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"name": "env-min", "config": {"networking": {"type": "none"}}}' \
  "${BASE_URL}/v1/environments"
```

**预期结果**：

- HTTP 201；响应含 `id`、`name: "env-min"`、`config.networking.type == "none"`。
- 记录 `ENV_ID=$id`。

#### E-2 创建环境缺 config → 422（P1）· 文档 §6.2 / §10

**步骤**：

```bash
curl -sS -w "\nHTTP:%{http_code}\n" -X POST -H "Authorization: Bearer ${DB_KEY}" \
  -H "Content-Type: application/json" -d '{"name": "env-bad"}' \
  "${BASE_URL}/v1/environments"
```

**预期结果**：

- HTTP 422
- 响应体（Pydantic 校验错误统一形态；`detail.errors` 每项含 `loc/msg/type`，且**不含** `ctx`/`url`）：

```json
{
  "error": "invalid_request",
  "message": "Request validation failed",
  "detail": {"errors": [{"type": "missing", "loc": ["body", "config"], "msg": "Field required", "input": {"name": "env-bad"}}]}
}
```

#### E-3 创建 Agent（最小三要素）→ 201（P0）· 文档 §5 Step 1

**步骤**：

```bash
curl -sS -w "\nHTTP:%{http_code}\n" -X POST -H "Authorization: Bearer ${DB_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"name": "agent-min", "model": {"provider": "ollama", "id": "llama3.1:8b"}, "system": "You are a test agent."}' \
  "${BASE_URL}/v1/agents"
```

**预期结果**：

- HTTP 201；响应含 `id`、`name: "agent-min"`、`model.provider == "ollama"`、`system` 回显。
- 记录 `AGENT_ID=$id`。

#### E-4 非法 provider 枚举 → 422（P1）· 文档 §10

**步骤**：

```bash
curl -sS -w "\nHTTP:%{http_code}\n" -X POST -H "Authorization: Bearer ${DB_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"name": "agent-bad", "model": {"provider": "anthropic", "id": "x"}, "system": "s"}' \
  "${BASE_URL}/v1/agents"
```

**预期结果**：

- HTTP 422
- 响应体：

```json
{
  "error": "invalid_request",
  "message": "Request validation failed",
  "detail": {"errors": [{"type": "enum", "loc": ["body", "model", "provider"], "msg": "…", "input": "anthropic"}]}
}
```

（`loc` 指向 `body.model.provider`；`msg` 含枚举取值提示。）

---

### F. 会话创建（幂等/校验）

> **本组前置**：`AGENT_ID`（E-3）、`ENV_ID`（E-1）、`DB_KEY`。源码依据：`app/routes/sessions.py` L316-360（创建 + 幂等）、L369-396（引用校验）、L366-367（配额前置）。

#### F-1 最小创建 → 201，初始状态 provisioning（P0）· 文档 §5 Step 3

**步骤**：

```bash
curl -sS -w "\nHTTP:%{http_code}\n" -X POST -H "Authorization: Bearer ${DB_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"agent_id": "'"${AGENT_ID}"'", "environment_id": "'"${ENV_ID}"'"}' \
  "${BASE_URL}/v1/sessions"
```

**预期结果**：

- HTTP 201；响应 `status == "provisioning"`（INSERT 固定初值），含 `id`、`agent_id`、`environment_id`、`usage`。
- 记录 `SESSION_ID=$id`。

#### F-2 缺 agent_id → 422（P0）· 文档 §6.2 / §11

**步骤**：

```bash
curl -sS -w "\nHTTP:%{http_code}\n" -X POST -H "Authorization: Bearer ${DB_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"environment_id": "'"${ENV_ID}"'"}' \
  "${BASE_URL}/v1/sessions"
```

**预期结果**：

- HTTP 422
- 响应体：

```json
{
  "error": "invalid_request",
  "message": "Request validation failed",
  "detail": {"errors": [{"type": "missing", "loc": ["body", "agent_id"], "msg": "Field required", "input": {"environment_id": "${ENV_ID}"}}]}
}
```

#### F-3 agent_id 不存在/跨项目 → 404（P1）· 文档 §8

**步骤**：

```bash
curl -sS -w "\nHTTP:%{http_code}\n" -X POST -H "Authorization: Bearer ${DB_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"agent_id": "00000000-0000-0000-0000-0000000000bb", "environment_id": "'"${ENV_ID}"'"}' \
  "${BASE_URL}/v1/sessions"
```

**预期结果**：

- HTTP 404
- 响应体：

```json
{"error": "not_found", "message": "Agent 00000000-0000-0000-0000-0000000000bb not found", "detail": null}
```

（跨项目 agent 同样 404：查询带 `project_id` 过滤；不泄露存在性。`environment_id` 不存在对应 `"Environment {id} not found"`。）

#### F-4 Idempotency-Key：同 key 同 body → 重放同一会话（P0）· 文档 §6.3

**步骤**：

```bash
BODY='{"agent_id": "'"${AGENT_ID}"'", "environment_id": "'"${ENV_ID}"'"}'
R1=$(curl -sS -X POST -H "Authorization: Bearer ${DB_KEY}" -H "Content-Type: application/json" \
  -H "Idempotency-Key: itc-f4" -d "${BODY}" "${BASE_URL}/v1/sessions")
R2=$(curl -sS -X POST -H "Authorization: Bearer ${DB_KEY}" -H "Content-Type: application/json" \
  -H "Idempotency-Key: itc-f4" -d "${BODY}" "${BASE_URL}/v1/sessions")
echo "$R1" | jq -r .id; echo "$R2" | jq -r .id
```

**预期结果**：

- 两次均 HTTP 201，且 `id` 完全相同（第二次为重放已存响应，不产生新会话）。

#### F-5 Idempotency-Key：同 key 不同 body → 409（P1）· 文档 §6.3

**步骤**：同 F-4 的 key，但把 `environment_id` 换成另一个有效值。

**预期结果**：

- HTTP 409
- 响应体：

```json
{"error": "conflict", "message": "Idempotency-Key 'itc-f4' was already used with a different request body", "detail": null}
```

---

### G. 事件与 A4 状态门控

> **本组前置**：`SESSION_ID`（F-1，provisioning 或其后续状态）、`DB_KEY`。
> 源码依据：`app/routes/sessions.py` L2368-2504（A4 矩阵、批量原子性、归档检查）。
> A4 允许集矩阵（逐状态核实）：
>
> | 会话状态 | 允许的事件类型 |
> |---|---|
> | `provisioning` | `user.interrupt` |
> | `running` | `user.message` / `user.steer` / `user.interrupt` |
> | `idle` + `stop_reason=requires_action` | `user.custom_tool_result` / `user.tool_confirmation` / `user.interrupt` |
> | `idle` | `user.message` / `user.interrupt` |
> | `error` / `terminated` / `failed` | `user.interrupt` |
> | 其他（`rescheduling` 等） | 同 `running` |
>
> 无 Docker 环境（`LINCHPIN_SKIP_DOCKER=true`）下会话会在数秒内由 provisioning 转入 `error`——G-1/G-2 应在创建后立即执行；G-7 利用该终态验证 `error` 分支。G-3/G-4 需要沙箱与真实模型后端，受限环境标记 SKIP。

#### G-1 provisioning 状态发 user.message → 409 + blocked 明细（P0）· 文档 §5 Step 4 / §12

**前置条件**：新建一个会话（复用 F-1 步骤，记 `SESSION_ID`），创建后立即执行本用例。

**步骤**：

```bash
curl -sS -w "\nHTTP:%{http_code}\n" -X POST -H "Authorization: Bearer ${DB_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"events": [{"type": "user.message", "payload": {"text": "hello"}}]}' \
  "${BASE_URL}/v1/sessions/${SESSION_ID}/events"
```

**预期结果**：

- HTTP 409
- 响应体（`blocked` 为按字典序排序的被拒类型；`status`/`stop_reason` 回显会话当时状态）：

```json
{
  "error": "conflict",
  "message": "…does not accept events: ['user.message']…",
  "detail": {"blocked": ["user.message"], "status": "provisioning", "stop_reason": null}
}
```

#### G-2 provisioning 状态发 user.interrupt → 201（P0）· 文档 §5 Step 4

**步骤**：同 G-1，事件改为 `{"type": "user.interrupt", "payload": {"reason": "cancel"}}`。

**预期结果**：

- HTTP 201
- 响应体为数组（`response_model=list[EventResponse]`），元素含：

```json
{
  "session_id": "${SESSION_ID}", "type": "user.interrupt", "cursor": "…",
  "seq": 1, "event_id": "…", "payload": {"reason": "cancel"}, "processed_at": "…"
}
```

- 记录返回的 `cursor`/`seq`（H 组续传依据）。无沙箱环境中，此 interrupt 即取消 provisioning，会话随后进入终态。

#### G-3 running 状态发 user.message → 201（P1，需沙箱+模型后端）· 文档 §5 Step 4

**前置条件**：沙箱可用且 Agent 的模型后端可达；会话已流转至 `running`（通过 GET /v1/sessions/{id} 轮询确认）。

**步骤**：同 G-1。

**预期结果**：HTTP 201，元素 `type == "user.message"`。payload 约定 `{"text": "…"}`（`content` 为兼容别名）。

#### G-4 running 状态发 user.steer → 201（P1，需沙箱+模型后端）· 文档 §5 Step 4

**前置条件**：同 G-3。

**步骤**：同 G-1，事件 `{"type": "user.steer", "payload": {"text": "转向：只输出 JSON"}}`。

**预期结果**：HTTP 201；后续 agent 输出中该指令以 `<steer injected="true">` 包裹注入（结果侧验证，非响应体断言）。

#### G-5 批量事件部分被拒 → 整批 409、零写入（P1）· 文档 §12

**前置条件**：会话处于 `provisioning`（或 `error`/`terminated`/`failed`——两者均只允许 interrupt）。

**步骤**：

```bash
# 记录当前事件数
BEFORE=$(curl -sS -H "Authorization: Bearer ${DB_KEY}" \
  "${BASE_URL}/v1/sessions/${SESSION_ID}/events?limit=1" | jq '.total // .events | length')

# 发送混合批次：合法 interrupt + 非法 user.message
curl -sS -w "\nHTTP:%{http_code}\n" -X POST -H "Authorization: Bearer ${DB_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"events": [{"type": "user.interrupt", "payload": {}}, {"type": "user.message", "payload": {"text": "x"}}]}' \
  "${BASE_URL}/v1/sessions/${SESSION_ID}/events"

AFTER=$(curl -sS -H "Authorization: Bearer ${DB_KEY}" \
  "${BASE_URL}/v1/sessions/${SESSION_ID}/events?limit=1" | jq '.total // .events | length')
echo "before=${BEFORE} after=${AFTER}"
```

**预期结果**：

- HTTP 409，响应体 `detail.blocked == ["user.message"]`
- `before == after`：**整批原子**——先检查后写入，被拒批次一个事件都不落库。

#### G-6 归档会话拒绝一切事件 → 409（P1）· 文档 §12

**前置条件**：新建会话 `ARC_ID`（F-1 步骤）。

**步骤**：

```bash
curl -sS -o /dev/null -w "archive:%{http_code}\n" -X POST \
  -H "Authorization: Bearer ${DB_KEY}" "${BASE_URL}/v1/sessions/${ARC_ID}/archive"

curl -sS -w "\nHTTP:%{http_code}\n" -X POST -H "Authorization: Bearer ${DB_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"events": [{"type": "user.interrupt", "payload": {}}]}' \
  "${BASE_URL}/v1/sessions/${ARC_ID}/events"
```

**预期结果**：

- 归档 HTTP 200（返回 SessionResponse）
- 追加事件 HTTP 409（**先于 A4 矩阵检查**，interrupt 也被拒）：

```json
{"error": "conflict", "message": "Session ${ARC_ID} is archived", "detail": null}
```

#### G-7 终态（error）分支：user.message 被拒、user.interrupt 放行（P0，无沙箱环境即可执行）· 文档 §12

**前置条件**：无 Docker 环境中 F-1 的会话已自然转入 `error`（GET /v1/sessions/{id} 确认 `status`）；有沙箱环境可人工制造：对 provisioning 会话执行 G-2 后跳过本用例。

**步骤**：对 `error` 会话先发 `user.message`（预期 409），再发 `user.interrupt`（预期 201）。

**预期结果**：

- `user.message` → HTTP 409，`detail` 形如：

```json
{"blocked": ["user.message"], "status": "error", "stop_reason": null}
```

- `user.interrupt` → HTTP 201。
- 验证矩阵 `error/terminated/failed → 仅 user.interrupt` 分支。

---

### H. SSE 流

> **本组前置**：`SESSION_ID`（F-1/G 组，已有 ≥1 个事件）、`DB_KEY`。
> 源码依据：`app/routes/sessions.py` L2578-2674（双路由、帧构建、cursor 校验）。

#### H-1 全量重放：帧结构与字段（P0）· 文档 §7

**步骤**：

```bash
curl -sS -N --max-time 5 -H "Authorization: Bearer ${DB_KEY}" \
  "${BASE_URL}/v1/sessions/${SESSION_ID}/stream" | head -20
```

**预期结果**：

- HTTP 200，`Content-Type: text/event-stream`
- 每个事件一帧，三行结构（源码 `_sse_frame`）：

```
id: 1
event: user.interrupt
data: {"session_id": "…", "type": "user.interrupt", "cursor": "…", "seq": 1, "event_id": "…", "payload": {…}, "processed_at": "…"}

```

- 断言：`id` 与 `data.seq` 一致且单调递增；`data` 恰含 `session_id/type/cursor/seq/event_id/payload/processed_at` 七键；连接保持（Phase 2 live streaming）直至超时。

#### H-2 Last-Event-ID 续传（P0）· 文档 §7

**步骤**：用 G-2 返回的 `seq` 作断点。

```bash
curl -sS -N --max-time 4 -H "Authorization: Bearer ${DB_KEY}" \
  -H "Last-Event-ID: 1" \
  "${BASE_URL}/v1/sessions/${SESSION_ID}/stream"
```

**预期结果**：

- 仅收到 `seq > 1` 的事件（数字 header 被当作 seq 直接映射回 cursor；源码 `Last-Event-ID` 优先于 `?cursor=`）。
- 断点之后无事件时连接挂起等待新事件（Phase 2），不报错。

#### H-3 非法 Last-Event-ID → 422（P1）· 文档 §7

**步骤**：

```bash
curl -sS -w "\nHTTP:%{http_code}\n" -H "Authorization: Bearer ${DB_KEY}" \
  -H "Last-Event-ID: @@bogus@@" \
  "${BASE_URL}/v1/sessions/${SESSION_ID}/stream"
```

**预期结果**：

- HTTP 422
- 响应体：

```json
{"error": "validation_error", "message": "Invalid Last-Event-ID: @@bogus@@", "detail": null}
```

#### H-4 非法 `?cursor=` → 422（P1）· 文档 §7

**步骤**：

```bash
curl -sS -w "\nHTTP:%{http_code}\n" -H "Authorization: Bearer ${DB_KEY}" \
  "${BASE_URL}/v1/sessions/${SESSION_ID}/stream?cursor=@@bogus@@"
```

**预期结果**：

- HTTP 422
- 响应体：

```json
{"error": "validation_error", "message": "Invalid cursor: @@bogus@@", "detail": null}
```

#### H-5 别名端点 `/events/stream` 行为一致（P1）· 文档 §7

**步骤**：同 H-1，路径换为 `/v1/sessions/${SESSION_ID}/events/stream`。

**预期结果**：与 H-1 完全一致（双装饰器注册同一 handler）。

#### H-6 非法 UUID / 不存在会话 → 404（P1）· 文档 §7

**步骤**：

```bash
curl -sS -w "\nHTTP:%{http_code}\n" -H "Authorization: Bearer ${DB_KEY}" \
  "${BASE_URL}/v1/sessions/not-a-uuid/stream"
```

**预期结果**：

- HTTP 404（UUID 解析失败与"不存在/跨项目"同形，均不泄露细节）
- 响应体：

```json
{"error": "not_found", "message": "Session not-a-uuid not found", "detail": null}
```

#### H-7 streaming 轮询端点（JSON，非 SSE）（P2）· 文档 §20

**步骤**：

```bash
curl -sS -w "\nHTTP:%{http_code}\n" -H "Authorization: Bearer ${DB_KEY}" \
  "${BASE_URL}/v1/sessions/${SESSION_ID}/streaming"
```

**预期结果**：

- HTTP 200，`Content-Type: application/json`（**不是** `text/event-stream`）
- 响应体：模型正在生成时为当前已累积文本，否则 `null`。无模型后端环境恒为 `null`。

---

### I. 通用约定（分页/类型过滤/版本协商）

> **本组前置**：`DB_KEY`、`SESSION_ID`。源码依据：`app/routes/sessions.py`（分页 L1443、类型过滤 L2338-2362）、`app/main.py`/`app/api_version.py`（版本协商）。

#### I-1 分页边界：limit=0 与 limit=101 → 422（P1）· 文档 §6.1

**步骤**：

```bash
curl -sS -o /dev/null -w "l0:%{http_code}\n" -H "Authorization: Bearer ${DB_KEY}" \
  "${BASE_URL}/v1/sessions?limit=0"
curl -sS -o /dev/null -w "l101:%{http_code}\n" -H "Authorization: Bearer ${DB_KEY}" \
  "${BASE_URL}/v1/sessions?limit=101"
curl -sS -o /dev/null -w "ok:%{http_code}\n" -H "Authorization: Bearer ${DB_KEY}" \
  "${BASE_URL}/v1/sessions?limit=100"
```

**预期结果**：

- `limit=0`（约束 `ge=1`）与 `limit=101`（约束 `le=100`）均 HTTP 422，响应体：

```json
{"error": "invalid_request", "message": "Request validation failed", "detail": {"errors": [{"type": "greater_than_equal", "loc": ["query", "limit"], "msg": "…", "input": 0}]}}
```

- `limit=100` HTTP 200。
- 备注：events 列表默认 `limit=50`、上限同为 100；sessions 列表默认 `limit=20`；`offset` 约束 `ge=0`。

#### I-2 events 类型过滤：未知类型 → 422 invalid_event_type（P1）· 文档 §6.1 / §12

**步骤**：

```bash
curl -sS -w "\nHTTP:%{http_code}\n" -H "Authorization: Bearer ${DB_KEY}" \
  "${BASE_URL}/v1/sessions/${SESSION_ID}/events?types[]=user.message&types[]=not.a.type"
```

**预期结果**：

- HTTP 422
- 响应体（`invalid` 为被拒类型的原样列表；`Valid types` 列表随 EVENT_TYPES 常量）：

```json
{
  "error": "invalid_event_type",
  "message": "unknown event type(s): ['not.a.type']. Valid types: [……]",
  "detail": {"invalid": ["not.a.type"]}
}
```

#### I-3 无版本头 → 回显 `Linchpin-API-Version: v1`（P0）· 文档 §6.4

**步骤**：

```bash
curl -sS -D - -o /dev/null -H "Authorization: Bearer ${DB_KEY}" "${BASE_URL}/v1/agents" | grep -i linchpin
```

**预期结果**：

- HTTP 200
- 响应头含 `Linchpin-API-Version: v1`（缺省请求按 legacy v1 解析并回显，客户端可据此判断服务端是否理解了请求）。

#### I-4 声明 v2 版本头 → 回显 `2026-05-13`（P1）· 文档 §6.4

**步骤**：

```bash
curl -sS -D - -o /dev/null -H "Authorization: Bearer ${DB_KEY}" \
  -H "Linchpin-API-Version: 2026-05-13" \
  "${BASE_URL}/v1/agents" | grep -i linchpin
```

**预期结果**：HTTP 200，响应头 `Linchpin-API-Version: 2026-05-13`。

#### I-5 未知版本 → 400 + supported_versions（P1）· 文档 §6.4

**步骤**：

```bash
curl -sS -w "\nHTTP:%{http_code}\n" -H "Authorization: Bearer ${DB_KEY}" \
  -H "Linchpin-API-Version: 1999-01-01" \
  "${BASE_URL}/v1/agents"
```

**预期结果**：

- HTTP 400
- 响应体：

```json
{
  "error": "unsupported_api_version",
  "message": "Linchpin-API-Version: '1999-01-01' is not a known wire-format version. Supported: ['2026-05-13'], or omit for legacy v1.",
  "detail": {"supported_versions": ["2026-05-13"]}
}
```

- 备注：该 400 由中间件短路返回，**不携带** `Linchpin-API-Version` 回显头（与 I-3/I-4 形成对照断言）。

#### I-6 events 类型过滤：空白条目视为未过滤（P2）· 文档 §6.1

**步骤**：

```bash
# ① 仅空条目（Starlette 解析为 [""]）——与省略参数等价
curl -sS -o /dev/null -w "blank:%{http_code}\n" -H "Authorization: Bearer ${DB_KEY}" \
  "${BASE_URL}/v1/sessions/${SESSION_ID}/events?types[]="
# ② 空条目与有效类型混用——空条目被剔除，不产生 422
curl -sS -w "\nHTTP:%{http_code}\n" -H "Authorization: Bearer ${DB_KEY}" \
  "${BASE_URL}/v1/sessions/${SESSION_ID}/events?types[]=&types[]=user.interrupt"
```

**预期结果**：① ② 均 HTTP 200；② 仅返回 `user.interrupt` 事件（空条目按文档契约忽略）。

---

### J. 限流与配额

> **本组前置**：`BOOTSTRAP_KEY`（签发限流 key / 改项目配额）。源码依据：`app/auth.py` L220-234（限流）、`app/quotas.py`（并发/月度 token 配额，fail-open：依赖异常时放行）。

#### J-1 key 级限流：rate_limit_rpm=3 → 第 4 发 429 + Retry-After（P1）· 文档 §6.5 / §4.2

**前置条件**：无 Docker 环境即可执行（限流在认证层，与沙箱无关）。

**步骤**：

```bash
# ① 签发限流 key
RPM_KEY=$(curl -sS -X POST -H "Authorization: Bearer ${BOOTSTRAP_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"project_id": "'"${PROJECT_ID}"'", "name": "rpm-key", "rate_limit_rpm": 3}' \
  "${BASE_URL}/v1/api-keys" | jq -r .key)

# ② 连续 4 发（滑动窗口内）
for i in 1 2 3 4; do
  curl -sS -D - -o /dev/null -H "Authorization: Bearer ${RPM_KEY}" \
    "${BASE_URL}/v1/api-keys" | grep -E "^HTTP|^Retry-After"
done
```

**预期结果**：

- 前 3 发 HTTP 200；第 4 发 HTTP 429
- 第 4 发响应体：

```json
{
  "error": "rate_limited",
  "message": "Rate limit exceeded: 3 requests per minute",
  "detail": {"retry_after": 60}
}
```

- 第 4 发响应头含 `Retry-After`（整秒数，`detail.retry_after` 与之同值；`retry_after` 为估算值，不必恰为 60）。
- 备注：bootstrap key（`api_key_id=None`）与未设限流 key 直接放行，不受此逻辑影响。

#### J-2 并发会话配额：max_concurrent_sessions=1 → 第二个会话 429（P1，需沙箱或会话可停留非终态）· 文档 §5 Step 2 / §11

**前置条件**：沙箱可用（会话能停留在 provisioning/running 等非终态）。**无 Docker 环境会话数秒内转入 error 终态（不计入活跃数），本用例无法稳定构造，标记 SKIP。**

**步骤**：

```bash
# ① bootstrap 把项目并发上限改为 1
curl -sS -o /dev/null -w "patch:%{http_code}\n" -X PATCH \
  -H "Authorization: Bearer ${BOOTSTRAP_KEY}" -H "Content-Type: application/json" \
  -d '{"max_concurrent_sessions": 1}' "${BASE_URL}/v1/projects/${PROJECT_ID}"

# ② 第一个会话（保持非终态）
curl -sS -o /dev/null -w "s1:%{http_code}\n" -X POST \
  -H "Authorization: Bearer ${DB_KEY}" -H "Content-Type: application/json" \
  -d '{"agent_id": "'"${AGENT_ID}"'", "environment_id": "'"${ENV_ID}"'"}' \
  "${BASE_URL}/v1/sessions"

# ③ 立即开第二个会话
curl -sS -w "\nHTTP:%{http_code}\n" -X POST \
  -H "Authorization: Bearer ${DB_KEY}" -H "Content-Type: application/json" \
  -d '{"agent_id": "'"${AGENT_ID}"'", "environment_id": "'"${ENV_ID}"'"}' \
  "${BASE_URL}/v1/sessions"
```

**预期结果**：

- ① HTTP 200
- ② HTTP 201
- ③ HTTP 429
- 响应体（计数含 provisioning 等非终态、未归档会话；`error/terminated/failed` 不计）：

```json
{
  "error": "quota_exceeded",
  "message": "Concurrent session limit reached (1/1 active)",
  "detail": {"limit_type": "concurrent_sessions", "limit": 1, "active": 1}
}
```

- 备查：月度 token 配额超限形态相同，`detail.limit_type == "monthly_tokens"`（依赖真实用量，不单列用例，出现即视为通过）。

#### J-3 配额 fail-open（P2）· 文档 §6.5

**步骤**：停止 PostgreSQL（或断开连接），再创建会话。

**预期结果**：不因配额子系统不可用而 5xx——依赖异常时放行（fail-open），请求按无配额路径继续（可能因其他原因失败，但**不得**出现 `quota_exceeded`）。测试后恢复数据库。

---

### K. 越权访问（租户隔离）

> **本组前置**：第二个项目及 key。`SESSION_ID`（F-1，属项目 A）。
> 签发：`curl -X POST ${BASE_URL}/v1/api-keys -H "Authorization: Bearer ${BOOTSTRAP_KEY}" -d '{"project_id": "'"${PROJECT2_ID}"'", "name": "proj-b-key"}'`（bootstrap 允许跨项目签发，见 D 组）。

#### K-1 跨项目读会话 → 404（P0）· 文档 §8

**步骤**：

```bash
curl -sS -w "\nHTTP:%{http_code}\n" -H "Authorization: Bearer ${K2_KEY}" \
  "${BASE_URL}/v1/sessions/${SESSION_ID}"
```

**预期结果**：

- HTTP 404（资源存在但属其他项目，按 404 处理，不泄露存在性）
- 响应体：

```json
{"error": "not_found", "message": "Session ${SESSION_ID} not found", "detail": null}
```

#### K-2 跨项目 SSE → 404（P1）· 文档 §8

**步骤**：同 K-1，路径换 `/v1/sessions/${SESSION_ID}/stream`。

**预期结果**：HTTP 404，响应体同 K-1（流式端点同样做 project 范围校验，不存在与跨项目同形）。

#### K-3 跨项目引用资源 → 404（P1）· 文档 §8

**步骤**：用 `K2_KEY` 以项目 A 的 `AGENT_ID` 创建会话。

```bash
curl -sS -w "\nHTTP:%{http_code}\n" -X POST -H "Authorization: Bearer ${K2_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"agent_id": "'"${AGENT_ID}"'", "environment_id": "'"${ENV_ID}"'"}' \
  "${BASE_URL}/v1/sessions"
```

**预期结果**：

- HTTP 404
- 响应体：

```json
{"error": "not_found", "message": "Agent ${AGENT_ID} not found", "detail": null}
```

（agent 查询带 `project_id` 过滤：引用其他项目的资源与引用不存在的资源同形。）

---

### L. 审计日志

> **本组前置**：C、D 组已执行（已产生 `project.created` / `api_key.created` / `api_key.revoked` 记录）、`BOOTSTRAP_KEY`。
> 源码依据：`app/routes/audit.py`（NDJSON 流、过滤参数、字段结构）。

#### L-1 导出为 NDJSON、newest-first、含关键动作（P1）· 文档 §5 Step 7 / §25

**步骤**：

```bash
curl -sS -H "Authorization: Bearer ${BOOTSTRAP_KEY}" "${BASE_URL}/v1/audit?limit=50" > /tmp/audit.ndjson
head -3 /tmp/audit.ndjson
```

**预期结果**：

- HTTP 200，`Content-Type: application/x-ndjson`（每行一个 JSON 对象）
- 每行字段结构：

```json
{"id": "…", "at": "…", "project_id": "…", "actor": "…", "action": "…", "resource_type": "…", "resource_id": "…", "detail": {}}
```

- 断言：行序按 `at` 降序（最新在前）；本文件内能检索到 `"action":"project.created"`、`"action":"api_key.created"`（D-1/D-2 产生）；若执行过 D-5 还有 `"action":"api_key.revoked"`。

#### L-2 按 action 过滤（P2）· 文档 §25

**步骤**：

```bash
curl -sS -H "Authorization: Bearer ${BOOTSTRAP_KEY}" \
  "${BASE_URL}/v1/audit?action=api_key.created" | jq -r '.action' | sort -u
```

**预期结果**：HTTP 200；输出仅 `api_key.created` 一种（过滤参数精确匹配）。

#### L-3 审计记录绑定项目（P2）· 文档 §25

**步骤**：分别用项目 A 的 key 与 `K2_KEY` 各做一次审计查询，比对两份输出的 `project_id` 集合。

**预期结果**：两份互不重叠（审计按 `ctx.project_id` 过滤，租户隔离与 K 组一致）。

---

## 4. 测试数据清理

```bash
# 1) 吊销全部测试 key（逐个执行，预期均 204）
for id in "${KEY_ID_RO}" "${KEY_ID_FULL}"; do
  curl -sS -o /dev/null -w "%{http_code}\n" -X DELETE \
    -H "Authorization: Bearer ${BOOTSTRAP_KEY}" "${BASE_URL}/v1/api-keys/${id}"
done

# 2) 归档并删除测试会话（可选，按需）
# 3) 清理临时文件
rm -f /tmp/audit.ndjson
```

清理后建议复核：`GET /v1/api-keys` 无 `status: "active"` 的测试残留 key。

---

## 5. 无 Docker 受限环境的用例取舍

`LINCHPIN_SKIP_DOCKER=true` 时沙箱与容器生命周期不可用（服务启动日志有明确 WARNING），会话将在数秒内由 `provisioning` 转入 `error` 终态：

| 用例 | 受限环境处置 |
|---|---|
| G-1 / G-2 / G-5 | 创建会话后**立即**执行（抢在状态流转前），仍可验证 |
| G-3 / G-4 | SKIP（需沙箱 + 真实模型后端） |
| G-7 | **受限环境专属可用**（error 分支自然达成） |
| J-2 | SKIP（活跃会话无法驻留） |
| 其余全部 | 正常执行 |

---

*本文档由接入文档（docs/api-integration-guide.md）派生；预期结果中的错误信封、状态矩阵、scope 规则、幂等/限流/配额语义均与 linchpin-api 0.7.0 源码逐条核对。*
