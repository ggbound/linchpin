import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { apiClient, ApiError } from '../api/client';
import type { ApiKey, ApiKeyCreated, Project, UpdateProjectRequest } from '../types';
import LoadingSpinner from '../components/LoadingSpinner';
import ErrorMessage from '../components/ErrorMessage';
import ConfirmDialog from '../components/ConfirmDialog';
import './ApiKeysView.css';

interface QuotaDraft {
  max: string;
  budget: string;
}

// Mirrors the backend's centralized scope enforcement (app/auth.py
// _check_scopes): mutating requests to /v1/{resource}/... require
// "{resource}:write" or "*".
const SCOPE_OPTIONS: Array<{ value: string; labelKey: string }> = [
  { value: '*', labelKey: 'scopeAll' },
  { value: 'agents:write', labelKey: 'scopeAgents' },
  { value: 'sessions:write', labelKey: 'scopeSessions' },
  { value: 'environments:write', labelKey: 'scopeEnvironments' },
  { value: 'memory_stores:write', labelKey: 'scopeMemoryStores' },
  { value: 'vaults:write', labelKey: 'scopeVaults' },
  { value: 'deployments:write', labelKey: 'scopeDeployments' },
  { value: 'webhook_endpoints:write', labelKey: 'scopeWebhookEndpoints' },
  { value: 'skills:write', labelKey: 'scopeSkills' },
  { value: 'dreams:write', labelKey: 'scopeDreams' },
  { value: 'files:write', labelKey: 'scopeFiles' },
  { value: 'artifacts:write', labelKey: 'scopeArtifacts' },
  { value: 'executor-nodes:write', labelKey: 'scopeExecutorNodes' },
];

export default function ApiKeysView() {
  const { t, i18n } = useTranslation('apiKeys');
  const [keys, setKeys] = useState<ApiKey[]>([]);
  const [projects, setProjects] = useState<Project[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<ApiError | null>(null);

  // Create form
  const [name, setName] = useState('');
  const [projectId, setProjectId] = useState('');
  const [scopesSelected, setScopesSelected] = useState<string[]>(['*']);
  const [rpmInput, setRpmInput] = useState('');
  const [creating, setCreating] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  // One-time plaintext key display
  const [createdKey, setCreatedKey] = useState<ApiKeyCreated | null>(null);
  const [keyCopied, setKeyCopied] = useState(false);

  // Revoke
  const [revokeTarget, setRevokeTarget] = useState<ApiKey | null>(null);
  const [revoking, setRevoking] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  // Project quota drafts
  const [quotaDrafts, setQuotaDrafts] = useState<Record<string, QuotaDraft>>({});
  const [savingQuotaId, setSavingQuotaId] = useState<string | null>(null);
  const [quotaNotice, setQuotaNotice] = useState<string | null>(null);
  const [quotaError, setQuotaError] = useState<string | null>(null);

  const fetchData = useCallback(async () => {
    setLoading(true);
    try {
      const [keysRes, projectsRes] = await Promise.all([
        apiClient.get<ApiKey[]>('/v1/api-keys'),
        apiClient.get<Project[]>('/v1/projects'),
      ]);
      setKeys(keysRes);
      setProjects(projectsRes);
      setQuotaDrafts(Object.fromEntries(
        projectsRes.map(p => [p.id, {
          max: p.max_concurrent_sessions != null ? String(p.max_concurrent_sessions) : '',
          budget: p.monthly_token_budget != null ? String(p.monthly_token_budget) : '',
        }]),
      ));
      setError(null);
    } catch (err) {
      setError(err as ApiError);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  const handleCreateKey = async (e: React.FormEvent) => {
    e.preventDefault();
    setFormError(null);
    setQuotaNotice(null);

    const scopes = scopesSelected;
    const rpm = rpmInput.trim() ? Number(rpmInput.trim()) : null;
    if (rpm !== null && (!Number.isFinite(rpm) || rpm <= 0)) {
      setFormError(t('rateLimitInvalid'));
      return;
    }
    if (scopes.length === 0) {
      setFormError(t('scopesRequired'));
      return;
    }

    setCreating(true);
    try {
      const created = await apiClient.post<ApiKeyCreated>('/v1/api-keys', {
        ...(name.trim() ? { name: name.trim() } : {}),
        ...(projectId ? { project_id: projectId } : {}),
        ...(scopes.length > 0 ? { scopes } : {}),
        ...(rpm !== null ? { rate_limit_rpm: rpm } : {}),
      });
      setKeys(prev => [created, ...prev]);
      setCreatedKey(created);
      setKeyCopied(false);
      setName('');
      setProjectId('');
      setScopesSelected(['*']);
      setRpmInput('');
    } catch (err) {
      const apiErr = err as ApiError;
      setFormError(apiErr.message || t('createFailed'));
    } finally {
      setCreating(false);
    }
  };

  const handleCopyKey = async () => {
    if (!createdKey) return;
    try {
      await navigator.clipboard.writeText(createdKey.key);
      setKeyCopied(true);
      setTimeout(() => setKeyCopied(false), 2000);
    } catch {
      // Clipboard unavailable — user can select the text manually.
    }
  };

  const handleRevoke = async () => {
    if (!revokeTarget) return;
    setRevoking(true);
    setActionError(null);
    try {
      await apiClient.delete(`/v1/api-keys/${revokeTarget.id}`);
      setKeys(prev => prev.filter(k => k.id !== revokeTarget.id));
      setRevokeTarget(null);
    } catch (err) {
      const apiErr = err as ApiError;
      setActionError(apiErr.message || t('revokeFailed'));
      setRevokeTarget(null);
    } finally {
      setRevoking(false);
    }
  };

  const updateQuotaDraft = (pid: string, field: 'max' | 'budget', value: string) => {
    setQuotaDrafts(prev => ({
      ...prev,
      [pid]: { ...prev[pid], [field]: value },
    }));
  };

  const handleSaveQuota = async (project: Project) => {
    const draft = quotaDrafts[project.id];
    if (!draft) return;
    setSavingQuotaId(project.id);
    setQuotaNotice(null);
    setQuotaError(null);
    try {
      const body: UpdateProjectRequest = {
        max_concurrent_sessions: draft.max.trim() === '' ? null : Number(draft.max.trim()),
        monthly_token_budget: draft.budget.trim() === '' ? null : Number(draft.budget.trim()),
      };
      if (body.max_concurrent_sessions !== null && !Number.isFinite(body.max_concurrent_sessions)) {
        setQuotaError(t('invalidMaxSessions', { name: project.name }));
        return;
      }
      if (body.monthly_token_budget !== null && !Number.isFinite(body.monthly_token_budget)) {
        setQuotaError(t('invalidTokenBudget', { name: project.name }));
        return;
      }
      const updated = await apiClient.patch<Project>(`/v1/projects/${project.id}`, body);
      setProjects(prev => prev.map(p => (p.id === updated.id ? updated : p)));
      setQuotaDrafts(prev => ({
        ...prev,
        [updated.id]: {
          max: updated.max_concurrent_sessions != null ? String(updated.max_concurrent_sessions) : '',
          budget: updated.monthly_token_budget != null ? String(updated.monthly_token_budget) : '',
        },
      }));
      setQuotaNotice(t('quotaUpdated', { name: project.name }));
    } catch (err) {
      const apiErr = err as ApiError;
      if (apiErr.status === 403) {
        setQuotaError(t('quotaRequiresBootstrapKey'));
      } else {
        setQuotaError(apiErr.message || t('quotaUpdateFailed'));
      }
    } finally {
      setSavingQuotaId(null);
    }
  };

  if (loading) return <LoadingSpinner />;

  if (error) {
    return (
      <ErrorMessage
        status={error.status}
        message={error.message}
        onRetry={fetchData}
      />
    );
  }

  return (
    <div className="apikeys-view" data-testid="apikeys-view">
      <div className="apikeys-header">
        <h1>{t('title')}</h1>
        <p>{t('subtitle')}</p>
      </div>

      {createdKey && (
        <div className="created-key-banner" data-testid="created-key-banner">
          <div className="created-key-header">
            <strong>
              {t('createdBanner', { name: createdKey.name || t('defaultKeyName') })}
            </strong>
            <button
              className="created-key-dismiss"
              onClick={() => setCreatedKey(null)}
              aria-label={t('dismiss')}
              data-testid="created-key-dismiss"
            >
              ✕
            </button>
          </div>
          <div className="created-key-row">
            <code data-testid="created-key-value">{createdKey.key}</code>
            <button className="btn-copy-key" onClick={handleCopyKey}>
              {keyCopied ? t('common:copied') : t('common:copy')}
            </button>
          </div>
        </div>
      )}

      {actionError && (
        <div className="form-error" role="alert" data-testid="apikeys-action-error">
          {actionError}
        </div>
      )}

      <section className="detail-section" data-testid="apikey-create-section">
        <h2>{t('createTitle')}</h2>
        <form className="apikey-create-form" onSubmit={handleCreateKey}>
          <div className="apikey-form-row">
            <div className="apikey-form-field">
              <label htmlFor="apikey-name">{t('common:name')}</label>
              <input
                id="apikey-name"
                type="text"
                value={name}
                onChange={e => setName(e.target.value)}
                placeholder={t('namePlaceholder')}
                data-testid="apikey-create-name"
              />
            </div>
            <div className="apikey-form-field">
              <label htmlFor="apikey-project">{t('projectLabel')}</label>
              <select
                id="apikey-project"
                value={projectId}
                onChange={e => setProjectId(e.target.value)}
                data-testid="apikey-create-project"
              >
                <option value="">{t('noneOption')}</option>
                {projects.map(p => (
                  <option key={p.id} value={p.id}>{p.name}</option>
                ))}
              </select>
            </div>
          </div>
          <div className="apikey-form-row">
            <div className="apikey-form-field apikey-scopes-field">
              <span className="apikey-form-field-label">{t('scopes')}</span>
              <div className="scope-checks" role="group" aria-label={t('scopes')} data-testid="apikey-create-scopes">
                {SCOPE_OPTIONS.map(opt => (
                  <label key={opt.value} className="scope-check">
                    <input
                      type="checkbox"
                      checked={scopesSelected.includes(opt.value)}
                      onChange={e =>
                        setScopesSelected(prev =>
                          e.target.checked
                            ? [...prev, opt.value]
                            : prev.filter(s => s !== opt.value),
                        )
                      }
                    />
                    <span>{t(opt.labelKey)}</span>
                  </label>
                ))}
              </div>
              <p className="apikey-scopes-hint">
                {t('scopesHint')}
              </p>
              {scopesSelected.length === 0 && (
                <div className="form-error" role="alert" data-testid="apikey-scopes-error">
                  {t('scopesRequired')}
                </div>
              )}
            </div>
            <div className="apikey-form-field">
              <label htmlFor="apikey-rpm">{t('rateLimitField')}</label>
              <input
                id="apikey-rpm"
                type="number"
                min={1}
                value={rpmInput}
                onChange={e => setRpmInput(e.target.value)}
                placeholder={t('unlimited')}
                data-testid="apikey-create-rpm"
              />
            </div>
          </div>
          {formError && (
            <div className="form-error" role="alert" data-testid="apikey-create-error">
              {formError}
            </div>
          )}
          <button
            type="submit"
            className="btn-primary apikey-create-submit"
            disabled={creating || scopesSelected.length === 0}
            data-testid="apikey-create-submit"
          >
            {creating ? t('common:creating') : t('createKey')}
          </button>
        </form>
      </section>

      <section className="detail-section" data-testid="apikeys-list-section">
        <h2>{t('keysCount', { total: keys.length })}</h2>
        {keys.length === 0 ? (
          <p className="empty-text">{t('emptyKeys')}</p>
        ) : (
          <div className="apikeys-table-wrapper">
            <table className="apikeys-table" data-testid="apikeys-table">
              <thead>
                <tr>
                  <th>{t('common:name')}</th>
                  <th>{t('prefix')}</th>
                  <th>{t('scopes')}</th>
                  <th>{t('statusLabel')}</th>
                  <th>{t('rateLimit')}</th>
                  <th>{t('lastUsed')}</th>
                  <th>{t('common:actions')}</th>
                </tr>
              </thead>
              <tbody>
                {keys.map(key => (
                  <tr key={key.id} data-testid="apikey-row">
                    <td className="apikey-name-cell">{key.name || '—'}</td>
                    <td><code>{key.prefix}…</code></td>
                    <td className="apikey-scopes-cell">{key.scopes.join(', ')}</td>
                    <td>
                      <span
                        className={`apikey-status apikey-status--${key.status}`}
                        data-testid={`apikey-status-${key.id}`}
                      >
                        {key.status}
                      </span>
                    </td>
                    <td>{key.rate_limit_rpm != null ? `${key.rate_limit_rpm} rpm` : '—'}</td>
                    <td>
                      {key.last_used_at
                        ? new Date(key.last_used_at).toLocaleString(i18n.language)
                        : t('never')}
                    </td>
                    <td>
                      <button
                        className="btn-sm-danger"
                        disabled={revoking || key.status !== 'active'}
                        onClick={() => setRevokeTarget(key)}
                        data-testid={`apikey-revoke-btn-${key.id}`}
                      >
                        {t('revoke')}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="detail-section" data-testid="projects-quota-section">
        <h2>{t('projectQuotas')}</h2>
        <p className="section-hint">
          {t('quotaHint')}
        </p>
        {quotaNotice && (
          <div className="skill-notice" role="status" data-testid="quota-notice">
            {quotaNotice}
          </div>
        )}
        {quotaError && (
          <div className="form-error" role="alert" data-testid="quota-error">
            {quotaError}
          </div>
        )}
        {projects.length === 0 ? (
          <p className="empty-text">{t('noProjects')}</p>
        ) : (
          <table className="quota-table" data-testid="quota-table">
            <thead>
              <tr>
                <th>{t('project')}</th>
                <th>{t('maxConcurrentSessions')}</th>
                <th>{t('monthlyTokenBudget')}</th>
                <th>{t('common:actions')}</th>
              </tr>
            </thead>
            <tbody>
              {projects.map(project => (
                <tr key={project.id} data-testid="project-quota-row">
                  <td>
                    <span className="quota-project-name">{project.name}</span>
                    <span className="quota-project-slug">{project.slug}</span>
                  </td>
                  <td>
                    <input
                      type="number"
                      min={1}
                      className="quota-input"
                      value={quotaDrafts[project.id]?.max ?? ''}
                      onChange={e => updateQuotaDraft(project.id, 'max', e.target.value)}
                      placeholder={t('unlimited')}
                      data-testid={`quota-max-input-${project.id}`}
                    />
                  </td>
                  <td>
                    <input
                      type="number"
                      min={0}
                      className="quota-input"
                      value={quotaDrafts[project.id]?.budget ?? ''}
                      onChange={e => updateQuotaDraft(project.id, 'budget', e.target.value)}
                      placeholder={t('unlimited')}
                      data-testid={`quota-budget-input-${project.id}`}
                    />
                  </td>
                  <td>
                    <button
                      className="btn-sm-secondary"
                      disabled={savingQuotaId === project.id}
                      onClick={() => handleSaveQuota(project)}
                      data-testid={`quota-save-btn-${project.id}`}
                    >
                      {savingQuotaId === project.id ? t('common:saving') : t('common:save')}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <ConfirmDialog
        open={revokeTarget !== null}
        title={t('revokeTitle')}
        message={
          revokeTarget
            ? t('revokeMessage', { name: revokeTarget.name || `${revokeTarget.prefix}…` })
            : ''
        }
        onConfirm={handleRevoke}
        onCancel={() => setRevokeTarget(null)}
      />
    </div>
  );
}
