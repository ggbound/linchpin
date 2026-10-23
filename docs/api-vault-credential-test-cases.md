# Linchpin Vault 凭据与模型 API Key 链路专项测试用例

| 项目 | 内容 |
|---|---|
| 对应文档 | [docs/api-integration-guide.md](api-integration-guide.md) 第 23 章（凭据保险库 `vaults`） |
| 主测试文档 | [docs/api-integration-test-cases.md](api-integration-test-cases.md)（主链路 56 例；其 §1.1 声明"vault 另行专项测试"——本篇即该专项续篇） |
| 被测版本 | Linchpin API 0.7.0（wire 版本 `v1` / `2026-05-13`） |
| 用例总数 | 39（P0×15 · P1×20 · P2×4） |
| 用例来源 | 全部预期结果均以 linchpin-api 源码为依据逐条核实（routes/vaults.py / models.py / routes/sessions.py / credentials.py / orchestrator.py / auth.py / api_version.py / alembic 0002_vaults_credentials.py），非猜测值 |
| 执行报告 | [docs/api-vault-credential-test-report.md](api-vault-credential-test-report.md)（2026-10-22 执行：**27 PASS · 12 SKIP · 0 FAIL** @ 0.7.0） |

---

## 1. 使用说明

### 1.1 适用范围

覆盖 vault 凭据与模型 API Key 的完整链路：

- Vault CRUD 与级联行为（归档清密文、删除级联）；
- 四种 `credential_type`（`bearer_token` / `api_key` / `oauth` / `byok_kek`）的创建、唯一性约束、生命周期与密文不泄漏；
- 会话绑定 vault 的两种请求形态（旧 `vault_ids` / 新 `resources`）、折叠去重与弃用信号；
- BYOK 会话加密（`x-events-encrypted` + `x-kek-credential-id` 校验链）；
- 认证 scope（`vaults:write`）与租户隔离；
- 运行时模型密钥解析（vault 凭据 → 环境变量回退 → `credential_missing` 失败语义）。

不含：主链路（认证/会话/事件/SSE，见主测试文档）、deployments 等其他管理面。

### 1.2 执行环境要求

| 项 | 要求 |
|---|---|
| 服务 | linchpin-api 已启动（默认 `http://localhost:8000`），PostgreSQL 可达，加密服务已配置 Fernet 主密钥（`VAULT_ENCRYPTION_KEY`；未配置时所有凭据创建将 5xx） |
| bootstrap key | 同主文档 §1.2：`LINCHPIN_API_KEY` 经 `export BOOTSTRAP_KEY=...` 注入；**测试记录/报告中只允许占位符 `lpk_…`，禁止完整明文** |
| 全权/受限 key | 复用主文档 D 组签发的 `DB_KEY`（scopes `["*"]`）与 `RO_KEY`（scopes `["sessions:read"]`）；Z-3 另需项目 B 的 `K2_KEY` |
| 沙箱要求 | **S-4/S-5/S-6/S-7 四个校验失败用例无需沙箱**（vault 校验先于 sandbox 解析，`sessions.py` L409-460 < L919）；**S-1/S-2/S-3/Y 组/R 组的 201 成功用例需要可用 Docker 沙箱**——`LINCHPIN_SKIP_DOCKER=true` 下会话创建直接 503 `sandbox_runtime_unavailable`（`sandbox.py` `sandbox_for_runtime`，app.state.sandbox 为 None），无法执行 |
| 输出目录 | 会话创建会先写 `<LINCHPIN_FILES_ROOT>/_session_outputs/`（默认 `/var/lib/linchpin`，`watcher.py` L69-73）。macOS 开发机该路径不可写，会话创建将在沙箱解析**之前** 500 `PermissionError`——需为服务进程设置可写的 `LINCHPIN_FILES_ROOT`（如 `/tmp/linchpin-files`）才能触及 503 分级 |
| 模型后端 | 仅 R 组需要 mock 模型后端（见 R-0 的 mock 脚本）或真实 OpenAI 后端；mock 以 `model.base_url` 覆盖默认地址，无需外网 |
| 工具 | `curl`、`jq`、`python3`（`cryptography`，用于生成合法 Fernet KEK） |

### 1.3 变量约定

```bash
export BASE_URL="http://localhost:8000"
export BOOTSTRAP_KEY="${LINCHPIN_API_KEY}"   # bootstrap key（全权、不限流）
export DB_KEY="lpk_..."                      # 主文档 D-2 签发的默认全权 key（scopes=["*"]）
export RO_KEY="lpk_..."                      # 主文档 D-1 签发的受限 key（scopes=["sessions:read"]）
export K2_KEY="lpk_..."                      # 主文档 K 组：项目 B 的 key（Z-3/S-7 用）
export PROJECT2_ID="..."                     # 项目 B id（主文档 K 组产出）
export ENV_ID="..."        export AGENT_ID="..."   # 主文档 E 组产出
export VAULT_ID="..."      export VAULT2_ID="..."  # V-1 / C 组专用 vault
export CRED_ID="..."                         # C-2 创建的 openai api_key 凭据
export KEK_CRED_ID="..."                     # C-5 创建的 byok_kek 凭据
export MOCK_AGENT_ID="..."                   # R-0 创建的 base_url 指向 mock 的 agent
export FERNET_KEY="..."                      # C-5/Y 组测试用 KEK（测试后即弃，禁止入库）
```

### 1.4 优先级定义

| 级别 | 含义 |
|---|---|
| P0 | 凭据接入主链路，任一失败即阻断上线 |
| P1 | 重要边界与错误语义，上线前应通过 |
| P2 | 补充覆盖，允许降级放行 |

### 1.5 建议执行顺序

`V → C → S → Y → Z → R`。V/C 产出 `VAULT_ID`/`CRED_ID`/`KEK_CRED_ID`；S/Y 依赖这些 ID；Z 组独立，可插在任意位置；R 依赖 C-2 凭据 + R-0 mock。V-5/V-6、C-12/C-13/C-14 均使用专用 vault，不污染主链路数据。

### 1.6 预期结果书写约定

- 所有错误响应均为统一信封 `{"error", "message", "detail"}`；信封规则（源码 `main.py _error_envelope`）：detail 中携带 `error`/`message` 之外键时，这些键归入 `detail` 字段；**无附加键时 `detail` 为字面量 `null`**（不是空对象）。
- 请求体 Pydantic 校验失败（含枚举/Fernet 校验器）统一为：

```json
{"error": "invalid_request", "message": "Request validation failed",
 "detail": {"errors": [{"type": "…", "loc": ["body", "…"], "msg": "…", "input": "…"}]}}
```

（源码 `main.py` L416-431：`ctx`/`url` 键被剔除。）
- 所有 2xx 与错误响应都携带 `Linchpin-API-Version` 回显头。
- `…` 表示值不定的字段（UUID、时间戳等），断言只针对写出的部分。

### 1.7 安全注意事项

1. 凭据创建请求的明文密钥（`token`/`api_key`/`access_token`/`kek` 等）只在单次 curl 中出现，采集后立即写入受控变量（权限 600），不得回显到测试报告。
2. **凭据/vault 响应体设计上不含任何密文字段**——若任何响应出现密文即视为 P0 级安全缺陷（C-1/C-2/C-9 的反向断言）。
3. `FERNET_KEY`（BYOK 测试用 KEK）测试结束后立即销毁，不得复用到其他环境。
4. 测试结束执行第 4 节清理，删除全部测试 vault（级联删除凭据）。

---

## 2. 用例总览

| 组 | 主题 | 用例数 | P0 |
|---|---|---|---|
| V | Vault CRUD | 6 | 2 |
| C | 凭据管理（四种 credential_type） | 14 | 6 |
| S | 会话绑定 vault（vault_ids / resources） | 7 | 3 |
| Y | BYOK 会话加密 | 4 | 0 |
| Z | 认证 scope 与租户隔离 | 3 | 2 |
| R | 运行时模型密钥解析 | 5 | 2 |

### 2.1 被测接口速查表

| 用例 | 方法与路径 | 关键参数 | 预期摘要 |
|---|---|---|---|
| V-1 | POST `/v1/vaults` | `display_name`, `metadata` | 201，6 字段 + metadata 透传 |
| V-2 | GET `/v1/vaults` | `limit`, `offset` | 200；`has_more`/`next_cursor`/`created_at DESC`；offset 越界空页 |
| V-3 | GET `/v1/vaults/{vault_id}` | `vault_id` | 200，6 键 |
| V-4 | GET `/v1/vaults/{vault_id}` | `vault_id="not-a-uuid"` | **404**（非 422） |
| V-5 | POST `/v1/vaults/{vault_id}/archive` | `vault_id`（专用 vault + 1 凭据） | 200 级联归档凭据；重复归档 409 |
| V-6 | DELETE `/v1/vaults/{vault_id}` | `vault_id`（专用 vault + ≥1 凭据） | 204 级联物理删凭据；再删 404 |
| C-1 | POST `/v1/vaults/{vault_id}/credentials` | `bearer_token`：`mcp_server_url`+`token` | 201，9 键，无密文 |
| C-2 | 同上 | `api_key`：`provider="openai"` + `api_key` | 201（R 组密钥来源） |
| C-3 | 同上 | `api_key`：`provider="anthropic"` / `"ollama"` | 422 literal_error（loc 带 discriminator 段）/ 201 |
| C-4 | 同上 | `oauth`：`client_id`+`access_token`（不带可选字段） | 201；可选字段仅在有值时入密文 |
| C-5 | 同上 | `byok_kek`：合法 Fernet `kek` | 201，响应无 kek |
| C-6 | 同上 | `byok_kek`：`kek="not-a-fernet-key"` | 422（Fernet 校验器），DB 无残留 |
| C-7 | 同上 | `api_key` openrouter 建两次（api_key 不同） | 二次 409（同 vault provider 唯一）；跨 vault 201 |
| C-8 | 同上 | `bearer_token` 重复 `mcp_server_url` | 409 |
| C-9 | GET 单个 + GET 列表 | `credential_id` / `limit=100` | 200，9 键唯一，无密文；不存在 404 |
| C-10 | PATCH `/v1/vaults/{vault_id}/credentials/{credential_id}` | `api_key` 新值 / 空体 `{}` | 200 轮换（`updated_at` 变）/ 200 原样 |
| C-11 | 同上 | `oauth` 仅 PATCH `access_token` | 200；**密文整体替换**（`refresh_token` 丢失，DB 旁证） |
| C-12 | POST `…/credentials/{credential_id}/archive` → 重复归档 → PATCH → GET → DELETE | 归档凭据 | 200 / 409 / 409 / **200** / **204** |
| C-13 | POST `/v1/vaults/{vault_id}/credentials` | 专用 vault 循环建 20+1 个 bearer_token | 前 20 个 201；第 21 个 409 |
| C-14 | 同上 | 对已归档 vault 建凭据 | 409 `"Vault … is archived"` |
| S-1 | POST `/v1/sessions` | body 旧形态 `vault_ids` | 201 + `Linchpin-Deprecation` 头（需沙箱） |
| S-2 | POST `/v1/sessions` | body 新形态 `resources` | 201，无弃用信号（需沙箱） |
| S-3 | POST `/v1/sessions` | 双形态同时出现 | 201，折叠去重（需沙箱） |
| S-4 | POST `/v1/sessions` | `vault_ids=[全零 UUID]` | 422 `"Vault … not found"`，无副作用 |
| S-5 | POST `/v1/sessions` | `vault_ids=[已归档 vault]` | 422 `"Vault … is archived"` |
| S-6 | POST `/v1/sessions` | `vault_ids=["not-a-uuid"]` | 422 `"Invalid vault id: …"` |
| S-7 | POST `/v1/sessions` | `vault_ids=[项目 B vault]` | 422（与不存在同文案） |
| Y-1 | POST `/v1/sessions` | `x-events-encrypted: true` + `x-kek-credential-id`，不绑 KEK vault | 422（KEK vault 不在 vault_ids；需沙箱） |
| Y-2 | POST `/v1/sessions` | `x-kek-credential-id: junk` | 422（id 带 `'…'` 引号；需沙箱） |
| Y-3 | POST `/v1/sessions` | `x-kek-credential-id` 指向不存在 / 已归档 | 404 `"… not found (or archived)"`（需沙箱） |
| Y-4 | POST `/v1/sessions` | 合法 KEK + 绑定 vault | 201，`events_encrypted=true`（需沙箱） |
| Z-1 | GET `/v1/vaults/{vault_id}` | `RO_KEY`（scopes `["sessions:read"]`） | 200（GET 放行） |
| Z-2 | POST `/v1/vaults` | `RO_KEY` | 403 `"missing scope vaults:write"` |
| Z-3 | GET `/v1/vaults/{vault_id}` | `DB_KEY` 访问项目 B vault | 404（不泄露存在性） |
| R-0 | POST `/v1/agents` + mock 后端 | `model.base_url=http://127.0.0.1:9201/v1` | agent 201（准备步骤，非用例） |
| R-1 | POST `/v1/sessions` + events | 绑定含 openai 凭据的 vault | mock 收到 `Bearer sk-test-openai-1`（需沙箱） |
| R-2 | 同 R-1 | 服务进程注入 `OPENAI_API_KEY` | 仍注入 vault 凭据（优先级）（需沙箱） |
| R-3 | 同 R-1 | 不绑定任何 vault | env 回退值（需沙箱） |
| R-4 | 同 R-1 | 绑定无 openai 凭据的 vault | 201 → `failed` / `credential_missing` + `session.error`（需沙箱） |
| R-5 | 同 R-1 | openai 凭据已归档 + env 注入 | 跳过归档凭据走 env 回退（需沙箱） |

---

## 3. 分组用例

### V. Vault CRUD

> **本组前置**：`DB_KEY`。源码依据：`app/routes/vaults.py`（L105-232 CRUD）、`app/models.py` L1282-1290（VaultResponse 6 字段）。

#### V-1 创建 vault → 201，6 字段 + metadata 透传（P0）· 文档 §23.2

**前置条件**：`DB_KEY`。

**步骤**：

```bash
curl -sS -w "\nHTTP:%{http_code}\n" -X POST -H "Authorization: Bearer ${DB_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"display_name": "test-vault-1", "metadata": {"env": "test", "team": "qa"}}' \
  "${BASE_URL}/v1/vaults"
```

**预期结果**：

- HTTP 201
- 响应体恰好 6 键：`id`、`display_name`、`metadata`、`created_at`、`updated_at`、`archived_at`
- `display_name == "test-vault-1"`；`metadata` 原样透传（`{"env":"test","team":"qa"}`）；`archived_at == null`
- 记录 `VAULT_ID=$id`。

#### V-2 列表分页：limit/has_more/next_cursor/排序（P1）· 文档 §23.1

**前置条件**：至少已存在 2 个 vault（V-1 + 任意一个，可用 V-5 的专用 vault）。

**步骤**：

```bash
curl -sS -H "Authorization: Bearer ${DB_KEY}" "${BASE_URL}/v1/vaults?limit=1" | jq .
curl -sS -H "Authorization: Bearer ${DB_KEY}" "${BASE_URL}/v1/vaults?limit=1&offset=1" | jq '.data[0].id'
```

**预期结果**：

- 两次均 HTTP 200
- 第一页：`data` 恰 1 条；`has_more == true`；`next_cursor == null`（本 API 游标字段恒为 null，实际用 `limit`/`offset` 翻页）
- 排序为 `created_at DESC`：第一页 `data[0].id` 是最新创建的 vault；第二页 `data[0].id` 是更早创建的那个
- 越界（`offset` 超过总数）：200 且 `data == []`、`has_more == false`

#### V-3 查询单个 vault → 200（P1）· 文档 §23.3

**步骤**：`GET /v1/vaults/${VAULT_ID}`。

**预期结果**：HTTP 200，字段集合与 V-1 相同（6 键），`id == VAULT_ID`。

#### V-4 非法 UUID 路径参数 → 404（非 422）（P1）· 文档 §23.3

**步骤**：

```bash
curl -sS -w "\nHTTP:%{http_code}\n" -H "Authorization: Bearer ${DB_KEY}" \
  "${BASE_URL}/v1/vaults/not-a-uuid"
```

**预期结果**：

- HTTP **404**（不是 422！路由内部手工解析 UUID，`vaults.py` L97）
- 响应体：

```json
{"error": "not_found", "message": "Vault not-a-uuid not found", "detail": null}
```

- 对照：会话绑定里 `vault_ids` 含非法 UUID 是 **422**（见 S-6）。

#### V-5 归档 vault：级联归档凭据 + 重复归档 409（P1）· 文档 §23.5

**前置条件**：创建专用 vault（记 `VAULT_ARCH_ID`）并在其中创建 1 个任意凭据（记 `CRED_ARCH_ID`）。

**步骤**：

```bash
# 1) 归档
curl -sS -w "\nHTTP:%{http_code}\n" -X POST -H "Authorization: Bearer ${DB_KEY}" \
  "${BASE_URL}/v1/vaults/${VAULT_ARCH_ID}/archive"
# 2) 重复归档
curl -sS -w "\nHTTP:%{http_code}\n" -X POST -H "Authorization: Bearer ${DB_KEY}" \
  "${BASE_URL}/v1/vaults/${VAULT_ARCH_ID}/archive"
```

**预期结果**：

- 第 1 步 HTTP 200，`archived_at` 非空
- 凭据被级联归档：`GET /v1/vaults/${VAULT_ARCH_ID}/credentials/${CRED_ARCH_ID}` → 200 且 `archived_at` 非空（归档事务同步 `UPDATE credentials SET archived_at=now(), encrypted_secrets=NULL`，`vaults.py` L217-227；密文已清空，但响应本就不含密文）
- 第 2 步 HTTP 409：

```json
{"error": "conflict", "message": "Vault ${VAULT_ARCH_ID} is already archived", "detail": null}
```

#### V-6 删除 vault → 204 且级联删除凭据（P0）· 文档 §23.4

**前置条件**：创建专用 vault（记 `VAULT_DEL_ID`）并创建 ≥1 个凭据。

**步骤**：

```bash
# 1) 删除
curl -sS -w "\nHTTP:%{http_code}\n" -X DELETE -H "Authorization: Bearer ${DB_KEY}" \
  "${BASE_URL}/v1/vaults/${VAULT_DEL_ID}" -o /dev/null
# 2) 复核
curl -sS -o /dev/null -w "vault:%{http_code}\n" -H "Authorization: Bearer ${DB_KEY}" \
  "${BASE_URL}/v1/vaults/${VAULT_DEL_ID}"
# 3) 再删一次
curl -sS -w "\nHTTP:%{http_code}\n" -X DELETE -H "Authorization: Bearer ${DB_KEY}" \
  "${BASE_URL}/v1/vaults/${VAULT_DEL_ID}"
```

**预期结果**：

- 第 1 步 HTTP **204**（无响应体）
- 第 2 步 vault 404；其下凭据随之物理删除（FK 级联；`GET /v1/vaults/${VAULT_DEL_ID}/credentials` 因 vault 先 404 也返回 404）
- 第 3 步 HTTP 404：`{"error":"not_found","message":"Vault ${VAULT_DEL_ID} not found","detail":null}`

---

### C. 凭据管理（四种 credential_type）

> **本组前置**：`VAULT_ID`（V-1）；另创建重复性/上限用例专用 vault `VAULT2_ID`。源码依据：`vaults.py` L240-543、`models.py` L1182-1275（判别联合 + UpdateCredentialRequest）、alembic `0002_vaults_credentials.py` L49/L55（`uq_credentials_vault_mcp_url` / `uq_credentials_vault_provider` 唯一索引）。
> CredentialResponse 恰 9 键：`id`、`vault_id`、`credential_type`、`mcp_server_url`、`provider`、`client_id`、`created_at`、`updated_at`、`archived_at`（`models.py` L1293-1304）——各创建用例通用此断言，不再重复。

#### C-1 创建 bearer_token 凭据 → 201 且响应无密文（P0）· 文档 §23.7

**步骤**：

```bash
curl -sS -w "\nHTTP:%{http_code}\n" -X POST -H "Authorization: Bearer ${DB_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"credential_type": "bearer_token", "mcp_server_url": "https://mcp.example.test/sse", "token": "test-mcp-token-1"}' \
  "${BASE_URL}/v1/vaults/${VAULT_ID}/credentials"
```

**预期结果**：

- HTTP 201
- 9 键断言（见组前置）；`credential_type == "bearer_token"`、`mcp_server_url` 回显、`provider == null`、`client_id == null`、`archived_at == null`、`vault_id == VAULT_ID`
- **反向断言**：`echo` 响应体用 `grep -c "test-mcp-token-1"` 为 0（明文 token 不出现在任何响应字段）

#### C-2 创建 api_key 凭据（provider=openai）→ 201（P0，核心新用例）· 文档 §23.7

**步骤**：

```bash
curl -sS -w "\nHTTP:%{http_code}\n" -X POST -H "Authorization: Bearer ${DB_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"credential_type": "api_key", "provider": "openai", "api_key": "sk-test-openai-1"}' \
  "${BASE_URL}/v1/vaults/${VAULT_ID}/credentials"
```

**预期结果**：

- HTTP 201
- `credential_type == "api_key"`、`provider == "openai"`、`mcp_server_url == null`
- **反向断言**：响应体不含 `sk-test-openai-1`
- 记录 `CRED_ID=$id`。

说明：该凭据是"智能体模型调用"链路的密钥来源——页面模型配置选择 openai 供应商后，运行时由 orchestrator 按 `(vault_id, provider)` 解密注入 `Authorization: Bearer …`（验证见 R-1）。provider 同样接受 `openrouter` / `ollama`（枚举边界见 C-3）。

#### C-3 provider 枚举边界（P1）· 文档 §23.7

**步骤**：

```bash
# 1) 非法枚举
curl -sS -w "\nHTTP:%{http_code}\n" -X POST -H "Authorization: Bearer ${DB_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"credential_type": "api_key", "provider": "anthropic", "api_key": "sk-x"}' \
  "${BASE_URL}/v1/vaults/${VAULT_ID}/credentials"
# 2) 合法枚举 ollama
curl -sS -o /dev/null -w "ollama:%{http_code}\n" -X POST -H "Authorization: Bearer ${DB_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"credential_type": "api_key", "provider": "ollama", "api_key": "sk-unused"}' \
  "${BASE_URL}/v1/vaults/${VAULT_ID}/credentials"
```

**预期结果**：

- 第 1 步 HTTP 422，`detail.errors[0]` 含 `type == "literal_error"`、`loc == ["body", "api_key", "provider"]`（判别联合的 loc 带 discriminator 段 `api_key`）、`msg == "Input should be 'openrouter', 'openai' or 'ollama'"`
- 第 2 步 HTTP 201（创建层不区分"是否需要密钥"，`ollama` 免密钥的语义体现在运行时——R-1 说明；本凭据创建后可归档清理）

#### C-4 oauth 凭据：可选字段仅在有值时入密文（P1）· 文档 §23.7

**步骤**：

```bash
# 1) 只带 access_token + client_id
curl -sS -w "\nHTTP:%{http_code}\n" -X POST -H "Authorization: Bearer ${DB_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"credential_type": "oauth", "mcp_server_url": "https://mcp-oauth.example.test/sse",
       "client_id": "cid-123", "access_token": "at-1"}' \
  "${BASE_URL}/v1/vaults/${VAULT_ID}/credentials"
```

**预期结果**：

- HTTP 201；`client_id == "cid-123"`（`client_id` 是顶层列，明文回显）；`credential_type == "oauth"`
- 密文侧键集合（`vaults.py` `_build_secrets_json` L246-252：`refresh_token`/`client_secret` 仅在有值时写入）接口层无观察点，DB 层复核为可选步骤（解密 `encrypted_secrets` 键集合恰为 `["access_token"]`）
- 记录 `OAUTH_CRED_ID=$id`（C-11 用）。

#### C-5 创建 byok_kek 凭据（合法 Fernet）→ 201（P0）· 文档 §23.7

**前置条件**：`python3 -c "import cryptography"` 可用。

**步骤**：

```bash
export FERNET_KEY=$(python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
curl -sS -w "\nHTTP:%{http_code}\n" -X POST -H "Authorization: Bearer ${DB_KEY}" \
  -H "Content-Type: application/json" \
  -d "{\"credential_type\": \"byok_kek\", \"kek\": \"${FERNET_KEY}\"}" \
  "${BASE_URL}/v1/vaults/${VAULT_ID}/credentials"
```

**预期结果**：

- HTTP 201；`credential_type == "byok_kek"`、`mcp_server_url == null`、`provider == null`
- **反向断言**：响应体不含 `FERNET_KEY` 值
- 记录 `KEK_CRED_ID=$id`（Y 组用）。

#### C-6 创建 byok_kek 凭据（非法 kek）→ 422（P0）· 文档 §23.7

**步骤**：

```bash
curl -sS -w "\nHTTP:%{http_code}\n" -X POST -H "Authorization: Bearer ${DB_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"credential_type": "byok_kek", "kek": "not-a-fernet-key"}' \
  "${BASE_URL}/v1/vaults/${VAULT_ID}/credentials"
```

**预期结果**：

- HTTP 422，`detail.errors[0].msg == "Value error, kek is not a valid Fernet key: Fernet key must be 32 url-safe base64-encoded bytes."`（`models.py` L1233-1242 字段校验器；判别联合的 loc 带 discriminator 段：`loc == ["body", "byok_kek", "kek"]`）
- DB 无残留行（校验发生在加密入库前）

#### C-7 同 vault 重复 provider → 409（P0）· 文档 §23.7 / 唯一索引

**步骤**：

```bash
# 1) VAULT2_ID 首次创建 openrouter
curl -sS -o /dev/null -w "first:%{http_code}\n" -X POST -H "Authorization: Bearer ${DB_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"credential_type": "api_key", "provider": "openrouter", "api_key": "sk-or-1"}' \
  "${BASE_URL}/v1/vaults/${VAULT2_ID}/credentials"
# 2) 同 vault 同 provider（api_key 不同）
curl -sS -w "\nHTTP:%{http_code}\n" -X POST -H "Authorization: Bearer ${DB_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"credential_type": "api_key", "provider": "openrouter", "api_key": "sk-or-2"}' \
  "${BASE_URL}/v1/vaults/${VAULT2_ID}/credentials"
# 3) 对照：另一 vault 同 provider 允许（C-2 已在 VAULT_ID 建过 openai，此处建 openrouter 验证跨 vault 不冲突）
curl -sS -o /dev/null -w "cross-vault:%{http_code}\n" -X POST -H "Authorization: Bearer ${DB_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"credential_type": "api_key", "provider": "openrouter", "api_key": "sk-or-3"}' \
  "${BASE_URL}/v1/vaults/${VAULT_ID}/credentials"
```

**预期结果**：

- 第 1 步 201；第 2 步 HTTP **409**（`asyncpg.UniqueViolationError` → `vaults.py` L319-328）：

```json
{"error": "conflict", "message": "A credential for 'openrouter' already exists in vault ${VAULT2_ID}", "detail": null}
```

（`target` 取 `mcp_server_url or provider`，`vaults.py` L321。）
- 第 3 步 201——唯一性作用域是**同 vault 内**，跨 vault 同 provider 允许

#### C-8 同 vault 重复 mcp_server_url → 409（P1）· 文档 §23.7

**步骤**：在 `VAULT2_ID` 先创建 `bearer_token`（url `https://dup.example.test/sse`）→ 201；再创建相同 url 的 `bearer_token`（token 不同）。

**预期结果**：

- 第二次 HTTP 409：

```json
{"error": "conflict", "message": "A credential for 'https://dup.example.test/sse' already exists in vault ${VAULT2_ID}", "detail": null}
```

#### C-9 凭据查询无密文泄漏（GET 单个 / 列表 / 已归档）（P0）· 文档 §23.6 / §23.8

**步骤**：

```bash
# 1) 单个
curl -sS -H "Authorization: Bearer ${DB_KEY}" \
  "${BASE_URL}/v1/vaults/${VAULT_ID}/credentials/${CRED_ID}"
# 2) 列表
curl -sS -H "Authorization: Bearer ${DB_KEY}" \
  "${BASE_URL}/v1/vaults/${VAULT_ID}/credentials?limit=100" | jq '[.data[] | keys] | unique'
```

**预期结果**：

- 两步均 200；每个对象键集合恰为 9 键（见组前置），jq 输出唯一一种键集合
- **反向断言**：响应不含 `api_key`/`token`/`kek`/`access_token` 任何明文（列表端点同样"secrets omitted"，`vaults.py` L344）
- 不存在的凭据 ID：404 `{"error":"not_found","message":"Credential ${CRED_ID} not found","detail":null}`（非法 UUID 同样 404，`vaults.py` L377-394）

#### C-10 PATCH 轮换 api_key + 空体原样（P1）· 文档 §23.9

**步骤**：

```bash
# 1) 轮换
curl -sS -w "\nHTTP:%{http_code}\n" -X PATCH -H "Authorization: Bearer ${DB_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"api_key": "sk-test-openai-rotated"}' \
  "${BASE_URL}/v1/vaults/${VAULT_ID}/credentials/${CRED_ID}"
# 2) 空体
curl -sS -o /dev/null -w "empty:%{http_code}\n" -X PATCH -H "Authorization: Bearer ${DB_KEY}" \
  -H "Content-Type: application/json" -d '{}' \
  "${BASE_URL}/v1/vaults/${VAULT_ID}/credentials/${CRED_ID}"
```

**预期结果**：

- 第 1 步 HTTP 200，9 键断言，`updated_at` 变化、其余字段不变
- 第 2 步 HTTP 200 且响应与当前状态一致（无密文字段传入时直接返回现状，`vaults.py` L439-441，`updated_at` 不变）
- 后续 R-1 验证轮换后的新 key 被注入（可选串联）

#### C-11 PATCH 是整体替换而非字段合并（现行为固化）（P1）· 文档 §23.9

**前置条件**：使用 C-4 创建的 `OAUTH_CRED_ID`（密文含 `access_token` + 可选 `refresh_token`）。

**步骤**：

```bash
# 1) 仅 PATCH access_token
curl -sS -o /dev/null -w "patch:%{http_code}\n" -X PATCH -H "Authorization: Bearer ${DB_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"access_token": "at-2"}' \
  "${BASE_URL}/v1/vaults/${VAULT_ID}/credentials/${OAUTH_CRED_ID}"
# 2)（可选 DB 层复核）解密后密文键集合恰为 ["access_token"]——refresh_token 丢失
```

**预期结果**：

- 第 1 步 HTTP 200
- **现行为固化**：PATCH 用非 None 字段重建密文后**整体替换** `encrypted_secrets`（`vaults.py` L432-448，`SET encrypted_secrets = $1`），未带的字段被静默清空——调用方轮换单字段会丢失其余密文。若未来实现改为合并语义，本用例应同步更新。接口层旁证：审计 `credential.updated` 的 `detail.fields` 只记录本次写入的键（`vaults.py` L456-459）

#### C-12 凭据归档生命周期（P1）· 文档 §23.11

**前置条件**：在 `VAULT2_ID` 创建 1 个专用凭据（记 `CRED2_ARCH_ID`）。

**步骤**：

```bash
# 1) 归档
curl -sS -o /dev/null -w "arch:%{http_code}\n" -X POST -H "Authorization: Bearer ${DB_KEY}" \
  "${BASE_URL}/v1/vaults/${VAULT2_ID}/credentials/${CRED2_ARCH_ID}/archive"
# 2) 重复归档
curl -sS -w "\nHTTP:%{http_code}\n" -X POST -H "Authorization: Bearer ${DB_KEY}" \
  "${BASE_URL}/v1/vaults/${VAULT2_ID}/credentials/${CRED2_ARCH_ID}/archive"
# 3) PATCH 已归档凭据
curl -sS -w "\nHTTP:%{http_code}\n" -X PATCH -H "Authorization: Bearer ${DB_KEY}" \
  -H "Content-Type: application/json" -d '{"token": "x"}' \
  "${BASE_URL}/v1/vaults/${VAULT2_ID}/credentials/${CRED2_ARCH_ID}"
# 4) GET 已归档凭据
curl -sS -o /dev/null -w "get:%{http_code}\n" -H "Authorization: Bearer ${DB_KEY}" \
  "${BASE_URL}/v1/vaults/${VAULT2_ID}/credentials/${CRED2_ARCH_ID}"
# 5) DELETE 已归档凭据
curl -sS -o /dev/null -w "del:%{http_code}\n" -X DELETE -H "Authorization: Bearer ${DB_KEY}" \
  "${BASE_URL}/v1/vaults/${VAULT2_ID}/credentials/${CRED2_ARCH_ID}"
```

**预期结果**：

- 第 1 步 200（`archived_at` 非空，密文清空）
- 第 2 步 409：`{"error":"conflict","message":"Credential ${CRED2_ARCH_ID} is already archived","detail":null}`（`vaults.py` L524-528）
- 第 3 步 409：`{"error":"conflict","message":"Credential ${CRED2_ARCH_ID} is archived","detail":null}`（`vaults.py` L425-430）
- 第 4 步 **200**——GET 不过滤已归档（`vaults.py` L385-394 查询无 `archived_at IS NULL`）
- 第 5 步 **204**——DELETE 不过滤已归档，可物理删除（`vaults.py` L480-489）

#### C-13 单 vault 20 凭据上限（P2）· 文档 §23.7（`vaults.py` L46/L278-290）

**前置条件**：专用 vault `VAULT3_ID`。

**步骤**：循环创建 20 个 `bearer_token`（`mcp_server_url` 各不相同，如 `https://cap-{i}.example.test/sse`）→ 全部 201；再创建第 21 个。

**预期结果**：

- 前 20 个全部 201；第 21 个 HTTP 409：

```json
{"error": "conflict", "message": "Vault ${VAULT3_ID} already has 20 active credentials", "detail": null}
```

- 上限按**未归档**凭据计数：归档 1 个后可再创建 1 个（可选验证）

#### C-14 已归档 vault 中创建凭据 → 409（P1）· 文档 §23.7（`vaults.py` L269-274）

**步骤**：对 V-5 已归档的 `VAULT_ARCH_ID` 创建任意凭据。

**预期结果**：

- HTTP 409：

```json
{"error": "conflict", "message": "Vault ${VAULT_ARCH_ID} is archived", "detail": null}
```

---

### S. 会话绑定 vault（vault_ids / resources）

> **本组前置**：`AGENT_ID`、`ENV_ID`（主文档 E 组）、`VAULT_ID` + `CRED_ID`（V-1/C-2 产出）、`DB_KEY`。
> **沙箱要求**：S-4/S-5/S-6/S-7 的 422 发生在 sandbox 解析（`sessions.py` L919）之前的 vault 校验（L409-460），**无沙箱可执行**；S-1/S-2/S-3 需要 201，**必须可用沙箱**（`LINCHPIN_SKIP_DOCKER=true` 下创建即 503 `sandbox_runtime_unavailable`，标记 SKIP）。
> 源码依据：`sessions.py` L409-460（vault 校验）、L1298-1325（弃用信号）、`api_version.py` L97-124。

#### S-1 旧形态 vault_ids → 201 + Linchpin-Deprecation 头（P0，需沙箱）· 文档 §20.2

**步骤**：

```bash
curl -sS -D - -o /tmp/s1-body.json -X POST -H "Authorization: Bearer ${DB_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"agent_id": "'"${AGENT_ID}"'", "environment_id": "'"${ENV_ID}"'", "vault_ids": ["'"${VAULT_ID}"'"]}' \
  "${BASE_URL}/v1/sessions"
```

**预期结果**：

- HTTP 201
- **响应头**：`Linchpin-Deprecation: vault_ids`（`api_version.py` L107/L124；请求体仍为 v1 形态则无版本协商头干扰）
- 响应体 `vault_ids == [VAULT_ID]`
- （可选）事件流随后出现 `session.deprecation_used` 事件

#### S-2 新形态 resources → 201 且无弃用信号（P0，需沙箱）· 文档 §20.2

**步骤**：同 S-1，body 中 `vault_ids` 替换为 `"resources": [{"type": "vault", "vault_id": "'"${VAULT_ID}"'"}]`。

**预期结果**：

- HTTP 201；**无** `Linchpin-Deprecation` 响应头
- 响应体 `vault_ids == [VAULT_ID]`（resources 折叠进 vault_ids，`sessions.py` L415-425），`resources` 数组回显该绑定

#### S-3 双形态同时出现 → 折叠去重（P1，需沙箱）· 文档 §20.2

**步骤**：body 同时含 `"vault_ids": ["'"${VAULT_ID}"'"]` 与 `"resources": [{"type": "vault", "vault_id": "'"${VAULT_ID}"'"}]`。

**预期结果**：

- HTTP 201；带 `Linchpin-Deprecation: vault_ids` 头（旧形态出现即标记）
- 响应体 `vault_ids` 恰好 1 个元素（两形态折叠去重，`sessions.py` L415-425）

#### S-4 绑定不存在的 vault → 422（P0）· 文档 §20.2

**步骤**：

```bash
curl -sS -w "\nHTTP:%{http_code}\n" -X POST -H "Authorization: Bearer ${DB_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"agent_id": "'"${AGENT_ID}"'", "environment_id": "'"${ENV_ID}"'",
       "vault_ids": ["00000000-0000-0000-0000-000000000000"]}' \
  "${BASE_URL}/v1/sessions"
```

**预期结果**：

- HTTP 422：

```json
{"error": "validation_error", "message": "Vault 00000000-0000-0000-0000-000000000000 not found", "detail": null}
```

- 校验先于落库与 provisioning，无任何副作用（无会话行、无容器）

#### S-5 绑定已归档 vault → 422（P1）· 文档 §20.2

**步骤**：同 S-4，`vault_ids` 用 V-5 归档的 `VAULT_ARCH_ID`。

**预期结果**：

- HTTP 422：`{"error":"validation_error","message":"Vault ${VAULT_ARCH_ID} is archived","detail":null}`（`sessions.py` L456-460）

#### S-6 vault_ids 非法 UUID → 422（P2）· 文档 §20.2

**步骤**：同 S-4，`vault_ids: ["not-a-uuid"]`。

**预期结果**：

- HTTP 422：`{"error":"validation_error","message":"Invalid vault id: not-a-uuid","detail":null}`（`sessions.py` L435-440）
- 对照：vault 端点路径非法 UUID 是 **404**（V-4），会话绑定内是 **422**

#### S-7 绑定跨项目 vault → 422（P1）· 文档 §20.2

**前置条件**：`K2_KEY`（项目 B）；用 `K2_KEY` 在项目 B 创建一个 vault（记 `VAULT_B_ID`）。

**步骤**：用 `DB_KEY`（项目 A）按 S-4 步骤创建会话，`vault_ids: ["'"${VAULT_B_ID}"'"]`。

**预期结果**：

- HTTP 422：`{"error":"validation_error","message":"Vault ${VAULT_B_ID} not found","detail":null}`（跨项目与不存在同文案，不泄露存在性；`sessions.py` L443-451）
- 对照：vault 端点直接访问跨项目资源是 **404**（Z-3）

---

### Y. BYOK 会话加密（x-events-encrypted + x-kek-credential-id）

> **本组前置**：`KEK_CRED_ID`（C-5）、`VAULT_ID`、`AGENT_ID`、`ENV_ID`、`DB_KEY`。
> **沙箱要求**：KEK 校验链（`sessions.py` L258-313）位于 sandbox 解析（L919）**之后**、落库（L1059）之前，因此**本组全部用例需要可用沙箱**（SKIP_DOCKER 下统一 503）。
> 源码依据：`sessions.py` L322-339（请求头定义）、L1040-1057（解析时机）、L258-313（校验链）。

#### Y-1 KEK 凭据的 vault 不在 vault_ids → 422（P1）· 文档 §20.2

**步骤**：创建会话，请求头带 `x-events-encrypted: true` 与 `x-kek-credential-id: ${KEK_CRED_ID}`，body **不绑定** `VAULT_ID`（无 `vault_ids`/`resources`）。

**预期结果**：

- HTTP 422：

```json
{"error": "validation_error", "message": "KEK credential ${KEK_CRED_ID} belongs to vault ${VAULT_ID}, which is not in the session's vault_ids", "detail": null}
```

（`sessions.py` L298-309；空 `vault_ids` 同样触发。）

#### Y-2 x-kek-credential-id 非法 UUID → 422（P2）· 文档 §20.2

**步骤**：请求头 `x-kek-credential-id: junk` + `x-events-encrypted: true`。

**预期结果**：

- HTTP 422（注意 message 中 id 带 Python `!r` 引号，`sessions.py` L276）：

```json
{"error": "validation_error", "message": "x-kek-credential-id 'junk' is not a valid credential id", "detail": null}
```

#### Y-3 KEK 凭据不存在/已归档 → 404（P1）· 文档 §20.2

**步骤**：

1. `x-kek-credential-id` 指向随机 UUID（如 `00000000-0000-0000-0000-000000000000`）；
2. 用 C-12 归档的凭据 `CRED2_ARCH_ID` 作 `x-kek-credential-id`（归档分支）。

**预期结果**：

- 两步均 HTTP 404（`sessions.py` L287-297）：

```json
{"error": "not_found", "message": "byok_kek credential ${ID} not found (or archived)", "detail": null}
```

（查询按 `credential_type = 'byok_kek' AND archived_at IS NULL` 过滤，非 byok_kek 凭据同样落此分支。）

#### Y-4 合法 BYOK 组合 → 201（P1，需沙箱）· 文档 §20.2

**步骤**：创建会话，请求头 `x-events-encrypted: true` + `x-kek-credential-id: ${KEK_CRED_ID}`，body 绑定 `vault_ids: [VAULT_ID]`。

**预期结果**：

- HTTP 201；响应体 `events_encrypted == true`（`models.py` L1107）
- 会话数据密钥改由客户 KEK 包裹（`sessions.py` L1051-1055 `wrap_data_key(kek=...)`）；信封加密的事件形态验证可衔接主文档 H 组事件读取（`type` 为业务事件的 payload 为密文形态）
- 复核 Y-1 的前提：同会话若去掉 `x-kek-credential-id` 仅保留 `x-events-encrypted: true` → 201 且走平台主密钥包裹（B8 基线行为）

---

### Z. 认证 scope 与租户隔离

> **本组前置**：`RO_KEY`（主文档 D-1，scopes `["sessions:read"]`）、`K2_KEY`（项目 B，用于 Z-3）。源码依据：`app/auth.py` L173-196（`_check_scopes`：GET/HEAD/OPTIONS 放行，其余需 `{resource}:write`）。

#### Z-1 只读 key 可 GET vault（P0）· 文档 §23.3

**步骤**：用 `RO_KEY` 请求 `GET /v1/vaults/${VAULT_ID}`（含列表 `GET /v1/vaults` 与凭据 GET 亦可）。

**预期结果**：HTTP 200（只读方法无需 write scope）。

#### Z-2 只读 key 非 GET → 403 missing scope（P0）· 文档 §23.2

**步骤**：

```bash
curl -sS -w "\nHTTP:%{http_code}\n" -X POST -H "Authorization: Bearer ${RO_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"display_name": "ro-vault"}' \
  "${BASE_URL}/v1/vaults"
```

**预期结果**：

- HTTP **403**（先于请求体校验/业务逻辑）：

```json
{"error": "forbidden", "message": "missing scope vaults:write", "detail": null}
```

（vault 资源映射的写 scope 为 `vaults:write`；PATCH/DELETE/归档等非 GET 方法同样 403。）

#### Z-3 跨项目 vault → 404（P1）· 文档 §23.3

**前置条件**：`K2_KEY` 在项目 B 创建 vault（记 `VAULT_B_ID`，可与 S-7 共用）。

**步骤**：用 `DB_KEY`（项目 A）请求 `GET /v1/vaults/${VAULT_B_ID}`。

**预期结果**：

- HTTP **404**（租户过滤混入 404，不泄露存在性；`vaults.py` `_get_vault_or_404` 带 `project_id` 过滤）：

```json
{"error": "not_found", "message": "Vault ${VAULT_B_ID} not found", "detail": null}
```

- 对照：同一资源在会话绑定校验中是 **422**（S-7）

---

### R. 运行时模型密钥解析（vault → env 回退 → credential_missing）

> **本组前置**：`CRED_ID`（C-2 的 openai 凭据，已轮换则用 C-10 的新值）、`VAULT_ID`、`ENV_ID`、`DB_KEY`；**可用 Docker 沙箱**（SKIP_DOCKER 下会话创建 503，本组全部 SKIP）；R-0 mock 模型后端。
> **环境变量约束**：linchpin-api 服务进程**不得**注入 `OPENAI_API_KEY`（保证 R-1/R-4 解析路径纯净）；R-2/R-3 需临时注入后重启服务。env 注入的是服务进程环境，非测试 shell。
> 源码依据：`orchestrator.py` L1280-1322（解析优先级与失败语义）、`credentials.py` L28-70（`resolve_api_key` SQL 过滤）、`providers.py` L498-521（OpenAI 适配器）、L681-696（`get_provider` 按 provider 选择适配器）。

#### R-0 准备（非用例）：mock 模型后端 + 专用 agent

**mock 脚本**（写入 `/tmp/mock_openai.py`，记录 Authorization 头，返回固定 chat.completion）：

```python
from http.server import BaseHTTPRequestHandler, HTTPServer

class H(BaseHTTPRequestHandler):
    def do_POST(self):
        with open("/tmp/mock-auth.txt", "a") as f:
            f.write(self.headers.get("Authorization", "<none>") + "\n")
        body = (b'{"id":"cmpl-mock","object":"chat.completion","created":0,'
                b'"model":"gpt-4o-mini","choices":[{"index":0,'
                b'"message":{"role":"assistant","content":"mock-ok"},'
                b'"finish_reason":"stop"}],'
                b'"usage":{"prompt_tokens":1,"completion_tokens":1,"total_tokens":2}}')
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass

HTTPServer(("127.0.0.1", 9201), H).serve_forever()
```

**启动与建 agent**：

```bash
rm -f /tmp/mock-auth.txt
python3 /tmp/mock_openai.py &          # 监听 127.0.0.1:9201
curl -sS -o /dev/null -w "agent:%{http_code}\n" -X POST -H "Authorization: Bearer ${DB_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"name": "mock-openai-agent", "model": {"provider": "openai", "id": "gpt-4o-mini", "base_url": "http://127.0.0.1:9201/v1"}, "system": "You are a test agent."}' \
  "${BASE_URL}/v1/agents"
```

**预期**：agent 201，记录 `MOCK_AGENT_ID=$id`。`base_url` 覆盖默认 `https://api.openai.com/v1`（`providers.py` `get_provider`），模型调用由 API 进程服务端发起（非容器内），故 mock 用 127.0.0.1 可达。

#### R-1 vault 凭据按 (vault_id, provider) 解密注入（P0）· 文档 §23 概念

**步骤**：

```bash
# 1) 创建会话：mock agent + 绑定 VAULT_ID（内含 C-2 的 openai 凭据）
SESSION_ID=$(curl -sS -X POST -H "Authorization: Bearer ${DB_KEY}" -H "Content-Type: application/json" \
  -d '{"agent_id": "'"${MOCK_AGENT_ID}"'", "environment_id": "'"${ENV_ID}"'", "vault_ids": ["'"${VAULT_ID}"'"]}' \
  "${BASE_URL}/v1/sessions" | jq -r .id)
# 2) 等待 provisioning → running（轮询 GET /v1/sessions/${SESSION_ID}）
# 3) 触发一轮对话
curl -sS -o /dev/null -w "events:%{http_code}\n" -X POST -H "Authorization: Bearer ${DB_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"events": [{"type": "user.message", "payload": {"text": "hello"}}]}' \
  "${BASE_URL}/v1/sessions/${SESSION_ID}/events"
# 4) 轮询至 idle 后核对注入
sleep 3; cat /tmp/mock-auth.txt
```

**预期结果**：

- 会话最终 `status == "idle"`、`stop_reason == null`（不被凭据问题打断）
- `/tmp/mock-auth.txt` 出现一行 `Bearer sk-test-openai-1`（**恰为 C-2 创建的明文 api_key**——vault 凭据解密注入链路贯通；若执行过 C-10 则为 `sk-test-openai-rotated`）

#### R-2 优先级：vault 凭据 > 环境变量（P1）

**前置条件**：服务进程注入 `OPENAI_API_KEY=sk-env-fallback` 并重启；R-1 环境保持（`VAULT_ID` 仍含 openai 凭据）。

**步骤**：重复 R-1（新会话，`rm -f /tmp/mock-auth.txt` 后重新触发）。

**预期结果**：

- `/tmp/mock-auth.txt` 的值仍是 **vault 凭据明文**（`sk-test-openai-1`），而非 `sk-env-fallback`——`orchestrator.py` L1297-1301：有 `vault_ids` 时优先 `resolve_api_key`，命中即不再看 env

#### R-3 env 回退（P1）

**前置条件**：同 R-2（env 已注入 `sk-env-fallback`）。

**步骤**：创建会话（mock agent）**不绑定任何 vault** → 触发 `user.message` → 查 `/tmp/mock-auth.txt`。

**预期结果**：

- `/tmp/mock-auth.txt` 出现 `Bearer sk-env-fallback`——无 vault 绑定时回退 `OPENAI_API_KEY`（`orchestrator.py` L1317-1319）

#### R-4 全缺失 → credential_missing 失败语义（P0）

**前置条件**：服务进程**无** `OPENAI_API_KEY`（R-1 同款环境）；`VAULT_ID2` 为不含 openai 凭据的 vault（可用 V-1 后在 `VAULT2_ID` 绑定，其中只有 C-7 的 openrouter 凭据）。

**步骤**：

```bash
# 1) 创建会话：mock agent（provider=openai）+ 绑定无 openai 凭据的 vault
SESSION_ID=$(curl -sS -X POST -H "Authorization: Bearer ${DB_KEY}" -H "Content-Type: application/json" \
  -d '{"agent_id": "'"${MOCK_AGENT_ID}"'", "environment_id": "'"${ENV_ID}"'", "vault_ids": ["'"${VAULT2_ID}"'"]}' \
  "${BASE_URL}/v1/sessions" | jq -r .id)
# 2) 触发对话（创建成功即执行——创建本身不校验密钥可用性）
curl -sS -o /dev/null -X POST -H "Authorization: Bearer ${DB_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"events": [{"type": "user.message", "payload": {"text": "hi"}}]}' \
  "${BASE_URL}/v1/sessions/${SESSION_ID}/events"
# 3) 轮询终态与失败语义
curl -sS -H "Authorization: Bearer ${DB_KEY}" "${BASE_URL}/v1/sessions/${SESSION_ID}" | jq '{status, stop_reason}'
curl -sS -H "Authorization: Bearer ${DB_KEY}" "${BASE_URL}/v1/sessions/${SESSION_ID}/events?limit=5" | jq '.data[] | select(.type == "session.error")'
```

**预期结果**：

- 第 1 步 HTTP **201**（密钥缺失在首轮对话才暴露，`orchestrator.py` 步骤 5 在 agent 循环内）
- 终态 `status == "failed"`、`stop_reason == "credential_missing"`（`orchestrator.py` L1314，stop_reason 写库并在 SessionResponse 可见，`models.py` L1087）
- 事件流出现 `session.error`，payload 含 `"source": "credential_resolution"` 与提示文案 `No API key found for provider 'openai'. …`（`orchestrator.py` L1308-1313）
- mock 的 `/tmp/mock-auth.txt` 无新增行（未发出任何模型请求）

#### R-5 已归档凭据被跳过 → env 回退（P2）

**前置条件**：服务进程注入 `OPENAI_API_KEY=sk-env-fallback`；`VAULT_ID` 的 openai 凭据已归档（用 C-12 方式归档 `CRED_ID`，或另建一个后归档）。

**步骤**：重复 R-1（会话绑定 `VAULT_ID` + 触发对话）。

**预期结果**：

- 会话正常完成（不 failed）；`/tmp/mock-auth.txt` 的值为 **env 回退值** `Bearer sk-env-fallback`——`resolve_api_key` 的 SQL 过滤 `credential_type='api_key' AND provider=$2 AND archived_at IS NULL`（`credentials.py` L28-70）跳过已归档凭据；解密失败的凭据同样跳过继续找下一条

---

## 4. 清理

```bash
# 1) 删除全部测试 vault（级联删除凭据；归档的 vault 用 DELETE 同样可物理删除）
for v in ${VAULT_ID} ${VAULT2_ID} ${VAULT3_ID} ${VAULT_ARCH_ID} ${VAULT_DEL_ID} ${VAULT_B_ID}; do
  [ -n "${v}" ] && curl -sS -o /dev/null -w "del vault ${v}: %{http_code}\n" \
    -X DELETE -H "Authorization: Bearer ${DB_KEY}" "${BASE_URL}/v1/vaults/${v}"
done

# 2) 终止/删除 R 组会话与 mock agent、mock 进程
[ -n "${MOCK_AGENT_ID}" ] && curl -sS -o /dev/null -w "del agent: %{http_code}\n" \
  -X DELETE -H "Authorization: Bearer ${DB_KEY}" "${BASE_URL}/v1/agents/${MOCK_AGENT_ID}"
pkill -f mock_openai.py
rm -f /tmp/mock-auth.txt /tmp/mock_openai.py /tmp/s1-body.json

# 3) 销毁 BYOK 测试 KEK
unset FERNET_KEY

# 4) 若 R-2/R-3 临时注入过 OPENAI_API_KEY，从服务进程环境中移除并重启服务
```

清理校验：`GET /v1/vaults` 无残留 test-vault；审计日志中本篇产生的 `vault.deleted` 逐一可查（衔接主文档 L 组方式）。
