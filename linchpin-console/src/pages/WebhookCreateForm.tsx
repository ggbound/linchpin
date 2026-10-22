import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { apiClient, ApiError } from '../api/client';
import type { WebhookEventType, WebhookEndpoint } from '../types';
import './WebhookCreateForm.css';

const WEBHOOK_EVENTS: { value: WebhookEventType; label: string }[] = [
  { value: 'session.created', label: 'Session created' },
  { value: 'session.status_idle', label: 'Session status idle' },
  { value: 'session.status', label: 'Session status (unified transition)' },
  { value: 'session.status_terminated', label: 'Session status terminated' },
  { value: 'session.terminated', label: 'Session terminated' },
  { value: 'session.failed', label: 'Session failed' },
  { value: 'session.error', label: 'Session error' },
  { value: 'session.requires_action', label: 'Session requires action' },
  { value: 'vault.credential.created', label: 'Vault credential created' },
  { value: 'vault.credential.updated', label: 'Vault credential updated' },
  { value: 'vault.credential.deleted', label: 'Vault credential deleted' },
];

const DEFAULT_EVENTS = new Set(['session.status_idle', 'session.error', 'session.requires_action']);

export default function WebhookCreateForm() {
  const navigate = useNavigate();

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
      setFormError('URL is required.');
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
      setFormError(apiErr.message || 'Failed to create webhook endpoint');
      setSubmitting(false);
    }
  };

  return (
    <div className="webhook-create-view" data-testid="webhook-create-view">
      <Link to="/webhooks" className="back-link" data-testid="back-link">
        ← Webhooks
      </Link>

      <h1>New Webhook Endpoint</h1>
      <p className="webhook-create-intro">
        Register an HTTP endpoint to receive event notifications. The signing secret
        is shown once after creation — store it securely.
      </p>

      <form onSubmit={handleSubmit} data-testid="webhook-create-form">
        <div className="webhook-form-field">
          <label htmlFor="webhook-url">URL</label>
          <input
            id="webhook-url"
            type="text"
            value={url}
            onChange={e => setUrl(e.target.value)}
            placeholder="https://example.com/hooks/linchpin"
            data-testid="webhook-url-input"
          />
        </div>

        <div className="webhook-form-field">
          <label htmlFor="webhook-description">Description (optional)</label>
          <input
            id="webhook-description"
            type="text"
            value={description}
            onChange={e => setDescription(e.target.value)}
            placeholder="What is this endpoint for?"
            data-testid="webhook-desc-input"
          />
        </div>

        <fieldset className="webhook-events-fieldset">
          <legend>Subscribed events</legend>
          <p className="webhook-events-hint">
            Leave all unchecked to use the default template
            {' '}(<code>session.status_idle</code>, <code>session.error</code>,{' '}
            <code>session.requires_action</code>).
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
                  {ev.label}
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
            {submitting ? 'Creating…' : 'Create endpoint'}
          </button>
          <Link to="/webhooks" className="btn-cancel-link">
            Cancel
          </Link>
        </div>
      </form>
    </div>
  );
}
