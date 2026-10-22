import { useEffect, useState, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { apiClient, ApiError } from '../api/client';
import type { Environment, PaginatedResponse } from '../types';
import LoadingSpinner from '../components/LoadingSpinner';
import ErrorMessage from '../components/ErrorMessage';
import Pagination from '../components/Pagination';
import './EnvironmentListView.css';

export default function EnvironmentListView() {
  const navigate = useNavigate();
  const { t, i18n } = useTranslation('environments');

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
    if (diffDay < 30) return t('time:daysAgo', { count: diffDay });

    return new Date(dateStr).toLocaleDateString(i18n.language);
  };

  const [environments, setEnvironments] = useState<Environment[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const [nextCursor, setNextCursor] = useState<string | undefined>();
  const [hasMore, setHasMore] = useState(false);

  const fetchEnvironments = useCallback(async (cursor?: string) => {
    try {
      const params: Record<string, string> = {};
      if (cursor) params.cursor = cursor;

      const res = await apiClient.get<PaginatedResponse<Environment>>('/v1/environments', params);
      if (cursor) {
        setEnvironments(prev => [...prev, ...res.data]);
      } else {
        setEnvironments(res.data);
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
    fetchEnvironments().finally(() => setLoading(false));
  }, [fetchEnvironments]);

  const handleLoadMore = async () => {
    if (!nextCursor) return;
    setLoadingMore(true);
    await fetchEnvironments(nextCursor);
    setLoadingMore(false);
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
          fetchEnvironments().finally(() => setLoading(false));
        }}
      />
    );
  }

  return (
    <div className="environment-list-view" data-testid="environment-list-view">
      <div className="environment-list-header">
        <div>
          <h1>{t('listTitle')}</h1>
          <p>{t('listSubtitle')}</p>
        </div>
        <button
          className="btn-primary"
          data-testid="new-environment-btn"
          onClick={() => navigate('/environments/new')}
        >
          {t('listNewEnvironment')}
        </button>
      </div>

      <div className="environment-list-table-wrapper">
        <table className="environment-list-table" data-testid="environment-table">
          <thead>
            <tr>
              <th>{t('common:id')}</th>
              <th>{t('common:name')}</th>
              <th>{t('listSpec')}</th>
              <th>{t('listNetworking')}</th>
              <th>{t('common:created')}</th>
            </tr>
          </thead>
          <tbody>
            {environments.length === 0 ? (
              <tr>
                <td colSpan={5} className="empty-row">
                  {t('listEmpty')}
                </td>
              </tr>
            ) : (
              environments.map(env => (
                <tr
                  key={env.id}
                  className="environment-row"
                  data-testid={`environment-row-${env.id}`}
                  onClick={() => navigate(`/environments/${env.id}`)}
                  role="link"
                  tabIndex={0}
                  onKeyDown={e => {
                    if (e.key === 'Enter') navigate(`/environments/${env.id}`);
                  }}
                >
                  <td className="cell-id" data-testid="environment-cell-id">
                    <code>{env.id}</code>
                  </td>
                  <td data-testid="environment-cell-name">{env.name}</td>
                  <td data-testid="environment-cell-spec">
                    <code className="spec-badge">{env.config.resources.tier}</code>
                  </td>
                  <td data-testid="environment-cell-networking">
                    {env.config.networking.type}
                  </td>
                  <td data-testid="environment-cell-created">
                    {formatRelativeTime(env.created_at)}
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
