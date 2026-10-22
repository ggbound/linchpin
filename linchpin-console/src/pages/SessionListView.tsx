import { useEffect, useState, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { Link, useNavigate } from 'react-router-dom';
import { apiClient, ApiError } from '../api/client';
import type { Agent, Session, PaginatedResponse } from '../types';
import LoadingSpinner from '../components/LoadingSpinner';
import ErrorMessage from '../components/ErrorMessage';
import Pagination from '../components/Pagination';
import StatusBadge from '../components/StatusBadge';
import SessionCreateDialog from '../components/SessionCreateDialog';
import './SessionListView.css';

// Search only filters already-loaded rows; a full session ID pasted from
// elsewhere won't match anything. Offer a direct jump for that case.
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export default function SessionListView() {
  const navigate = useNavigate();
  const { t, i18n } = useTranslation('sessions');

  const [sessions, setSessions] = useState<Session[]>([]);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const [searchQuery, setSearchQuery] = useState('');
  const [agentFilter, setAgentFilter] = useState('');
  const [showArchived, setShowArchived] = useState(false);
  const [nextCursor, setNextCursor] = useState<string | undefined>();
  const [hasMore, setHasMore] = useState(false);
  const [showCreateDialog, setShowCreateDialog] = useState(false);

  const fetchSessions = useCallback(async (cursor?: string) => {
    try {
      const params: Record<string, string> = {};
      if (cursor) params.cursor = cursor;
      if (agentFilter) params.agent_id = agentFilter;

      const res = await apiClient.get<PaginatedResponse<Session>>('/v1/sessions', params);
      if (cursor) {
        setSessions(prev => [...prev, ...res.data]);
      } else {
        setSessions(res.data);
      }
      setNextCursor(res.next_cursor);
      setHasMore(res.has_more);
      setError(null);
    } catch (err) {
      setError(err as ApiError);
    }
  }, [agentFilter]);

  const fetchAgents = useCallback(async () => {
    try {
      const res = await apiClient.get<PaginatedResponse<Agent>>('/v1/agents');
      setAgents(res.data);
    } catch {
      // Non-critical — agent dropdown just won't populate
    }
  }, []);

  useEffect(() => {
    setLoading(true);
    Promise.all([fetchSessions(), fetchAgents()]).finally(() => setLoading(false));
  }, [fetchSessions, fetchAgents]);

  const handleLoadMore = async () => {
    if (!nextCursor) return;
    setLoadingMore(true);
    await fetchSessions(nextCursor);
    setLoadingMore(false);
  };

  // Client-side filtering
  let filtered = sessions;

  // Hide archived unless toggled on
  if (!showArchived) {
    filtered = filtered.filter(s => !s.archived_at);
  }

  // Search by ID
  if (searchQuery) {
    filtered = filtered.filter(s =>
      s.id.toLowerCase().includes(searchQuery.toLowerCase()),
    );
  }

  const agentNames = new Map(agents.map(a => [a.id, a.name]));

  const formatRelativeTime = (dateStr: string): string => {
    const now = Date.now();
    const then = new Date(dateStr).getTime();
    const diffMs = now - then;
    const diffSec = Math.floor(diffMs / 1000);

    if (diffSec < 10) return t('time:justNow');
    if (diffSec < 60) return t('time:secondsAgo', { count: diffSec });
    const diffMin = Math.floor(diffSec / 60);
    if (diffMin < 60) return t('time:minutesAgo', { count: diffMin });
    const diffHr = Math.floor(diffMin / 60);
    if (diffHr < 24) return t('time:hoursAgo', { count: diffHr });
    const diffDay = Math.floor(diffHr / 24);
    if (diffDay < 7) return t('time:daysAgo', { count: diffDay });
    if (diffDay < 30) return t('time:weeksAgo', { count: Math.floor(diffDay / 7) });

    return new Date(dateStr).toLocaleDateString(i18n.language);
  };

  if (loading) return <LoadingSpinner />;

  if (error) {
    return (
      <ErrorMessage
        status={error.status}
        message={error.message}
        onRetry={() => {
          setError(null);
          setLoading(true);
          Promise.all([fetchSessions(), fetchAgents()]).finally(() => setLoading(false));
        }}
      />
    );
  }

  return (
    <div className="session-list-view" data-testid="session-list-view">
      <div className="session-list-header">
        <div>
          <h1>{t('listTitle')}</h1>
          <p>{t('listSubtitle')}</p>
        </div>
        <button
          className="btn-primary"
          data-testid="new-session-btn"
          onClick={() => setShowCreateDialog(true)}
        >
          {t('listNewSession')}
        </button>
      </div>

      <div className="session-list-toolbar">
        <input
          type="text"
          className="search-input"
          placeholder={t('listSearchPlaceholder')}
          value={searchQuery}
          onChange={e => setSearchQuery(e.target.value)}
          data-testid="session-search-input"
          aria-label={t('listSearchAriaLabel')}
        />

        <select
          className="filter-select"
          value={agentFilter}
          onChange={e => {
            setAgentFilter(e.target.value);
          }}
          data-testid="agent-filter-select"
          aria-label={t('listFilterByAgentAriaLabel')}
        >
          <option value="">{t('listAllAgents')}</option>
          {agents.map(a => (
            <option key={a.id} value={a.id}>
              {a.name} ({a.id.slice(0, 8)})
            </option>
          ))}
        </select>

        <label className="archived-toggle" data-testid="archived-toggle">
          <input
            type="checkbox"
            checked={showArchived}
            onChange={e => setShowArchived(e.target.checked)}
          />
          {t('listShowArchived')}
        </label>
      </div>

      <div className="session-list-table-wrapper">
        <table className="session-list-table" data-testid="session-table">
          <thead>
            <tr>
              <th>{t('common:id')}</th>
              <th>{t('listColumnTitle')}</th>
              <th>{t('listColumnStatus')}</th>
              <th>{t('listColumnAgent')}</th>
              <th>{t('common:created')}</th>
            </tr>
          </thead>
          <tbody>
            {filtered.length === 0 ? (
              <tr>
                <td colSpan={5} className="empty-row">
                  {searchQuery ? (
                    <>
                      {t('listNoSearchResults')}{' '}
                      {UUID_RE.test(searchQuery.trim()) && (
                        <button
                          className="session-jump-btn"
                          data-testid="session-jump-btn"
                          onClick={() => navigate(`/sessions/${searchQuery.trim()}`)}
                        >
                          {t('listOpenShort', { id: searchQuery.trim().slice(0, 8) })}
                        </button>
                      )}
                    </>
                  ) : (
                    t('listNoSessions')
                  )}
                </td>
              </tr>
            ) : (
              filtered.map(session => (
                <tr
                  key={session.id}
                  className="session-row"
                  data-testid={`session-row-${session.id}`}
                  onClick={() => navigate(`/sessions/${session.id}`)}
                  role="link"
                  tabIndex={0}
                  onKeyDown={e => {
                    if (e.key === 'Enter') navigate(`/sessions/${session.id}`);
                  }}
                >
                  <td className="cell-id" data-testid="session-cell-id">
                    <code>{session.id}</code>
                  </td>
                  <td data-testid="session-cell-title">
                    {session.title || t('listUntitled')}
                  </td>
                  <td data-testid="session-cell-status">
                    <StatusBadge status={session.status} />
                  </td>
                  <td className="cell-id" data-testid="session-cell-agent">
                    <Link
                      to={`/agents/${session.agent_id}`}
                      onClick={e => e.stopPropagation()}
                    >
                      {agentNames.get(session.agent_id) ?? session.agent_id}
                    </Link>
                  </td>
                  <td data-testid="session-cell-created">
                    {formatRelativeTime(session.created_at)}
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      <Pagination hasMore={hasMore} onLoadMore={handleLoadMore} loading={loadingMore} />

      <SessionCreateDialog
        open={showCreateDialog}
        onClose={() => setShowCreateDialog(false)}
        onCreated={id => {
          setShowCreateDialog(false);
          navigate(`/sessions/${id}`);
        }}
      />
    </div>
  );
}
