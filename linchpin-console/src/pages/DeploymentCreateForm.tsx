import { useEffect, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { apiClient, ApiError } from '../api/client';
import type { Agent, Environment, Deployment, InitialEventConfig, PaginatedResponse } from '../types';
import LoadingSpinner from '../components/LoadingSpinner';
import ErrorMessage from '../components/ErrorMessage';
import { TIMEZONES } from '../lib/timezones';
import { isValidCronExpr } from '../lib/cron';
import './DeploymentCreateForm.css';

interface EventRow {
  type: 'user.message' | 'user.steer';
  content: string;
}

export default function DeploymentCreateForm() {
  const navigate = useNavigate();

  const [agents, setAgents] = useState<Agent[]>([]);
  const [environments, setEnvironments] = useState<Environment[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<ApiError | null>(null);

  const [agentId, setAgentId] = useState('');
  const [environmentId, setEnvironmentId] = useState('');
  const [cron, setCron] = useState('');
  const [timezone, setTimezone] = useState('UTC');
  const [eventRows, setEventRows] = useState<EventRow[]>([
    { type: 'user.message', content: '' },
  ]);
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const [agentRes, envRes] = await Promise.all([
          apiClient.get<PaginatedResponse<Agent>>('/v1/agents?limit=100'),
          apiClient.get<PaginatedResponse<Environment>>('/v1/environments?limit=100'),
        ]);
        if (cancelled) return;
        const activeAgents = agentRes.data.filter(a => !a.archived_at);
        const activeEnvs = envRes.data.filter(e => !e.archived_at);
        setAgents(activeAgents);
        setEnvironments(activeEnvs);
        if (activeAgents.length > 0) setAgentId(prev => prev || activeAgents[0].id);
        if (activeEnvs.length > 0) setEnvironmentId(prev => prev || activeEnvs[0].id);
        setError(null);
      } catch (err) {
        if (!cancelled) setError(err as ApiError);
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    load();
    return () => { cancelled = true; };
  }, []);

  const updateRow = (idx: number, patch: Partial<EventRow>) => {
    setEventRows(prev => prev.map((r, i) => (i === idx ? { ...r, ...patch } : r)));
  };

  const cronInvalid = cron.trim() !== '' && !isValidCronExpr(cron);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (cronInvalid) {
      setCreateError('Invalid cron expression — expected 5 space-separated fields, e.g. 0 9 * * 1-5.');
      return;
    }
    setCreating(true);
    setCreateError(null);

    try {
      const initial_events: InitialEventConfig[] = eventRows
        .filter(r => r.content.trim())
        .map(r => ({ type: r.type, payload: { content: r.content.trim() } }));

      const body = {
        agent_id: agentId,
        environment_id: environmentId,
        cron: cron.trim(),
        timezone: timezone.trim() || 'UTC',
        ...(initial_events.length > 0 && { initial_events }),
      };

      const created = await apiClient.post<Deployment>('/v1/deployments', body);
      navigate(`/deployments/${created.id}`);
    } catch (err) {
      const apiErr = err as ApiError;
      setCreateError(apiErr.message || 'Failed to create deployment');
      setCreating(false);
    }
  };

  if (loading) return <LoadingSpinner />;

  if (error) {
    return (
      <ErrorMessage
        status={error.status}
        message={error.message}
        onRetry={() => {
          setError(null);
          setLoading(true);
          window.location.reload();
        }}
      />
    );
  }

  return (
    <div className="deployment-create-view" data-testid="deployment-create-view">
      <Link to="/deployments" className="back-link" data-testid="back-link">
        ← Deployments
      </Link>

      <h1 className="deployment-create-title">New Deployment</h1>
      <p className="deployment-create-subtitle">
        Schedule an agent to run automatically on a cron expression. Every trigger spawns a
        fresh session in the selected environment and seeds the configured initial events.
      </p>

      <form className="deployment-form" onSubmit={handleSubmit} data-testid="deployment-create-form">
        <div className="deployment-form-row">
          <div className="form-field">
            <label htmlFor="dep-agent">Agent</label>
            <select
              id="dep-agent"
              value={agentId}
              onChange={e => setAgentId(e.target.value)}
              required
              data-testid="dep-agent-select"
            >
              {agents.length === 0 && <option value="">No agents available</option>}
              {agents.map(a => (
                <option key={a.id} value={a.id}>{a.name}</option>
              ))}
            </select>
          </div>
          <div className="form-field">
            <label htmlFor="dep-env">Environment</label>
            <select
              id="dep-env"
              value={environmentId}
              onChange={e => setEnvironmentId(e.target.value)}
              required
              data-testid="dep-env-select"
            >
              {environments.length === 0 && <option value="">No environments available</option>}
              {environments.map(e => (
                <option key={e.id} value={e.id}>{e.name}</option>
              ))}
            </select>
          </div>
        </div>

        <div className="deployment-form-row">
          <div className="form-field">
            <label htmlFor="dep-cron">Cron Expression</label>
            <input
              id="dep-cron"
              type="text"
              value={cron}
              onChange={e => setCron(e.target.value)}
              placeholder="*/15 * * * *"
              required
              data-testid="dep-cron-input"
            />
            <p className="section-hint">
              5-field cron: minute hour day month weekday — e.g. <code>0 9 * * 1-5</code> runs
              weekdays at 09:00.
            </p>
            {cronInvalid && (
              <div className="form-error" role="alert" data-testid="dep-cron-error">
                Invalid cron expression — expected 5 space-separated fields, e.g. 0 9 * * 1-5.
              </div>
            )}
          </div>
          <div className="form-field">
            <label htmlFor="dep-timezone">Timezone</label>
            <select
              id="dep-timezone"
              value={timezone}
              onChange={e => setTimezone(e.target.value)}
              data-testid="dep-timezone-input"
            >
              {timezone && !TIMEZONES.includes(timezone) && (
                <option value={timezone}>{timezone}</option>
              )}
              {TIMEZONES.map(tz => (
                <option key={tz} value={tz}>{tz}</option>
              ))}
            </select>
            <p className="section-hint">IANA timezone used to evaluate the cron schedule.</p>
          </div>
        </div>

        <div className="form-field">
          <label>Initial Events (optional)</label>
          <p className="section-hint">
            Events seeded into every session this deployment triggers. Only
            user.message / user.steer are supported.
          </p>
          {eventRows.map((row, idx) => (
            <div className="dep-event-row" key={idx} data-testid="dep-event-row">
              <select
                value={row.type}
                onChange={e => updateRow(idx, { type: e.target.value as EventRow['type'] })}
                data-testid="dep-event-type"
              >
                <option value="user.message">user.message</option>
                <option value="user.steer">user.steer</option>
              </select>
              <input
                type="text"
                value={row.content}
                onChange={e => updateRow(idx, { content: e.target.value })}
                placeholder="Message content delivered to the agent"
                data-testid="dep-event-content"
              />
              <button
                type="button"
                className="dep-event-remove"
                onClick={() => setEventRows(prev => prev.filter((_, i) => i !== idx))}
                disabled={eventRows.length === 1}
                aria-label="Remove event"
                data-testid="dep-event-remove"
              >
                ✕
              </button>
            </div>
          ))}
          <button
            type="button"
            className="dep-event-add"
            onClick={() => setEventRows(prev => [...prev, { type: 'user.message', content: '' }])}
            data-testid="dep-event-add"
          >
            + Add event
          </button>
        </div>

        {createError && (
          <div className="form-error" role="alert" data-testid="deployment-create-error">
            {createError}
          </div>
        )}

        <div className="deployment-form-actions">
          <button
            type="submit"
            className="btn-primary"
            disabled={creating || !agentId || !environmentId || !cron.trim() || cronInvalid}
            data-testid="deployment-create-submit"
          >
            {creating ? 'Creating…' : 'Create deployment'}
          </button>
          <Link to="/deployments" className="btn-cancel-link">Cancel</Link>
        </div>
      </form>
    </div>
  );
}
