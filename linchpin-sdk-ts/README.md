# linchpin-sdk

Zero-dependency TypeScript SDK for the [Linchpin](../README.md) self-hosted AI
agent runtime (ATLAS FR-12.1).

- **No runtime dependencies** — built on the global `fetch` (Node >= 18, all
  modern browsers and workers).
- **Typed resources** — sessions, threads, deployments, artifacts, usage and
  audit, mirroring the FastAPI models in `linchpin-api/app/models.py`.
- **SSE streaming** — WHATWG-compliant incremental parser with resume support
  via `Last-Event-ID` (Atlas A3).
- **Typed errors** — the unified error envelope (`{error, message, detail}`)
  maps onto a `LinchpinError` hierarchy (`NotFoundError`, `ConflictError`,
  `ValidationError`, …).

## Install

```bash
npm install linchpin-sdk
```

For local development against a source checkout:

```bash
cd linchpin-sdk-ts
npm install
npm test        # vitest
npm run build   # emits dist/
```

## Quick start

```ts
import { LinchpinClient } from "linchpin-sdk";

const client = new LinchpinClient({
  baseUrl: "https://linchpin.example.com",
  apiKey: process.env.LINCHPIN_API_KEY!,
});

// Create a session
const session = await client.sessions.create({
  agent_id: "<agent-uuid>",
  environment_id: "<environment-uuid>",
  title: "My first session",
});

// Post a user message and read events back
await client.sessions.postEvents(session.id, [
  { type: "user.message", payload: { text: "Hello!" } },
]);

const page = await client.sessions.listEvents(session.id, { limit: 50 });
console.log(page.events, page.next_cursor);
```

## Streaming (SSE)

`GET /v1/sessions/{id}/stream` emits `id:` / `event:` / `data:` frames whose
data is the envelope `{session_id, type, cursor, seq, event_id, payload,
processed_at}`. The generator decodes the JSON for you and keeps the raw
frame:

```ts
let lastId: string | undefined;

for await (const frame of client.sessions.stream(session.id)) {
  console.log(frame.event, frame.payload);
  lastId = frame.id; // remember for resume
}
```

To resume exactly where a previous stream stopped, pass the last seen frame
id — the server prioritizes it over `cursor`:

```ts
for await (const frame of client.sessions.stream(session.id, { lastEventId })) {
  // replays everything after lastId, then goes live
}
```

`client.stream(path)` is a generic SSE consumer for any streaming endpoint.

## Threads (subagent orchestration, FR-6.1)

An agent that declares `multi_agents` gets a `spawn_subagent` tool. The same
server capability is available programmatically:

```ts
const worker = await client.sessions.spawnThread(parentSession.id, {
  agent_id: "<worker-agent-uuid>",
  input_message: "Research the Q3 numbers",
  metadata: { subagent_name: "researcher" },
});
console.log(worker.id, worker.status);
```

## Deployments, artifacts, usage, audit

```ts
await client.deployments.create({ agent_id, environment_id, cron: "0 9 * * *" });
await client.deployments.pause(deploymentId);

const artifacts = await client.artifacts.list({ session_id: session.id });
const content = await client.artifacts.content(artifactId); // raw text

const usage = await client.usage.get({ group_by: "day", from: "2026-01-01" });

for await (const entry of client.audit.export({ resource_type: "session" })) {
  console.log(entry.action, entry.at);
}
```

`audit.export` streams the NDJSON export (`GET /v1/audit`) as parsed records.

## Error handling

```ts
import { ConflictError, NotFoundError, LinchpinClient, LinchpinError } from "linchpin-sdk";

try {
  await client.sessions.get("missing-id");
} catch (error) {
  if (error instanceof NotFoundError) {
    error.status;  // 404
    error.code;    // "not_found"
    error.message; // "Session missing-id not found"
    error.detail;  // extra fields, usually null
  } else if (error instanceof LinchpinError) {
    // any other API error — status/code/detail always present
  }
  // NetworkError wraps transport failures with status 0
}
```

## API coverage

| Resource | Methods |
| --- | --- |
| `client.sessions` | `create`, `get`, `list`, `update`, `delete`, `archive`, `listEvents`, `postEvents`, `listThreads`, `spawnThread`, `stream` |
| `client.deployments` | `create`, `get`, `list`, `update`, `pause`, `resume`, `delete` |
| `client.artifacts` | `list`, `get`, `content` |
| `client.usage` | `get` |
| `client.audit` | `export` (NDJSON stream) |

Requirements: Node >= 18 (or any runtime with `fetch`, `ReadableStream` and
`TextDecoder`). Zero runtime dependencies; `typescript` and `vitest` are
dev-only.
