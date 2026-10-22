import { useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { apiClient, ApiError } from '../api/client';
import type {
  Session,
  SessionResource,
  ThreadListResponse,
  PaginatedResponse,
  FileRecord,
  FilesystemListing,
  FilesystemEntry,
  OutcomeEvaluation,
  OutcomeEvaluationsResponse,
  Agent,
  Environment,
  Vault,
} from '../types';
import LoadingSpinner from '../components/LoadingSpinner';
import ErrorMessage from '../components/ErrorMessage';
import StatusBadge from '../components/StatusBadge';
import EventStreamPanel from '../components/EventStreamPanel';
import MessageInput from '../components/MessageInput';
import ConfirmDialog from '../components/ConfirmDialog';
import './SessionDetailView.css';

const TERMINAL_STATUSES = ['terminated', 'failed'];

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/** Trigger a browser download for a blob fetched from an authenticated endpoint. */
function downloadBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

function shortId(id: string): string {
  return `${id.slice(0, 8)}…`;
}

/** Per-type detail value for a resource-mount row: a link when the
 * referenced entity has a console page, otherwise a plain summary. */
function ResourceDetailValue({
  res,
  vaultNames,
}: {
  res: SessionResource;
  vaultNames: Record<string, string>;
}) {
  const cfg = res.config as Record<string, unknown>;
  const idOf = (key: string) => (typeof cfg[key] === 'string' ? (cfg[key] as string) : null);

  switch (res.type) {
    case 'file': {
      const fileId = idOf('file_id');
      if (!fileId) break;
      return (
        <Link to="/files" title={fileId}>
          file:{shortId(fileId)}
        </Link>
      );
    }
    case 'memory_store': {
      const storeId = idOf('memory_store_id');
      if (!storeId) break;
      const access = typeof cfg.access === 'string' ? cfg.access : null;
      return (
        <>
          <Link to={`/memory-stores/${storeId}`} title={storeId}>
            memory_store:{shortId(storeId)}
          </Link>
          {access && <span className="resource-config-extra">{access}</span>}
        </>
      );
    }
    case 'vault': {
      const vaultId = idOf('vault_id');
      if (!vaultId) break;
      return (
        <Link to={`/vaults/${vaultId}`} title={vaultId}>
          {vaultNames[vaultId] ?? `vault:${shortId(vaultId)}`}
        </Link>
      );
    }
    case 'git_repository':
    case 'github_repository': {
      const url = idOf('url');
      if (!url) break;
      const branch = typeof cfg.branch === 'string' ? cfg.branch : null;
      return (
        <>
          <a href={url} target="_blank" rel="noreferrer">
            {url}
          </a>
          {branch && <span className="resource-config-extra">{branch}</span>}
        </>
      );
    }
  }
  return <code>{JSON.stringify(res.config)}</code>;
}

export default function SessionDetailView() {
  const { id } = useParams<{ id: string }>();
  const [session, setSession] = useState<Session | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<ApiError | null>(null);

  // Lifecycle action state
  const [showTerminateDialog, setShowTerminateDialog] = useState(false);
  const [terminating, setTerminating] = useState(false);
  const [archiving, setArchiving] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  // Rename state
  const [renaming, setRenaming] = useState(false);
  const [renameValue, setRenameValue] = useState('');
  const [renameSaving, setRenameSaving] = useState(false);

  // Threads / artifacts
  const [threads, setThreads] = useState<Session[] | null>(null);
  const [artifacts, setArtifacts] = useState<FileRecord[] | null>(null);
  const [downloadingId, setDownloadingId] = useState<string | null>(null);

  // Outcome evaluations (grader results)
  const [evaluations, setEvaluations] = useState<OutcomeEvaluation[] | null>(null);
  const [showEvalForm, setShowEvalForm] = useState(false);
  const [evalScore, setEvalScore] = useState('');
  const [evalRationale, setEvalRationale] = useState('');
  const [evalError, setEvalError] = useState<string | null>(null);
  const [evalSubmitting, setEvalSubmitting] = useState(false);

  // Filesystem browser
  const [fsPath, setFsPath] = useState('/');
  const [fsListing, setFsListing] = useState<FilesystemListing | null>(null);
  const [fsLoading, setFsLoading] = useState(false);
  const [fsError, setFsError] = useState<string | null>(null);
  const [downloadingFs, setDownloadingFs] = useState<string | null>(null);

  // Display names for the resources referenced by this session.
  const [agentName, setAgentName] = useState<string | null>(null);
  const [environmentName, setEnvironmentName] = useState<string | null>(null);
  const [vaultNames, setVaultNames] = useState<Record<string, string>>({});

  const fetchSession = async () => {
    try {
      setError(null);
      const data = await apiClient.get<Session>(`/v1/sessions/${id}`);
      setSession(data);
    } catch (err) {
      setError(err as ApiError);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    setLoading(true);
    setThreads(null);
    setArtifacts(null);
    setEvaluations(null);
    setFsPath('/');
    setFsListing(null);
    fetchSession();
    apiClient
      .get<ThreadListResponse>(`/v1/sessions/${id}/threads`)
      .then((res) => setThreads(res.data))
      .catch(() => setThreads([]));
    apiClient
      .get<PaginatedResponse<FileRecord>>(`/v1/artifacts?session_id=${id}&limit=100`)
      .then((res) => setArtifacts(res.data))
      .catch(() => setArtifacts([]));
    apiClient
      .get<OutcomeEvaluationsResponse>(`/v1/sessions/${id}/outcome_evaluations`)
      .then((res) => setEvaluations(res.data))
      .catch(() => setEvaluations([]));
  }, [id]);

  // Resolve display names for the agent / environment referenced by the session.
  useEffect(() => {
    if (!session) return;
    let cancelled = false;
    apiClient.get<Agent>(`/v1/agents/${session.agent_id}`)
      .then((a) => { if (!cancelled) setAgentName(a.name); })
      .catch(() => {});
    apiClient.get<Environment>(`/v1/environments/${session.environment_id}`)
      .then((e) => { if (!cancelled) setEnvironmentName(e.name); })
      .catch(() => {});
    return () => { cancelled = true; };
  }, [session?.agent_id, session?.environment_id]);

  // Vault id → display_name map for the mounted vaults row.
  useEffect(() => {
    let cancelled = false;
    apiClient.get<PaginatedResponse<Vault>>('/v1/vaults?limit=100')
      .then((res) => {
        if (cancelled) return;
        const map: Record<string, string> = {};
        for (const v of res.data) map[v.id] = v.display_name;
        setVaultNames(map);
      })
      .catch(() => {});
    return () => { cancelled = true; };
  }, []);

  // Filesystem listing — only meaningful while a sandbox container exists.
  useEffect(() => {
    if (!session || !session.container_id) return;
    let cancelled = false;
    setFsLoading(true);
    setFsError(null);
    apiClient
      .get<FilesystemListing>(
        `/v1/sessions/${session.id}/filesystem?path=${encodeURIComponent(fsPath)}`,
      )
      .then((listing) => {
        if (!cancelled) setFsListing(listing);
      })
      .catch((err) => {
        if (!cancelled) setFsError((err as ApiError).message || 'Failed to list directory.');
      })
      .finally(() => {
        if (!cancelled) setFsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [session, fsPath]);

  const handleTerminate = async () => {
    if (!session || terminating) return;
    setTerminating(true);
    setActionError(null);
    setShowTerminateDialog(false);
    try {
      await apiClient.delete(`/v1/sessions/${session.id}`);
      setSession((prev) => prev ? { ...prev, status: 'terminated' } : prev);
    } catch (err) {
      const apiErr = err as ApiError;
      setActionError(apiErr.message || 'Failed to terminate session');
    } finally {
      setTerminating(false);
    }
  };

  const handleArchive = async () => {
    if (!session || archiving) return;
    setArchiving(true);
    setActionError(null);
    try {
      await apiClient.post(`/v1/sessions/${session.id}/archive`, {});
      setSession((prev) =>
        prev ? { ...prev, archived_at: new Date().toISOString() } : prev,
      );
    } catch (err) {
      const apiErr = err as ApiError;
      setActionError(apiErr.message || 'Failed to archive session');
    } finally {
      setArchiving(false);
    }
  };

  const handleRename = async () => {
    if (!session || renameSaving) return;
    const title = renameValue.trim();
    if (!title) return;
    setRenameSaving(true);
    setActionError(null);
    try {
      const updated = await apiClient.post<Session>(`/v1/sessions/${session.id}`, { title });
      setSession(updated);
      setRenaming(false);
    } catch (err) {
      setActionError((err as ApiError).message || 'Failed to rename session');
    } finally {
      setRenameSaving(false);
    }
  };

  const handleRecordEvaluation = async () => {
    if (!session || evalSubmitting) return;
    const score = Number(evalScore);
    if (Number.isNaN(score) || score < 0 || score > 1) {
      setEvalError('Score must be a number between 0 and 1.');
      return;
    }
    setEvalSubmitting(true);
    setEvalError(null);
    try {
      const created = await apiClient.post<OutcomeEvaluation>(
        `/v1/sessions/${session.id}/outcome_evaluations`,
        {
          score,
          rationale: evalRationale.trim() || null,
        },
      );
      setEvaluations((prev) => [created, ...(prev || [])]);
      setShowEvalForm(false);
      setEvalScore('');
      setEvalRationale('');
    } catch (err) {
      setEvalError((err as ApiError).message || 'Failed to record evaluation');
    } finally {
      setEvalSubmitting(false);
    }
  };

  const handleDownloadArtifact = async (artifact: FileRecord) => {
    if (!session || downloadingId) return;
    setDownloadingId(artifact.id);
    try {
      const blob = await apiClient.download(`/v1/artifacts/${artifact.id}/content`);
      downloadBlob(blob, artifact.filename);
    } catch (err) {
      setActionError((err as ApiError).message || 'Failed to download artifact.');
    } finally {
      setDownloadingId(null);
    }
  };

  const handleDownloadFsFile = async (entry: FilesystemEntry) => {
    if (!session || downloadingFs) return;
    const filePath = fsPath === '/' ? `/${entry.name}` : `${fsPath}/${entry.name}`;
    setDownloadingFs(filePath);
    try {
      const blob = await apiClient.download(
        `/v1/sessions/${session.id}/filesystem/raw?path=${encodeURIComponent(filePath)}`,
      );
      downloadBlob(blob, entry.name);
    } catch (err) {
      setFsError((err as ApiError).message || 'Failed to download file.');
    } finally {
      setDownloadingFs(null);
    }
  };

  if (loading) return <LoadingSpinner />;

  if (error) {
    return (
      <ErrorMessage
        status={error.status}
        message={error.message}
        onRetry={() => {
          setLoading(true);
          fetchSession();
        }}
      />
    );
  }

  if (!session) return null;

  const isTerminal = TERMINAL_STATUSES.includes(session.status);
  const isArchived = !!session.archived_at;
  const resources: SessionResource[] = session.resources || [];

  // Breadcrumb segments for the filesystem path
  const breadcrumbParts = fsPath.split('/').filter(Boolean);

  return (
    <div className="session-detail-view" data-testid="session-detail-view">
      <Link to="/sessions" className="back-link" data-testid="back-link">
        ← Sessions
      </Link>

      <div className="session-detail-header">
        <div className="session-detail-title-row">
          {renaming ? (
            <form
              className="session-rename-form"
              onSubmit={(e) => {
                e.preventDefault();
                handleRename();
              }}
            >
              <input
                className="session-rename-input"
                value={renameValue}
                onChange={(e) => setRenameValue(e.target.value)}
                disabled={renameSaving}
                autoFocus
                maxLength={200}
                aria-label="Session title"
                data-testid="session-rename-input"
              />
              <button
                type="submit"
                className="btn-sm-secondary"
                disabled={renameSaving || !renameValue.trim()}
                data-testid="session-rename-save"
              >
                {renameSaving ? 'Saving…' : 'Save'}
              </button>
              <button
                type="button"
                className="btn-sm-secondary"
                onClick={() => setRenaming(false)}
                disabled={renameSaving}
                data-testid="session-rename-cancel"
              >
                Cancel
              </button>
            </form>
          ) : (
            <>
              <h1 data-testid="session-title">{session.title || 'Untitled Session'}</h1>
              <button
                className="btn-sm-secondary session-rename-btn"
                onClick={() => {
                  setRenameValue(session.title || '');
                  setRenaming(true);
                }}
                data-testid="session-rename-btn"
              >
                Rename
              </button>
            </>
          )}
          <StatusBadge status={session.status} />
          {isArchived && (
            <span className="archived-badge" data-testid="archived-badge">Archived</span>
          )}
        </div>
        <div className="session-detail-meta">
          <span data-testid="session-id">
            <code>{session.id}</code>
          </span>
          <span className="meta-separator">·</span>
          <span data-testid="session-created">
            Created {new Date(session.created_at).toLocaleString()}
          </span>
          <span className="meta-separator">·</span>
          <span data-testid="session-updated">
            Updated {new Date(session.updated_at).toLocaleString()}
          </span>
        </div>
      </div>

      {/* Lifecycle actions */}
      <section className="detail-section lifecycle-actions" data-testid="lifecycle-actions">
        {!isTerminal && (
          <button
            className="btn-terminate"
            onClick={() => setShowTerminateDialog(true)}
            disabled={terminating}
            data-testid="terminate-btn"
          >
            {terminating ? 'Terminating…' : 'Terminate'}
          </button>
        )}
        {!isArchived && (
          <button
            className="btn-archive"
            onClick={handleArchive}
            disabled={archiving}
            data-testid="archive-btn"
          >
            {archiving ? 'Archiving…' : 'Archive'}
          </button>
        )}
        {actionError && (
          <span className="lifecycle-error" data-testid="lifecycle-error">{actionError}</span>
        )}
      </section>

      <ConfirmDialog
        open={showTerminateDialog}
        title="Terminate Session"
        message="Are you sure you want to terminate this session? This action cannot be undone."
        onConfirm={handleTerminate}
        onCancel={() => setShowTerminateDialog(false)}
      />

      <section className="detail-section" data-testid="session-info-section">
        <h2>Session Info</h2>
        <dl className="detail-grid">
          <dt>Agent</dt>
          <dd data-testid="session-agent-id">
            <Link to={`/agents/${session.agent_id}`}>{agentName ?? session.agent_id}</Link>
          </dd>
          <dt>Environment</dt>
          <dd data-testid="session-environment-id">
            <Link to={`/environments/${session.environment_id}`}>{environmentName ?? session.environment_id}</Link>
          </dd>
          {session.container_id && (
            <>
              <dt>Container</dt>
              <dd data-testid="session-container-id"><code>{session.container_id}</code></dd>
            </>
          )}
          {session.stop_reason && (
            <>
              <dt>Stop Reason</dt>
              <dd data-testid="session-stop-reason"><code>{session.stop_reason}</code></dd>
            </>
          )}
          {typeof session.retry_count === 'number' && session.retry_count > 0 && (
            <>
              <dt>Retries</dt>
              <dd data-testid="session-retry-count">{session.retry_count}</dd>
            </>
          )}
          {session.parent_session_id && (
            <>
              <dt>Parent Session</dt>
              <dd data-testid="session-parent-id">
                <Link to={`/sessions/${session.parent_session_id}`}>
                  {session.parent_session_id}
                </Link>
              </dd>
            </>
          )}
          {session.deployment_id && (
            <>
              <dt>Deployment</dt>
              <dd data-testid="session-deployment-id">
                <Link to={`/deployments/${session.deployment_id}`}>
                  {session.deployment_id}
                </Link>
              </dd>
            </>
          )}
          {typeof session.events_encrypted === 'boolean' && (
            <>
              <dt>Events Encrypted</dt>
              <dd data-testid="session-events-encrypted">
                {session.events_encrypted ? 'Yes' : 'No'}
              </dd>
            </>
          )}
          {session.ttl_seconds != null && (
            <>
              <dt>TTL</dt>
              <dd data-testid="session-ttl">{session.ttl_seconds}s</dd>
            </>
          )}
          {session.vault_ids && session.vault_ids.length > 0 && (
            <>
              <dt>Vaults</dt>
              <dd data-testid="session-vault-ids">
                {session.vault_ids.map(vid => (
                  <div key={vid}>
                    <Link to={`/vaults/${vid}`}>{vaultNames[vid] ?? vid}</Link>
                  </div>
                ))}
              </dd>
            </>
          )}
        </dl>
      </section>

      {resources.length > 0 && (
        <section className="detail-section" data-testid="session-resources-section">
          <h2>Resource Mounts</h2>
          <table className="resources-table">
            <thead>
              <tr>
                <th>Type</th>
                <th>Mount Path</th>
                <th>State</th>
                <th>Detail</th>
              </tr>
            </thead>
            <tbody>
              {resources.map((res) => (
                <tr key={res.id} data-testid="resource-item">
                  <td data-testid="resource-type">{res.type}</td>
                  <td><code>{res.mount_path || '—'}</code></td>
                  <td>
                    <span
                      className={`resource-state resource-state--${res.state}`}
                      data-testid="resource-state"
                    >
                      {res.state}
                    </span>
                  </td>
                  <td className="resource-detail">
                    {res.error ? (
                      <span className="resource-error">{res.error}</span>
                    ) : (
                      <ResourceDetailValue res={res} vaultNames={vaultNames} />
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}

      <section className="detail-section" data-testid="session-stats-section">
        <h2>Stats</h2>
        <div className="stats-grid">
          <div className="stat-card" data-testid="stat-total-events">
            <span className="stat-value">{session.stats.total_events}</span>
            <span className="stat-label">Total Events</span>
          </div>
          <div className="stat-card" data-testid="stat-tool-calls">
            <span className="stat-value">{session.stats.tool_calls}</span>
            <span className="stat-label">Tool Calls</span>
          </div>
          <div className="stat-card" data-testid="stat-model-turns">
            <span className="stat-value">{session.stats.model_turns}</span>
            <span className="stat-label">Model Turns</span>
          </div>
        </div>
      </section>

      <section className="detail-section" data-testid="session-usage-section">
        <h2>Token Usage</h2>
        <div className="stats-grid">
          <div className="stat-card" data-testid="usage-input-tokens">
            <span className="stat-value">{session.usage.input_tokens.toLocaleString()}</span>
            <span className="stat-label">Input Tokens</span>
          </div>
          <div className="stat-card" data-testid="usage-output-tokens">
            <span className="stat-value">{session.usage.output_tokens.toLocaleString()}</span>
            <span className="stat-label">Output Tokens</span>
          </div>
        </div>
      </section>

      {threads !== null && threads.length > 0 && (
        <section className="detail-section" data-testid="threads-section">
          <h2>Threads</h2>
          <table className="resources-table">
            <thead>
              <tr>
                <th>Title</th>
                <th>Status</th>
                <th>Created</th>
              </tr>
            </thead>
            <tbody>
              {threads.map((t) => (
                <tr key={t.id} data-testid="thread-row">
                  <td>
                    <Link to={`/sessions/${t.id}`}>{t.title || t.id}</Link>
                  </td>
                  <td><StatusBadge status={t.status} /></td>
                  <td>{new Date(t.created_at).toLocaleString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}

      <section className="detail-section" data-testid="artifacts-section">
        <h2>Artifacts</h2>
        {artifacts === null ? (
          <LoadingSpinner />
        ) : artifacts.length === 0 ? (
          <p className="empty-text">No artifacts produced by this session.</p>
        ) : (
          <table className="resources-table">
            <thead>
              <tr>
                <th>Filename</th>
                <th>Size</th>
                <th>Created</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {artifacts.map((a) => (
                <tr key={a.id} data-testid="artifact-row">
                  <td data-testid="artifact-filename">{a.filename}</td>
                  <td>{formatBytes(a.size_bytes)}</td>
                  <td>{new Date(a.created_at).toLocaleString()}</td>
                  <td>
                    <button
                      className="btn-download"
                      onClick={() => handleDownloadArtifact(a)}
                      disabled={downloadingId === a.id}
                      data-testid="artifact-download"
                    >
                      {downloadingId === a.id ? '…' : 'Download'}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <section className="detail-section" data-testid="outcome-evaluations-section">
        <h2>
          Outcome Evaluations ({evaluations?.length ?? 0})
          <button
            className="btn-refresh"
            onClick={() => setShowEvalForm((v) => !v)}
            data-testid="evaluation-toggle-form-btn"
          >
            {showEvalForm ? 'Cancel' : 'Record Evaluation'}
          </button>
        </h2>
        {showEvalForm && (
          <div className="eval-form" data-testid="evaluation-form">
            {evalError && (
              <div className="form-error" role="alert" data-testid="evaluation-error">
                {evalError}
              </div>
            )}
            <div className="eval-form-row">
              <label htmlFor="eval-score">Score (0–1)</label>
              <input
                id="eval-score"
                type="number"
                min={0}
                max={1}
                step={0.01}
                value={evalScore}
                onChange={(e) => setEvalScore(e.target.value)}
                placeholder="0.85"
                data-testid="evaluation-score-input"
              />
            </div>
            <div className="eval-form-row">
              <label htmlFor="eval-rationale">Rationale</label>
              <textarea
                id="eval-rationale"
                value={evalRationale}
                onChange={(e) => setEvalRationale(e.target.value)}
                rows={2}
                placeholder="Why this score?"
                data-testid="evaluation-rationale-input"
              />
            </div>
            <p className="section-hint">
              Note: a score at or above the outcome&apos;s success threshold will auto-terminate the session.
            </p>
            <button
              className="btn-primary"
              onClick={handleRecordEvaluation}
              disabled={evalSubmitting}
              data-testid="evaluation-submit-btn"
            >
              {evalSubmitting ? 'Submitting…' : 'Submit'}
            </button>
          </div>
        )}
        {evaluations === null ? (
          <LoadingSpinner />
        ) : evaluations.length === 0 ? (
          <p className="empty-text">No outcome evaluations recorded for this session.</p>
        ) : (
          <table className="resources-table">
            <thead>
              <tr>
                <th>Score</th>
                <th>Grader</th>
                <th>Rationale</th>
                <th>Recorded</th>
              </tr>
            </thead>
            <tbody>
              {evaluations.map((ev) => (
                <tr key={ev.id} data-testid="evaluation-row">
                  <td data-testid="evaluation-score">
                    <span className={`eval-score ${ev.score >= 0.7 ? 'eval-score--high' : 'eval-score--low'}`}>
                      {ev.score.toFixed(2)}
                    </span>
                  </td>
                  <td data-testid="evaluation-grader">{ev.grader_id || '—'}</td>
                  <td className="eval-rationale" data-testid="evaluation-rationale">
                    {ev.rationale || '—'}
                  </td>
                  <td>{new Date(ev.created_at).toLocaleString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <section className="detail-section" data-testid="filesystem-section">
        <h2>Filesystem</h2>
        {!session.container_id ? (
          <p className="empty-text">
            Filesystem is unavailable — no sandbox container is attached to this session.
          </p>
        ) : (
          <>
            <div className="fs-breadcrumb" data-testid="fs-breadcrumb">
              <button className="fs-crumb" onClick={() => setFsPath('/')} data-testid="fs-crumb-root">
                /
              </button>
              {breadcrumbParts.map((part, i) => {
                const target = '/' + breadcrumbParts.slice(0, i + 1).join('/');
                return (
                  <span key={target}>
                    <button className="fs-crumb" onClick={() => setFsPath(target)}>
                      {part}
                    </button>
                    {i < breadcrumbParts.length - 1 && <span className="fs-crumb-sep">/</span>}
                  </span>
                );
              })}
            </div>
            {fsLoading ? (
              <LoadingSpinner />
            ) : fsError ? (
              <p className="empty-text" data-testid="fs-error">{fsError}</p>
            ) : !fsListing || fsListing.entries.length === 0 ? (
              <p className="empty-text">Directory is empty.</p>
            ) : (
              <table className="resources-table">
                <thead>
                  <tr>
                    <th>Name</th>
                    <th>Size</th>
                    <th>Modified</th>
                    <th>Permissions</th>
                    <th></th>
                  </tr>
                </thead>
                <tbody>
                  {fsListing.entries.map((entry) => (
                    <tr key={entry.name} data-testid="fs-row">
                      <td>
                        {entry.type === 'directory' ? (
                          <button
                            className="fs-dir-link"
                            onClick={() =>
                              setFsPath(fsPath === '/' ? `/${entry.name}` : `${fsPath}/${entry.name}`)
                            }
                            data-testid="fs-dir-link"
                          >
                            {entry.name}/
                          </button>
                        ) : (
                          <span data-testid="fs-file-name">
                            {entry.name}
                            {entry.type === 'symlink' && entry.link_target && (
                              <span className="fs-symlink-target"> → {entry.link_target}</span>
                            )}
                          </span>
                        )}
                      </td>
                      <td>{entry.type === 'directory' ? '—' : formatBytes(entry.size)}</td>
                      <td>{entry.modified ? new Date(entry.modified).toLocaleString() : '—'}</td>
                      <td>
                        <code>{entry.permissions}</code>
                      </td>
                      <td>
                        {entry.type === 'file' && (
                          <button
                            className="btn-download"
                            onClick={() => handleDownloadFsFile(entry)}
                            disabled={downloadingFs !== null}
                            data-testid="fs-download"
                          >
                            {downloadingFs === (fsPath === '/' ? `/${entry.name}` : `${fsPath}/${entry.name}`) ? '…' : 'Download'}
                          </button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </>
        )}
      </section>

      <section className="detail-section" data-testid="event-stream-section">
        <h2>Events</h2>
        <EventStreamPanel sessionId={session.id} sessionStatus={session.status} />
      </section>

      <section className="detail-section" data-testid="message-input-section">
        <MessageInput sessionId={session.id} disabled={isTerminal} sessionStatus={session.status} />
      </section>
    </div>
  );
}
