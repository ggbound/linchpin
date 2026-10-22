import { useEffect, useState, useCallback } from 'react';
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
      setActionError(apiErr.message || 'Failed to cancel dream');
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
          <h1>Dreams</h1>
          <p>
            Curate past sessions into distilled memory stores. Each Dream lets a dreamer
            agent read filtered session history and write a curated memory store.
          </p>
        </div>
        <button
          className="btn-primary"
          data-testid="new-dream-btn"
          onClick={() => navigate('/dreams/new')}
        >
          New dream
        </button>
      </div>

      {actionError && (
        <div className="dream-action-error" role="alert" data-testid="dream-action-error">
          {actionError}
        </div>
      )}

      <div className="dream-filter-row">
        <label htmlFor="dream-status-filter">Status</label>
        <select
          id="dream-status-filter"
          value={statusFilter}
          onChange={e => setStatusFilter(e.target.value as DreamStatus | 'all')}
          data-testid="dream-status-filter"
        >
          {STATUS_FILTERS.map(s => (
            <option key={s} value={s}>{s === 'all' ? 'All statuses' : s}</option>
          ))}
        </select>
      </div>

      <div className="dream-table-wrapper">
        <table className="dream-table" data-testid="dream-table">
          <thead>
            <tr>
              <th>Output Store</th>
              <th>Input Store</th>
              <th>Dreamer Agent</th>
              <th>Status</th>
              <th>Created</th>
              <th>Ended</th>
              <th>Error</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {dreams.length === 0 ? (
              <tr>
                <td colSpan={8} className="empty-row" data-testid="dream-empty">
                  No dreams yet. Create one to curate past sessions into a memory store.
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
                      'Default'
                    )}
                  </td>
                  <td>
                    <span className={`dream-status dream-status--${dream.status}`} data-testid="dream-status">
                      {dream.status}
                    </span>
                  </td>
                  <td>{new Date(dream.created_at).toLocaleString()}</td>
                  <td>{dream.ended_at ? new Date(dream.ended_at).toLocaleString() : '—'}</td>
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
                          {cancelingId === dream.id ? 'Canceling…' : 'Cancel'}
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
        title="Cancel Dream"
        message={
          cancelTarget
            ? `Cancel "${cancelTarget.output_store_name}"? The dreamer session will be terminated and no output store will be produced.`
            : ''
        }
        onConfirm={() => handleCancel(cancelTarget)}
        onCancel={() => setCancelTarget(null)}
      />
    </div>
  );
}
