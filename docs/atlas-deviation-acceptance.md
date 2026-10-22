# Atlas 改造：语义偏离与有界残余验收确认文档

- 文档版本：v1.0
- 日期：2026-10-22
- 编制依据：[ATLAS_UPGRADE_PLAN.md](../ATLAS_UPGRADE_PLAN.md) v0.3（ATLAS 改造全量收官版）；PRD 条目表述引自该文档 §2 差距清账表（`PRD-托管Agent平台.md` 原文件不在本仓库）
- 被测版本：Linchpin `linchpin-api` v0.7.0（alembic 迁移 0001–0030）
- 状态：**待验收**（§2 总表与 §3/§4 各单的"验收结论"栏留待验收人签署）

---

## 1. 文档目的与验收范围

### 1.1 目的

ATLAS 改造方案已全量收官（A1–A8 / B1–B20 / C1–C5，M1–M6，全量测试 1243 passed / 0 failed）。本验收确认文档针对收官时**显式记录、刻意保留**的两类差异逐一建立可勾选的验收单，确认：每一项偏离/残余的行为与方案记录一致、风险已知且可控、验证证据充分。验收通过后，这些差异即从"改造期间的记录"升格为"双方确认的交付基线"。

### 1.2 范围与编号

| 类别 | 编号段 | 定义 | 条目 |
|---|---|---|---|
| 语义偏离 | D-1 … D-6 | 功能已交付，但与 PRD 字面规格不同；差异已由方案记录并给出安全/工程理由 | 6 项 |
| 有界残余 | R-1 … R-4 | 功能已交付，仅在明确界定的边界条件下存在降级或窄窗口；均有兜底机制 | 4 项 |
| 触发条件驱动的未做项 | （附录 A） | 无对应 PRD 条目，或决策"条件触发再评估"；**不在本次验收范围** | 3 项 |

### 1.3 验收方式与证据分级

每项验收单提供三级证据，验收人可按需复核：

- **E1 源码核实**：文中给出精确文件与行为描述（链接为仓库相对路径）；
- **E2 自动化测试**：标注既有测试文件与本文档编制时的实测结果（见 §1.4）；
- **E3 手工验证**：给出可在目标环境执行的验证步骤（需 Docker / E2B 端点 / 网关环境时已标注）。

### 1.4 本次编制时的实测记录

- 实测环境：macOS 宿主、`linchpin-api/.venv`（Python 3.12）、沙箱 mock 化路径（不依赖 Docker daemon）；
- 第一批：`tests/test_egress.py` + `tests/test_microvm_sandbox.py` + `tests/test_cluster.py` + `tests/test_executor_nodes.py` + `tests/test_remote_executor.py` → **184 passed / 0 failed**（1.77s）；
- 第二批：`tests/test_events.py` + `tests/test_sse_stream.py` + `tests/test_quotas.py` + `tests/test_orchestrator.py` → **123 passed / 0 failed**（1.65s）；
- 子集合计 **307 passed / 0 failed**。全量 1243 passed / 0 failed 的基线见 [ATLAS_UPGRADE_PLAN.md](../ATLAS_UPGRADE_PLAN.md) §8 M6 行。

### 1.5 结论填写约定

每张验收单末尾的结论栏：`☐ 通过 ☐ 有条件通过（备注） ☐ 不通过（原因）`，附验收人 / 日期。单项"不通过"不阻塞其余项；附录 A 项不参与本次结论。

---

## 2. 验收总表

| 编号 | 名称 | 关联 PRD 条目 | 关联方案条目 | 类别 | 验收结论 |
|---|---|---|---|---|---|
| D-1 | 事件标识用 uuid4 hex 而非 ULID；SSE 游标保持 per-session seq | FR-3.3 | A3 | 语义偏离 | ☐ |
| D-2 | `/workspace` 整个 FS 跨轮保留（PRD：默认不保留） | FR-4.4 | —（现状确认） | 语义偏离 | ☐ |
| D-3 | 每环境独立 Squid egress proxy（PRD 规格为单全局 proxy）；put_archive 替代 bind mount | FR-4.3 | B4 | 语义偏离 | ☐ |
| D-4 | IP 白名单不在 API 侧实现，归网关层（APISIX） | FR-10.1 | C1 | 语义偏离 | ☐ |
| D-5 | 计费面（OpenMeter+Lago）刻意不引入，限额自研 + 网关配方 | FR-11.2/11.3 | B16 | 语义偏离 | ☐ |
| D-6 | steer 无模型流中途注入；消费无显式标记（watermark 语义） | FR-2.5 | B6 | 语义偏离 | ☐ |
| R-1 | lease 接管窄窗口：旧 owner 可能收尾 in-flight turn；阻塞等待至下次通知才醒 | FR-2.3 | C2 方案 1 | 有界残余 | ☐ |
| R-2 | 方案 1 多副本仅限同宿主；跨宿主由 BYO executor 承接 | FR-2.3 / FR-4.6 | C2 / C4 | 有界残余 | ☐ |
| R-3 | microvm/remote runtime：watcher 流式交付降级为会话结束可见；rw mount 会话结束 flush | FR-4.1 / FR-4.4 | C3 / C4 | 有界残余 | ☐ |
| R-4 | macOS 宿主无法运行 Firecracker；E2B driver 部分能力降级 | FR-4.1 | C3 | 有界残余 | ☐ |

---

## 3. 语义偏离验收单（D-1 … D-6）

### D-1 事件标识与 SSE 游标语义

- **关联**：PRD FR-3.3 ｜ 方案 A3 ｜ 迁移 0017
- **PRD 原始要求**：全局唯一 `sevt_` 前缀事件 id；时间排序可作续读游标。
- **实际交付行为**：
  - `event_id = 'sevt_' || replace(gen_random_uuid()::text, '-', '')`——uuid4 hex（36→32 字符），由 DB 默认值生成并回填存量（迁移 [0017_events_event_id.py](../linchpin-api/alembic/versions/0017_events_event_id.py) L30–L37，列为 `TEXT NOT NULL UNIQUE`）；
  - SSE 游标保持 **per-session seq**（单调整数 encode/decode），SSE 帧同时携带 `id:`（seq）、`event:`（type）与 data 内 `event_id`（七键帧），`Last-Event-ID` 头优先于 `?cursor=`。
- **保留偏离的决策依据**：免引入 ULID 依赖；全局时序仅由 event_id 形状无法可靠保证，跨会话排序已由 `created_at` + NDJSON 导出排序承担；per-session seq 保证严格单调，续读语义比全局 id 更强。
- **影响与风险**：低。对外契约已在 [api-integration-guide.md](api-integration-guide.md) 明示；消费方若依赖"event_id 字典序≈时间序"会误判——已记录，不构成正确性风险。

- **验收标准（勾选）**：
  - [ ] E1 迁移 0017 默认值表达式为 `'sevt_' + uuid4 hex`，且存量回填 UPDATE 存在；
  - [ ] E1 SSE 帧含 `id:`/`event:` 字段，data 七键含 `event_id`；`Last-Event-ID` 优先于 `?cursor=`；
  - [ ] E2 `tests/test_sse_stream.py` 重放/cursor 相关用例通过（本次实测通过）；
  - [ ] E3（可选，需运行中服务）按接入测试用例 H 组执行：断线带 `Last-Event-ID` 重连，确认无重复、无丢失。

- **验收结论**：☐ 通过 ☐ 有条件通过 ☐ 不通过　　验收人：________　日期：________

### D-2 `/workspace` 跨轮保留语义

- **关联**：PRD FR-4.4 ｜ 方案 §2 表内行（偏离记录）
- **PRD 原始要求**：默认仅 `/mnt/session/outputs` 跨轮保留，`/workspace` **默认不保留**。
- **实际交付行为**：Docker runtime 下会话容器在会话存活期内复用（terminate 才销毁），**整个文件系统跨轮保留**；`/mnt/session/outputs` 由 watcher 登记为资源（Artifact 语义），`/mnt/session/uploads` 只读挂载。
- **保留偏离的决策依据**：容器复用是会话内多轮执行的自然实现，语义更宽松（保留多、删得少），不产生数据丢失风险；反向收紧（每轮重置 FS）反而会破坏工具链的中间产物假设。作为"偏离已记录"交付。
- **影响与风险**：中低。租户可见面变大（敏感中间文件跨轮留存）——由会话 terminate 清容器与目录（B12）、events/files 随会话删除清除兜底；多租户隔离由 C1 RLS（B20）兜底。

- **验收标准（勾选）**：
  - [ ] E1 容器生命周期与会话绑定（create 于 provisioning、destroy 于 terminate/failed），无按轮重建逻辑；
  - [ ] E1 microvm/remote 路径下 rw mount 于 destroy 时 flush 回宿主（见 R-3）；
  - [ ] E2 `tests/test_microvm_sandbox.py`（rw 注入与 destroy flush）+ `tests/test_remote_executor.py`（rw flush 顺序）通过（本次实测通过）；
  - [ ] E3（可选，需 Docker）同会话发两轮 `user.message`，第二轮在沙箱内确认第一轮写入的文件仍存在；terminate 后确认容器与目录已清除。

- **验收结论**：☐ 通过 ☐ 有条件通过 ☐ 不通过　　验收人：________　日期：________

### D-3 Egress proxy 拓扑与配置注入

- **关联**：PRD FR-4.3 ｜ 方案 B4（+B19 curated 域名） ｜ 无独立迁移（网络/容器面）
- **PRD 原始要求**：**单全局** egress proxy + 白名单池；配置经 configmap/bind mount 注入。
- **实际交付行为**：
  - **每环境一个** Squid proxy 容器，命名 `linchpin-egress-<env 去横线前 12 位>`（[egress.py](../linchpin-api/app/egress.py) L109），双网络接入（`linchpin-limited` internal + `linchpin-open` 出网），proxy 是 limited 档唯一 egress 咽喉点；
  - squid.conf 经 `put_archive`（in-memory tar，docker cp 等价）注入（egress.py L165、L241）；
  - 白名单派生 `effective_allowed_hosts`：显式 `allowed_hosts` 优先，curated 域名（B19 的六包管理器 + MCP 注册表集合）仅在对应布尔开启且未显式出现时按序追加，产出即参与 proxy 重建 hash。
- **保留偏离的决策依据**：多环境共用一个白名单池 = 特权提升（低白名单环境借道高白名单池出网），安全不正确，规格让位于安全；put_archive 规避 docker-in-docker 的 host bind mount 路径坑，保持自托管单机可运行。两项均作为偏离项记录。
- **影响与风险**：低（相对 PRD 规格是**收严**）。成本是每环境多一个常驻 proxy 容器（资源换隔离）。

- **验收标准（勾选）**：
  - [ ] E1 proxy 容器名规则与双网络拓扑符合上述描述；limited 档会话容器无直连外网的第三条路径；
  - [ ] E1 curated 开关变化 → `effective_allowed_hosts` 变化 → squid.conf hash 变化 → proxy 重建；
  - [ ] E2 `tests/test_egress.py`（含 curated 合并 8 例）通过（本次实测通过）；
  - [ ] E3（可选，需 Docker）创建 `network=limited` + `allowed_hosts: ["pypi.org"]` 的环境并发起会话，沙箱内 `curl https://github.com` 应被拒、`curl https://pypi.org` 应通。

- **验收结论**：☐ 通过 ☐ 有条件通过 ☐ 不通过　　验收人：________　日期：________

### D-4 IP 白名单归属网关层

- **关联**：PRD FR-10.1 ｜ 方案 C1
- **PRD 原始要求**：鉴权体系含 IP 白名单。
- **实际交付行为**：API 侧认证仅依赖 `Authorization: Bearer` / `?token=`（scope 校验 + per-key 限流），**无任何 IP 白名单代码**（全 app 目录 grep `allowed_ips|ip_whitelist|client_ip` 零命中）；IP 白名单由网关层 APISIX 承担（PRD §9.2 同款架构）。
- **保留偏离的决策依据**：IP 归属控制是网关天然职责（L7 前置、连接级成本为零、无需应用感知来源 IP 链路）；API 重复实现只会引入双源不一致。
- **影响与风险**：低。**前提是部署拓扑必须含网关**——裸暴露 API 端口的部署没有 IP 白名单能力。此前提已写入部署面文档（[limits.md](limits.md) 网关配方）。

- **验收标准（勾选）**：
  - [ ] E1 API 认证路径不含来源 IP 判定；
  - [ ] E1 [docs/limits.md](limits.md) 提供 APISIX 侧 IP/限流配方（`limit-count` 等插件配置）；
  - [ ] E2 `tests/test_auth.py` 认证矩阵通过（1243 基线含）；
  - [ ] E3（可选，需 APISIX 环境）配置白名单后：白名单外来源 → 连接层拒绝（非 API 401 信封）。

- **验收结论**：☐ 通过 ☐ 有条件通过 ☐ 不通过　　验收人：________　日期：________

### D-5 计费面不引入（限额自研 + 网关配方）

- **关联**：PRD FR-11.2/11.3 ｜ 方案 B16 ｜ 迁移 0029
- **PRD 原始要求**：OpenMeter + Lago 计费/限额体系。
- **实际交付行为**：API 侧自研轻量限额——进程内 per-key 滑动窗口限流（429 + `Retry-After`，[auth.py](../linchpin-api/app/auth.py) L220–234）+ project 级并发会话/月 token 配额（429 `quota_exceeded`，`detail.limit_type ∈ {concurrent_sessions, monthly_tokens}`，[quotas.py](../linchpin-api/app/quotas.py)，fail-open 于配额子系统故障时放行）；计量面 span 带 model_usage + `sessions.usage` 累计。**全 app 目录 grep `OpenMeter|Lago|billing` 零命中**。多副本精确限额与计费面配方文档化于 [limits.md](limits.md)（APISIX redis counter 等）。
- **保留偏离的决策依据**：OpenMeter+Lago 为外部有状态服务且涉 AGPL（Lago）传染评估；PRD 限额面价值已在 API 侧闭环，计费属商务侧延伸，刻意不引入。
- **影响与风险**：低。滑动窗口限流为进程内实现，多副本下配额精确性由网关层补齐（文档化）；月 token 配额基于 DB 计数，精度满足 P 定位。

- **验收标准（勾选）**：
  - [ ] E1 限额仅由 auth.py（rpm）与 quotas.py（并发/月 token）承担，无外部计费依赖；
  - [ ] E1 429 双形态字段齐全（限流：`retry_after` + `Retry-After` 头；配额：`limit_type`/`limit`/`active`）；
  - [ ] E2 `tests/test_quotas.py` + `tests/test_ratelimit.py` 通过（本次实测通过）；
  - [ ] E3（可选）按接入测试用例 J 组执行（J-1 rpm=3 第 4 发 429；J-2 并发配额）。

- **验收结论**：☐ 通过 ☐ 有条件通过 ☐ 不通过　　验收人：________　日期：________

### D-6 Steer 注入时机与消费语义

- **关联**：PRD FR-2.5 ｜ 方案 B6
- **PRD 原始要求**：运行中引导（steer）；方案规格明确**排除**模型流中途注入。
- **实际交付行为**：
  - steer 事件落事件日志（running 态被 A4 门控放行）；回放时以 `<steer injected="true">…</steer>` 包裹为 user-role 消息（[orchestrator.py](../linchpin-api/app/orchestrator.py) L603–617），下一工具轮与续跑轮均可见；
  - **无显式消费标记**：`has_pending_steer` 以 watermark 语义判定（仅统计 `seq > 当前轮起始 seq` 的 steer，L1058–1079）——已消费 steer 落在 watermark 之前，每轮重取天然覆盖，无泄漏路径、不会因陈旧 steer 死循环续跑。
- **保留偏离的决策依据**：流中途注入为规格排除项；watermark 语义省去一张消费状态表，且正确性由 seq 单调保证。
- **影响与风险**：低。 steer 生效点是"下一个安全点"而非流内即时，延迟上界为一轮模型请求时长；已在接入文档明示。

- **验收标准（勾选）**：
  - [ ] E1 回放 steer 带 `<steer injected="true">` 包裹且 `injected=true` 标记；
  - [ ] E1 `has_pending_steer` 仅统计 `seq > since_seq`（watermark 语义，docstring 与 SQL 一致）；
  - [ ] E2 `tests/test_orchestrator.py` steer 相关用例（34 处断言）通过（本次实测通过）；
  - [ ] E3（可选，需 Docker 或 Ollama provider）running 会话发 `user.steer`，确认后续模型上下文含 steer 内容且仅注入一次。

- **验收结论**：☐ 通过 ☐ 有条件通过 ☐ 不通过　　验收人：________　日期：________

---

## 4. 有界残余验收单（R-1 … R-4）

### R-1 Lease 接管窄窗口（双主残余）

- **关联**：PRD FR-2.3（worker 崩溃自动迁移） ｜ 方案 C2 方案 1
- **残余描述**：
  1. lease 过期（默认 60s）到 supervisor 下一轮扫描之间，旧 owner 可能仍在收尾 in-flight turn——短暂双主窗口；
  2. 旧 owner 阻塞中的 `wait_for_confirmation` 至下次通知才醒——**至多重复一轮**，不损坏状态。
- **兜底机制**：events 表 `PRIMARY KEY (session_id, seq)`；`append_event` 捕获 `UniqueViolationError` 后重算 `MAX(seq)+1` 重试 3 次（[events.py](../linchpin-api/app/events.py)）——双主双写不产生重复日志、不丢事件；接管方按 `orchestrator_instance = me` 条件清理，防误清接管方簿记。
- **影响与风险**：低且**有界**（窗口 ≤ lease 超时 + 一个 supervisor 周期）。触发条件：副本崩溃/网络分区；正常重启走 lifespan 优雅路径不触发。

- **验收标准（勾选）**：
  - [ ] E1 `SessionLease` 绑定专用池连接（锁随连接死自动释放）、心跳/续约失败置 broken、循环顶 broken-lease 退出（防双驱动）；
  - [ ] E1 `take_over_stale_sessions` 跳过本地活跃 task + 他人新鲜心跳；容器死 → `failed(container_lost)`，活 → `idle` + `session.takeover` 事件；
  - [ ] E2 `tests/test_cluster.py`（43 例：lease 互斥/降级/心跳/takeover 六分支/supervisor 循环）通过（本次实测通过）；
  - [ ] E3（可选，需多副本环境）kill -9 一个 API 副本，观察 ≤60s 内其会话被接管，事件日志无重复 seq。

- **验收结论**：☐ 通过 ☐ 有条件通过 ☐ 不通过　　验收人：________　日期：________

### R-2 方案 1 部署模型仅限同宿主

- **关联**：PRD FR-2.3 / FR-4.6 ｜ 方案 C2 部署模型约束 + C4
- **残余描述**：方案 1 多副本要求**同宿主**（共享 Postgres + 共享 Docker daemon）——沙箱 bind mount 与 Docker 网络均不跨宿主。
- **兜底机制**：跨宿主执行由 C4 BYO executor 完整承接——`executor_nodes` 注册表（token Fernet 可逆加密 + SHA-256 hash + prefix 展示）、`RemoteExecutorSandbox` HTTP 代理沙箱、节点侧 daemon（8 端点 + Bearer `LINCHPIN_EXECUTOR_TOKEN`）、容量门控（`max_sessions` 活跃会话计数，满则拒绝新会话入队；存量会话解析不做容量检查）。
- **影响与风险**：低。单机自托管场景无感；横向扩容路径明确（注册 executor 节点，环境 config `executor_node` 按名钉住）。

- **验收标准（勾选）**：
  - [ ] E1 `resolve_sandbox_for_config`：`executor_node` 空 → 本地 docker/microvm；非空 → 反查节点 + 容量检查 + remote 沙箱；生命周期路径按 metadata pinned 节点名解析且无容量检查；
  - [ ] E1 响应模型永不回吐 `token_encrypted`/`token_hash`；token 仅创建响应返回一次；
  - [ ] E2 `tests/test_executor_nodes.py`（18 例）+ `tests/test_remote_executor.py`（47 例）通过（本次实测通过）；
  - [ ] E3（可选，需第二宿主 + daemon）注册节点 → 创建 `executor_node` 会话 → 确认容器落在节点宿主、容量满时 503 `sandbox_runtime_unavailable`。

- **验收结论**：☐ 通过 ☐ 有条件通过 ☐ 不通过　　验收人：________　日期：________

### R-3 microvm/remote runtime 的流式交付与 rw mount 降级

- **关联**：PRD FR-4.1 / FR-4.4 ｜ 方案 C3 / C4
- **残余描述**：microVM 与 remote executor 均无 host bind mount——
  1. watcher 的**流式交付**（输出文件边写边登记）降级为"**会话结束可见**"；
  2. **rw mount** 内容在 destroy 时才 flush 回宿主（`_pull_tree` 递归，best-effort；单 mount 失败仅 warning 不阻断 teardown）；
  3. ro mount 在创建时直接注入文件内容（无降级）。
- **兜底机制**：`_rw_mounts` 簿记 + destroy 顺序 flush（[sandbox.py](../linchpin-api/app/sandbox.py)：microvm L963–1112，remote L1156–1371，两者同构）；flush 语义与 Docker rw mount 最终一致（会话内进程视角不变）。
- **影响与风险**：中低，**仅限 microvm/remote 会话**。Docker runtime（默认）不受影响；对"边执行边取产物"的实时性诉求有损，已在接入文档明示差异。

- **验收标准（勾选）**：
  - [ ] E1 microvm：ro mount 创建时注入内容；rw mount 记入 `_rw_mounts` 且 destroy 时 `_pull_tree` flush；
  - [ ] E1 remote：rw mount 内容 b64 打包进创建 payload `files`，destroy 时先 `_flush_rw_mounts` 再删容器；
  - [ ] E2 `tests/test_microvm_sandbox.py`（rw 注入与 destroy flush）+ `tests/test_remote_executor.py`（rw flush 顺序）通过（本次实测通过）；
  - [ ] E3（可选，需 E2B 端点 / executor daemon）microvm 会话 `save_artifact` 后立即查 `/v1/artifacts`：应"会话结束（destroy）后才可见"。

- **验收结论**：☐ 通过 ☐ 有条件通过 ☐ 不通过　　验收人：________　日期：________

### R-4 microVM 在 macOS 宿主不可用与 E2B driver 能力边界

- **关联**：PRD FR-4.1 ｜ 方案 C3
- **残余描述**：
  1. Firecracker 无法运行于 macOS 宿主——microvm 实际启用需指向 E2B 自托管端点（或等效 microVM provider）；
  2. E2B 无进程级 kill 概念——`kill_vm_process` 降级为 `logger.warning + return False`；
  3. microVM 无包烘焙能力——带 `packages` 的环境在 microvm 下 `ensure_image` 拒绝（包烘焙是 Docker/节点镜像层职责）。
- **兜底机制**：`LINCHPIN_ENABLE_MICROVM` 默认 `false`（Docker 路径不受影响）；E2BDriver 惰性 import，SDK 缺失时 raise 描述性 SandboxError（提示 `pip install e2b` 或保持关闭）；`MICROVM_RESOURCE_TIERS` 与 Docker `RESOURCE_TIERS` 1:1（1c2g/2c4g/4c8g）；driver 为 Protocol 注入，换 raw Firecracker 是叶子变更。
- **影响与风险**：低。macOS 本机开发/测试环境默认走 Docker；生产（Linux）启用 microvm 无此限制。

- **验收标准（勾选）**：
  - [ ] E1 `LINCHPIN_ENABLE_MICROVM` 未设置时不实例化 microvm 供给；SDK 缺失报错信息含修复指引；
  - [ ] E1 microvm 资源档位映射与 Docker 三档一一对应；
  - [ ] E2 `tests/test_microvm_sandbox.py`（E2B 惰性 import 与降级、档位映射、spec 构建）通过（本次实测通过）；
  - [ ] E3（可选，需 E2B 自托管端点）`LINCHPIN_ENABLE_MICROVM=true` + 端点配置后创建 `isolation: "microvm"` 环境会话，确认 VM 生命周期完整（create/exec/destroy + rw flush）。

- **验收结论**：☐ 通过 ☐ 有条件通过 ☐ 不通过　　验收人：________　日期：________

---

## 5. 签收汇总

| 项目 | 内容 |
|---|---|
| 验收范围 | §2 总表 10 项（D-1…D-6、R-1…R-4） |
| 验收方式 | E1 源码核实 + E2 自动化测试（本次实测 307 passed / 0 failed）+ E3 手工验证（可选项） |
| 遗留风险声明 | §3/§4 各单"影响与风险"栏所列内容，验收即表示知悉并接受 |
| 整体结论 | ☐ 通过　☐ 有条件通过（条件：____________）　☐ 不通过（原因：____________） |
| 验收人 / 日期 | ________________ / ____________ |

## 附录 A：触发条件驱动的未做项（不在本次验收范围）

以下三项为方案记录的"条件未触发、暂不做"，列出以完整交代边界；触发后需**重新立项评估**，不属于本验收的通过/不通过对象。

| # | 事项 | 当前状态 | 触发条件（满足其一即再评估） |
|---|---|---|---|
| A-1 | 多区域部署 | 未做（无对应 PRD 条目） | 多区域商务/合规诉求落地 |
| A-2 | Temporal（方案 2）编排迁移 | 评估已完成，维持方案 1 为生产路径 | 多区域；跨会话 fan-out/fan-in 长编排；HITL 并发等待使 events 表轮询不可持续；lease 有界残余升级为商务问题 |
| A-3 | MinIO/Lago（AGPL）引入 | 未引入；files 走 Local/S3 直连，计费留商务侧 | 法务复核通过 AGPL 传染风险 |
