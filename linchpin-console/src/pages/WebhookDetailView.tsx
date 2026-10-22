import { useCallback, useEffect, useState } from 'react';
import { Link, useLocation, useNavigate, useParams } from 'react-router-dom';
import { apiClient, ApiError } from '../api/client';
import type {
  WebhookEndpoint,
  WebhookDelivery,
  PaginatedResponse,
} from '../types';
import LoadingSpinner from '../components/LoadingSpinner';
import ErrorMessage from '../components/ErrorMessage';
import ConfirmDialog from '../components/ConfirmDialog';
import './WebhookDetailView.css';

export default function WebhookDetailView() {
  const { id } = useParams<{ id: string }>();
  const location = useLocation();
  const navigate = useNavigate();

  const [endpoint, setEndpoint] = useState<WebhookEndpoint | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<ApiError | null>(null);

  // One-time secret display: from create navigation state or rotate response.
  const [oneTimeSecret, setOneTimeSecret] = useState<string | null>(
    (location.state as { secret?: string } | null)?.secret ?? null,
  );
  const [secretCopied, setSecretCopied] = useState(false);

  const [toggling, setToggling] = useState(false);
  const [rotating, setRotating] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);

  const [deliveries, setDeliveries] = useState<WebhookDelivery[]>([]);
  const [deliveriesLoading, setDeliveriesLoading] = useState(true);

  const fetchDeliveries = useCallback(async () => {
    setDeliveriesLoading(true);
    try {
      const res = await apiClient.get<PaginatedResponse<WebhookDelivery>>(
        `/v1/webhook_endpoints/${id}/deliveries?limit=50`,
      );
      setDeliveries(res.data);
    } catch {
      setDeliveries([]);
    } finally {
      setDeliveriesLoading(false);
    }
  }, [id]);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const ep = await apiClient.get<WebhookEndpoint>(`/v1/webhook_endpoints/${id}`);
        if (cancelled) return;
        setEndpoint(ep);
        setError(null);
      } catch (err) {
        if (!cancelled) setError(err as ApiError);
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    load();
    return () => { cancelled = true; };
  }, [id]);

  useEffect(() => {
    if (endpoint) fetchDeliveries();
  }, [endpoint, fetchDeliveries]);

  const handleCopySecret = async () => {
    if (!oneTimeSecret) return;
    try {
      await navigator.clipboard.writeText(oneTimeSecret);
      setSecretCopied(true);
      setTimeout(() => setSecretCopied(false), 2000);
    } catch {
      // Clipboard unavailable — user can still select the text manually.
    }
  };

  const handleToggleEnabled = async () => {
    if (!endpoint) return;
    setToggling(true);
    setActionError(null);
    try {
      const updated = await apiClient.patch<WebhookEndpoint>(
        `/v1/webhook_endpoints/${endpoint.id}`,
        { enabled: !endpoint.enabled },
      );
      setEndpoint(updated);
    } catch (err) {
      const apiErr = err as ApiError;
      setActionError(apiErr.message || 'Failed to update webhook endpoint');
    } finally {
      setToggling(false);
    }
  };

  const handleRotateSecret = async () => {
    if (!endpoint) return;
    setRotating(true);
    setActionError(null);
    try {
      const updated = await apiClient.patch<WebhookEndpoint>(
        `/v1/webhook_endpoints/${endpoint.id}`,
        { rotate_secret: true },
      );
      setEndpoint(updated);
      if (updated.secret) {
        setOneTimeSecret(updated.secret);
        setSecretCopied(false);
      }
    } catch (err) {
      const apiErr = err as ApiError;
      setActionError(apiErr.message || 'Failed to rotate secret');
    } finally {
      setRotating(false);
    }
  };

  const handleDelete = async () => {
    if (!endpoint) return;
    setDeleting(true);
    setActionError(null);
    try {
      await apiClient.delete(`/v1/webhook_endpoints/${endpoint.id}`);
      navigate('/webhooks');
    } catch (err) {
      const apiErr = err as ApiError;
      setActionError(apiErr.message || 'Failed to delete webhook endpoint');
      setShowDeleteConfirm(false);
      setDeleting(false);
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
          apiClient.get<WebhookEndpoint>(`/v1/webhook_endpoints/${id}`)
            .then(ep => setEndpoint(ep))
            .catch(e => setError(e as ApiError))
            .finally(() => setLoading(false));
        }}
      />
    );
  }

  if (!endpoint) return null;

  return (
    <div className="webhook-detail-view" data-testid="webhook-detail-view">
      <Link to="/webhooks" className="back-link" data-testid="back-link">
        ← Webhooks
      </Link>

      <div className="webhook-detail-header">
        <h1 data-testid="webhook-title">Webhook Endpoint</h1>
        <div className="webhook-detail-url">
          <code>{endpoint.url}</code>
          <span
            className={`webhook-enabled-badge ${endpoint.enabled ? 'is-enabled' : 'is-disabled'}`}
            data-testid="webhook-enabled-badge"
          >
            {endpoint.enabled ? 'enabled' : 'disabled'}
          </span>
        </div>
        {endpoint.description && (
          <p className="webhook-detail-desc">{endpoint.description}</p>
        )}
      </div>

      {oneTimeSecret && (
        <div className="secret-banner" data-testid="secret-banner">
          <div className="secret-banner-header">
            <strong>Signing secret — shown once</strong>
            <button
              className="secret-banner-dismiss"
              onClick={() => setOneTimeSecret(null)}
              aria-label="Dismiss"
              data-testid="secret-banner-dismiss"
            >
              ✕
            </button>
          </div>
          <p className="secret-banner-hint">
            Store this secret securely. It is used to sign delivery payloads and
            cannot be retrieved again (rotate it to get a new one).
          </p>
          <div className="secret-banner-row">
            <code data-testid="secret-value">{oneTimeSecret}</code>
            <button className="btn-copy-secret" onClick={handleCopySecret}>
              {secretCopied ? 'Copied' : 'Copy'}
            </button>
          </div>
        </div>
      )}

      {actionError && (
        <div className="form-error" role="alert" data-testid="webhook-action-error">
          {actionError}
        </div>
      )}

      <section className="detail-section webhook-lifecycle-actions" data-testid="lifecycle-actions">
        <button
          className="btn-sm-secondary"
          disabled={toggling}
          onClick={handleToggleEnabled}
          data-testid={endpoint.enabled ? 'webhook-disable-btn' : 'webhook-enable-btn'}
        >
          {toggling ? 'Working…' : endpoint.enabled ? 'Disable' : 'Enable'}
        </button>
        <button
          className="btn-sm-secondary"
          disabled={rotating}
          onClick={handleRotateSecret}
          data-testid="webhook-rotate-btn"
        >
          {rotating ? 'Rotating…' : 'Rotate secret'}
        </button>
        <button
          className="btn-sm-danger"
          disabled={deleting}
          onClick={() => setShowDeleteConfirm(true)}
          data-testid="webhook-delete-btn"
        >
          Delete
        </button>
      </section>

      <section className="detail-section" data-testid="webhook-info-section">
        <h2>Configuration</h2>
        <dl className="detail-grid">
          <dt>Endpoint ID</dt>
          <dd><code>{endpoint.id}</code></dd>
          <dt>URL</dt>
          <dd><code>{endpoint.url}</code></dd>
          <dt>Status</dt>
          <dd>{endpoint.enabled ? 'Enabled' : 'Disabled'}</dd>
          <dt>Created</dt>
          <dd>{new Date(endpoint.created_at).toLocaleString()}</dd>
          {endpoint.updated_at !== endpoint.created_at && (
            <>
              <dt>Updated</dt>
              <dd>{new Date(endpoint.updated_at).toLocaleString()}</dd>
            </>
          )}
        </dl>
      </section>

      <section className="detail-section" data-testid="webhook-events-section">
        <h2>Subscribed Events</h2>
        {endpoint.enabled_events.length > 0 ? (
          <div className="webhook-events-chips">
            {endpoint.enabled_events.map(ev => (
              <code key={ev} className="webhook-event-chip" data-testid="webhook-event-chip">
                {ev}
              </code>
            ))}
          </div>
        ) : (
          <p className="webhook-events-default-note">
            No explicit subscription — receives the default template
            {' '}(<code>session.status_idle</code>, <code>session.error</code>,{' '}
            <code>session.requires_action</code>).
          </p>
        )}
      </section>

      <section className="detail-section" data-testid="deliveries-section">
        <h2>
          Deliveries ({deliveries.length})
          <button
            className="btn-refresh"
            onClick={fetchDeliveries}
            disabled={deliveriesLoading}
            data-testid="deliveries-refresh-btn"
          >
            Refresh
          </button>
        </h2>
        {deliveriesLoading ? (
          <LoadingSpinner />
        ) : deliveries.length === 0 ? (
          <p className="empty-text">No deliveries recorded for this endpoint yet.</p>
        ) : (
          <div className="deliveries-table-wrapper">
            <table className="deliveries-table">
              <thead>
                <tr>
                  <th>Event</th>
                  <th>Status</th>
                  <th>Attempts</th>
                  <th>Last Attempt</th>
                  <th>Code</th>
                  <th>Error</th>
                </tr>
              </thead>
              <tbody>
                {deliveries.map(d => (
                  <tr key={d.id} data-testid="delivery-row">
                    <td><code>{d.event_type}</code></td>
                    <td>
                      <span className={`delivery-status delivery-status--${d.status}`}>
                        {d.status}
                      </span>
                    </td>
                    <td>{d.attempts}</td>
                    <td>
                      {d.last_attempt_at
                        ? new Date(d.last_attempt_at).toLocaleString()
                        : '—'}
                    </td>
                    <td>{d.last_status_code ?? '—'}</td>
                    <td className="delivery-error-cell">
                      {d.last_error ? (
                        <span title={d.last_error}>{d.last_error}</span>
                      ) : (
                        '—'
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <ConfirmDialog
        open={showDeleteConfirm}
        title="Delete Webhook Endpoint"
        message="Are you sure you want to delete this endpoint? It will stop receiving events immediately."
        onConfirm={handleDelete}
        onCancel={() => setShowDeleteConfirm(false)}
      />
    </div>
  );
}
