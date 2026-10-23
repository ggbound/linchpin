# 凭据保险库（Vault）接入指南 — 专题文档

| 项目 | 内容 |
|---|---|
| 读者 | 需要接入 vault 凭据能力的调用方（前端 / 第三方集成） |
| 适用版本 | Linchpin API 0.7.0（wire 版本 `v1` / `2026-05-13`） |
| 字段级参考 | [docs/api-integration-guide.md](api-integration-guide.md) §23（端点全表）与 §26（Schema 速查） |
| 行为依据 | 下文"实测"结论均来自 39 例专项测试（[用例](api-vault-credential-test-cases.md) / [执行报告](api-vault-credential-test-report.md)：27 PASS · 12 SKIP · 0 FAIL @ 0.7.0） |

本文档是 §23 的**行为导向**补充：不讲全量字段表，讲"怎么用、边界在哪、出错长什么样"。

---

## 1. 核心概念

**Vault（凭据保险库）** 是凭据的分组容器，隶属项目（受租户隔离）。一个 vault 内可存放最多 **20 个未归档凭据**。

**四种凭据类型**（`credential_type`）及用途：

| credential_type | 用途 | 关键字段 | 说明 |
|---|---|---|---|
| `bearer_token` | MCP 服务鉴权 | `mcp_server_url` + `token` | 会话中 MCP 工具调用时按服务地址匹配 |
| `oauth` | MCP 服务鉴权 | `mcp_server_url` + `client_id` + `access_token` / `refresh_token` / `client_secret` | `client_id` 是顶层列（明文回显）；其余入密文，可选字段仅在有值时写入 |
| `api_key` | **智能体模型调用** | `provider` + `api_key` | 运行时按 `(vault_id, provider)` 解密注入 LLM 请求（见 §3 第 4 步） |
| `byok_kek` | BYOK 会话加密 | `kek`（合法 Fernet key） | 客户托管密钥加密密钥（见 §4） |

**关键约束**：

- 唯一性（作用域均为**同 vault 内**，跨 vault 可重复）：
  - `bearer_token` / `oauth`：`mcp_server_url` 唯一（`uq_credentials_vault_mcp_url`）；
  - `api_key`：`provider` 唯一（`uq_credentials_vault_provider`）；
  - 违反返回 409：`"A credential for '{target}' already exists in vault {vault_id}"`。
- 数量上限：单 vault **20 个未归档**凭据，超限 409 `"Vault {vault_id} already has 20 active credentials"`（实测上限按未归档计数，归档 1 个可再建 1 个）。
- **密文永不回显**：所有凭据响应固定 9 键——`id`、`vault_id`、`credential_type`、`mcp_server_url`、`provider`、`client_id`、`created_at`、`updated_at`、`archived_at`——不含任何密文字段。
- `provider` 枚举：`openrouter` / `openai` / `ollama`（创建层不校验"是否需要密钥"，`ollama` 免密钥的语义体现在运行时）。

---

## 2. 认证与隔离

- 所有请求携带 `Authorization: Bearer lpk_…`（API key）。
- **方法级 scope**：`GET` / `HEAD` / `OPTIONS` 无需 write scope；其余方法（POST/PATCH/DELETE/归档）需要 `vaults:write`（scopes 含 `*` 亦可），否则 403 `{"error":"forbidden","message":"missing scope vaults:write","detail":null}`，且先于请求体校验。
- **租户隔离**：跨项目访问 vault 端点返回 404（与"不存在"同文案，不泄露存在性）；对照：会话绑定跨项目 vault 返回 422（见 §6 错误速查）。
- 所有 2xx 与错误响应均带 `Linchpin-API-Version` 回显头。

---

## 3. 快速上手（模型 API Key 链路）

以"让智能体使用 vault 里的 OpenAI 密钥"为例：

### 第 1 步：创建 vault

```bash
curl -sS -X POST -H "Authorization: Bearer ${DB_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"display_name": "prod-credentials", "metadata": {"team": "qa"}}' \
  "${BASE_URL}/v1/vaults"
# 201 → 记录 id 为 VAULT_ID
```

### 第 2 步：在 vault 中创建 api_key 凭据

```bash
curl -sS -X POST -H "Authorization: Bearer ${DB_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"credential_type": "api_key", "provider": "openai", "api_key": "sk-…"}' \
  "${BASE_URL}/v1/vaults/${VAULT_ID}/credentials"
# 201；响应不含 api_key 明文
```

### 第 3 步：创建会话时绑定 vault（两种形态）

```bash
# 形态 A（新，推荐）：resources 中 type="vault" 条目，等效 vault_ids
-d '{"agent_id": "…", "environment_id": "…",
     "resources": [{"type": "vault", "id": "'"${VAULT_ID}"'"}]}'

# 形态 B（旧行为，仍支持）：vault_ids 数组
-d '{"agent_id": "…", "environment_id": "…", "vault_ids": ["'"${VAULT_ID}"'"]}'
# 旧行为会话响应携带 Linchpin-Deprecation 弃用信号；双形态同时出现时折叠去重
```

绑定校验（先于落库与 sandbox 解析，无副作用）：不存在/跨项目 → 422 `"Vault {id} not found"`；已归档 → 422 `"Vault {id} is archived"`；`vault_ids` 非法 UUID → 422 `"Invalid vault id: not-a-uuid"`。

### 第 4 步：运行时自动注入

会话内智能体发起模型调用时，服务端按 `(vault_id, provider)` 匹配 vault 中的 `api_key` 凭据，**解密后作为 `Authorization: Bearer …` 注入**——调用方无需（也无法）在会话接口中传递密钥。

密钥解析优先级：

```
vault 凭据（绑定 vault 中匹配 provider 的未归档 api_key）
  → 环境变量（OPENROUTER_API_KEY / OPENAI_API_KEY）
  → ollama 免密钥
```

全缺失且供应商非 `ollama` 时：会话创建本身**仍返回 201**（密钥在首轮对话才暴露），随后会话以 `status == "failed"`、`stop_reason == "credential_missing"` 终止，事件流出现 `session.error`（`payload.source == "credential_resolution"`，提示 `No API key found for provider 'openai'. …`）。已归档凭据会被解析跳过（自动走 env 回退）。

---

## 4. BYOK 会话加密（byok_kek）

适用于"会话事件用客户自己的密钥加密"场景。流程：

1. 在某个 vault 中创建 `byok_kek` 凭据（`kek` 必须是合法 Fernet key，非法直接 422，不入库）；
2. 创建会话时带两个请求头，且**该凭据所在 vault 必须绑定进会话**：

```bash
curl -sS -X POST -H "Authorization: Bearer ${DB_KEY}" \
  -H "x-events-encrypted: true" \
  -H "x-kek-credential-id: ${KEK_CRED_ID}" \
  -d '{"agent_id": "…", "environment_id": "…", "vault_ids": ["'"${VAULT_ID}"'"]}' \
  "${BASE_URL}/v1/sessions"
# 201；响应 events_encrypted == true；会话数据密钥改由客户 KEK 包裹
```

**校验链行为**（按校验顺序，全部发生在 sandbox 解析**之后**）：

| 校验 | 结果 |
|---|---|
| `x-kek-credential-id` 非法 UUID | 422 `"x-kek-credential-id 'junk' is not a valid credential id"`（id 带 `'…'` 引号） |
| KEK 凭据不存在 / 已归档 / 非 byok_kek 类型 | 404 `"byok_kek credential {id} not found (or archived)"` |
| KEK 凭据所在 vault 未绑定会话（含未带 vault_ids） | 422 `"KEK credential {id} belongs to vault {vault_id}, which is not in the session's vault_ids"` |
| 全部通过 | 201，`events_encrypted == true` |

---

## 5. 生命周期语义

### Vault

| 操作 | 行为（实测 @ 0.7.0） |
|---|---|
| 归档（POST `/archive`） | 200，`archived_at` 置位；**级联归档其下全部凭据并清空其密文**；重复归档 409 `"Vault {id} is already archived"`；归档后不可再建凭据（409 `"Vault {id} is archived"`） |
| 删除（DELETE） | 204，级联**物理删除**其下凭据；**已归档的 vault 也可直接 DELETE（204）**；再删 404 |

### Credential

| 操作 | 行为（实测 @ 0.7.0） |
|---|---|
| 归档（POST `…/archive`） | 200，`archived_at` 置位 + 密文清空；重复归档 409 `"Credential {id} is already archived"`；归档后 PATCH 409 `"Credential {id} is archived"` |
| **PATCH（更新）** | ⚠️ **密文整体替换而非字段合并**——请求中未带的密文字段被**静默清空**（例如仅 PATCH `access_token` 会使原 `refresh_token` 丢失）。轮换密钥时请在单次 PATCH 中带上**全部**需保留的密文字段。空体 `{}` 原样返回、`updated_at` 不变 |
| GET / DELETE 对已归档凭据 | **不过滤**：GET 已归档仍 200（字段可见），DELETE 已归档仍 204（可物理删除） |
| 数量上限 | 按**未归档**计数（归档可腾出名额） |

### 列表分页（vault 与凭据列表通用）

`limit`（≤100，默认 20）+ `offset` 翻页，排序 `created_at DESC`；**`next_cursor` 恒为 null**，游标翻页不可用。`offset` 越界返回 200 + `data=[]` + `has_more=false`。

---

## 6. 错误速查表（实测 @ 0.7.0）

统一错误信封 `{"error", "message", "detail"}`；无附加键时 `detail` 为字面量 `null`。Pydantic 校验失败的 `detail.errors[]` 中，判别联合的 `loc` **带 discriminator 段**（如 `["body","api_key","provider"]`）。

| 场景 | 状态码 | message（原文） |
|---|---|---|
| 路径 vault_id 非法 UUID | 404 | `Vault not-a-uuid not found`（非 422） |
| 跨项目访问 vault 端点 | 404 | `Vault {id} not found`（不泄露存在性） |
| 凭据不存在 | 404 | `Credential {id} not found` |
| provider 非法枚举 | 422 | `Input should be 'openrouter', 'openai' or 'ollama'` |
| byok kek 非 Fernet key | 422 | `Value error, kek is not a valid Fernet key: Fernet key must be 32 url-safe base64-encoded bytes.` |
| 同 vault 重复 provider / mcp_server_url | 409 | `A credential for '{target}' already exists in vault {vault_id}` |
| 超 20 凭据上限 | 409 | `Vault {vault_id} already has 20 active credentials` |
| 已归档 vault 中建凭据 | 409 | `Vault {vault_id} is archived` |
| vault / 凭据重复归档 | 409 | `… is already archived` |
| PATCH 已归档凭据 | 409 | `Credential {id} is archived` |
| 只读 key 执行写操作 | 403 | `missing scope vaults:write` |
| 会话绑定不存在 / 跨项目 vault | 422 | `Vault {id} not found`（同文案） |
| 会话绑定已归档 vault | 422 | `Vault {id} is archived` |
| 会话 vault_ids 非法 UUID | 422 | `Invalid vault id: not-a-uuid` |
| x-kek-credential-id 非法 | 422 | `x-kek-credential-id 'junk' is not a valid credential id` |
| KEK 凭据不存在 / 已归档 | 404 | `byok_kek credential {id} not found (or archived)` |
| KEK vault 未绑定会话 | 422 | `KEK credential {id} belongs to vault {vault_id}, which is not in the session's vault_ids` |

---

## 7. 端点索引与测试覆盖

端点全表见接入指南 §23：[23.1](api-integration-guide.md) 列表 · 23.2 建 vault · 23.3 查 vault · 23.4 删 vault · 23.5 归档 vault · 23.6 凭据列表 · 23.7 建凭据 · 23.8 查凭据 · 23.9 PATCH 凭据 · 23.10 删凭据 · 23.11 归档凭据；会话绑定见 §20.2。

本文所有"实测"结论的执行证据：

- 用例：[docs/api-vault-credential-test-cases.md](api-vault-credential-test-cases.md)（39 例，含逐步骤 curl 与源码依据）；
- 执行报告：[docs/api-vault-credential-test-report.md](api-vault-credential-test-report.md)（27 PASS · 12 SKIP · 0 FAIL @ 0.7.0）。

> 注意：报告中 12 例 SKIP（会话 201 成功路径 S-1/2/3、BYOK 全组 Y-1~4、运行时注入 R-1~5）在无 Docker 沙箱环境未实测，相应行为（§3 第 3/4 步成功路径、§4 成功路径）目前以源码为依据，补测后如有出入以实测为准。
