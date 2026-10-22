import { useEffect, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { apiClient, ApiError } from '../api/client';
import type { Environment, EnvironmentPackages, LimitedNetworking } from '../types';
import LoadingSpinner from '../components/LoadingSpinner';
import ErrorMessage from '../components/ErrorMessage';
import ConfirmDialog from '../components/ConfirmDialog';
import './EnvironmentDetailView.css';

const TIER_LABELS: Record<string, string> = {
  '1c2g': 'detailTier1c2g',
  '2c4g': 'detailTier2c4g',
  '4c8g': 'detailTier4c8g',
};

const NETWORKING_LABELS: Record<string, string> = {
  none: 'detailNetworkingNone',
  limited: 'detailNetworkingLimited',
  unrestricted: 'detailNetworkingUnrestricted',
};

const PACKAGE_MANAGERS: (keyof EnvironmentPackages)[] = [
  'apt',
  'pip',
  'npm',
  'cargo',
  'gem',
  'go',
];

function NetworkingDetails({ networking }: { networking: Environment['config']['networking'] }) {
  const { t } = useTranslation('environments');
  if (networking.type !== 'limited') {
    return <dd data-testid="networking-type">{t(NETWORKING_LABELS[networking.type])}</dd>;
  }
  const limited = networking as LimitedNetworking;
  return (
    <dd data-testid="networking-type">
      {t(NETWORKING_LABELS.limited)}
      <div className="limited-detail">
        <div className="limited-row">
          <span className="limited-label">{t('detailAllowedHosts')}</span>
          {limited.allowed_hosts.length > 0 ? (
            <ul className="host-list" data-testid="allowed-hosts">
              {limited.allowed_hosts.map(host => (
                <li key={host}>
                  <code>{host}</code>
                </li>
              ))}
            </ul>
          ) : (
            <span className="muted-value">{t('common:none')}</span>
          )}
        </div>
        <div className="limited-row">
          <span className="limited-label">{t('detailCuratedMcpDomains')}</span>
          <span>{limited.allow_mcp_servers ? t('detailAllowed') : t('detailBlocked')}</span>
        </div>
        <div className="limited-row">
          <span className="limited-label">{t('detailPackageManagerDomains')}</span>
          <span>{limited.allow_package_managers ? t('detailAllowed') : t('detailBlocked')}</span>
        </div>
      </div>
    </dd>
  );
}

function PackagesList({ packages }: { packages: EnvironmentPackages }) {
  const { t } = useTranslation('environments');
  const any = PACKAGE_MANAGERS.some(m => packages[m].length > 0);
  if (!any) {
    return <dd data-testid="packages-list">{t('common:none')}</dd>;
  }
  return (
    <dd data-testid="packages-list">
      <div className="packages-detail">
        {PACKAGE_MANAGERS.map(manager => {
          if (packages[manager].length === 0) return null;
          return (
            <div className="packages-row" key={manager}>
              <span className="packages-manager">{manager}</span>
              <div className="package-tags">
                {packages[manager].map(pkg => (
                  <code className="package-tag" key={pkg}>
                    {pkg}
                  </code>
                ))}
              </div>
            </div>
          );
        })}
      </div>
    </dd>
  );
}

export default function EnvironmentDetailView() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const { t, i18n } = useTranslation('environments');
  const [environment, setEnvironment] = useState<Environment | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<ApiError | null>(null);

  const [actionError, setActionError] = useState<string | null>(null);
  const [archiving, setArchiving] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [showArchiveConfirm, setShowArchiveConfirm] = useState(false);
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);

  const fetchEnvironment = async () => {
    try {
      setError(null);
      const data = await apiClient.get<Environment>(`/v1/environments/${id}`);
      setEnvironment(data);
    } catch (err) {
      setError(err as ApiError);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    setLoading(true);
    fetchEnvironment();
  }, [id]);

  const handleArchive = async () => {
    if (!environment) return;
    setShowArchiveConfirm(false);
    setArchiving(true);
    setActionError(null);
    try {
      const updated = await apiClient.post<Environment>(
        `/v1/environments/${environment.id}/archive`,
        {},
      );
      setEnvironment(updated);
    } catch (err) {
      setActionError((err as ApiError).message || t('detailArchiveFailed'));
    } finally {
      setArchiving(false);
    }
  };

  const handleDelete = async () => {
    if (!environment) return;
    setShowDeleteConfirm(false);
    setDeleting(true);
    setActionError(null);
    try {
      await apiClient.delete(`/v1/environments/${environment.id}`);
      navigate('/environments');
    } catch (err) {
      const apiErr = err as ApiError;
      setActionError(
        apiErr.status === 409
          ? t('detailDeleteConflict')
          : apiErr.message || t('detailDeleteFailed'),
      );
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
          fetchEnvironment();
        }}
      />
    );
  }

  if (!environment) return null;

  const { config } = environment;
  const isArchived = Boolean(environment.archived_at);

  return (
    <div className="environment-detail-view" data-testid="environment-detail-view">
      <Link to="/environments" className="back-link" data-testid="back-link">
        ← {t('listTitle')}
      </Link>

      <div className="environment-detail-header">
        <h1 data-testid="environment-name">
          {environment.name}
          {isArchived && (
            <span className="archived-badge" data-testid="archived-badge">
              {t('detailArchivedBadge')}
            </span>
          )}
        </h1>
        <div className="environment-detail-meta">
          <span data-testid="environment-id">
            <code>{environment.id}</code>
          </span>
          <span className="meta-separator">·</span>
          <span data-testid="environment-created">
            {t('detailCreatedAt', {
              time: new Date(environment.created_at).toLocaleString(i18n.language),
            })}
          </span>
          {isArchived && (
            <>
              <span className="meta-separator">·</span>
              <span data-testid="environment-archived">
                {t('detailArchivedAt', {
                  time: new Date(environment.archived_at as string).toLocaleString(i18n.language),
                })}
              </span>
            </>
          )}
        </div>
      </div>

      {actionError && (
        <div className="form-error" role="alert" data-testid="environment-action-error">
          {actionError}
        </div>
      )}

      <section className="detail-section env-lifecycle-actions" data-testid="lifecycle-actions">
        {!isArchived && (
          <button
            className="btn-archive"
            disabled={archiving}
            onClick={() => setShowArchiveConfirm(true)}
            data-testid="archive-button"
          >
            {archiving ? t('detailArchiving') : t('detailArchive')}
          </button>
        )}
        <button
          className="btn-sm-danger"
          disabled={deleting}
          onClick={() => setShowDeleteConfirm(true)}
          data-testid="delete-button"
        >
          {deleting ? t('common:deleting') : t('common:delete')}
        </button>
      </section>

      <section className="detail-section" data-testid="resources-section">
        <h2>{t('detailResources')}</h2>
        <dl className="detail-grid">
          <dt>{t('detailSpecTier')}</dt>
          <dd data-testid="resource-tier">
            <span className="tier-badge">{config.resources.tier}</span>
            <span className="tier-label">
              {TIER_LABELS[config.resources.tier] ? t(TIER_LABELS[config.resources.tier]) : ''}
            </span>
          </dd>
        </dl>
      </section>

      <section className="detail-section" data-testid="networking-section">
        <h2>{t('detailNetworking')}</h2>
        <dl className="detail-grid">
          <dt>{t('detailMode')}</dt>
          <NetworkingDetails networking={config.networking} />
        </dl>
      </section>

      <section className="detail-section" data-testid="packages-section">
        <h2>{t('detailPreInstalledPackages')}</h2>
        <dl className="detail-grid">
          <dt>{t('detailPackages')}</dt>
          <PackagesList packages={config.packages} />
        </dl>
      </section>

      <section className="detail-section" data-testid="advanced-section">
        <h2>{t('detailAdvanced')}</h2>
        <dl className="detail-grid">
          <dt>{t('detailIsolation')}</dt>
          <dd data-testid="isolation">{config.isolation}</dd>
          <dt>{t('detailToolSearch')}</dt>
          <dd data-testid="tool-search">
            {config.tool_search && config.tool_search.enabled
              ? t('detailToolSearchEnabled', {
                  threshold: config.tool_search.threshold,
                  maxResults: config.tool_search.max_results,
                })
              : t('common:disabled')}
          </dd>
          <dt>{t('detailExecutorNode')}</dt>
          <dd data-testid="executor-node">
            {config.executor_node ? (
              <Link to="/executor-nodes" title={t('detailViewExecutorNodes')}>{config.executor_node}</Link>
            ) : (
              t('detailLocalServer')
            )}
          </dd>
        </dl>
      </section>

      <ConfirmDialog
        open={showArchiveConfirm}
        title={t('detailArchiveConfirmTitle')}
        message={t('detailArchiveConfirmMessage', { name: environment.name })}
        onConfirm={handleArchive}
        onCancel={() => setShowArchiveConfirm(false)}
      />
      <ConfirmDialog
        open={showDeleteConfirm}
        title={t('detailDeleteConfirmTitle')}
        message={t('detailDeleteConfirmMessage', { name: environment.name })}
        onConfirm={handleDelete}
        onCancel={() => setShowDeleteConfirm(false)}
      />
    </div>
  );
}
