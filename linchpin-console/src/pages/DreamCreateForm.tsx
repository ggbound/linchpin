import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link, useNavigate } from 'react-router-dom';
import { apiClient, ApiError } from '../api/client';
import type { Agent, MemoryStore, Dream, PaginatedResponse } from '../types';
import LoadingSpinner from '../components/LoadingSpinner';
import ErrorMessage from '../components/ErrorMessage';
import './DreamCreateForm.css';

interface StoreListResponse {
  data: MemoryStore[];
  next_cursor: string | null;
}

export default function DreamCreateForm() {
  const navigate = useNavigate();
  const { t } = useTranslation('dreams');

  const [stores, setStores] = useState<MemoryStore[]>([]);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<ApiError | null>(null);

  const [inputStoreId, setInputStoreId] = useState('');
  const [outputName, setOutputName] = useState('');
  const [dreamerAgentId, setDreamerAgentId] = useState('');
  const [filterAgentId, setFilterAgentId] = useState('');
  const [since, setSince] = useState('');
  const [until, setUntil] = useState('');
  const [limit, setLimit] = useState('');
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const [storeRes, agentRes] = await Promise.all([
          apiClient.get<StoreListResponse>('/v1/memory_stores', {
            include_archived: 'false',
            limit: '100',
          }),
          apiClient.get<PaginatedResponse<Agent>>('/v1/agents?limit=100'),
        ]);
        if (cancelled) return;
        const activeStores = storeRes.data.filter(s => !s.archived_at);
        const activeAgents = agentRes.data.filter(a => !a.archived_at);
        setStores(activeStores);
        setAgents(activeAgents);
        if (activeStores.length > 0) setInputStoreId(prev => prev || activeStores[0].id);
        setError(null);
      } catch (err) {
        if (!cancelled) setError(err as ApiError);
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    load();
    return () => { cancelled = true; };
  }, []);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (since && until && new Date(since) > new Date(until)) {
      setCreateError(t('createSinceUntilError'));
      return;
    }
    setCreating(true);
    setCreateError(null);

    try {
      const filter: Record<string, unknown> = {};
      if (filterAgentId) filter.agent_id = filterAgentId;
      if (since) filter.since = new Date(since).toISOString();
      if (until) filter.until = new Date(until).toISOString();
      if (limit.trim()) {
        const n = Number(limit);
        if (!Number.isInteger(n) || n < 1 || n > 100) {
          setCreateError(t('createLimitError'));
          setCreating(false);
          return;
        }
        filter.limit = n;
      }
      const hasFilter = Object.keys(filter).length > 0;

      const body = {
        input_memory_store_id: inputStoreId,
        output_store_name: outputName.trim(),
        ...(hasFilter && { session_filter: filter }),
        ...(dreamerAgentId && { dreamer_agent_id: dreamerAgentId }),
      };

      const created = await apiClient.post<Dream>('/v1/dreams', body);
      navigate(`/dreams/${created.id}`);
    } catch (err) {
      const apiErr = err as ApiError;
      let message = apiErr.message || t('createFailed');
      if (apiErr.status === 409) {
        message = t('createNameConflict', { name: outputName.trim() });
      } else if (message.includes('dreamer_not_configured')) {
        message = t('createDreamerNotConfigured');
      }
      setCreateError(message);
      setCreating(false);
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
          window.location.reload();
        }}
      />
    );
  }

  return (
    <div className="dream-create-view" data-testid="dream-create-view">
      <Link to="/dreams" className="back-link" data-testid="back-link">
        ← {t('createBack')}
      </Link>

      <h1 className="dream-create-title">{t('createTitle')}</h1>
      <p className="dream-create-subtitle">{t('createSubtitle')}</p>

      <form className="dream-form" onSubmit={handleSubmit} data-testid="dream-create-form">
        <div className="dream-form-row">
          <div className="form-field">
            <label htmlFor="dream-input-store">{t('createInputStoreLabel')}</label>
            <select
              id="dream-input-store"
              value={inputStoreId}
              onChange={e => setInputStoreId(e.target.value)}
              required
              data-testid="dream-input-store-select"
            >
              {stores.length === 0 && <option value="">{t('createNoStores')}</option>}
              {stores.map(s => (
                <option key={s.id} value={s.id}>{s.name}</option>
              ))}
            </select>
            <p className="section-hint">
              {t('createInputStoreHint')}
            </p>
          </div>
          <div className="form-field">
            <label htmlFor="dream-output-name">{t('createOutputNameLabel')}</label>
            <input
              id="dream-output-name"
              type="text"
              value={outputName}
              onChange={e => setOutputName(e.target.value)}
              placeholder="curated-insights"
              required
              data-testid="dream-output-name-input"
            />
            <p className="section-hint">
              {t('createOutputNameHint')}
            </p>
          </div>
        </div>

        <div className="form-field">
          <label htmlFor="dream-agent">{t('createDreamerAgentLabel')}</label>
          <select
            id="dream-agent"
            value={dreamerAgentId}
            onChange={e => setDreamerAgentId(e.target.value)}
            data-testid="dream-agent-select"
          >
            <option value="">{t('createPlatformDefault')}</option>
            {agents.map(a => (
              <option key={a.id} value={a.id}>{a.name}</option>
            ))}
          </select>
          <p className="section-hint">
            {t('createDreamerAgentHint')}
          </p>
        </div>

        <div className="dream-filter-section">
          <h2>{t('createSessionFilterTitle')}</h2>
          <p className="section-hint">
            {t('createSessionFilterHint')}
          </p>
          <div className="dream-form-row">
            <div className="form-field">
              <label htmlFor="dream-filter-agent">{t('createFilterAgentLabel')}</label>
              <select
                id="dream-filter-agent"
                value={filterAgentId}
                onChange={e => setFilterAgentId(e.target.value)}
                data-testid="dream-filter-agent-select"
              >
                <option value="">{t('createAnyAgent')}</option>
                {agents.map(a => (
                  <option key={a.id} value={a.id}>{a.name}</option>
                ))}
              </select>
            </div>
            <div className="form-field">
              <label htmlFor="dream-filter-limit">{t('createSessionLimitLabel')}</label>
              <input
                id="dream-filter-limit"
                type="number"
                min={1}
                max={100}
                value={limit}
                onChange={e => setLimit(e.target.value)}
                placeholder="100"
                data-testid="dream-filter-limit-input"
              />
            </div>
          </div>
          <div className="dream-form-row">
            <div className="form-field">
              <label htmlFor="dream-filter-since">{t('createSinceLabel')}</label>
              <input
                id="dream-filter-since"
                type="datetime-local"
                value={since}
                onChange={e => setSince(e.target.value)}
                data-testid="dream-filter-since-input"
              />
            </div>
            <div className="form-field">
              <label htmlFor="dream-filter-until">{t('createUntilLabel')}</label>
              <input
                id="dream-filter-until"
                type="datetime-local"
                value={until}
                onChange={e => setUntil(e.target.value)}
                data-testid="dream-filter-until-input"
              />
            </div>
          </div>
        </div>

        {createError && (
          <div className="form-error" role="alert" data-testid="dream-create-error">
            {createError}
          </div>
        )}

        <div className="dream-form-actions">
          <button
            type="submit"
            className="btn-primary"
            disabled={creating || !inputStoreId || !outputName.trim()}
            data-testid="dream-create-submit"
          >
            {creating ? t('common:creating') : t('createSubmit')}
          </button>
          <Link to="/dreams" className="btn-cancel-link">{t('common:cancel')}</Link>
        </div>
      </form>
    </div>
  );
}
