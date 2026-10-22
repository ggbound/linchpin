import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { apiClient, ApiError } from '../api/client';
import type { ExecutorNode } from '../types';
import LoadingSpinner from '../components/LoadingSpinner';
import ErrorMessage from '../components/ErrorMessage';
import ConfirmDialog from '../components/ConfirmDialog';
import './ExecutorNodeListView.css';

// The registration response carries the daemon token exactly once.
type ExecutorNodeCreatedResponse = ExecutorNode & { token: string };

// The dialog uses a click handler instead of a <form> submit, so there is no
// native URL validation — mirror it in JS.
function isValidHttpUrl(value: string): boolean {
  try {
    const parsed = new URL(value);
    return parsed.protocol === 'http:' || parsed.protocol === 'https:';
  } catch {
    return false;
  }
}

export default function ExecutorNodeListView() {
  const { t, i18n } = useTranslation('executorNodes');

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

  const newUrlTrimmed = newUrl.trim();
  const newUrlInvalid = newUrlTrimmed !== '' && !isValidHttpUrl(newUrlTrimmed);

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
    if (newUrlInvalid) {
      setCreateError(t('registerUrlError'));
      return;
    }
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
      setNotice(t('registerSuccess', { name: created.name }));
      await fetchNodes();
    } catch (err) {
      const apiErr = err as ApiError;
      setCreateError(
        apiErr.status === 409
          ? (apiErr.message || t('registerNameConflict'))
          : (apiErr.message || t('registerFailed')),
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
      setNotice(t('deleteSuccess', { name: deleteTarget.name }));
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
          <h1>{t('listTitle')}</h1>
          <p>{t('listSubtitle')}</p>
        </div>
        <button
          className="btn-primary"
          onClick={() => {
            setCreateOpen(true);
            setCreateError(null);
          }}
          data-testid="register-node-btn"
        >
          {t('listRegisterNode')}
        </button>
      </div>

      {issuedToken && (
        <div className="node-token-banner" data-testid="token-banner">
          <div className="node-token-banner-text">
            <strong>{t('tokenBannerLabel')}</strong>
            <code data-testid="token-value">{issuedToken}</code>
            <span>
              {t('tokenBannerHintBefore')}
              {' '}
              <code>LINCHPIN_EXECUTOR_TOKEN</code>
              {' '}
              {t('tokenBannerHintAfter')}
            </span>
          </div>
          <button
            onClick={() => setIssuedToken(null)}
            data-testid="token-dismiss"
          >
            {t('common:close')}
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
          {t('listEmpty')}
        </p>
      ) : (
        <div className="executor-node-table-wrapper">
          <table className="executor-node-table" data-testid="executor-node-table">
            <thead>
              <tr>
                <th>{t('common:name')}</th>
                <th>{t('listColumnUrl')}</th>
                <th>{t('listColumnStatus')}</th>
                <th>{t('listColumnMaxSessions')}</th>
                <th>{t('listColumnTokenPrefix')}</th>
                <th>{t('listColumnLastHeartbeat')}</th>
                <th>{t('common:created')}</th>
                <th>{t('common:actions')}</th>
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
                      {node.status === 'online' ? t('listOnline') : t('listOffline')}
                    </span>
                  </td>
                  <td>{node.max_sessions}</td>
                  <td>
                    <code>{node.prefix}</code>
                  </td>
                  <td>
                    {node.last_heartbeat_at
                      ? new Date(node.last_heartbeat_at).toLocaleString(i18n.language)
                      : t('listNever')}
                  </td>
                  <td>{new Date(node.created_at).toLocaleString(i18n.language)}</td>
                  <td>
                    <button
                      className="btn-sm-danger"
                      onClick={() => setDeleteTarget(node)}
                      data-testid={`node-delete-btn-${node.id}`}
                    >
                      {t('common:delete')}
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
            <h2>{t('registerTitle')}</h2>
            <div className="form-field">
              <label htmlFor="node-create-name">{t('common:name')}</label>
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
              <label htmlFor="node-create-url">{t('registerUrlLabel')}</label>
              <input
                id="node-create-url"
                type="url"
                value={newUrl}
                onChange={e => setNewUrl(e.target.value)}
                placeholder="http://10.0.0.8:8100"
                data-testid="node-create-url"
              />
            </div>
            {newUrlInvalid && (
              <div className="form-error" role="alert" data-testid="node-create-url-error">
                {t('registerUrlError')}
              </div>
            )}
            <div className="form-field">
              <label htmlFor="node-create-max-sessions">{t('registerMaxSessionsLabel')}</label>
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
                {t('common:cancel')}
              </button>
              <button
                className="btn-primary"
                disabled={creating || !newName.trim() || !newUrl.trim() || newUrlInvalid}
                onClick={handleCreate}
                data-testid="node-create-submit"
              >
                {creating ? t('registerSubmitting') : t('registerSubmit')}
              </button>
            </div>
          </div>
        </div>
      )}

      <ConfirmDialog
        open={deleteTarget !== null}
        title={t('deleteDialogTitle')}
        message={
          deleteTarget
            ? t('deleteDialogMessage', { name: deleteTarget.name })
            : ''
        }
        onConfirm={handleDelete}
        onCancel={() => setDeleteTarget(null)}
      />
    </div>
  );
}
