import { useEffect, useState, useCallback } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { apiClient, ApiError } from '../api/client';
import type { Deployment, Agent, Environment, PaginatedResponse } from '../types';
import LoadingSpinner from '../components/LoadingSpinner';
import ErrorMessage from '../components/ErrorMessage';
import './DeploymentListView.css';

export default function DeploymentListView() {
  const navigate = useNavigate();
  const { t, i18n } = useTranslation('deployments');

  const [deployments, setDeployments] = useState<Deployment[]>([]);
  const [agentNames, setAgentNames] = useState<Record<string, string>>({});
  const [envNames, setEnvNames] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<ApiError | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [togglingId, setTogglingId] = useState<string | null>(null);

  const fetchPage = useCallback(async () => {
    try {
      const res = await apiClient.get<PaginatedResponse<Deployment>>('/v1/deployments?limit=100');
      setDeployments(res.data);
      setError(null);
    } catch (err) {
      setError(err as ApiError);
    }
  }, []);

  useEffect(() => {
    setLoading(true);
    Promise.all([
      fetchPage(),
      apiClient.get<PaginatedResponse<Agent>>('/v1/agents?limit=100')
        .then(res => {
          const map: Record<string, string> = {};
          res.data.forEach(a => { map[a.id] = a.name; });
          setAgentNames(map);
        })
        .catch(() => undefined),
      apiClient.get<PaginatedResponse<Environment>>('/v1/environments?limit=100')
        .then(res => {
          const map: Record<string, string> = {};
          res.data.forEach(e => { map[e.id] = e.name; });
          setEnvNames(map);
        })
        .catch(() => undefined),
    ]).finally(() => setLoading(false));
  }, [fetchPage]);

  const handleToggle = async (dep: Deployment) => {
    setActionError(null);
    setTogglingId(dep.id);
    try {
      const action = dep.status === 'active' ? 'pause' : 'resume';
      const updated = await apiClient.post<Deployment>(`/v1/deployments/${dep.id}/${action}`, {});
      setDeployments(prev => prev.map(d => (d.id === dep.id ? updated : d)));
    } catch (err) {
      const apiErr = err as ApiError;
      setActionError(apiErr.message || t('listActionFailed'));
    } finally {
      setTogglingId(null);
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
          fetchPage().finally(() => setLoading(false));
        }}
      />
    );
  }

  return (
    <div className="deployment-list-view" data-testid="deployment-list-view">
      <div className="deployment-list-header">
        <div>
          <h1>{t('listTitle')}</h1>
          <p>{t('listSubtitle')}</p>
        </div>
        <button
          className="btn-primary"
          data-testid="new-deployment-btn"
          onClick={() => navigate('/deployments/new')}
        >
          {t('listNew')}
        </button>
      </div>

      {actionError && (
        <div className="deployment-action-error" role="alert" data-testid="deployment-action-error">
          {actionError}
        </div>
      )}

      <div className="deployment-table-wrapper">
        <table className="deployment-table" data-testid="deployment-table">
          <thead>
            <tr>
              <th>{t('listColumnAgent')}</th>
              <th>{t('listColumnEnvironment')}</th>
              <th>{t('listColumnCron')}</th>
              <th>{t('listColumnTimezone')}</th>
              <th>{t('listColumnNextRun')}</th>
              <th>{t('listColumnStatus')}</th>
              <th>{t('common:actions')}</th>
            </tr>
          </thead>
          <tbody>
            {deployments.length === 0 ? (
              <tr>
                <td colSpan={7} className="empty-row">
                  {t('listEmpty')}
                </td>
              </tr>
            ) : (
              deployments.map(dep => (
                <tr
                  key={dep.id}
                  className="deployment-row"
                  data-testid={`deployment-row-${dep.id}`}
                  onClick={() => navigate(`/deployments/${dep.id}`)}
                  role="link"
                  tabIndex={0}
                  onKeyDown={e => {
                    if (e.key === 'Enter') navigate(`/deployments/${dep.id}`);
                  }}
                >
                  <td data-testid="deployment-cell-agent">
                    <Link
                      to={`/agents/${dep.agent_id}`}
                      className="deployment-entity-link"
                      onClick={e => e.stopPropagation()}
                    >
                      {agentNames[dep.agent_id] || dep.agent_id.slice(0, 8)}
                    </Link>
                  </td>
                  <td data-testid="deployment-cell-env">
                    <Link
                      to={`/environments/${dep.environment_id}`}
                      className="deployment-entity-link"
                      onClick={e => e.stopPropagation()}
                    >
                      {envNames[dep.environment_id] || dep.environment_id.slice(0, 8)}
                    </Link>
                  </td>
                  <td><code>{dep.cron}</code></td>
                  <td>{dep.timezone}</td>
                  <td data-testid="deployment-cell-next-run">
                    {dep.status === 'active' && dep.next_run_at
                      ? new Date(dep.next_run_at).toLocaleString(i18n.language)
                      : '—'}
                  </td>
                  <td>
                    <span className={`deployment-status deployment-status--${dep.status}`}>
                      {t(`status:${dep.status}`, { defaultValue: dep.status })}
                    </span>
                  </td>
                  <td>
                    <div
                      className="deployment-actions-cell"
                      onClick={e => e.stopPropagation()}
                      onKeyDown={e => e.stopPropagation()}
                    >
                      <button
                        className="btn-sm-secondary"
                        disabled={togglingId === dep.id}
                        onClick={() => handleToggle(dep)}
                        data-testid={`deployment-${dep.status === 'active' ? 'pause' : 'resume'}-btn`}
                      >
                        {dep.status === 'active' ? t('listPause') : t('listResume')}
                      </button>
                    </div>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
