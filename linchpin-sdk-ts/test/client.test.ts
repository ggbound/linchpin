import { describe, expect, it, vi } from "vitest";

import { LinchpinClient } from "../src/client.js";
import {
  APIError,
  NetworkError,
  NotFoundError,
  ServerError,
} from "../src/errors.js";
import type { SessionStreamEvent } from "../src/types.js";

const BASE = "http://localhost:8000";
const SESSION = {
  id: "s-1",
  agent_id: "a-1",
  agent_version: 1,
  environment_id: "e-1",
  status: "running",
};

type Handler = (url: URL, init: RequestInit) => Promise<Response>;

function makeClient(handler: Handler) {
  const fetchMock = vi.fn(handler);
  const client = new LinchpinClient({
    baseUrl: BASE,
    apiKey: "test-key",
    fetch: fetchMock as unknown as typeof fetch,
  });
  return { client, fetchMock };
}

const encoder = new TextEncoder();

function sseResponse(frames: string[]): Response {
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const frame of frames) controller.enqueue(encoder.encode(frame));
      controller.close();
    },
  });
  return new Response(body, {
    status: 200,
    headers: { "content-type": "text/event-stream" },
  });
}

function ndjsonResponse(lines: string[]): Response {
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const line of lines) controller.enqueue(encoder.encode(line + "\n"));
      controller.close();
    },
  });
  return new Response(body, { status: 200 });
}

describe("LinchpinClient transport", () => {
  it("sends the bearer token and a JSON body on create", async () => {
    const { client, fetchMock } = makeClient(async () =>
      Response.json(SESSION, { status: 201 }),
    );

    const session = await client.sessions.create({
      agent_id: "a-1",
      environment_id: "e-1",
      title: "hello",
      metadata: { k: "v" },
    });

    expect(session.id).toBe("s-1");
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toBe(`${BASE}/v1/sessions`);
    expect(init.method).toBe("POST");
    const headers = init.headers as Record<string, string>;
    expect(headers.Authorization).toBe("Bearer test-key");
    expect(headers["Content-Type"]).toBe("application/json");
    expect(JSON.parse(init.body as string)).toEqual({
      agent_id: "a-1",
      environment_id: "e-1",
      title: "hello",
      metadata: { k: "v" },
    });
  });

  it("normalizes a trailing slash on baseUrl", async () => {
    const { client, fetchMock } = makeClient(async () => Response.json(SESSION));
    await client.sessions.get("s-1");
    expect(String(fetchMock.mock.calls[0][0])).toBe(`${BASE}/v1/sessions/s-1`);
  });

  it("serializes list query params and omits undefined/null", async () => {
    const { client, fetchMock } = makeClient(async () =>
      Response.json({ data: [], next_cursor: null, has_more: false }),
    );

    await client.sessions.list({ limit: 10, offset: 5 });
    expect(String(fetchMock.mock.calls[0][0])).toBe(`${BASE}/v1/sessions?limit=10&offset=5`);

    await client.usage.get({ from: "2026-01-01", to: undefined, group_by: "day" });
    expect(String(fetchMock.mock.calls[1][0])).toBe(`${BASE}/v1/usage?from=2026-01-01&group_by=day`);
  });

  it("returns undefined for an empty body", async () => {
    const { client } = makeClient(async () => new Response(null, { status: 204 }));
    const result = await client.request<unknown>("GET", "/v1/nothing");
    expect(result).toBeUndefined();
  });

  it("throws typed errors from the unified envelope", async () => {
    const { client } = makeClient(async () =>
      Response.json(
        { error: "not_found", message: "Session x not found", detail: null },
        { status: 404 },
      ),
    );
    await expect(client.sessions.get("x")).rejects.toMatchObject({
      name: "NotFoundError",
      status: 404,
      code: "not_found",
      message: "Session x not found",
    });
  });

  it("falls back to APIError/ServerError for non-JSON error bodies", async () => {
    const { client } = makeClient(async () => new Response("boom", { status: 500 }));
    await expect(client.sessions.get("x")).rejects.toBeInstanceOf(ServerError);
    await expect(client.sessions.get("x")).rejects.toMatchObject({ message: "boom" });

    const { client: client2 } = makeClient(async () => new Response("weird", { status: 418 }));
    await expect(client2.sessions.get("x")).rejects.toBeInstanceOf(APIError);
  });

  it("wraps transport failures in NetworkError", async () => {
    const { client } = makeClient(async () => {
      throw new TypeError("fetch failed");
    });
    await expect(client.sessions.get("x")).rejects.toBeInstanceOf(NetworkError);
  });

  it("rejects a client with missing baseUrl or apiKey", () => {
    expect(() => new LinchpinClient({ baseUrl: "", apiKey: "k" })).toThrow(TypeError);
    expect(() => new LinchpinClient({ baseUrl: BASE, apiKey: "" })).toThrow(TypeError);
  });
});

describe("sessions resource", () => {
  it("maps get/update/delete/archive onto the right verbs", async () => {
    const { client, fetchMock } = makeClient(async () => Response.json(SESSION));

    await client.sessions.get("s-1");
    await client.sessions.update("s-1", { title: "new" });
    await client.sessions.delete("s-1");
    await client.sessions.archive("s-1");

    const calls = fetchMock.mock.calls.map(([url, init]) => [
      String(url),
      (init as RequestInit).method,
    ]);
    expect(calls).toEqual([
      [`${BASE}/v1/sessions/s-1`, "GET"],
      [`${BASE}/v1/sessions/s-1`, "POST"],
      [`${BASE}/v1/sessions/s-1`, "DELETE"],
      [`${BASE}/v1/sessions/s-1/archive`, "POST"],
    ]);
  });

  it("posts event batches inside an {events} envelope", async () => {
    const { client, fetchMock } = makeClient(async () => Response.json([]));

    await client.sessions.postEvents("s-1", [
      { type: "user.message", payload: { text: "hi" } },
    ]);

    const [, init] = fetchMock.mock.calls[0];
    expect(String(fetchMock.mock.calls[0][0])).toBe(`${BASE}/v1/sessions/s-1/events`);
    expect(JSON.parse(init.body as string)).toEqual({
      events: [{ type: "user.message", payload: { text: "hi" } }],
    });
  });

  it("spawns threads under /threads", async () => {
    const { client, fetchMock } = makeClient(async () =>
      Response.json({ ...SESSION, parent_session_id: "p-1" }, { status: 201 }),
    );

    await client.sessions.spawnThread("p-1", {
      agent_id: "worker",
      input_message: "go dig",
    });

    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toBe(`${BASE}/v1/sessions/p-1/threads`);
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toEqual({
      agent_id: "worker",
      input_message: "go dig",
    });
  });
});

describe("other resources", () => {
  it("creates, pauses, updates and deletes deployments", async () => {
    const { client, fetchMock } = makeClient(async () => Response.json({ id: "d-1" }));

    await client.deployments.create({ agent_id: "a", environment_id: "e", cron: "0 9 * * *" });
    await client.deployments.pause("d-1");
    await client.deployments.update("d-1", { cron: "30 9 * * *" });
    await client.deployments.delete("d-1");

    const calls = fetchMock.mock.calls.map(([url, init]) => [
      String(url),
      (init as RequestInit).method,
    ]);
    expect(calls).toEqual([
      [`${BASE}/v1/deployments`, "POST"],
      [`${BASE}/v1/deployments/d-1/pause`, "POST"],
      [`${BASE}/v1/deployments/d-1`, "PATCH"],
      [`${BASE}/v1/deployments/d-1`, "DELETE"],
    ]);
  });

  it("fetches artifact metadata and raw content", async () => {
    const { client, fetchMock } = makeClient(async (url) => {
      if (String(url).endsWith("/content")) return new Response("file-contents");
      return Response.json({ id: "f-1", path: "/tmp/out.txt" });
    });

    const artifact = await client.artifacts.get("f-1");
    const content = await client.artifacts.content("f-1");

    expect(artifact.id).toBe("f-1");
    expect(content).toBe("file-contents");
    expect(String(fetchMock.mock.calls[1][0])).toBe(`${BASE}/v1/artifacts/f-1/content`);
  });

  it("streams the audit log as parsed NDJSON records", async () => {
    const { client } = makeClient(async () =>
      ndjsonResponse([
        '{"action":"session.create","resource_type":"session"}',
        '{"action":"session.archive","resource_type":"session"}',
      ]),
    );

    const actions: string[] = [];
    for await (const entry of client.audit.export({ resource_type: "session" })) {
      actions.push(entry.action as string);
    }
    expect(actions).toEqual(["session.create", "session.archive"]);
  });
});

describe("SSE streaming", () => {
  it("parses stream frames and sends Last-Event-ID for resume", async () => {
    const { client, fetchMock } = makeClient(async () =>
      sseResponse([
        'id: 1\nevent: session.message\ndata: {"seq":1,"type":"session.message","payload":{"text":"hi"}}\n\n',
        'id: 2\nevent: session.status\ndata: {"seq":2,"type":"session.status"}\n\n',
      ]),
    );

    const events: SessionStreamEvent[] = [];
    for await (const event of client.sessions.stream("s-1", { lastEventId: "0" })) {
      events.push(event);
    }

    expect(events).toHaveLength(2);
    expect(events[0].id).toBe("1");
    expect(events[0].event).toBe("session.message");
    expect(events[0].payload?.seq).toBe(1);
    expect(events[1].payload?.type).toBe("session.status");

    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toBe(`${BASE}/v1/sessions/s-1/stream`);
    expect((init.headers as Record<string, string>)["Last-Event-ID"]).toBe("0");
  });

  it("passes cursor as a query param when no lastEventId is given", async () => {
    const { client, fetchMock } = makeClient(async () => sseResponse([]));

    const events: SessionStreamEvent[] = [];
    for await (const event of client.sessions.stream("s-1", { cursor: "c-1" })) {
      events.push(event);
    }

    expect(events).toHaveLength(0);
    expect(String(fetchMock.mock.calls[0][0])).toBe(`${BASE}/v1/sessions/s-1/stream?cursor=c-1`);
  });

  it("throws NotFoundError when the stream endpoint 404s", async () => {
    const { client } = makeClient(async () =>
      Response.json({ error: "not_found", message: "nope" }, { status: 404 }),
    );
    await expect(client.sessions.stream("missing").next()).rejects.toBeInstanceOf(NotFoundError);
  });
});
