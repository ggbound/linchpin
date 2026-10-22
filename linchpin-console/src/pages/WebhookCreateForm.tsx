import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { apiClient, ApiError } from '../api/client';
import type { WebhookEventType, WebhookEndpoint } from '../types';
import './WebhookCreateForm.css';

const WEBHOOK_EVENTS: { value: WebhookEventType; label: string }[] = [
  { value: 'session.created', label: 'createEventSessionCreated' },
  { value: 'session.status_idle', label: 'createEventSessionStatusIdle' },
  { value: 'session.status', label: 'createEventSessionStatus' },
  { value: 'session.status_terminated', label: 'createEventSessionStatusTerminated' },
  { value: 'session.terminated', label: 'createEventSessionTerminated' },
  { value: 'session.failed', label: 'createEventSessionFailed' },
  { value: 'session.error', label: 'createEventSessionError' },
  { value: 'session.requires_action', label: 'createEventSessionRequiresAction' },
  { value: 'vault.credential.created', label: 'createEventVaultCredentialCreated' },
  { value: 'vault.credential.updated', label: 'createEventVaultCredentialUpdated' },
  { value: 'vault.credential.deleted', label: 'createEventVaultCredentialDeleted' },
];

const DEFAULT_EVENTS = new Set(['session.status_idle', 'session.error', 'session.requires_action']);

export default function WebhookCreateForm() {
  const navigate = useNavigate();
  const { t } = useTranslation('webhooks');

  const [url, setUrl] = useState('');
  const [description, setDescription] = useState('');
  const [selected, setSelected] = useState<Set<string>>(new Set(DEFAULT_EVENTS));
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  const toggleEvent = (event: WebhookEventType) => {
    setSelected(prev => {
      const next = new Set(prev);
      if (next.has(event)) {
        next.delete(event);
      } else {
        next.add(event);
      }
      return next;
    });
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    const trimmedUrl = url.trim();
    if (!trimmedUrl) {
      setFormError(t('createUrlRequired'));
      return;
    }
    setSubmitting(true);
    setFormError(null);
    try {
      const created = await apiClient.post<WebhookEndpoint>('/v1/webhook_endpoints', {
        url: trimmedUrl,
        ...(selected.size > 0 ? { enabled_events: Array.from(selected) } : {}),
        ...(description.trim() ? { description: description.trim() } : {}),
      });
      // The secret is only returned once, on the create response — pass it
      // to the detail page for one-time display.
      navigate(`/webhooks/${created.id}`, { state: { secret: created.secret } });
    } catch (err) {
      const apiErr = err as ApiError;
      setFormError(apiErr.message || t('createFailed'));
      setSubmitting(false);
    }
  };

  return (
    <div className="webhook-create-view" data-testid="webhook-create-view">
      <Link to="/webhooks" className="back-link" data-testid="back-link">
        ← {t('listTitle')}
      </Link>

      <h1>{t('createTitle')}</h1>
      <p className="webhook-create-intro">
        {t('createIntro')}
      </p>

      <form onSubmit={handleSubmit} data-testid="webhook-create-form">
        <div className="webhook-form-field">
          <label htmlFor="webhook-url">{t('createFieldUrl')}</label>
          <input
            id="webhook-url"
            type="url"
            value={url}
            onChange={e => setUrl(e.target.value)}
            placeholder="https://example.com/hooks/linchpin"
            data-testid="webhook-url-input"
          />
        </div>

        <div className="webhook-form-field">
          <label htmlFor="webhook-description">{t('createFieldDescription')}</label>
          <input
            id="webhook-description"
            type="text"
            value={description}
            onChange={e => setDescription(e.target.value)}
            placeholder={t('createDescriptionPlaceholder')}
            data-testid="webhook-desc-input"
          />
        </div>

        <fieldset className="webhook-events-fieldset">
          <legend>{t('createSubscribedEvents')}</legend>
          <p className="webhook-events-hint">
            {t('createDefaultTemplateBefore')}
            <code>session.status_idle</code>, <code>session.error</code>,{' '}
            <code>session.requires_action</code>
            {t('createDefaultTemplateAfter')}
          </p>
          <div className="webhook-events-grid">
            {WEBHOOK_EVENTS.map(ev => (
              <label key={ev.value} className="webhook-event-option">
                <input
                  type="checkbox"
                  checked={selected.has(ev.value)}
                  onChange={() => toggleEvent(ev.value)}
                  data-testid={`webhook-event-checkbox-${ev.value}`}
                />
                <span>
                  {t(ev.label)}
                  <code className="webhook-event-code">{ev.value}</code>
                </span>
              </label>
            ))}
          </div>
        </fieldset>

        {formError && (
          <div className="form-error" role="alert" data-testid="webhook-create-error">
            {formError}
          </div>
        )}

        <div className="webhook-form-actions">
          <button
            type="submit"
            className="btn-primary"
            disabled={submitting}
            data-testid="webhook-create-submit"
          >
            {submitting ? t('common:creating') : t('createSubmit')}
          </button>
          <Link to="/webhooks" className="btn-cancel-link">
            {t('common:cancel')}
          </Link>
        </div>
      </form>
    </div>
  );
}
