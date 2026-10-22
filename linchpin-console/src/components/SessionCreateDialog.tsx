import { Fragment, useEffect, useState } from 'react';
import { apiClient, ApiError } from '../api/client';
import type {
  Agent,
  Environment,
  Vault,
  Session,
  PaginatedResponse,
  FileRecord,
  SessionResourceConfig,
} from '../types';
import './SessionCreateDialog.css';

interface SessionCreateDialogProps {
  open: boolean;
  onClose: () => void;
  onCreated: (id: string) => void;
}

interface ResourceRow {
  kind: 'file' | 'git_repository';
  fileId: string;
  mountPath: string;
  gitUrl: string;
  gitBranch: string;
  gitToken: string;
  gitShallow: boolean;
}

const emptyResourceRow = (): ResourceRow => ({
  kind: 'file',
  fileId: '',
  mountPath: '',
  gitUrl: '',
  gitBranch: '',
  gitToken: '',
  gitShallow: true,
});

export default function SessionCreateDialog({ open, onClose, onCreated }: SessionCreateDialogProps) {
  const [agents, setAgents] = useState<Agent[]>([]);
  const [environments, setEnvironments] = useState<Environment[]>([]);
  const [vaults, setVaults] = useState<Vault[]>([]);
  const [uploadedFiles, setUploadedFiles] = useState<FileRecord[]>([]);
  const [agentId, setAgentId] = useState('');
  const [environmentId, setEnvironmentId] = useState('');
  const [title, setTitle] = useState('');
  const [ttl, setTtl] = useState('');
  const [selectedVaultIds, setSelectedVaultIds] = useState<string[]>([]);
  const [resourceRows, setResourceRows] = useState<ResourceRow[]>([]);
  const [resourceErrors, setResourceErrors] = useState<Record<number, string>>({});
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    // Reset form state when dialog opens
    setAgentId('');
    setEnvironmentId('');
    setTitle('');
    setTtl('');
    setSelectedVaultIds([]);
    setResourceRows([]);
    setResourceErrors({});
    setError(null);

    const load = async () => {
      try {
        const [agentRes, envRes, vaultRes, fileRes] = await Promise.all([
          apiClient.get<PaginatedResponse<Agent>>('/v1/agents'),
          apiClient.get<PaginatedResponse<Environment>>('/v1/environments'),
          apiClient.get<PaginatedResponse<Vault>>('/v1/vaults'),
          apiClient.get<PaginatedResponse<FileRecord>>('/v1/files?limit=100'),
        ]);
        setAgents(agentRes.data);
        setEnvironments(envRes.data);
        setVaults(vaultRes.data.filter(v => !v.archived_at));
        // Only source='upload' files may be mounted (deliverables cannot be re-mounted)
        setUploadedFiles(fileRes.data.filter(f => f.source === 'upload' && !f.archived_at));
      } catch (err) {
        const apiErr = err as ApiError;
        setError(apiErr.message || 'Failed to load agents or environments');
      }
    };
    load();
  }, [open]);

  if (!open) return null;

  const addResourceRow = () => setResourceRows([...resourceRows, emptyResourceRow()]);
  const removeResourceRow = (index: number) => {
    setResourceRows(resourceRows.filter((_, i) => i !== index));
    // Row indices shift on removal — drop row errors and re-validate on next submit.
    setResourceErrors({});
  };
  const updateResourceRow = (index: number, patch: Partial<ResourceRow>) => {
    setResourceRows(resourceRows.map((r, i) => (i === index ? { ...r, ...patch } : r)));
    setResourceErrors(prev => {
      if (!(index in prev)) return prev;
      const next = { ...prev };
      delete next[index];
      // All rows fixed — clear the stale banner set at last submit.
      if (Object.keys(next).length === 0) setError(null);
      return next;
    });
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!agentId || !environmentId) return;

    // Validate resource rows — incomplete rows must block submit instead of
    // being silently dropped.
    const rowErrors: Record<number, string> = {};
    resourceRows.forEach((row, i) => {
      if (row.kind === 'file') {
        if (!row.fileId) rowErrors[i] = 'Select a file to mount.';
      } else if (!row.gitUrl.trim()) {
        rowErrors[i] = 'Repository URL is required.';
      } else if (!row.mountPath.trim()) {
        rowErrors[i] = 'Mount path is required.';
      }
    });
    if (Object.keys(rowErrors).length > 0) {
      setResourceErrors(rowErrors);
      setError('Some resource mounts are incomplete — fix the highlighted rows.');
      return;
    }
    setResourceErrors({});

    // TTL sanity: a non-positive TTL would make the reaper expire the session
    // immediately, and a non-numeric value silently becomes null server-side.
    const ttlTrimmed = ttl.trim();
    if (ttlTrimmed) {
      const ttlNum = Number(ttlTrimmed);
      if (!Number.isFinite(ttlNum) || ttlNum < 1) {
        setError('TTL must be a positive number of seconds (at least 1).');
        return;
      }
    }

    setSubmitting(true);
    setError(null);

    // Build resources[] — vaults + file/git rows (vault_ids is deprecated server-side)
    const resources: SessionResourceConfig[] = selectedVaultIds.map((vaultId) => ({
      type: 'vault',
      vault_id: vaultId,
    }));
    for (const row of resourceRows) {
      if (row.kind === 'file' && row.fileId) {
        const entry: SessionResourceConfig = { type: 'file', file_id: row.fileId };
        if (row.mountPath.trim()) entry.mount_path = row.mountPath.trim();
        resources.push(entry);
      } else if (row.kind === 'git_repository' && row.gitUrl.trim() && row.mountPath.trim()) {
        const entry: SessionResourceConfig = {
          type: 'git_repository',
          url: row.gitUrl.trim(),
          mount_path: row.mountPath.trim(),
          shallow: row.gitShallow,
        };
        if (row.gitBranch.trim()) entry.branch = row.gitBranch.trim();
        if (row.gitToken.trim()) entry.authorization_token = row.gitToken.trim();
        resources.push(entry);
      }
    }

    try {
      const body: Record<string, unknown> = {
        agent_id: agentId,
        environment_id: environmentId,
      };
      if (title.trim()) body.title = title.trim();
      if (ttl.trim()) body.ttl_seconds = Number(ttl);
      if (resources.length > 0) body.resources = resources;

      const session = await apiClient.post<Session>('/v1/sessions', body);
      onCreated(session.id);
    } catch (err) {
      const apiErr = err as ApiError;
      setError(apiErr.message || 'Failed to create session');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="dialog-overlay" data-testid="session-create-overlay" onClick={onClose}>
      <div
        className="dialog-panel"
        data-testid="session-create-dialog"
        role="dialog"
        aria-labelledby="session-create-title"
        onClick={e => e.stopPropagation()}
      >
        <h2 id="session-create-title" className="dialog-title">New Session</h2>

        <form onSubmit={handleSubmit} className="dialog-form">
          <div className="form-field">
            <label htmlFor="session-agent">Agent</label>
            <select
              id="session-agent"
              value={agentId}
              onChange={e => setAgentId(e.target.value)}
              required
              data-testid="session-agent-select"
            >
              <option value="">Select an agent</option>
              {agents.map(a => (
                <option key={a.id} value={a.id}>
                  {a.name} ({a.id.slice(0, 8)})
                </option>
              ))}
            </select>
          </div>

          <div className="form-field">
            <label htmlFor="session-environment">Environment</label>
            <select
              id="session-environment"
              value={environmentId}
              onChange={e => setEnvironmentId(e.target.value)}
              required
              data-testid="session-environment-select"
            >
              <option value="">Select an environment</option>
              {environments.map(env => (
                <option key={env.id} value={env.id}>
                  {env.name} ({env.id.slice(0, 8)})
                </option>
              ))}
            </select>
          </div>

          <div className="form-field">
            <label htmlFor="session-title">Title (optional)</label>
            <input
              id="session-title"
              type="text"
              value={title}
              onChange={e => setTitle(e.target.value)}
              placeholder="Session title"
              data-testid="session-title-input"
            />
          </div>

          <div className="form-field">
            <label htmlFor="session-ttl">TTL in seconds (optional)</label>
            <input
              id="session-ttl"
              type="number"
              min="1"
              step="1"
              value={ttl}
              onChange={e => setTtl(e.target.value)}
              placeholder="e.g. 3600"
              data-testid="session-ttl-input"
            />
            <p className="dialog-hint">
              Idle sessions are reaped after this many seconds. Must be at least 1.
            </p>
          </div>

          <div className="form-field">
            <label>Credential Vaults (optional)</label>
            <div className="vault-checkbox-list" data-testid="session-vault-select">
              {vaults.length === 0 ? (
                <span className="vault-checkbox-empty">No vaults available</span>
              ) : (
                vaults.map(v => (
                  <label key={v.id} className="vault-checkbox-item">
                    <input
                      type="checkbox"
                      checked={selectedVaultIds.includes(v.id)}
                      onChange={e => {
                        if (e.target.checked) {
                          setSelectedVaultIds(prev => [...prev, v.id]);
                        } else {
                          setSelectedVaultIds(prev => prev.filter(id => id !== v.id));
                        }
                      }}
                      data-testid={`vault-checkbox-${v.id}`}
                    />
                    <span>{v.display_name}</span>
                    <code className="vault-checkbox-id">{v.id.slice(0, 8)}</code>
                  </label>
                ))
              )}
            </div>
          </div>

          <div className="form-field">
            <label>Resource Mounts (optional)</label>
            {resourceRows.map((row, i) => (
              <Fragment key={i}>
                <div
                  className={`resource-row${resourceErrors[i] ? ' resource-row-invalid' : ''}`}
                  data-testid="resource-row"
                >
                  <select
                    className="resource-kind"
                    value={row.kind}
                    onChange={e => updateResourceRow(i, { kind: e.target.value as ResourceRow['kind'] })}
                    data-testid="resource-kind-select"
                  >
                    <option value="file">File (uploaded)</option>
                    <option value="git_repository">Git repository</option>
                  </select>
                  {row.kind === 'file' ? (
                    <>
                      <select
                        value={row.fileId}
                        onChange={e => updateResourceRow(i, { fileId: e.target.value })}
                        data-testid="resource-file-select"
                      >
                        <option value="">Select uploaded file</option>
                        {uploadedFiles.map(f => (
                          <option key={f.id} value={f.id}>
                            {f.filename} ({f.id.slice(0, 8)})
                          </option>
                        ))}
                      </select>
                      <input
                        type="text"
                        placeholder="Mount path (optional)"
                        value={row.mountPath}
                        onChange={e => updateResourceRow(i, { mountPath: e.target.value })}
                        data-testid="resource-mount-path"
                      />
                    </>
                  ) : (
                    <>
                      <input
                        type="text"
                        placeholder="https://github.com/org/repo.git"
                        value={row.gitUrl}
                        onChange={e => updateResourceRow(i, { gitUrl: e.target.value })}
                        data-testid="resource-git-url"
                      />
                      <input
                        type="text"
                        placeholder="/src/repo (required)"
                        value={row.mountPath}
                        onChange={e => updateResourceRow(i, { mountPath: e.target.value })}
                        data-testid="resource-git-mount"
                      />
                      <input
                        type="text"
                        placeholder="Branch (optional)"
                        value={row.gitBranch}
                        onChange={e => updateResourceRow(i, { gitBranch: e.target.value })}
                        data-testid="resource-git-branch"
                      />
                      <input
                        type="password"
                        placeholder="Token (optional)"
                        value={row.gitToken}
                        onChange={e => updateResourceRow(i, { gitToken: e.target.value })}
                        data-testid="resource-git-token"
                      />
                      <label className="resource-shallow">
                        <input
                          type="checkbox"
                          checked={row.gitShallow}
                          onChange={e => updateResourceRow(i, { gitShallow: e.target.checked })}
                          data-testid="resource-git-shallow"
                        />
                        Shallow clone
                      </label>
                    </>
                  )}
                  <button
                    type="button"
                    className="resource-remove"
                    onClick={() => removeResourceRow(i)}
                    aria-label="Remove resource"
                    data-testid="resource-remove"
                  >
                    ✕
                  </button>
                </div>
                {resourceErrors[i] && (
                  <p className="resource-row-error" data-testid={`resource-row-error-${i}`}>
                    {resourceErrors[i]}
                  </p>
                )}
              </Fragment>
            ))}
            <button
              type="button"
              className="resource-add-btn"
              onClick={addResourceRow}
              data-testid="resource-add"
            >
              + Add resource
            </button>
          </div>

          {error && (
            <div className="dialog-error" data-testid="session-create-error" role="alert">
              {error}
            </div>
          )}

          <div className="dialog-actions">
            <button
              type="button"
              className="dialog-cancel-btn"
              onClick={onClose}
              data-testid="session-create-cancel"
            >
              Cancel
            </button>
            <button
              type="submit"
              className="btn-primary"
              disabled={submitting || !agentId || !environmentId}
              data-testid="session-create-submit"
            >
              {submitting ? 'Creating…' : 'Create session'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
