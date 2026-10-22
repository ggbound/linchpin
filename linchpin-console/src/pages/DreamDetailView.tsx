import { useEffect, useState, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { Link, useParams } from 'react-router-dom';
import { apiClient, ApiError } from '../api/client';
import type { Dream, Agent, MemoryStore, PaginatedResponse } from '../types';
import LoadingSpinner from '../components/LoadingSpinner';
import ErrorMessage from '../components/ErrorMessage';
import ConfirmDialog from '../components/ConfirmDialog';
import './DreamDetailView.css';

export default function DreamDetailView() {
  const { id } = useParams<{ id: string }>();
  const { t, i18n } = useTranslation('dreams');

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
      setActionError(apiErr.message || t('detailCancelFailed'));
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
        ← {t('detailBack')}
      </Link>

      <div className="dream-detail-header">
        <div className="dream-detail-title-row">
          <h1 data-testid="dream-title">{dream.output_store_name}</h1>
          <span className={`dream-status dream-status--${dream.status}`} data-testid="dream-status">
            {t(`status:${dream.status}`, { defaultValue: String(dream.status) })}
          </span>
        </div>
        <div className="dream-detail-meta">
          <span>{t('detailCreatedAt', { time: new Date(dream.created_at).toLocaleString(i18n.language) })}</span>
          {dream.started_at && (
            <>
              <span className="meta-separator">·</span>
              <span>{t('detailStartedAt', { time: new Date(dream.started_at).toLocaleString(i18n.language) })}</span>
            </>
          )}
          {dream.ended_at && (
            <>
              <span className="meta-separator">·</span>
              <span>{t('detailEndedAt', { time: new Date(dream.ended_at).toLocaleString(i18n.language) })}</span>
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
            {canceling ? t('detailCanceling') : t('detailCancelDream')}
          </button>
        </section>
      )}

      {dream.error && (
        <section className="detail-section" data-testid="dream-error-section">
          <h2>{t('common:error')}</h2>
          <p className="dream-error-text" data-testid="dream-error-text">{dream.error}</p>
        </section>
      )}

      <section className="detail-section" data-testid="dream-info-section">
        <h2>
          {t('detailConfiguration')}
          <button
            className="btn-refresh"
            onClick={handleRefresh}
            disabled={loading}
            data-testid="dream-refresh-btn"
          >
            {t('common:refresh')}
          </button>
        </h2>
        <dl className="detail-grid">
          <dt>{t('detailInputStore')}</dt>
          <dd>
            <Link
              to={`/memory-stores/${dream.input_memory_store_id}`}
              data-testid="dream-input-store-link"
            >
              {storeNames[dream.input_memory_store_id] || dream.input_memory_store_id}
            </Link>
          </dd>
          <dt>{t('detailOutputStoreName')}</dt>
          <dd><code>{dream.output_store_name}</code></dd>
          <dt>{t('detailOutputStore')}</dt>
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
          <dt>{t('detailDreamerAgent')}</dt>
          <dd data-testid="dream-dreamer-agent">
            {dream.dreamer_agent_id ? (
              <Link to={`/agents/${dream.dreamer_agent_id}`} data-testid="dream-agent-link">
                {agentNames[dream.dreamer_agent_id] || dream.dreamer_agent_id}
              </Link>
            ) : (
              t('detailPlatformDefault')
            )}
          </dd>
          {dream.dreamer_session_id && (
            <>
              <dt>{t('detailDreamerSession')}</dt>
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
        <h2>{t('detailSessionFilter')}</h2>
        {hasFilter ? (
          <dl className="detail-grid">
            <dt>{t('detailFilterAgent')}</dt>
            <dd data-testid="dream-filter-agent">
              {sf.agent_id ? (
                <Link to={`/agents/${sf.agent_id}`}>
                  {agentNames[sf.agent_id] || sf.agent_id}
                </Link>
              ) : (
                t('detailAny')
              )}
            </dd>
            <dt>{t('detailSince')}</dt>
            <dd data-testid="dream-filter-since">
              {sf.since ? new Date(sf.since).toLocaleString(i18n.language) : t('detailAny')}
            </dd>
            <dt>{t('detailUntil')}</dt>
            <dd data-testid="dream-filter-until">
              {sf.until ? new Date(sf.until).toLocaleString(i18n.language) : t('detailAny')}
            </dd>
            <dt>{t('detailSessionLimit')}</dt>
            <dd data-testid="dream-filter-limit">{sf.limit}</dd>
          </dl>
        ) : (
          <p className="empty-text" data-testid="dream-filter-empty">
            {t('detailNoFilters', { limit: sf.limit })}
          </p>
        )}
      </section>

      <ConfirmDialog
        open={showCancelConfirm}
        title={t('detailCancelTitle')}
        message={t('detailCancelMessage')}
        onConfirm={handleCancel}
        onCancel={() => setShowCancelConfirm(false)}
      />
    </div>
  );
}
