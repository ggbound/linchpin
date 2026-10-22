import { useEffect, useState, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { apiClient, ApiError } from '../api/client';
import type { Agent, PaginatedResponse } from '../types';
import LoadingSpinner from '../components/LoadingSpinner';
import ErrorMessage from '../components/ErrorMessage';
import Pagination from '../components/Pagination';
import './AgentListView.css';

export default function AgentListView() {
  const { t, i18n } = useTranslation('agents');
  const navigate = useNavigate();

  const formatRelativeTime = (dateStr: string): string => {
    const now = Date.now();
    const then = new Date(dateStr).getTime();
    const diffMs = now - then;
    const diffSec = Math.floor(diffMs / 1000);

    if (diffSec < 60) return t('time:justNow');
    const diffMin = Math.floor(diffSec / 60);
    if (diffMin < 60) return t('time:minutesAgo', { count: diffMin });
    const diffHr = Math.floor(diffMin / 60);
    if (diffHr < 24) return t('time:hoursAgo', { count: diffHr });
    const diffDay = Math.floor(diffHr / 24);
    if (diffDay < 7) return t('time:daysAgo', { count: diffDay });
    if (diffDay < 30) return t('time:weeksAgo', { count: Math.floor(diffDay / 7) });

    return new Date(dateStr).toLocaleDateString(i18n.language);
  };

  const [agents, setAgents] = useState<Agent[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const [searchQuery, setSearchQuery] = useState('');
  const [nextCursor, setNextCursor] = useState<string | undefined>();
  const [hasMore, setHasMore] = useState(false);

  const fetchAgents = useCallback(async (cursor?: string) => {
    try {
      const params: Record<string, string> = {};
      if (cursor) params.cursor = cursor;

      const res = await apiClient.get<PaginatedResponse<Agent>>('/v1/agents', params);
      if (cursor) {
        setAgents(prev => [...prev, ...res.data]);
      } else {
        setAgents(res.data);
      }
      setNextCursor(res.next_cursor);
      setHasMore(res.has_more);
      setError(null);
    } catch (err) {
      setError(err as ApiError);
    }
  }, []);

  useEffect(() => {
    setLoading(true);
    fetchAgents().finally(() => setLoading(false));
  }, [fetchAgents]);

  const handleLoadMore = async () => {
    if (!nextCursor) return;
    setLoadingMore(true);
    await fetchAgents(nextCursor);
    setLoadingMore(false);
  };

  const filteredAgents = searchQuery
    ? agents.filter(a => a.id.toLowerCase().includes(searchQuery.toLowerCase()))
    : agents;

  if (loading) return <LoadingSpinner />;

  if (error) {
    return (
      <ErrorMessage
        status={error.status}
        message={error.message}
        onRetry={() => {
          setError(null);
          setLoading(true);
          fetchAgents().finally(() => setLoading(false));
        }}
      />
    );
  }

  return (
    <div className="agent-list-view" data-testid="agent-list-view">
      <div className="agent-list-header">
        <div>
          <h1>{t('listTitle')}</h1>
          <p>{t('listSubtitle')}</p>
        </div>
        <button
          className="btn-primary"
          data-testid="new-agent-btn"
          onClick={() => navigate('/agents/new')}
        >
          {t('listNewAgent')}
        </button>
      </div>

      <div className="agent-list-toolbar">
        <input
          type="text"
          className="search-input"
          placeholder={t('listSearchPlaceholder')}
          value={searchQuery}
          onChange={e => setSearchQuery(e.target.value)}
          data-testid="agent-search-input"
          aria-label={t('listSearchAriaLabel')}
        />
      </div>

      <div className="agent-list-table-wrapper">
        <table className="agent-list-table" data-testid="agent-table">
          <thead>
            <tr>
              <th>{t('common:id')}</th>
              <th>{t('common:name')}</th>
              <th>{t('listColModel')}</th>
              <th>{t('common:created')}</th>
            </tr>
          </thead>
          <tbody>
            {filteredAgents.length === 0 ? (
              <tr>
                <td colSpan={4} className="empty-row">
                  {searchQuery ? t('listNoMatch') : t('listEmpty')}
                </td>
              </tr>
            ) : (
              filteredAgents.map(agent => (
                <tr
                  key={agent.id}
                  className="agent-row"
                  data-testid={`agent-row-${agent.id}`}
                  onClick={() => navigate(`/agents/${agent.id}`)}
                  role="link"
                  tabIndex={0}
                  onKeyDown={e => {
                    if (e.key === 'Enter') navigate(`/agents/${agent.id}`);
                  }}
                >
                  <td className="cell-id" data-testid="agent-cell-id">
                    <code>{agent.id}</code>
                  </td>
                  <td data-testid="agent-cell-name">{agent.name}</td>
                  <td data-testid="agent-cell-model">
                    {agent.model.provider} / {agent.model.id}
                  </td>
                  <td data-testid="agent-cell-created">
                    {formatRelativeTime(agent.created_at)}
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      <Pagination hasMore={hasMore} onLoadMore={handleLoadMore} loading={loadingMore} />
    </div>
  );
}
