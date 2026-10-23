# Linchpin Vault 凭据与模型 API Key 链路 — 测试执行报告

| 项目 | 内容 |
|---|---|
| 对应用例文档 | [docs/api-vault-credential-test-cases.md](api-vault-credential-test-cases.md)（39 例：P0×15 · P1×20 · P2×4） |
| 对应接口文档 | [docs/api-integration-guide.md](api-integration-guide.md) 第 23 章（凭据保险库 `vaults`） |
| 被测版本 | Linchpin API 0.7.0（wire 版本 `v1` / `2026-05-13`） |
| 执行日期 | 2026-10-22 |
| 执行方式 | 手工执行（curl + jq + psql 复核），按 V → C → S → Y → Z → R 顺序 |
| **总体结果** | **39 例：27 PASS · 12 SKIP · 0 FAIL** |
| **服务缺陷** | **0 个**（P0/P1 主链路全部通过；SKIP 均为环境限制所致，详见第 5 节） |

---

## 1. 结果总览

### 1.1 分组结果

| 组 | 主题 | 用例数 | PASS | SKIP | FAIL |
|---|---|---|---|---|---|
| V | Vault CRUD | 6 | 6 | 0 | 0 |
| C | 凭据管理（四种 credential_type） | 14 | 14 | 0 | 0 |
| S | 会话绑定 vault（vault_ids / resources） | 7 | 4 | 3 | 0 |
| Y | BYOK 会话加密 | 4 | 0 | 4 | 0 |
| Z | 认证 scope 与租户隔离 | 3 | 3 | 0 | 0 |
| R | 运行时模型密钥解析 | 5 | 0 | 5 | 0 |
| **合计** | | **39** | **27** | **12** | **0** |

### 1.2 优先级结果

| 优先级 | 用例数 | PASS | SKIP | FAIL |
|---|---|---|---|---|
| P0 | 15 | 11 | 4 | 0 |
| P1 | 20 | 14 | 6 | 0 |
| P2 | 4 | 2 | 2 | 0 |

P0 通过率 11/11 可执行用例（4 例 SKIP 均因沙箱不可用）；**全部可执行用例零失败**。

---

## 2. 执行环境

| 项 | 实际值 |
|---|---|
| 服务 | linchpin-api（uvicorn）@ `http://localhost:8000` |
| 数据库 | PostgreSQL @ `postgresql://linchpin:linchpin@localhost:5432/linchpin` |
| 服务进程变量 | `VAULT_ENCRYPTION_KEY=/tmp/vault-tc/master-kek`、`LINCHPIN_FILES_ROOT=/tmp/linchpin-files`、`LINCHPIN_SKIP_DOCKER=true` |
| 沙箱 | 无 Docker（macOS 开发机） |
| 测试身份 | bootstrap key 签发的 `DB_KEY`（scopes `["*"]`）、`RO_KEY`（scopes `["sessions:read"]`）、`K2_KEY`（项目 B） |

### 执行期间的环境问题与处置（2 项，均已解决）

| # | 现象 | 根因 | 处置 |
|---|---|---|---|
| 1 | 首次创建凭据返回 500 | 服务进程未配置 `VAULT_ENCRYPTION_KEY`，Fernet 主密钥缺失 | 生成 master-kek 文件（600 权限）写入 `/tmp/vault-tc/master-kek`，为服务进程注入该变量后重启，凭据创建恢复正常 |
| 2 | 会话创建返回 500 `PermissionError` | macOS 下默认输出根 `/var/lib/linchpin` 不可写（`watcher.py` L69-73 在沙箱解析之前写 `_session_outputs/`） | 为服务进程设置 `LINCHPIN_FILES_ROOT=/tmp/linchpin-files` 后重启，触及 503 分级（见第 5 节） |

> 两项均为环境配置问题，非服务缺陷；已回写用例文档 §1.2（见 4.1）。

---

## 3. 分组执行明细

> "关键观测"为实际响应/DB 复核结果；详细步骤与预期见用例文档对应小节。

### V. Vault CRUD（6/6 PASS）

| 用例 | 优先级 | 结果 | 关键观测 |
|---|---|---|---|
| V-1 创建 vault | P0 | PASS | 201；响应 6 字段齐全，`metadata` 原样透传 |
| V-2 列表分页 | P1 | PASS | `limit=1` 时 `data` 恰 1 条、`has_more=true`；**`next_cursor` 恒为 null**——实际翻页只能用 `limit`/`offset`；排序 `created_at DESC`；`offset` 越界返回 200 + `data=[]` + `has_more=false` |
| V-3 查询单个 | P1 | PASS | 200，6 键与 V-1 一致 |
| V-4 路径非法 UUID | P1 | PASS | **404（非 422）**：`{"error":"not_found","message":"Vault not-a-uuid not found","detail":null}`（路由内部手工解析 UUID） |
| V-5 归档 vault | P1 | PASS | 归档 200 且 `archived_at` 非空；其下凭据**级联归档**（`archived_at` 非空、密文清空）；重复归档 409 `"Vault … is already archived"` |
| V-6 删除 vault | P0 | PASS | 204 无响应体；vault 复查 404；凭据随 FK 级联物理删除；再删返回 404 |

### C. 凭据管理（14/14 PASS）

| 用例 | 优先级 | 结果 | 关键观测 |
|---|---|---|---|
| C-1 创建 bearer_token | P0 | PASS | 201；CredentialResponse 恰 9 键；反向断言通过——响应任何字段均不含明文 token |
| C-2 创建 api_key（openai） | P0 | PASS | 201；`provider=="openai"`、`mcp_server_url==null`；无密文泄漏。产出 `CRED_ID` 供 R 组使用 |
| C-3 provider 枚举边界 | P1 | PASS | 非法枚举 422：`type=="literal_error"`、`msg=="Input should be 'openrouter', 'openai' or 'ollama'"`、**`loc==["body","api_key","provider"]`（判别联合的 loc 带 discriminator 段）**；`ollama` 创建 201（创建层不校验"是否需要密钥"） |
| C-4 oauth 可选字段 | P1 | PASS | 201；`client_id` 顶层列明文回显；DB 解密复核密文键集合恰为 `["access_token"]`——`refresh_token`/`client_secret` 仅在有值时写入密文 |
| C-5 创建 byok_kek（合法） | P0 | PASS | 201；`mcp_server_url`/`provider` 均 null；响应不含 FERNET_KEY 值 |
| C-6 创建 byok_kek（非法） | P0 | PASS | 422：`msg=="Value error, kek is not a valid Fernet key: Fernet key must be 32 url-safe base64-encoded bytes."`、`loc==["body","byok_kek","kek"]`；DB 无残留行（校验先于入库） |
| C-7 同 vault 重复 provider | P0 | PASS | 409：`"A credential for 'openrouter' already exists in vault …"`；**跨 vault 同 provider 允许（201）**——唯一性作用域为同 vault 内 |
| C-8 同 vault 重复 mcp_server_url | P1 | PASS | 409，message 模板与 C-7 同源（`target` 取 `mcp_server_url or provider`） |
| C-9 查询无密文泄漏 | P0 | PASS | GET 单个/列表均 200、9 键唯一；无 `api_key`/`token`/`kek`/`access_token` 任何明文；不存在凭据 404（非法 UUID 同样 404） |
| C-10 PATCH 轮换 + 空体 | P1 | PASS | 轮换 200、`updated_at` 变化、其余字段不变；空体 `{}` PATCH 200 且状态原样、`updated_at` 不变 |
| C-11 PATCH 整体替换语义 | P1 | PASS | 仅 PATCH `access_token` → 200；**DB 解密复核密文键恰为 `["access_token"]`——未带的 `refresh_token` 被静默清空**，确认 PATCH 是密文整体替换而非合并；审计 `credential.updated` 的 `detail.fields` 仅记录本次写入键 |
| C-12 凭据归档生命周期 | P1 | PASS | 归档 200（密文清空）；重复归档 409 `"… is already archived"`；PATCH 已归档 409 `"… is archived"`；**GET 已归档 200（不过滤）**；**DELETE 已归档 204（可物理删除）** |
| C-13 单 vault 20 上限 | P2 | PASS | 前 20 个全部 201；第 21 个 409 `"Vault … already has 20 active credentials"`；上限按**未归档**凭据计数 |
| C-14 已归档 vault 中建凭据 | P1 | PASS | 409 `"Vault … is archived"` |

### S. 会话绑定 vault（4 PASS · 3 SKIP）

| 用例 | 优先级 | 结果 | 关键观测 |
|---|---|---|---|
| S-1 旧形态 vault_ids | P0 | SKIP | 503 `sandbox_runtime_unavailable`（SKIP_DOCKER，见第 5 节） |
| S-2 新形态 resources | P0 | SKIP | 同上 |
| S-3 双形态折叠去重 | P1 | SKIP | 同上 |
| S-4 绑定不存在 vault | P0 | PASS | 422：`"Vault 00000000-… not found"`；校验先于落库与 provisioning，无副作用 |
| S-5 绑定已归档 vault | P1 | PASS | 422：`"Vault … is archived"` |
| S-6 vault_ids 非法 UUID | P2 | PASS | 422：`"Invalid vault id: not-a-uuid"`——**与 vault 端点路径非法 UUID 的 404 形成对照** |
| S-7 绑定跨项目 vault | P1 | PASS | 422，message 与"不存在"同文案——**不泄露存在性** |

### Y. BYOK 会话加密（0 PASS · 4 SKIP）

| 用例 | 优先级 | 结果 | 关键观测 |
|---|---|---|---|
| Y-1 KEK vault 不在 vault_ids | P1 | SKIP | 503（KEK 校验链位于 sandbox 解析之后，SKIP_DOCKER 下不可达，见第 5 节） |
| Y-2 x-kek-credential-id 非法 UUID | P2 | SKIP | 同上 |
| Y-3 KEK 凭据不存在/已归档 | P1 | SKIP | 同上 |
| Y-4 合法 BYOK 组合 | P1 | SKIP | 同上 |

### Z. 认证 scope 与租户隔离（3/3 PASS）

| 用例 | 优先级 | 结果 | 关键观测 |
|---|---|---|---|
| Z-1 只读 key 可 GET | P0 | PASS | `RO_KEY`（scopes `["sessions:read"]`）GET vault/列表/凭据均 200——GET/HEAD/OPTIONS 放行 |
| Z-2 只读 key 非 GET | P0 | PASS | 403：`"missing scope vaults:write"`，且先于请求体校验/业务逻辑 |
| Z-3 跨项目 vault | P1 | PASS | 404：`"Vault … not found"`——租户过滤混入 404，不泄露存在性（与会话绑定的 422 形成对照） |

### R. 运行时模型密钥解析（0 PASS · 5 SKIP）

> R-0（mock 模型后端 + 专用 agent）为准备步骤，不计入用例数。

| 用例 | 优先级 | 结果 | 关键观测 |
|---|---|---|---|
| R-1 vault 凭据解密注入 | P0 | SKIP | 503（本组需可用沙箱创建会话 + mock 后端，见第 5 节） |
| R-2 优先级 vault > env | P1 | SKIP | 同上 |
| R-3 env 回退 | P1 | SKIP | 同上 |
| R-4 全缺失 → credential_missing | P0 | SKIP | 同上 |
| R-5 已归档凭据跳过 → env 回退 | P2 | SKIP | 同上 |

---

## 4. 执行期间发现与处置

### 4.1 文档修正（3 处，已回写用例文档）

执行中发现用例文档 3 处与实际响应不符，均属**文档笔误、非服务缺陷**，已当场修正：

| # | 位置 | 修正内容 |
|---|---|---|
| 1 | C-3 预期 | 422 判别联合的 `loc` 补 discriminator 段：实际为 `["body", "api_key", "provider"]`（原文缺 `api_key` 段） |
| 2 | C-6 预期 | `msg` 补全实际全文（`"Value error, kek is not a valid Fernet key: …"`），`loc` 同样补 discriminator 段 `["body", "byok_kek", "kek"]` |
| 3 | §1.2 执行环境表 | 补充 `LINCHPIN_FILES_ROOT` 行——macOS 下默认 `/var/lib/linchpin` 不可写会导致会话创建在沙箱解析之前 500，需设置可写输出根（见 2. 环境问题 #2） |

### 4.2 执行期间 401 事件定性（非服务缺陷）

执行期间曾出现一次 `401 {"error":"unauthorized","message":"Invalid API key"}`，经三重证据定性为**测试操作手误，非服务缺陷**：

1. **时序证据**：该 401 发生在一个未 source 变量文件（`/tmp/lp-test-vars.sh`）的终端手动 curl 中——`$DB_KEY` 为空导致请求头为 `Authorization: Bearer `（空 token），服务端正确拒绝；
2. **复现实验**：回到正确环境后连续 30 次同类请求，**0 次 401**；
3. **旁证**：DB 连接池检查 + asyncpg release 强制 `RESET ALL` 后再压测 30 次，仍 0 次 401——排除连接状态/会话变量污染。

**结论**：认证链路无缺陷；后续执行统一从已 source 变量文件的终端发起。

### 4.3 值得回写接入指南的实测行为（已安排，见配套增补）

以下行为在 PASS 用例中实测确认，将增补至 [docs/api-integration-guide.md](api-integration-guide.md) §23：

- 列表分页 `next_cursor` 恒为 null，翻页用 `limit`/`offset`（V-2）；
- vault 路径非法 UUID 返回 404 而非 422（V-4），与会话绑定内 422（S-6）对照；
- 归档 vault 可直接 `DELETE`（204，物理删除）（V-6 / C-12 关联行为）；
- **PATCH 凭据是密文整体替换而非字段合并——未带字段被静默清空**（C-11，DB 解密旁证）；
- GET 类端点不过滤已归档凭据（C-12）；
- 20 上限按未归档凭据计数（C-13）；
- 422 判别联合 `loc` 带 discriminator 段（C-3/C-6）；409 message 模板（C-7/C-8/C-12/C-13/C-14）。

---

## 5. SKIP 说明（12 例）

### 5.1 SKIP 根因：503 分级机制

本机无 Docker，服务以 `LINCHPIN_SKIP_DOCKER=true` 运行，会话创建在 sandbox 解析处统一返回 503 `sandbox_runtime_unavailable`（`sandbox.py`：`app.state.sandbox` 为 None）。但**校验链的执行顺序决定了哪些用例仍可实测**：

| 校验链位置（sessions.py） | 顺序 | 可否在 SKIP_DOCKER 下实测 | 涉及用例 |
|---|---|---|---|
| vault 绑定校验（L409-460） | 先于 sandbox 解析（L919） | ✅ 可实测 422 | S-4~S-7（4 例 PASS） |
| KEK 校验链（L258-313） | **后于** sandbox 解析 | ❌ 统一 503 | Y-1~Y-4（4 例 SKIP） |
| 会话 201 落库（L1059） | 后于 sandbox 解析 | ❌ 统一 503 | S-1/S-2/S-3（3 例 SKIP） |
| R 组（会话 + 模型调用全链路） | — | ❌ 统一 503 | R-1~R-5（5 例 SKIP） |

### 5.2 SKIP 清单与补测条件

12 例 SKIP：S-1、S-2、S-3、Y-1、Y-2、Y-3、Y-4、R-1、R-2、R-3、R-4、R-5。

**补测条件**（满足其一即可）：

1. 提供 Docker 沙箱的宿主机上重启服务（去除 `LINCHPIN_SKIP_DOCKER` 或设为 `false`），按用例文档 §1.2 原样执行 12 例；
2. R 组另需 mock 模型后端（用例文档 R-0 脚本）或真实 OpenAI 后端；R-2/R-3/R-5 需临时向服务进程注入 `OPENAI_API_KEY` 后重启（测毕移除）。

风险评价：12 例 SKIP 均不影响"凭据管理面 CRUD/错误语义/隔离"这三大已验证面；SKIP 集中在"会话创建成功路径"与"运行时密钥注入链路"，建议在具备沙箱的集成环境补测后再宣布链路 100% 覆盖。

---

## 6. 数据清理记录

测试结束后已执行用例文档第 4 节清理（API 删除为主，psql 复核）：

| 对象 | 清理动作 | 终态 |
|---|---|---|
| 测试 vault（V/C/S/Z 组全部，含已归档） | `DELETE /v1/vaults/{id}`（级联删除凭据，归档 vault 亦可直接删） | 0 残留 |
| 测试 api_keys（3 把，`vault-tc-*`） | psql `DELETE FROM api_keys WHERE name LIKE 'vault-tc-%'` | DELETE 3，0 残留 |
| 测试 agent（`agent-vault-tc`，id `d62167b8-…`） | psql DELETE | DELETE 1，0 残留 |
| 测试 environments | 随 agent 清理核查 | 0 残留 |
| BYOK 测试 KEK（FERNET_KEY） | shell `unset`，未入库 | 已销毁 |
| mock 工件（`/tmp/mock_openai.py`、`/tmp/mock-auth.txt` 等） | 进程终止 + 文件删除 | 已清除 |
| **用户自有凭据（2 条，保留）** | 未触碰：`0dbd2103…`（api_key/openrouter → smoke-vault-r4，2026-10-20）、`0126dc2b…`（bearer_token/https://mcp.example.com → verify-vault，2026-10-21） | 原样保留 |

终态复核：test vaults 0 · test api_keys 0 · test agents 0 · test environments 0 · user credentials 2（保留）。

---

## 7. 环境变更告知

测试对运行环境做了以下**持久变更**，交接时请注意：

1. **新增 `VAULT_ENCRYPTION_KEY=/tmp/vault-tc/master-kek`**（服务进程变量）：
   - ⚠️ 库中现有 2 条用户自有凭据（见第 6 节）已由该 master-kek 加密——**更换该 key 将导致这些凭据无法解密**；
   - 生产部署时应改用持久化路径与密钥管理方案，勿沿用 `/tmp` 位置。
2. **新增 `LINCHPIN_FILES_ROOT=/tmp/linchpin-files`**（服务进程变量）：macOS 开发机专用；生产默认 `/var/lib/linchpin` 即可。
3. **`LINCHPIN_SKIP_DOCKER=true`** 仍生效：SKIP 的 12 例在此环境下不可执行。
4. **建议清理的临时工件**（600 权限，含测试密钥材料）：`/tmp/vault-tc/`、`/tmp/lp-test-vars.sh`、`/tmp/linchpin-files/`——确认 12 例 SKIP 补测完成后即可删除（删除 master-kek 前须先确认不再需要解密现有凭据）。

---

## 8. 结论与建议

**结论**：

- 39 例中可执行的 27 例**全部通过**，0 失败，0 服务缺陷；
- P0 主链路（凭据 CRUD、密文不泄漏、唯一性、vault 级联、会话绑定校验、scope 隔离、租户隔离）全部验证通过；
- 执行期间的 1 次 401 定性为操作手误，2 次 500 定性为环境配置缺失，均已处置并回写文档；
- 12 例 SKIP 全部因无 Docker 沙箱（503 分级机制），非服务问题。

**建议**：

1. **补测**：在具备 Docker 沙箱的集成环境补测 12 例 SKIP（S-1/2/3、Y-1~4、R-1~5），重点 R 组——vault 凭据 → 运行时注入的"最后一公里"目前仅有源码级依据、无运行时实证；
2. **文档**：将 4.3 列出的实测行为回写接入指南 §23（已安排），其中 **PATCH 密文整体替换** 对调用方影响最大，建议同时评估是否改为合并语义（若改，用例 C-11 需同步更新）；
3. **分页语义**：`next_cursor` 恒为 null 但字段仍暴露，建议要么实现游标、要么在文档中明确"翻页仅支持 limit/offset"（已按后者回写）；
4. **部署**：生产环境必须持久化 `VAULT_ENCRYPTION_KEY` 并纳入密钥管理，丢失即全部凭据不可解密。
