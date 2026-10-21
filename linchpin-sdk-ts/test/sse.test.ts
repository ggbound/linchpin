import { describe, expect, it } from "vitest";

import { SseParser, streamNdjson, streamSse } from "../src/sse.js";

/** Shorthand: feed chunks sequentially, collect all frames. */
function parse(...chunks: string[]) {
  const parser = new SseParser();
  const events = chunks.flatMap((chunk) => parser.push(chunk));
  return [...events, ...parser.flush()];
}

describe("SseParser", () => {
  it("parses a basic frame with id/event/data", () => {
    const events = parse("id: 7\nevent: session.message\ndata: {\"seq\":7}\n\n");
    expect(events).toEqual([
      { id: "7", event: "session.message", data: '{"seq":7}' },
    ]);
  });

  it("handles CRLF terminators", () => {
    const events = parse("id: 1\r\nevent: ping\r\ndata: hi\r\n\r\n");
    expect(events).toEqual([{ id: "1", event: "ping", data: "hi" }]);
  });

  it("handles bare-CR terminators", () => {
    const events = parse("event: a\rdata: x\r\r");
    expect(events).toEqual([{ id: undefined, event: "a", data: "x" }]);
  });

  it("joins multi-line data with newline", () => {
    const events = parse("data: line1\ndata: line2\n\n");
    expect(events).toEqual([{ id: undefined, event: "message", data: "line1\nline2" }]);
  });

  it("ignores comment and keep-alive lines", () => {
    const events = parse(": keep-alive\ndata: real\n\n");
    expect(events).toEqual([{ id: undefined, event: "message", data: "real" }]);
  });

  it("accepts fields without a space after the colon", () => {
    const events = parse('data:{"a":1}\n\n');
    expect(events).toEqual([{ id: undefined, event: "message", data: '{"a":1}' }]);
  });

  it("defaults the event type to message", () => {
    const events = parse("data: x\n\n");
    expect(events[0].event).toBe("message");
  });

  it("carries the last event id across frames", () => {
    const events = parse("id: 1\ndata: a\n\ndata: b\n\n");
    expect(events).toEqual([
      { id: "1", event: "message", data: "a" },
      { id: "1", event: "message", data: "b" },
    ]);
  });

  it("clears the event id on an empty id field", () => {
    const events = parse("id: 1\ndata: a\n\nid:\ndata: b\n\n");
    expect(events[0].id).toBe("1");
    expect(events[1].id).toBeUndefined();
  });

  it("strips a leading BOM", () => {
    const events = parse("\uFEFFdata: x\n\n");
    expect(events).toEqual([{ id: undefined, event: "message", data: "x" }]);
  });

  it("reassembles a frame split across chunks mid-line", () => {
    const events = parse("dat", "a: hel", "lo\n", "\n");
    expect(events).toEqual([{ id: undefined, event: "message", data: "hello" }]);
  });

  it("treats a CRLF pair split across chunks as one terminator", () => {
    // The \r ends chunk 1; the \n opens chunk 2. Counting them as two
    // terminators would dispatch "a" early — per spec both data lines
    // belong to the same (still unfinished) event.
    const events = parse("data: a\r", "\ndata: b\n\n");
    expect(events).toEqual([
      { id: undefined, event: "message", data: "a\nb" },
    ]);
  });

  it("does not dispatch frames with an empty data buffer", () => {
    const events = parse("event: ping\n\nid: 3\n\n");
    expect(events).toEqual([]);
  });

  it("flushes a final unterminated line at EOF", () => {
    const parser = new SseParser();
    expect(parser.push("data: tail")).toEqual([]);
    expect(parser.flush()).toEqual([{ id: undefined, event: "message", data: "tail" }]);
  });

  it("ignores unknown fields such as retry", () => {
    const events = parse("retry: 5000\ndata: x\n\n");
    expect(events).toEqual([{ id: undefined, event: "message", data: "x" }]);
  });
});

describe("streamSse", () => {
  it("streams frames from a Response body", async () => {
    const encoder = new TextEncoder();
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(encoder.encode("id: 1\ndata: {\"seq\":1}\n\n"));
        controller.enqueue(encoder.encode("id: 2\ndata: {\"seq\":2}\n\n"));
        controller.close();
      },
    });
    const response = new Response(body, {
      status: 200,
      headers: { "content-type": "text/event-stream" },
    });

    const events = [];
    for await (const event of streamSse(response)) events.push(event);

    expect(events.map((event) => event.id)).toEqual(["1", "2"]);
    expect(JSON.parse(events[0].data)).toEqual({ seq: 1 });
  });
});

describe("streamNdjson", () => {
  it("streams parsed records including a final unterminated line", async () => {
    const encoder = new TextEncoder();
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(encoder.encode('{"action":"a"}\n{"action":"b"}\n{"action":"c"}'));
        controller.close();
      },
    });
    const response = new Response(body, { status: 200 });

    const records: unknown[] = [];
    for await (const record of streamNdjson(response)) records.push(record);

    expect(records).toEqual([{ action: "a" }, { action: "b" }, { action: "c" }]);
  });
});
