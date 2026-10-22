import { useEffect, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { apiClient, ApiError } from '../api/client';
import type { Environment, EnvironmentPackages, LimitedNetworking } from '../types';
import LoadingSpinner from '../components/LoadingSpinner';
import ErrorMessage from '../components/ErrorMessage';
import ConfirmDialog from '../components/ConfirmDialog';
import './EnvironmentDetailView.css';

const TIER_LABELS: Record<string, string> = {
  '1c2g': 'Small — 1 vCPU / 2 GB RAM',
  '2c4g': 'Medium — 2 vCPU / 4 GB RAM',
  '4c8g': 'Large — 4 vCPU / 8 GB RAM',
};

const NETWORKING_LABELS: Record<string, string> = {
  none: 'None — no external access',
  limited: 'Limited — allowlisted domains only',
  unrestricted: 'Unrestricted — full access',
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
  if (networking.type !== 'limited') {
    return <dd data-testid="networking-type">{NETWORKING_LABELS[networking.type]}</dd>;
  }
  const limited = networking as LimitedNetworking;
  return (
    <dd data-testid="networking-type">
      {NETWORKING_LABELS.limited}
      <div className="limited-detail">
        <div className="limited-row">
          <span className="limited-label">Allowed hosts</span>
          {limited.allowed_hosts.length > 0 ? (
            <ul className="host-list" data-testid="allowed-hosts">
              {limited.allowed_hosts.map(host => (
                <li key={host}>
                  <code>{host}</code>
                </li>
              ))}
            </ul>
          ) : (
            <span className="muted-value">None</span>
          )}
        </div>
        <div className="limited-row">
          <span className="limited-label">Curated MCP domains</span>
          <span>{limited.allow_mcp_servers ? 'Allowed' : 'Blocked'}</span>
        </div>
        <div className="limited-row">
          <span className="limited-label">Package manager domains</span>
          <span>{limited.allow_package_managers ? 'Allowed' : 'Blocked'}</span>
        </div>
      </div>
    </dd>
  );
}

function PackagesList({ packages }: { packages: EnvironmentPackages }) {
  const any = PACKAGE_MANAGERS.some(m => packages[m].length > 0);
  if (!any) {
    return <dd data-testid="packages-list">None</dd>;
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
      setActionError((err as ApiError).message || 'Failed to archive environment');
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
          ? 'Cannot delete: non-terminated sessions still reference this environment. Terminate them first, or archive the environment instead.'
          : apiErr.message || 'Failed to delete environment',
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
        ← Environments
      </Link>

      <div className="environment-detail-header">
        <h1 data-testid="environment-name">
          {environment.name}
          {isArchived && (
            <span className="archived-badge" data-testid="archived-badge">
              Archived
            </span>
          )}
        </h1>
        <div className="environment-detail-meta">
          <span data-testid="environment-id">
            <code>{environment.id}</code>
          </span>
          <span className="meta-separator">·</span>
          <span data-testid="environment-created">
            Created {new Date(environment.created_at).toLocaleString()}
          </span>
          {isArchived && (
            <>
              <span className="meta-separator">·</span>
              <span data-testid="environment-archived">
                Archived {new Date(environment.archived_at as string).toLocaleString()}
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
            {archiving ? 'Archiving…' : 'Archive'}
          </button>
        )}
        <button
          className="btn-sm-danger"
          disabled={deleting}
          onClick={() => setShowDeleteConfirm(true)}
          data-testid="delete-button"
        >
          {deleting ? 'Deleting…' : 'Delete'}
        </button>
      </section>

      <section className="detail-section" data-testid="resources-section">
        <h2>Resources</h2>
        <dl className="detail-grid">
          <dt>Spec tier</dt>
          <dd data-testid="resource-tier">
            <span className="tier-badge">{config.resources.tier}</span>
            <span className="tier-label">{TIER_LABELS[config.resources.tier]}</span>
          </dd>
        </dl>
      </section>

      <section className="detail-section" data-testid="networking-section">
        <h2>Networking</h2>
        <dl className="detail-grid">
          <dt>Mode</dt>
          <NetworkingDetails networking={config.networking} />
        </dl>
      </section>

      <section className="detail-section" data-testid="packages-section">
        <h2>Pre-installed packages</h2>
        <dl className="detail-grid">
          <dt>Packages</dt>
          <PackagesList packages={config.packages} />
        </dl>
      </section>

      <section className="detail-section" data-testid="advanced-section">
        <h2>Advanced</h2>
        <dl className="detail-grid">
          <dt>Isolation</dt>
          <dd data-testid="isolation">{config.isolation}</dd>
          <dt>Tool search</dt>
          <dd data-testid="tool-search">
            {config.tool_search && config.tool_search.enabled
              ? `Enabled — activate above ${config.tool_search.threshold} tools, max ${config.tool_search.max_results} per search`
              : 'Disabled'}
          </dd>
          <dt>Executor node</dt>
          <dd data-testid="executor-node">
            {config.executor_node ? (
              <Link to="/executor-nodes" title="View executor nodes">{config.executor_node}</Link>
            ) : (
              'Local (this server)'
            )}
          </dd>
        </dl>
      </section>

      <ConfirmDialog
        open={showArchiveConfirm}
        title="Archive Environment"
        message={`Archive "${environment.name}"? Archived environments cannot be used for new sessions but remain reachable for forensics and replay.`}
        onConfirm={handleArchive}
        onCancel={() => setShowArchiveConfirm(false)}
      />
      <ConfirmDialog
        open={showDeleteConfirm}
        title="Delete Environment"
        message={`Permanently delete "${environment.name}"? This fails while non-terminated sessions still reference it — archive instead to keep history.`}
        onConfirm={handleDelete}
        onCancel={() => setShowDeleteConfirm(false)}
      />
    </div>
  );
}
