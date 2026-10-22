import { useEffect, useState, useCallback } from 'react';
import { Link, useParams } from 'react-router-dom';
import { apiClient, ApiError } from '../api/client';
import type { Dream, Agent, MemoryStore, PaginatedResponse } from '../types';
import LoadingSpinner from '../components/LoadingSpinner';
import ErrorMessage from '../components/ErrorMessage';
import ConfirmDialog from '../components/ConfirmDialog';
import './DreamDetailView.css';

export default function DreamDetailView() {
  const { id } = useParams<{ id: string }>();

  const [dream, setDream] = useState<Dream | null>(null);
  const [storeNames, setStoreNames] = useState<Record<string, string>>({});
  const [agentNames, setAgentNames] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<ApiError | null>(null);

  const [canceling, setCanceling] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [showCancelConfirm, setShowCancelConfirm] = useState(false);

  const fetchDream = useCallback(async () => {
    if (!id) return null;
    const d = await apiClient.get<Dream>(`/v1/dreams/${id}`);
    setDream(d);
    return d;
  }, [id]);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const d = await fetchDream();
        if (cancelled || !d) return;
        setError(null);
        // Resolve display names (non-fatal if they fail)
        apiClient.get<{ data: MemoryStore[] }>('/v1/memory_stores?include_archived=true&limit=100')
          .then(res => {
            if (cancelled) return;
            const map: Record<string, string> = {};
            res.data.forEach(s => { map[s.id] = s.name; });
            setStoreNames(map);
          })
          .catch(() => undefined);
        apiClient.get<PaginatedResponse<Agent>>('/v1/agents?limit=100')
          .then(res => {
            if (cancelled) return;
            const map: Record<string, string> = {};
            res.data.forEach(a => { map[a.id] = a.name; });
            setAgentNames(map);
          })
          .catch(() => undefined);
      } catch (err) {
        if (!cancelled) setError(err as ApiError);
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    load();
    return () => { cancelled = true; };
  }, [fetchDream]);

  const handleCancel = async () => {
    if (!dream) return;
    setCanceling(true);
    setActionError(null);
    try {
      const updated = await apiClient.post<Dream>(`/v1/dreams/${dream.id}/cancel`, {});
      setDream(updated);
      setShowCancelConfirm(false);
    } catch (err) {
      const apiErr = err as ApiError;
      setActionError(apiErr.message || 'Failed to cancel dream');
      setShowCancelConfirm(false);
    } finally {
      setCanceling(false);
    }
  };

  const handleRefresh = async () => {
    setError(null);
    setLoading(true);
    try {
      await fetchDream();
    } catch (err) {
      setError(err as ApiError);
    } finally {
      setLoading(false);
    }
  };

  if (loading) return <LoadingSpinner />;

  if (error) {
    return (
      <ErrorMessage
        status={error.status}
        message={error.message}
        onRetry={handleRefresh}
      />
    );
  }

  if (!dream) return null;

  const isTerminal = ['completed', 'failed', 'canceled'].includes(dream.status);
  const sf = dream.session_filter;
  const hasFilter = !!(sf.agent_id || sf.since || sf.until);

  return (
    <div className="dream-detail-view" data-testid="dream-detail-view">
      <Link to="/dreams" className="back-link" data-testid="back-link">
        ← Dreams
      </Link>

      <div className="dream-detail-header">
        <div className="dream-detail-title-row">
          <h1 data-testid="dream-title">{dream.output_store_name}</h1>
          <span className={`dream-status dream-status--${dream.status}`} data-testid="dream-status">
            {dream.status}
          </span>
        </div>
        <div className="dream-detail-meta">
          <span>Created {new Date(dream.created_at).toLocaleString()}</span>
          {dream.started_at && (
            <>
              <span className="meta-separator">·</span>
              <span>Started {new Date(dream.started_at).toLocaleString()}</span>
            </>
          )}
          {dream.ended_at && (
            <>
              <span className="meta-separator">·</span>
              <span>Ended {new Date(dream.ended_at).toLocaleString()}</span>
            </>
          )}
        </div>
      </div>

      {actionError && (
        <div className="form-error" role="alert" data-testid="dream-action-error">
          {actionError}
        </div>
      )}

      {!isTerminal && (
        <section className="detail-section dream-lifecycle-actions" data-testid="dream-actions-section">
          <button
            className="btn-primary"
            disabled={canceling}
            onClick={() => setShowCancelConfirm(true)}
            data-testid="dream-cancel-btn"
          >
            {canceling ? 'Canceling…' : 'Cancel dream'}
          </button>
        </section>
      )}

      {dream.error && (
        <section className="detail-section" data-testid="dream-error-section">
          <h2>Error</h2>
          <p className="dream-error-text" data-testid="dream-error-text">{dream.error}</p>
        </section>
      )}

      <section className="detail-section" data-testid="dream-info-section">
        <h2>
          Configuration
          <button
            className="btn-refresh"
            onClick={handleRefresh}
            disabled={loading}
            data-testid="dream-refresh-btn"
          >
            Refresh
          </button>
        </h2>
        <dl className="detail-grid">
          <dt>Input Store</dt>
          <dd>
            <Link
              to={`/memory-stores/${dream.input_memory_store_id}`}
              data-testid="dream-input-store-link"
            >
              {storeNames[dream.input_memory_store_id] || dream.input_memory_store_id}
            </Link>
          </dd>
          <dt>Output Store Name</dt>
          <dd><code>{dream.output_store_name}</code></dd>
          <dt>Output Store</dt>
          <dd data-testid="dream-output-store">
            {dream.output_memory_store_id ? (
              <Link
                to={`/memory-stores/${dream.output_memory_store_id}`}
                data-testid="dream-output-store-link"
              >
                {storeNames[dream.output_memory_store_id] || dream.output_memory_store_id}
              </Link>
            ) : (
              '—'
            )}
          </dd>
          <dt>Dreamer Agent</dt>
          <dd data-testid="dream-dreamer-agent">
            {dream.dreamer_agent_id ? (
              <Link to={`/agents/${dream.dreamer_agent_id}`} data-testid="dream-agent-link">
                {agentNames[dream.dreamer_agent_id] || dream.dreamer_agent_id}
              </Link>
            ) : (
              'Platform default'
            )}
          </dd>
          {dream.dreamer_session_id && (
            <>
              <dt>Dreamer Session</dt>
              <dd>
                <Link to={`/sessions/${dream.dreamer_session_id}`} data-testid="dream-session-link">
                  {dream.dreamer_session_id.slice(0, 8)}
                </Link>
              </dd>
            </>
          )}
        </dl>
      </section>

      <section className="detail-section" data-testid="dream-filter-section">
        <h2>Session Filter</h2>
        {hasFilter ? (
          <dl className="detail-grid">
            <dt>Agent</dt>
            <dd data-testid="dream-filter-agent">
              {sf.agent_id ? (
                <Link to={`/agents/${sf.agent_id}`}>
                  {agentNames[sf.agent_id] || sf.agent_id}
                </Link>
              ) : (
                'Any'
              )}
            </dd>
            <dt>Since</dt>
            <dd data-testid="dream-filter-since">
              {sf.since ? new Date(sf.since).toLocaleString() : 'Any'}
            </dd>
            <dt>Until</dt>
            <dd data-testid="dream-filter-until">
              {sf.until ? new Date(sf.until).toLocaleString() : 'Any'}
            </dd>
            <dt>Session Limit</dt>
            <dd data-testid="dream-filter-limit">{sf.limit}</dd>
          </dl>
        ) : (
          <p className="empty-text" data-testid="dream-filter-empty">
            No filters — the dreamer curates every past session (capped at {sf.limit}).
          </p>
        )}
      </section>

      <ConfirmDialog
        open={showCancelConfirm}
        title="Cancel Dream"
        message="Are you sure you want to cancel this dream? The dreamer session will be terminated and no output store will be produced."
        onConfirm={handleCancel}
        onCancel={() => setShowCancelConfirm(false)}
      />
    </div>
  );
}
