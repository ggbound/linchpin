import { useCallback, useEffect, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { apiClient, ApiError } from '../api/client';
import type { WebhookEndpoint, PaginatedResponse } from '../types';
import LoadingSpinner from '../components/LoadingSpinner';
import ErrorMessage from '../components/ErrorMessage';
import ConfirmDialog from '../components/ConfirmDialog';
import './WebhookListView.css';

export default function WebhookListView() {
  const navigate = useNavigate();

  const [endpoints, setEndpoints] = useState<WebhookEndpoint[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<ApiError | null>(null);

  const [deleteTarget, setDeleteTarget] = useState<WebhookEndpoint | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  const fetchEndpoints = useCallback(async () => {
    setLoading(true);
    try {
      const res = await apiClient.get<PaginatedResponse<WebhookEndpoint>>(
        '/v1/webhook_endpoints?limit=100',
      );
      setEndpoints(res.data);
      setError(null);
    } catch (err) {
      setError(err as ApiError);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchEndpoints();
  }, [fetchEndpoints]);

  const handleDelete = async () => {
    if (!deleteTarget) return;
    setDeleting(true);
    setActionError(null);
    try {
      await apiClient.delete(`/v1/webhook_endpoints/${deleteTarget.id}`);
      setEndpoints(prev => prev.filter(ep => ep.id !== deleteTarget.id));
      setDeleteTarget(null);
    } catch (err) {
      const apiErr = err as ApiError;
      setActionError(apiErr.message || 'Failed to delete webhook endpoint');
      setDeleteTarget(null);
    } finally {
      setDeleting(false);
    }
  };

  if (loading) return <LoadingSpinner />;

  if (error) {
    return (
      <ErrorMessage
        status={error.status}
        message={error.message}
        onRetry={fetchEndpoints}
      />
    );
  }

  return (
    <div className="webhook-list-view" data-testid="webhook-list-view">
      <div className="webhook-list-header">
        <div>
          <h1>Webhooks</h1>
          <p>HTTP endpoints that receive event notifications when sessions change.</p>
        </div>
        <button
          className="btn-primary"
          onClick={() => navigate('/webhooks/new')}
          data-testid="new-webhook-btn"
        >
          New webhook
        </button>
      </div>

      {actionError && (
        <div className="form-error" role="alert" data-testid="webhook-action-error">
          {actionError}
        </div>
      )}

      {endpoints.length === 0 ? (
        <div className="webhook-empty" data-testid="webhook-empty">
          <p className="empty-text">
            No webhook endpoints yet. Create one to receive session and vault events.
          </p>
        </div>
      ) : (
        <div className="webhook-table-wrapper">
          <table className="webhook-table" data-testid="webhook-table">
            <thead>
              <tr>
                <th>URL</th>
                <th>Events</th>
                <th>Status</th>
                <th>Created</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {endpoints.map(ep => (
                <tr
                  key={ep.id}
                  className="webhook-row"
                  tabIndex={0}
                  onClick={() => navigate(`/webhooks/${ep.id}`)}
                  onKeyDown={e => {
                    if (e.key === 'Enter') navigate(`/webhooks/${ep.id}`);
                  }}
                  data-testid="webhook-row"
                >
                  <td className="webhook-url-cell">
                    <Link to={`/webhooks/${ep.id}`} onClick={e => e.stopPropagation()}>
                      {ep.url}
                    </Link>
                    {ep.description && (
                      <span className="webhook-desc">{ep.description}</span>
                    )}
                  </td>
                  <td>
                    {ep.enabled_events.length > 0 ? (
                      <span className="webhook-events-count">
                        {ep.enabled_events.length} selected
                      </span>
                    ) : (
                      <span className="webhook-events-default">default template</span>
                    )}
                  </td>
                  <td>
                    <span
                      className={`webhook-enabled-badge ${ep.enabled ? 'is-enabled' : 'is-disabled'}`}
                    >
                      {ep.enabled ? 'enabled' : 'disabled'}
                    </span>
                  </td>
                  <td>{new Date(ep.created_at).toLocaleString()}</td>
                  <td onClick={e => e.stopPropagation()}>
                    <button
                      className="btn-sm-danger"
                      disabled={deleting}
                      onClick={() => setDeleteTarget(ep)}
                      data-testid="webhook-delete-btn"
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

      <ConfirmDialog
        open={deleteTarget !== null}
        title="Delete Webhook Endpoint"
        message={
          deleteTarget
            ? `Are you sure you want to delete ${deleteTarget.url}? Pending deliveries will not be retried.`
            : ''
        }
        onConfirm={handleDelete}
        onCancel={() => setDeleteTarget(null)}
      />
    </div>
  );
}
