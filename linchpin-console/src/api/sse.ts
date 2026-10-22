import type { SessionEvent, StreamState } from '../types';
import { getApiKey } from './client';

const BASE_URL: string =
  import.meta.env.VITE_API_URL || 'http://localhost:8000';

/** Close the stream after this many consecutive failed connections. */
const MAX_CONSECUTIVE_ERRORS = 8;

/**
 * SSE client for GET /v1/sessions/{id}/stream.
 *
 * - Auth rides in `?token=` (native EventSource cannot set headers).
 * - Initial history pickup rides in `?cursor=`; browser automatic
 *   reconnects send the `Last-Event-ID` header, which the server
 *   prefers over `?cursor=` — so reconnects resume exactly where the
 *   stream left off without any client bookkeeping.
 * - A circuit breaker closes the stream after MAX_CONSECUTIVE_ERRORS
 *   consecutive failed connections (404/401/403 should not spin).
 */
export class SSEClient {
  private eventSource: EventSource | null = null;
  private consecutiveErrors = 0;
  private closedByBreaker = false;
  private sessionId = '';
  private onEvent: ((event: SessionEvent) => void) | null = null;
  private onState: ((state: StreamState) => void) | null = null;

  connect(
    sessionId: string,
    cursor: string | undefined,
    onEvent: (event: SessionEvent) => void,
    onState?: (state: StreamState) => void,
  ): void {
    this.disconnect();
    this.sessionId = sessionId;
    this.onEvent = onEvent;
    this.onState = onState ?? null;
    this.consecutiveErrors = 0;
    this.closedByBreaker = false;
    this.open(cursor);
  }

  disconnect(): void {
    if (this.eventSource) {
      this.eventSource.close();
      this.eventSource = null;
    }
    this.onEvent = null;
    this.onState = null;
  }

  private setState(state: StreamState): void {
    if (this.onState) this.onState(state);
  }

  private open(cursor: string | undefined): void {
    const url = this.buildUrl(cursor);
    const es = new EventSource(url);
    this.eventSource = es;
    this.setState(this.consecutiveErrors === 0 ? 'connecting' : 'reconnecting');

    es.onopen = () => {
      this.consecutiveErrors = 0;
      this.setState('live');
    };

    es.onmessage = (msg) => {
      if (!msg.data) return;
      try {
        const parsed = JSON.parse(msg.data) as SessionEvent;
        this.onEvent?.(parsed);
      } catch {
        console.warn('[SSE] Failed to parse event:', msg.data);
      }
    };

    es.onerror = () => {
      // Non-200/error responses close the EventSource for good
      // (readyState CLOSED); transient network drops keep it
      // CONNECTING and the browser retries with Last-Event-ID.
      if (es.readyState === EventSource.CLOSED) {
        this.consecutiveErrors += 1;
        if (this.consecutiveErrors >= MAX_CONSECUTIVE_ERRORS) {
          this.closedByBreaker = true;
          this.eventSource = null;
          es.close();
          this.setState('closed');
          return;
        }
        // Reopen manually (with fresh ?cursor= — though Last-Event-ID
        // on the wire matters more; server prefers the header).
        setTimeout(() => {
          if (!this.closedByBreaker && this.onEvent) {
            this.open(undefined);
          }
        }, 1000);
      }
      // readyState CONNECTING → browser auto-reconnect is already
      // scheduled; just flag the state.
      this.setState('reconnecting');
    };
  }

  private buildUrl(cursor: string | undefined): string {
    const key = getApiKey();
    let url = `${BASE_URL}/v1/sessions/${this.sessionId}/stream`;
    const params: string[] = [];
    if (cursor) {
      params.push(`cursor=${encodeURIComponent(cursor)}`);
    }
    if (key) {
      params.push(`token=${encodeURIComponent(key)}`);
    }
    if (params.length > 0) {
      url += '?' + params.join('&');
    }
    return url;
  }
}
