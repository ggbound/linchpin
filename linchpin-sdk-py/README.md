# linchpin-sdk (Python)

Linchpin API 的零依赖 Python 客户端（FR-12.1）——与 [`linchpin-sdk-ts/`](../linchpin-sdk-ts) 语义对齐的同步版本。

## 特性

- **零运行时依赖**：仅用 stdlib（`urllib.request` + `json` + `codecs`）。`typing_extensions` 仅在 Python 3.10 上作为类型支持引入。
- **同步客户端**：`LinchpinClient` 暴露 `sessions` / `deployments` / `artifacts` / `usage` / `audit` 五个资源。
- **SSE + NDJSON 流式**：WHATWG server-sent-events 解析器（`id`/`event`/`data`、`\r\n`/`\r`/`\n` 终止符、`: ` 注释、多行 data、BOM、跨 chunk 拆分）与 NDJSON 记录流。
- **类型化错误层级**：`AuthenticationError` / `QuotaExceededError` / `PermissionDeniedError` / `NotFoundError` / `ConflictError` / `ValidationError` / `RateLimitedError` / `ServerError` / `APIError` / `NetworkError`，统一信封（`{"error", "message", "detail"}`）与 FastAPI legacy `detail` 形态均被容忍。
- **transport 可注入**：默认 stdlib urllib；测试或自定义 HTTP 栈可传入 `(method, url, headers, body, timeout) -> HttpResponse`。

## 安装

```bash
pip install -e ./linchpin-sdk-py
```

要求 Python >= 3.10。

## 快速开始

```python
from linchpin_sdk import LinchpinClient

client = LinchpinClient(base_url="http://localhost:8000", api_key="lp_...")

# 会话
session = client.sessions.create({"agent_id": "my-agent", "environment_id": "default"})
session = client.sessions.get(session["id"])
client.sessions.update(session["id"], {"title": "hello"})

# 事件
client.sessions.post_events(session["id"], [{"type": "user.message", "payload": {"text": "hi"}}])
events = client.sessions.list_events(session["id"], {"after_cursor": "c-1", "limit": 50})

# 子代理线程（FR-6.1）
thread = client.sessions.spawn_thread(session["id"], {"agent_id": "worker", "input_message": "go dig"})

# 流式（SSE；断线用 last_event_id 恢复，服务端优先于 cursor）
for frame in client.sessions.stream(session["id"], {"last_event_id": "42"}):
    print(frame.id, frame.event, frame.payload)

# Deployments / Artifacts / Usage / Audit
dep = client.deployments.create({"agent_id": "a", "environment_id": "e", "cron": "0 9 * * *"})
usage = client.usage.get({"group_by": "day", "from_": "2026-01-01"})
for entry in client.audit.export({"resource_type": "session"}):
    print(entry["action"])
```

## 错误处理

```python
from linchpin_sdk import LinchpinClient, NotFoundError, RateLimitedError, NetworkError

try:
    client.sessions.get("missing")
except NotFoundError as exc:      # HTTP 404
    print(exc.status, exc.code, exc.message, exc.detail)
except RateLimitedError:          # HTTP 429
    ...
except NetworkError as exc:       # 请求未达服务端（status == 0）
    print(exc.__cause__)
```

## Python 3.10+ 命名差异

- `from` 是保留字：`UsageParams.from_` / `AuditParams.from_` 会作为 `from` query 参数发送。
- 布尔 query 值序列化为 `"true"` / `"false"`（与 TS `String(true)` 一致），`None` 被省略。

## 测试

```bash
python -m pytest
```

80 例全绿：SSE 解析器（含跨 chunk CRLF、UTF-8 多字节拆分）、错误映射、全资源方法面与流式路径——全部走 fake transport，无真实网络。
