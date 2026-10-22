import { useCallback, useEffect, useState } from 'react';
import { apiClient, ApiError } from '../api/client';
import type { ExecutorNode } from '../types';
import LoadingSpinner from '../components/LoadingSpinner';
import ErrorMessage from '../components/ErrorMessage';
import ConfirmDialog from '../components/ConfirmDialog';
import './ExecutorNodeListView.css';

// The registration response carries the daemon token exactly once.
type ExecutorNodeCreatedResponse = ExecutorNode & { token: string };

export default function ExecutorNodeListView() {
  const [nodes, setNodes] = useState<ExecutorNode[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<ApiError | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  // Register dialog
  const [createOpen, setCreateOpen] = useState(false);
  const [newName, setNewName] = useState('');
  const [newUrl, setNewUrl] = useState('');
  const [newMaxSessions, setNewMaxSessions] = useState('5');
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);

  // One-time token banner after successful registration
  const [issuedToken, setIssuedToken] = useState<string | null>(null);

  // Delete
  const [deleteTarget, setDeleteTarget] = useState<ExecutorNode | null>(null);

  const fetchNodes = useCallback(async () => {
    setLoading(true);
    try {
      const data = await apiClient.get<ExecutorNode[]>('/v1/executor-nodes');
      setNodes(data);
      setError(null);
    } catch (err) {
      setError(err as ApiError);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchNodes();
  }, [fetchNodes]);

  const handleCreate = async () => {
    if (!newName.trim() || !newUrl.trim()) return;
    setCreating(true);
    setCreateError(null);
    try {
      const maxSessions = parseInt(newMaxSessions, 10);
      const created = await apiClient.post<ExecutorNodeCreatedResponse>(
        '/v1/executor-nodes',
        {
          name: newName.trim(),
          url: newUrl.trim(),
          max_sessions: Number.isFinite(maxSessions) && maxSessions >= 1 ? maxSessions : 5,
        },
      );
      setCreateOpen(false);
      setNewName('');
      setNewUrl('');
      setNewMaxSessions('5');
      setCreateError(null);
      setIssuedToken(created.token);
      setNotice(`Executor node "${created.name}" registered.`);
      await fetchNodes();
    } catch (err) {
      const apiErr = err as ApiError;
      setCreateError(
        apiErr.status === 409
          ? (apiErr.message || 'A node with this name already exists')
          : (apiErr.message || 'Failed to register executor node'),
      );
    } finally {
      setCreating(false);
    }
  };

  const handleDelete = async () => {
    if (!deleteTarget) return;
    try {
      await apiClient.delete(`/v1/executor-nodes/${deleteTarget.id}`);
      setDeleteTarget(null);
      setNotice(`Executor node "${deleteTarget.name}" removed.`);
      await fetchNodes();
    } catch (err) {
      setError(err as ApiError);
      setDeleteTarget(null);
    }
  };

  if (loading) return <LoadingSpinner />;

  if (error) {
    return (
      <ErrorMessage
        status={error.status}
        message={error.message}
        onRetry={fetchNodes}
      />
    );
  }

  return (
    <div className="executor-node-list-view" data-testid="executor-node-list-view">
      <div className="executor-node-header">
        <div>
          <h1>Executor Nodes</h1>
          <p>
            Register your own executor daemons (BYO compute). Each node runs
            the Linchpin executor daemon and authenticates with a daemon token
            issued exactly once at registration time.
          </p>
        </div>
        <button
          className="btn-primary"
          onClick={() => {
            setCreateOpen(true);
            setCreateError(null);
          }}
          data-testid="register-node-btn"
        >
          Register node
        </button>
      </div>

      {issuedToken && (
        <div className="node-token-banner" data-testid="token-banner">
          <div className="node-token-banner-text">
            <strong>Daemon token (shown once):</strong>
            <code data-testid="token-value">{issuedToken}</code>
            <span>
              Copy it now and configure it as
              {' '}
              <code>LINCHPIN_EXECUTOR_TOKEN</code>
              {' '}
              on the daemon — it cannot be retrieved again.
            </span>
          </div>
          <button
            onClick={() => setIssuedToken(null)}
            data-testid="token-dismiss"
          >
            Dismiss
          </button>
        </div>
      )}

      {notice && (
        <div className="executor-node-notice" data-testid="executor-node-notice">
          {notice}
        </div>
      )}

      {nodes.length === 0 ? (
        <p className="empty-text" data-testid="executor-nodes-empty">
          No executor nodes registered yet. Register a node to run sessions on
          your own infrastructure.
        </p>
      ) : (
        <div className="executor-node-table-wrapper">
          <table className="executor-node-table" data-testid="executor-node-table">
            <thead>
              <tr>
                <th>Name</th>
                <th>URL</th>
                <th>Status</th>
                <th>Max sessions</th>
                <th>Token prefix</th>
                <th>Last heartbeat</th>
                <th>Created</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {nodes.map(node => (
                <tr key={node.id} data-testid={`executor-node-row-${node.id}`}>
                  <td className="executor-node-name-cell">{node.name}</td>
                  <td className="executor-node-url-cell">
                    <code title={node.url}>{node.url}</code>
                  </td>
                  <td>
                    <span
                      className={`node-status node-status-${node.status}`}
                      data-testid={`node-status-${node.id}`}
                    >
                      {node.status === 'online' ? 'Online' : 'Offline'}
                    </span>
                  </td>
                  <td>{node.max_sessions}</td>
                  <td>
                    <code>{node.prefix}</code>
                  </td>
                  <td>
                    {node.last_heartbeat_at
                      ? new Date(node.last_heartbeat_at).toLocaleString()
                      : 'Never'}
                  </td>
                  <td>{new Date(node.created_at).toLocaleString()}</td>
                  <td>
                    <button
                      className="btn-sm-danger"
                      onClick={() => setDeleteTarget(node)}
                      data-testid={`node-delete-btn-${node.id}`}
                    >
                      Delete
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Register dialog */}
      {createOpen && (
        <div className="node-dialog-overlay">
          <div className="node-dialog" data-testid="node-create-dialog">
            <h2>Register executor node</h2>
            <div className="form-field">
              <label htmlFor="node-create-name">Name</label>
              <input
                id="node-create-name"
                value={newName}
                onChange={e => setNewName(e.target.value)}
                placeholder="gpu-worker-1"
                autoFocus
                data-testid="node-create-name"
              />
            </div>
            <div className="form-field">
              <label htmlFor="node-create-url">Daemon URL</label>
              <input
                id="node-create-url"
                value={newUrl}
                onChange={e => setNewUrl(e.target.value)}
                placeholder="http://10.0.0.8:8100"
                data-testid="node-create-url"
              />
            </div>
            <div className="form-field">
              <label htmlFor="node-create-max-sessions">Max concurrent sessions</label>
              <input
                id="node-create-max-sessions"
                type="number"
                min={1}
                value={newMaxSessions}
                onChange={e => setNewMaxSessions(e.target.value)}
                data-testid="node-create-max-sessions"
              />
            </div>
            {createError && (
              <div className="form-error" role="alert" data-testid="node-create-error">
                {createError}
              </div>
            )}
            <div className="node-dialog-actions">
              <button
                onClick={() => setCreateOpen(false)}
                data-testid="node-create-cancel"
              >
                Cancel
              </button>
              <button
                className="btn-primary"
                disabled={creating || !newName.trim() || !newUrl.trim()}
                onClick={handleCreate}
                data-testid="node-create-submit"
              >
                {creating ? 'Registering…' : 'Register'}
              </button>
            </div>
          </div>
        </div>
      )}

      <ConfirmDialog
        open={deleteTarget !== null}
        title="Delete Executor Node"
        message={
          deleteTarget
            ? `Remove "${deleteTarget.name}" from the registry? The daemon token becomes unusable and the node will no longer accept sessions.`
            : ''
        }
        onConfirm={handleDelete}
        onCancel={() => setDeleteTarget(null)}
      />
    </div>
  );
}
