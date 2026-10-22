import { useEffect, useState, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { Link, useNavigate } from 'react-router-dom';
import { apiClient, ApiError } from '../api/client';
import type { Dream, DreamStatus, DreamListResponse, Agent, MemoryStore, PaginatedResponse } from '../types';
import LoadingSpinner from '../components/LoadingSpinner';
import ErrorMessage from '../components/ErrorMessage';
import ConfirmDialog from '../components/ConfirmDialog';
import './DreamListView.css';

interface StoreListResponse {
  data: MemoryStore[];
  next_cursor: string | null;
}

const STATUS_FILTERS: Array<DreamStatus | 'all'> = [
  'all',
  'pending',
  'running',
  'completed',
  'failed',
  'canceled',
];

export default function DreamListView() {
  const navigate = useNavigate();
  const { t, i18n } = useTranslation('dreams');

  const [dreams, setDreams] = useState<Dream[]>([]);
  const [statusFilter, setStatusFilter] = useState<DreamStatus | 'all'>('all');
  const [storeNames, setStoreNames] = useState<Record<string, string>>({});
  const [agentNames, setAgentNames] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<ApiError | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [cancelingId, setCancelingId] = useState<string | null>(null);
  const [cancelTarget, setCancelTarget] = useState<Dream | null>(null);

  const fetchDreams = useCallback(async () => {
    try {
      const statusQuery = statusFilter === 'all' ? '' : `&status=${statusFilter}`;
      const res = await apiClient.get<DreamListResponse>(`/v1/dreams?limit=100${statusQuery}`);
      setDreams(res.data);
      setError(null);
    } catch (err) {
      setError(err as ApiError);
    }
  }, [statusFilter]);

  useEffect(() => {
    setLoading(true);
    Promise.all([
      fetchDreams(),
      apiClient.get<StoreListResponse>('/v1/memory_stores', { include_archived: 'true', limit: '100' })
        .then(res => {
          const map: Record<string, string> = {};
          res.data.forEach(s => { map[s.id] = s.name; });
          setStoreNames(map);
        })
        .catch(() => undefined),
      apiClient.get<PaginatedResponse<Agent>>('/v1/agents?limit=100')
        .then(res => {
          const map: Record<string, string> = {};
          res.data.forEach(a => { map[a.id] = a.name; });
          setAgentNames(map);
        })
        .catch(() => undefined),
    ]).finally(() => setLoading(false));
  }, [fetchDreams]);

  const handleCancel = async (dream: Dream | null) => {
    if (!dream) return;
    setActionError(null);
    setCancelingId(dream.id);
    setCancelTarget(null);
    try {
      const updated = await apiClient.post<Dream>(`/v1/dreams/${dream.id}/cancel`, {});
      setDreams(prev => prev.map(d => (d.id === dream.id ? updated : d)));
    } catch (err) {
      const apiErr = err as ApiError;
      setActionError(apiErr.message || t('listCancelFailed'));
    } finally {
      setCancelingId(null);
    }
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
          fetchDreams().finally(() => setLoading(false));
        }}
      />
    );
  }

  return (
    <div className="dream-list-view" data-testid="dream-list-view">
      <div className="dream-list-header">
        <div>
          <h1>{t('listTitle')}</h1>
          <p>{t('listSubtitle')}</p>
        </div>
        <button
          className="btn-primary"
          data-testid="new-dream-btn"
          onClick={() => navigate('/dreams/new')}
        >
          {t('listNewDream')}
        </button>
      </div>

      {actionError && (
        <div className="dream-action-error" role="alert" data-testid="dream-action-error">
          {actionError}
        </div>
      )}

      <div className="dream-filter-row">
        <label htmlFor="dream-status-filter">{t('listStatusLabel')}</label>
        <select
          id="dream-status-filter"
          value={statusFilter}
          onChange={e => setStatusFilter(e.target.value as DreamStatus | 'all')}
          data-testid="dream-status-filter"
        >
          {STATUS_FILTERS.map(s => (
            <option key={s} value={s}>
              {s === 'all' ? t('listAllStatuses') : t(`status:${s}`, { defaultValue: String(s) })}
            </option>
          ))}
        </select>
      </div>

      <div className="dream-table-wrapper">
        <table className="dream-table" data-testid="dream-table">
          <thead>
            <tr>
              <th>{t('listColumnOutputStore')}</th>
              <th>{t('listColumnInputStore')}</th>
              <th>{t('listColumnDreamerAgent')}</th>
              <th>{t('listColumnStatus')}</th>
              <th>{t('common:created')}</th>
              <th>{t('listColumnEnded')}</th>
              <th>{t('common:error')}</th>
              <th>{t('common:actions')}</th>
            </tr>
          </thead>
          <tbody>
            {dreams.length === 0 ? (
              <tr>
                <td colSpan={8} className="empty-row" data-testid="dream-empty">
                  {t('listEmpty')}
                </td>
              </tr>
            ) : (
              dreams.map(dream => (
                <tr
                  key={dream.id}
                  className="dream-row"
                  data-testid={`dream-row-${dream.id}`}
                  onClick={() => navigate(`/dreams/${dream.id}`)}
                  role="link"
                  tabIndex={0}
                  onKeyDown={e => {
                    if (e.key === 'Enter') navigate(`/dreams/${dream.id}`);
                  }}
                >
                  <td data-testid="dream-cell-output">{dream.output_store_name}</td>
                  <td data-testid="dream-cell-input">
                    <Link
                      to={`/memory-stores/${dream.input_memory_store_id}`}
                      className="dream-entity-link"
                      onClick={e => e.stopPropagation()}
                    >
                      {storeNames[dream.input_memory_store_id] || dream.input_memory_store_id.slice(0, 8)}
                    </Link>
                  </td>
                  <td data-testid="dream-cell-agent">
                    {dream.dreamer_agent_id ? (
                      <Link
                        to={`/agents/${dream.dreamer_agent_id}`}
                        className="dream-entity-link"
                        onClick={e => e.stopPropagation()}
                      >
                        {agentNames[dream.dreamer_agent_id] || dream.dreamer_agent_id.slice(0, 8)}
                      </Link>
                    ) : (
                      t('listDefaultAgent')
                    )}
                  </td>
                  <td>
                    <span className={`dream-status dream-status--${dream.status}`} data-testid="dream-status">
                      {t(`status:${dream.status}`, { defaultValue: String(dream.status) })}
                    </span>
                  </td>
                  <td>{new Date(dream.created_at).toLocaleString(i18n.language)}</td>
                  <td>{dream.ended_at ? new Date(dream.ended_at).toLocaleString(i18n.language) : '—'}</td>
                  <td
                    className="dream-error-cell"
                    title={dream.error || undefined}
                  >
                    {dream.error || '—'}
                  </td>
                  <td>
                    <div
                      className="dream-actions-cell"
                      onClick={e => e.stopPropagation()}
                      onKeyDown={e => e.stopPropagation()}
                    >
                      {(dream.status === 'pending' || dream.status === 'running') && (
                        <button
                          className="btn-sm-secondary"
                          disabled={cancelingId === dream.id}
                          onClick={() => setCancelTarget(dream)}
                          data-testid="dream-cancel-btn"
                        >
                          {cancelingId === dream.id ? t('listCanceling') : t('common:cancel')}
                        </button>
                      )}
                    </div>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      <ConfirmDialog
        open={cancelTarget !== null}
        title={t('listCancelDialogTitle')}
        message={
          cancelTarget
            ? t('listCancelDialogMessage', { name: cancelTarget.output_store_name })
            : ''
        }
        onConfirm={() => handleCancel(cancelTarget)}
        onCancel={() => setCancelTarget(null)}
      />
    </div>
  );
}
