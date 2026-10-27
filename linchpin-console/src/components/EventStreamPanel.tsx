import { useEffect, useMemo, useRef, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import { useTranslation } from 'react-i18next';
import { apiClient } from '../api/client';
import { SSEClient } from '../api/sse';
import type { SessionEvent, SessionStatus, PaginatedEventsResponse, StreamState } from '../types';
import ToolConfirmationCard from './ToolConfirmationCard';
import LoadingSpinner from './LoadingSpinner';
import './EventStreamPanel.css';

interface EventStreamPanelProps {
  sessionId: string;
  sessionStatus: SessionStatus;
}

/** Merge two event arrays, deduplicate by cursor, sort by seq ascending. */
export function mergeEvents(
  existing: SessionEvent[],
  incoming: SessionEvent[],
): SessionEvent[] {
  const map = new Map<string, SessionEvent>();
  for (const e of existing) map.set(e.cursor, e);
  for (const e of incoming) map.set(e.cursor, e);
  return Array.from(map.values()).sort((a, b) => a.seq - b.seq);
}

/** Return a CSS class suffix for the given event type. */
export function eventTypeClass(type: string): string {
  if (type === 'user.message') return 'user-message';
  if (type === 'user.steer') return 'user-message';
  if (type === 'agent.message') return 'agent-message';
  if (type === 'agent.message_delta') return 'agent-message-delta';
  if (type === 'agent.thinking') return 'agent-thinking';
  if (
    type === 'agent.tool_use' ||
    type === 'agent.tool_result' ||
    type === 'agent.mcp_tool_use' ||
    type === 'agent.mcp_tool_result' ||
    type === 'agent.custom_tool_use'
  ) return 'tool';
  if (type === 'session.error') return 'error';
  if (type === 'session.requires_action') return 'requires-action';
  if (type.startsWith('session.')) return 'status';
  return 'agent-message';
}

const TERMINAL_STATUSES: SessionStatus[] = ['terminated', 'failed'];
const POLL_INTERVAL_MS = 1000;
/** Page size for the REST history pull (server max is 100). */
const HISTORY_PAGE_SIZE = 100;
/** Safety cap on history pages pulled before falling back to the stream. */
const MAX_HISTORY_PAGES = 20;

const STREAM_STATE_KEYS: Record<StreamState, string> = {
  connecting: 'connecting',
  live: 'live',
  reconnecting: 'reconnecting',
  closed: 'closed',
};

/** i18n keys for tool event labels (fallback: derived from the raw type). */
const TOOL_LABEL_KEYS: Record<string, string> = {
  'agent.tool_use': 'toolUse',
  'agent.tool_result': 'toolResult',
  'agent.mcp_tool_use': 'mcpToolUse',
  'agent.mcp_tool_result': 'mcpToolResult',
  'agent.custom_tool_use': 'customToolUse',
};

export default function EventStreamPanel({ sessionId, sessionStatus }: EventStreamPanelProps) {
  const { t } = useTranslation('eventStream');
  const [events, setEvents] = useState<SessionEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [streamState, setStreamState] = useState<StreamState>('closed');
  const [streamingMessages, setStreamingMessages] = useState<Map<string, string>>(new Map());
  const listRef = useRef<HTMLDivElement>(null);
  const userScrolledUp = useRef(false);
  const sseRef = useRef<SSEClient | null>(null);
  const statusRef = useRef<SessionStatus>(sessionStatus);
  statusRef.current = sessionStatus;

  const scrollToBottom = () => {
    if (listRef.current && !userScrolledUp.current) {
      listRef.current.scrollTop = listRef.current.scrollHeight;
    }
  };

  const handleScroll = () => {
    if (!listRef.current) return;
    const { scrollTop, scrollHeight, clientHeight } = listRef.current;
    userScrolledUp.current = scrollHeight - scrollTop - clientHeight > 50;
  };

  // History pull (REST) + live tail (SSE) — keyed on session only so a
  // transient status change does not tear the stream down.
  useEffect(() => {
    let cancelled = false;

    setLoading(true);
    setEvents([]);

    async function pullHistory(): Promise<string | undefined | null> {
      let after: string | undefined;
      let merged: SessionEvent[] = [];
      for (let page = 0; page < MAX_HISTORY_PAGES; page++) {
        const params: Record<string, string> = { limit: String(HISTORY_PAGE_SIZE) };
        if (after) params['after_cursor'] = after;
        const res = await apiClient.get<PaginatedEventsResponse>(
          `/v1/sessions/${sessionId}/events`,
          params,
        );
        if (cancelled) return null;
        merged = mergeEvents(merged, res.events);
        if (!res.next_cursor || res.events.length === 0) {
          after = res.next_cursor ?? undefined;
          break;
        }
        after = res.next_cursor;
      }
      if (cancelled) return null;
      setEvents(merged);
      return after;
    }

    pullHistory().then((cursor) => {
      if (cancelled) return;
      setLoading(false);
      if (cursor === null) return;
      // Terminal sessions have nothing left to stream live.
      if (TERMINAL_STATUSES.includes(statusRef.current)) return;
      const sse = new SSEClient();
      sseRef.current = sse;
      sse.connect(
        sessionId,
        cursor,
        (ev) => setEvents((prev) => mergeEvents(prev, [ev])),
        setStreamState,
      );
    });

    return () => {
      cancelled = true;
      sseRef.current?.disconnect();
      sseRef.current = null;
      setStreamState('closed');
    };
  }, [sessionId]);

  // Deltas are in-memory only (never persisted), so the in-flight
  // bubble still comes from the /streaming poll.
  useEffect(() => {
    async function fetchStreaming() {
      try {
        const res = await apiClient.get<{ streaming: { message_id: string; text: string; done: boolean } | null }>(
          `/v1/sessions/${sessionId}/streaming`,
        );
        if (res.streaming && res.streaming.text && !res.streaming.done) {
          setStreamingMessages(new Map([[res.streaming.message_id, res.streaming.text]]));
        } else {
          setStreamingMessages(new Map());
        }
      } catch {
        // Silently handle
      }
    }

    if (TERMINAL_STATUSES.includes(sessionStatus)) {
      setStreamingMessages(new Map());
      return;
    }
    fetchStreaming();
    const interval = setInterval(fetchStreaming, POLL_INTERVAL_MS);
    return () => clearInterval(interval);
  }, [sessionId, sessionStatus]);

  // Disconnect the live stream when the session reaches a terminal state.
  useEffect(() => {
    if (TERMINAL_STATUSES.includes(sessionStatus)) {
      sseRef.current?.disconnect();
      setStreamState('closed');
    }
  }, [sessionStatus]);

  // Scroll to bottom when events or streaming messages change
  useEffect(() => {
    scrollToBottom();
  }, [events, streamingMessages]);

  // Tool-use ids that already have a user.tool_confirmation in the stream,
  // so replayed requires_action cards render as resolved instead of pending.
  const resolvedToolIds = useMemo(() => {
    const ids = new Set<string>();
    for (const e of events) {
      if (e.type === 'user.tool_confirmation') {
        const id = e.payload.tool_use_id;
        if (typeof id === 'string' && id) ids.add(id);
      }
    }
    return ids;
  }, [events]);

  const renderEvent = (event: SessionEvent) => {
    const cls = eventTypeClass(event.type);

    // Task 8.3: Skip rendering delta events as separate bubbles
    if (event.type === 'agent.message_delta') {
      return null;
    }

    if (event.type === 'session.requires_action') {
      const toolName = (event.payload.tool_name as string) ?? t('unknownTool');
      const toolUseId = (event.payload.tool_use_id as string) ?? '';
      const args = (event.payload.arguments as Record<string, unknown>) ?? {};
      return (
        <div key={event.cursor} className="event-bubble event-bubble--requires-action" data-testid="event-requires-action">
          <ToolConfirmationCard
            sessionId={sessionId}
            toolName={toolName}
            toolUseId={toolUseId}
            arguments={args}
            initiallyResolved={resolvedToolIds.has(toolUseId)}
            onResolved={() => {}}
          />
        </div>
      );
    }

    if (cls === 'tool') {
      const label = TOOL_LABEL_KEYS[event.type]
        ? t(TOOL_LABEL_KEYS[event.type])
        : event.type.replace('agent.', '').replace(/_/g, ' ');
      return (
        <div key={event.cursor} className="event-bubble event-bubble--tool" data-testid={`event-${cls}`}>
          <div className="tool-label">{label}</div>
          <pre>{JSON.stringify(event.payload, null, 2)}</pre>
        </div>
      );
    }

    if (cls === 'error') {
      return (
        <div key={event.cursor} className="event-bubble event-bubble--error" data-testid="event-error">
          {(event.payload.message as string) ?? (event.payload.error as string) ?? t('anErrorOccurred')}
        </div>
      );
    }

    if (cls === 'status') {
      return null;
    }

    // user.message, user.steer, agent.message, agent.thinking
    const text = (event.payload.text as string) ?? (event.payload.content as string) ?? (event.payload.message as string) ?? '';

    // Render markdown for agent messages
    if (cls === 'agent-message' && text) {
      return (
        <div key={event.cursor} className={`event-bubble event-bubble--${cls}`} data-testid={`event-${cls}`}>
          <ReactMarkdown>{text}</ReactMarkdown>
        </div>
      );
    }

    return (
      <div key={event.cursor} className={`event-bubble event-bubble--${cls}`} data-testid={`event-${cls}`}>
        {text}
      </div>
    );
  };

  if (loading) return <LoadingSpinner inline />;

  const showStateBadge =
    !TERMINAL_STATUSES.includes(sessionStatus) && streamState !== 'closed';

  return (
    <div className="event-stream-panel" data-testid="event-stream-panel">
      {showStateBadge && (
        <div className={`event-stream-state event-stream-state--${streamState}`} data-testid="stream-state">
          <span className="event-stream-state-dot" />
          {t(STREAM_STATE_KEYS[streamState])}
        </div>
      )}
      <div
        className="event-stream-list"
        ref={listRef}
        onScroll={handleScroll}
        data-testid="event-stream-list"
      >
        {events.length === 0 && streamingMessages.size === 0 ? (
          <div className="event-stream-empty">{t('noEvents')}</div>
        ) : (
          <>
            {events.map(renderEvent)}
            {/* Render active streaming bubbles */}
            {Array.from(streamingMessages.entries()).map(([messageId, text]) => (
              <div
                key={`streaming-${messageId}`}
                className="event-bubble event-bubble--agent-message event-bubble--streaming"
                data-testid="event-streaming"
              >
                <ReactMarkdown>{text}</ReactMarkdown>
                <span className="streaming-cursor" />
              </div>
            ))}
          </>
        )}
      </div>
    </div>
  );
}
