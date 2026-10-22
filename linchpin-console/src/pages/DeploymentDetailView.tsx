import { useEffect, useState, useCallback } from 'react';
import { Link, useParams } from 'react-router-dom';
import { apiClient, ApiError } from '../api/client';
import type {
  Deployment,
  Agent,
  Environment,
  Session,
  PaginatedResponse,
  UpdateDeploymentRequest,
  InitialEventConfig,
} from '../types';
import LoadingSpinner from '../components/LoadingSpinner';
import ErrorMessage from '../components/ErrorMessage';
import StatusBadge from '../components/StatusBadge';
import ConfirmDialog from '../components/ConfirmDialog';
import './DeploymentDetailView.css';

export default function DeploymentDetailView() {
  const { id } = useParams<{ id: string }>();

  const [deployment, setDeployment] = useState<Deployment | null>(null);
  const [agentName, setAgentName] = useState<string | null>(null);
  const [envName, setEnvName] = useState<string | null>(null);
  const [runs, setRuns] = useState<Session[]>([]);
  const [runsLoading, setRunsLoading] = useState(true);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<ApiError | null>(null);

  const [toggling, setToggling] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [showArchiveConfirm, setShowArchiveConfirm] = useState(false);
  const [archiving, setArchiving] = useState(false);

  // Edit state (PATCH cron/timezone/initial_events)
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [editCron, setEditCron] = useState('');
  const [editTimezone, setEditTimezone] = useState('');
  const [editEventsJson, setEditEventsJson] = useState('');

  const fetchRuns = useCallback(async () => {
    setRunsLoading(true);
    try {
      const res = await apiClient.get<PaginatedResponse<Session>>(
        `/v1/sessions?deployment_id=${id}&limit=100`,
      );
      setRuns(res.data);
    } catch {
      setRuns([]);
    } finally {
      setRunsLoading(false);
    }
  }, [id]);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const dep = await apiClient.get<Deployment>(`/v1/deployments/${id}`);
        if (cancelled) return;
        setDeployment(dep);
        setError(null);
        // Resolve display names (non-fatal if they fail)
        apiClient.get<Agent>(`/v1/agents/${dep.agent_id}`)
          .then(a => { if (!cancelled) setAgentName(a.name); })
          .catch(() => undefined);
        apiClient.get<Environment>(`/v1/environments/${dep.environment_id}`)
          .then(e => { if (!cancelled) setEnvName(e.name); })
          .catch(() => undefined);
        fetchRuns();
      } catch (err) {
        if (!cancelled) setError(err as ApiError);
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    load();
    return () => { cancelled = true; };
  }, [id, fetchRuns]);

  const handleToggle = async () => {
    if (!deployment) return;
    setToggling(true);
    setActionError(null);
    try {
      const action = deployment.status === 'active' ? 'pause' : 'resume';
      const updated = await apiClient.post<Deployment>(
        `/v1/deployments/${deployment.id}/${action}`,
        {},
      );
      setDeployment(updated);
    } catch (err) {
      const apiErr = err as ApiError;
      setActionError(apiErr.message || 'Failed to update deployment');
    } finally {
      setToggling(false);
    }
  };

  const enterEditMode = () => {
    if (!deployment) return;
    setEditCron(deployment.cron);
    setEditTimezone(deployment.timezone);
    setEditEventsJson(JSON.stringify(deployment.initial_events, null, 2));
    setActionError(null);
    setEditing(true);
  };

  const cancelEdit = () => {
    setEditing(false);
    setActionError(null);
  };

  const handleEditSave = async () => {
    if (!deployment || saving) return;
    setSaving(true);
    setActionError(null);

    const patch: UpdateDeploymentRequest = {};
    if (editCron.trim() !== deployment.cron) patch.cron = editCron.trim();
    if (editTimezone.trim() !== deployment.timezone) patch.timezone = editTimezone.trim();

    if (editEventsJson.trim() !== JSON.stringify(deployment.initial_events, null, 2)) {
      let parsed: unknown;
      try {
        parsed = JSON.parse(editEventsJson);
      } catch {
        setActionError('Initial events must be valid JSON.');
        setSaving(false);
        return;
      }
      if (!Array.isArray(parsed)) {
        setActionError('Initial events must be a JSON array.');
        setSaving(false);
        return;
      }
      const events: InitialEventConfig[] = [];
      for (const item of parsed) {
        const ev = item as Partial<InitialEventConfig>;
        if (!ev || (ev.type !== 'user.message' && ev.type !== 'user.steer') || typeof ev.payload !== 'object' || ev.payload === null) {
          setActionError('Each event needs a type ("user.message" or "user.steer") and a payload object.');
          setSaving(false);
          return;
        }
        events.push({ type: ev.type, payload: ev.payload });
      }
      patch.initial_events = events;
    }

    try {
      const updated = await apiClient.patch<Deployment>(
        `/v1/deployments/${deployment.id}`,
        patch,
      );
      setDeployment(updated);
      setEditing(false);
    } catch (err) {
      const apiErr = err as ApiError;
      setActionError(apiErr.message || 'Failed to update deployment');
    } finally {
      setSaving(false);
    }
  };

  const handleArchive = async () => {
    if (!deployment) return;
    setArchiving(true);
    setActionError(null);
    try {
      const updated = await apiClient.delete<Deployment>(`/v1/deployments/${deployment.id}`);
      setDeployment(updated);
      setShowArchiveConfirm(false);
    } catch (err) {
      const apiErr = err as ApiError;
      setActionError(apiErr.message || 'Failed to archive deployment');
      setShowArchiveConfirm(false);
    } finally {
      setArchiving(false);
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
          setError(null);
          apiClient.get<Deployment>(`/v1/deployments/${id}`)
            .then(d => setDeployment(d))
            .catch(e => setError(e as ApiError))
            .finally(() => setLoading(false));
        }}
      />
    );
  }

  if (!deployment) return null;

  const isArchived = !!deployment.archived_at;
  const isActive = deployment.status === 'active';

  return (
    <div className="deployment-detail-view" data-testid="deployment-detail-view">
      <Link to="/deployments" className="back-link" data-testid="back-link">
        ← Deployments
      </Link>

      <div className="deployment-detail-header">
        <div className="deployment-detail-title-row">
          <h1 data-testid="deployment-title">
            Deployment <code>{deployment.id.slice(0, 8)}</code>
          </h1>
          <span className={`deployment-status deployment-status--${deployment.status}`} data-testid="deployment-status">
            {deployment.status}
          </span>
          {isArchived && (
            <span className="archived-badge" data-testid="archived-badge">Archived</span>
          )}
        </div>
        <div className="deployment-detail-meta">
          <span>Created {new Date(deployment.created_at).toLocaleString()}</span>
          {deployment.archived_at && (
            <>
              <span className="meta-separator">·</span>
              <span>Archived {new Date(deployment.archived_at).toLocaleString()}</span>
            </>
          )}
        </div>
      </div>

      {actionError && (
        <div className="form-error" role="alert" data-testid="deployment-action-error">
          {actionError}
        </div>
      )}

      <section className="detail-section deployment-lifecycle-actions" data-testid="lifecycle-actions">
        {!isArchived && (
          <>
            <button
              className="btn-primary"
              disabled={toggling}
              onClick={handleToggle}
              data-testid={isActive ? 'deployment-pause-btn' : 'deployment-resume-btn'}
            >
              {toggling ? 'Working…' : isActive ? 'Pause' : 'Resume'}
            </button>
            <button
              className="btn-archive"
              disabled={archiving}
              onClick={() => setShowArchiveConfirm(true)}
              data-testid="deployment-archive-btn"
            >
              Archive
            </button>
          </>
        )}
      </section>

      <section className="detail-section" data-testid="deployment-info-section">
        <h2>
          Configuration
          {!editing && !isArchived && (
            <button
              className="btn-edit"
              onClick={enterEditMode}
              data-testid="deployment-edit-btn"
            >
              Edit
            </button>
          )}
        </h2>
        {editing ? (
          <div className="edit-fields" data-testid="deployment-edit-form">
            <div className="edit-field">
              <label htmlFor="dep-edit-cron">Cron Schedule</label>
              <input
                id="dep-edit-cron"
                type="text"
                value={editCron}
                onChange={(e) => setEditCron(e.target.value)}
                placeholder="*/15 * * * *"
                data-testid="deployment-edit-cron"
              />
              <span className="edit-field-hint">
                Changing the schedule recomputes the next run time from now.
              </span>
            </div>
            <div className="edit-field">
              <label htmlFor="dep-edit-timezone">Timezone</label>
              <input
                id="dep-edit-timezone"
                type="text"
                value={editTimezone}
                onChange={(e) => setEditTimezone(e.target.value)}
                placeholder="America/New_York"
                data-testid="deployment-edit-timezone"
              />
              <span className="edit-field-hint">IANA timezone name, e.g. Asia/Shanghai.</span>
            </div>
            <div className="edit-field">
              <label htmlFor="dep-edit-events">Initial Events (JSON)</label>
              <textarea
                id="dep-edit-events"
                className="dep-events-textarea"
                value={editEventsJson}
                onChange={(e) => setEditEventsJson(e.target.value)}
                rows={6}
                spellCheck={false}
                data-testid="deployment-edit-events"
              />
              <span className="edit-field-hint">
                Array of {"{"}&quot;type&quot;: &quot;user.message&quot; | &quot;user.steer&quot;, &quot;payload&quot;: {"{...}"}{"}"}. Use [] for none.
              </span>
            </div>
            <div className="edit-actions">
              <button
                className="btn-save"
                onClick={handleEditSave}
                disabled={saving}
                data-testid="deployment-edit-save"
              >
                {saving ? 'Saving…' : 'Save'}
              </button>
              <button
                className="btn-cancel"
                onClick={cancelEdit}
                disabled={saving}
                data-testid="deployment-edit-cancel"
              >
                Cancel
              </button>
            </div>
          </div>
        ) : (
          <dl className="detail-grid">
          <dt>Agent</dt>
          <dd>
            <Link to={`/agents/${deployment.agent_id}`} data-testid="deployment-agent-link">
              {agentName || deployment.agent_id}
            </Link>
          </dd>
          <dt>Environment</dt>
          <dd>
            <Link to={`/environments/${deployment.environment_id}`} data-testid="deployment-env-link">
              {envName || deployment.environment_id}
            </Link>
          </dd>
          <dt>Cron</dt>
          <dd><code>{deployment.cron}</code></dd>
          <dt>Timezone</dt>
          <dd>{deployment.timezone}</dd>
          <dt>Next Run</dt>
          <dd data-testid="deployment-next-run">
            {isActive && deployment.next_run_at
              ? new Date(deployment.next_run_at).toLocaleString()
              : '—'}
          </dd>
          </dl>
        )}
      </section>

      {deployment.initial_events.length > 0 && (
        <section className="detail-section" data-testid="deployment-events-section">
          <h2>Initial Events</h2>
          <ul className="dep-events-list">
            {deployment.initial_events.map((ev, i) => (
              <li key={i} className="dep-events-item" data-testid="deployment-initial-event">
                <code className="dep-events-type">{ev.type}</code>
                <span className="dep-events-content">
                  {typeof ev.payload.content === 'string' ? ev.payload.content : JSON.stringify(ev.payload)}
                </span>
              </li>
            ))}
          </ul>
        </section>
      )}

      <section className="detail-section" data-testid="runs-section">
        <h2>
          Run History ({runs.length})
          <button
            className="btn-refresh"
            onClick={fetchRuns}
            disabled={runsLoading}
            data-testid="runs-refresh-btn"
          >
            Refresh
          </button>
        </h2>
        {runsLoading ? (
          <LoadingSpinner />
        ) : runs.length === 0 ? (
          <p className="empty-text">No sessions have been triggered by this deployment yet.</p>
        ) : (
          <table className="runs-table">
            <thead>
              <tr>
                <th>Title</th>
                <th>Status</th>
                <th>Created</th>
              </tr>
            </thead>
            <tbody>
              {runs.map(run => (
                <tr key={run.id} data-testid="run-row">
                  <td>
                    <Link to={`/sessions/${run.id}`}>{run.title || run.id.slice(0, 8)}</Link>
                  </td>
                  <td><StatusBadge status={run.status} /></td>
                  <td>{new Date(run.created_at).toLocaleString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <ConfirmDialog
        open={showArchiveConfirm}
        title="Archive Deployment"
        message="Are you sure you want to archive this deployment? It will stop triggering new runs."
        onConfirm={handleArchive}
        onCancel={() => setShowArchiveConfirm(false)}
      />
    </div>
  );
}
