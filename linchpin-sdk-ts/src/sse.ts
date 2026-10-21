/**
 * Incremental SSE frame parser + response-body stream adapter (Atlas A3).
 *
 * Implements the WHATWG server-sent-events wire format without any
 * dependency: ``id`` / ``event`` / ``data`` fields, ``\r\n`` / ``\r`` /
 * ``\n`` terminators, ``:`` comments, multi-line ``data`` joined with
 * ``\n``, a leading BOM, and chunk boundaries split mid-line (including a
 * ``\r\n`` pair split across two chunks).
 */

import { LinchpinError } from "./errors.js";
import type { SseEvent } from "./types.js";

export class SseParser {
  private pending = "";
  private lastEventId: string | undefined;
  private eventType = "";
  private dataLines: string[] = [];
  private trailingCr = false;
  private seenFirstChunk = false;

  /** Feed one text chunk; returns every complete frame it terminated. */
  push(chunk: string): SseEvent[] {
    if (!this.seenFirstChunk) {
      this.seenFirstChunk = true;
      if (chunk.charCodeAt(0) === 0xfeff) chunk = chunk.slice(1);
    }

    // A \r\n pair split across chunks must stay ONE terminator.
    if (this.trailingCr) {
      this.trailingCr = false;
      if (chunk.startsWith("\n")) chunk = chunk.slice(1);
    }
    this.trailingCr = chunk.endsWith("\r");

    const events: SseEvent[] = [];
    const lines = (this.pending + chunk).split(/\r\n|\r|\n/);
    // The last element is the (possibly empty) unfinished line.
    this.pending = lines.pop() ?? "";

    for (const line of lines) {
      this.processLine(line, events);
    }
    return events;
  }

  /** Flush at EOF: a final unterminated line still counts, then dispatch. */
  flush(): SseEvent[] {
    const events: SseEvent[] = [];
    if (this.pending !== "") {
      const line = this.pending;
      this.pending = "";
      this.processLine(line, events);
    }
    this.dispatch(events);
    return events;
  }

  private processLine(line: string, out: SseEvent[]): void {
    // Blank line → dispatch whatever the buffered fields describe.
    if (line === "") {
      this.dispatch(out);
      return;
    }
    if (line.startsWith(":")) return; // comment / keep-alive

    const colon = line.indexOf(":");
    const field = colon === -1 ? line : line.slice(0, colon);
    let value = colon === -1 ? "" : line.slice(colon + 1);
    if (value.startsWith(" ")) value = value.slice(1);

    switch (field) {
      case "event":
        this.eventType = value;
        break;
      case "data":
        this.dataLines.push(value);
        break;
      case "id":
        // Spec: an empty (NUL-free) id clears the last-event-id buffer.
        if (!value.includes("\0")) this.lastEventId = value === "" ? undefined : value;
        break;
      default:
        break; // unknown fields (e.g. retry) are ignored
    }
  }

  private dispatch(out: SseEvent[]): void {
    const data = this.dataLines.join("\n");
    this.dataLines = [];
    const event = this.eventType || "message";
    this.eventType = "";
    if (data === "") {
      // Spec: an event with an empty data buffer is not dispatched.
      return;
    }
    out.push({ id: this.lastEventId, event, data });
  }
}

/** Consume a fetch ``Response`` body as a stream of parsed SSE frames. */
export async function* streamSse(response: Response): AsyncGenerator<SseEvent> {
  if (!response.body) {
    throw new LinchpinError(0, "no_stream_body", "Response has no body to stream", null);
  }
  const parser = new SseParser();
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      if (value) {
        for (const event of parser.push(decoder.decode(value, { stream: true }))) {
          yield event;
        }
      }
    }
    const tail = decoder.decode();
    if (tail !== "") {
      for (const event of parser.push(tail)) yield event;
    }
    for (const event of parser.flush()) yield event;
  } finally {
    reader.releaseLock();
  }
}

/** Consume a fetch ``Response`` body as newline-delimited JSON records. */
export async function* streamNdjson(response: Response): AsyncGenerator<unknown> {
  if (!response.body) {
    throw new LinchpinError(0, "no_stream_body", "Response has no body to stream", null);
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      if (value) buffer += decoder.decode(value, { stream: true });
      let newline = buffer.indexOf("\n");
      while (newline !== -1) {
        const line = buffer.slice(0, newline).trim();
        buffer = buffer.slice(newline + 1);
        if (line !== "") yield JSON.parse(line);
        newline = buffer.indexOf("\n");
      }
    }
    const tail = buffer.trim();
    if (tail !== "") yield JSON.parse(tail);
  } finally {
    reader.releaseLock();
  }
}
