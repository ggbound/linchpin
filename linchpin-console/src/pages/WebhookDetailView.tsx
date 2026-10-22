import { useCallback, useEffect, useState } from 'react';
import { Link, useLocation, useNavigate, useParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
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
  const { t, i18n } = useTranslation('webhooks');

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
      setActionError(apiErr.message || t('detailActionFailed'));
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
      setActionError(apiErr.message || t('detailRotateFailed'));
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
      setActionError(apiErr.message || t('detailDeleteFailed'));
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
        ← {t('listTitle')}
      </Link>

      <div className="webhook-detail-header">
        <h1 data-testid="webhook-title">{t('detailTitle')}</h1>
        <div className="webhook-detail-url">
          <code>{endpoint.url}</code>
          <span
            className={`webhook-enabled-badge ${endpoint.enabled ? 'is-enabled' : 'is-disabled'}`}
            data-testid="webhook-enabled-badge"
          >
            {endpoint.enabled ? t('common:enabled') : t('common:disabled')}
          </span>
        </div>
        {endpoint.description && (
          <p className="webhook-detail-desc">{endpoint.description}</p>
        )}
      </div>

      {oneTimeSecret && (
        <div className="secret-banner" data-testid="secret-banner">
          <div className="secret-banner-header">
            <strong>{t('detailSecretShownOnce')}</strong>
            <button
              className="secret-banner-dismiss"
              onClick={() => setOneTimeSecret(null)}
              aria-label={t('detailDismiss')}
              data-testid="secret-banner-dismiss"
            >
              ✕
            </button>
          </div>
          <p className="secret-banner-hint">
            {t('detailSecretHint')}
          </p>
          <div className="secret-banner-row">
            <code data-testid="secret-value">{oneTimeSecret}</code>
            <button className="btn-copy-secret" onClick={handleCopySecret}>
              {secretCopied ? t('common:copied') : t('common:copy')}
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
          {toggling ? t('detailWorking') : endpoint.enabled ? t('common:disable') : t('common:enable')}
        </button>
        <button
          className="btn-sm-secondary"
          disabled={rotating}
          onClick={handleRotateSecret}
          data-testid="webhook-rotate-btn"
        >
          {rotating ? t('detailRotating') : t('detailRotateSecret')}
        </button>
        <button
          className="btn-sm-danger"
          disabled={deleting}
          onClick={() => setShowDeleteConfirm(true)}
          data-testid="webhook-delete-btn"
        >
          {t('common:delete')}
        </button>
      </section>

      <section className="detail-section" data-testid="webhook-info-section">
        <h2>{t('detailConfiguration')}</h2>
        <dl className="detail-grid">
          <dt>{t('detailFieldEndpointId')}</dt>
          <dd><code>{endpoint.id}</code></dd>
          <dt>{t('detailFieldUrl')}</dt>
          <dd><code>{endpoint.url}</code></dd>
          <dt>{t('detailFieldStatus')}</dt>
          <dd>{endpoint.enabled ? t('common:enabled') : t('common:disabled')}</dd>
          <dt>{t('common:created')}</dt>
          <dd>{new Date(endpoint.created_at).toLocaleString(i18n.language)}</dd>
          {endpoint.updated_at !== endpoint.created_at && (
            <>
              <dt>{t('common:updated')}</dt>
              <dd>{new Date(endpoint.updated_at).toLocaleString(i18n.language)}</dd>
            </>
          )}
        </dl>
      </section>

      <section className="detail-section" data-testid="webhook-events-section">
        <h2>{t('detailSubscribedEvents')}</h2>
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
            {t('detailDefaultTemplateBefore')}
            <code>session.status_idle</code>, <code>session.error</code>,{' '}
            <code>session.requires_action</code>
            {t('detailDefaultTemplateAfter')}
          </p>
        )}
      </section>

      <section className="detail-section" data-testid="deliveries-section">
        <h2>
          {t('detailDeliveries', { total: deliveries.length })}
          <button
            className="btn-refresh"
            onClick={fetchDeliveries}
            disabled={deliveriesLoading}
            data-testid="deliveries-refresh-btn"
          >
            {t('common:refresh')}
          </button>
        </h2>
        {deliveriesLoading ? (
          <LoadingSpinner />
        ) : deliveries.length === 0 ? (
          <p className="empty-text">{t('detailNoDeliveries')}</p>
        ) : (
          <div className="deliveries-table-wrapper">
            <table className="deliveries-table">
              <thead>
                <tr>
                  <th>{t('detailColumnEvent')}</th>
                  <th>{t('detailColumnStatus')}</th>
                  <th>{t('detailColumnAttempts')}</th>
                  <th>{t('detailColumnLastAttempt')}</th>
                  <th>{t('detailColumnCode')}</th>
                  <th>{t('detailColumnError')}</th>
                </tr>
              </thead>
              <tbody>
                {deliveries.map(d => (
                  <tr key={d.id} data-testid="delivery-row">
                    <td><code>{d.event_type}</code></td>
                    <td>
                      <span className={`delivery-status delivery-status--${d.status}`}>
                        {t(`status:${d.status}`, { defaultValue: String(d.status) })}
                      </span>
                    </td>
                    <td>{d.attempts}</td>
                    <td>
                      {d.last_attempt_at
                        ? new Date(d.last_attempt_at).toLocaleString(i18n.language)
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
        title={t('detailDeleteTitle')}
        message={t('detailDeleteMessage')}
        onConfirm={handleDelete}
        onCancel={() => setShowDeleteConfirm(false)}
      />
    </div>
  );
}
