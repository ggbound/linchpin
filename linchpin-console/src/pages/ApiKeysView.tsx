import { useCallback, useEffect, useState } from 'react';
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

export default function ApiKeysView() {
  const [keys, setKeys] = useState<ApiKey[]>([]);
  const [projects, setProjects] = useState<Project[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<ApiError | null>(null);

  // Create form
  const [name, setName] = useState('');
  const [projectId, setProjectId] = useState('');
  const [scopesInput, setScopesInput] = useState('*');
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

    const scopes = scopesInput.split(',').map(s => s.trim()).filter(Boolean);
    const rpm = rpmInput.trim() ? Number(rpmInput.trim()) : null;
    if (rpm !== null && (!Number.isFinite(rpm) || rpm <= 0)) {
      setFormError('Rate limit must be a positive number (requests per minute).');
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
      setScopesInput('*');
      setRpmInput('');
    } catch (err) {
      const apiErr = err as ApiError;
      setFormError(apiErr.message || 'Failed to create API key');
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
      setActionError(apiErr.message || 'Failed to revoke API key');
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
        setQuotaError(`Invalid value for "${project.name}" max concurrent sessions.`);
        return;
      }
      if (body.monthly_token_budget !== null && !Number.isFinite(body.monthly_token_budget)) {
        setQuotaError(`Invalid value for "${project.name}" monthly token budget.`);
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
      setQuotaNotice(`Quota updated for "${project.name}". Leaving a field empty clears the limit.`);
    } catch (err) {
      const apiErr = err as ApiError;
      if (apiErr.status === 403) {
        setQuotaError('Quota management requires the bootstrap API key.');
      } else {
        setQuotaError(apiErr.message || 'Failed to update project quota');
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
        <h1>API Keys</h1>
        <p>
          Keys authenticate console and API clients. The plaintext key is shown once
          at creation — store it securely.
        </p>
      </div>

      {createdKey && (
        <div className="created-key-banner" data-testid="created-key-banner">
          <div className="created-key-header">
            <strong>
              {createdKey.name || 'New API key'} created — copy it now, it will not be shown again
            </strong>
            <button
              className="created-key-dismiss"
              onClick={() => setCreatedKey(null)}
              aria-label="Dismiss"
              data-testid="created-key-dismiss"
            >
              ✕
            </button>
          </div>
          <div className="created-key-row">
            <code data-testid="created-key-value">{createdKey.key}</code>
            <button className="btn-copy-key" onClick={handleCopyKey}>
              {keyCopied ? 'Copied' : 'Copy'}
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
        <h2>Create API Key</h2>
        <form className="apikey-create-form" onSubmit={handleCreateKey}>
          <div className="apikey-form-row">
            <div className="apikey-form-field">
              <label htmlFor="apikey-name">Name</label>
              <input
                id="apikey-name"
                type="text"
                value={name}
                onChange={e => setName(e.target.value)}
                placeholder="e.g. ci-pipeline"
                data-testid="apikey-create-name"
              />
            </div>
            <div className="apikey-form-field">
              <label htmlFor="apikey-project">Project (optional)</label>
              <select
                id="apikey-project"
                value={projectId}
                onChange={e => setProjectId(e.target.value)}
                data-testid="apikey-create-project"
              >
                <option value="">— none —</option>
                {projects.map(p => (
                  <option key={p.id} value={p.id}>{p.name}</option>
                ))}
              </select>
            </div>
          </div>
          <div className="apikey-form-row">
            <div className="apikey-form-field">
              <label htmlFor="apikey-scopes">Scopes (comma-separated)</label>
              <input
                id="apikey-scopes"
                type="text"
                value={scopesInput}
                onChange={e => setScopesInput(e.target.value)}
                placeholder="*"
                data-testid="apikey-create-scopes"
              />
            </div>
            <div className="apikey-form-field">
              <label htmlFor="apikey-rpm">Rate limit (RPM, optional)</label>
              <input
                id="apikey-rpm"
                type="number"
                min={1}
                value={rpmInput}
                onChange={e => setRpmInput(e.target.value)}
                placeholder="unlimited"
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
            disabled={creating}
            data-testid="apikey-create-submit"
          >
            {creating ? 'Creating…' : 'Create key'}
          </button>
        </form>
      </section>

      <section className="detail-section" data-testid="apikeys-list-section">
        <h2>Keys ({keys.length})</h2>
        {keys.length === 0 ? (
          <p className="empty-text">No API keys yet. Create one above.</p>
        ) : (
          <div className="apikeys-table-wrapper">
            <table className="apikeys-table" data-testid="apikeys-table">
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Prefix</th>
                  <th>Scopes</th>
                  <th>Status</th>
                  <th>Rate Limit</th>
                  <th>Last Used</th>
                  <th>Actions</th>
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
                        ? new Date(key.last_used_at).toLocaleString()
                        : 'never'}
                    </td>
                    <td>
                      <button
                        className="btn-sm-danger"
                        disabled={revoking || key.status !== 'active'}
                        onClick={() => setRevokeTarget(key)}
                        data-testid={`apikey-revoke-btn-${key.id}`}
                      >
                        Revoke
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
        <h2>Project Quotas</h2>
        <p className="section-hint">
          Limits per project. Leave a field empty to clear the limit (sends an
          explicit null). Quota changes require the bootstrap API key.
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
          <p className="empty-text">No projects found.</p>
        ) : (
          <table className="quota-table" data-testid="quota-table">
            <thead>
              <tr>
                <th>Project</th>
                <th>Max Concurrent Sessions</th>
                <th>Monthly Token Budget</th>
                <th>Actions</th>
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
                      placeholder="unlimited"
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
                      placeholder="unlimited"
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
                      {savingQuotaId === project.id ? 'Saving…' : 'Save'}
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
        title="Revoke API Key"
        message={
          revokeTarget
            ? `Are you sure you want to revoke "${revokeTarget.name || revokeTarget.prefix}…"? Clients using it will immediately lose access.`
            : ''
        }
        onConfirm={handleRevoke}
        onCancel={() => setRevokeTarget(null)}
      />
    </div>
  );
}
