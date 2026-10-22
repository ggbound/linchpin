import { useEffect, useState, FormEvent } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { apiClient, ApiError, extractValidationErrors, FieldValidationError } from '../api/client';
import type {
  CreateEnvironmentRequest,
  EnvironmentPackages,
  SandboxResourceTier,
  SandboxIsolation,
  ExecutorNode,
} from '../types';
import './EnvironmentCreateForm.css';

const TIER_OPTIONS: { value: SandboxResourceTier; label: string }[] = [
  { value: '1c2g', label: 'createTier1c2g' },
  { value: '2c4g', label: 'createTier2c4g' },
  { value: '4c8g', label: 'createTier4c8g' },
];

const PACKAGE_MANAGERS: { key: keyof EnvironmentPackages; placeholder: string }[] = [
  { key: 'apt', placeholder: 'curl, git, jq' },
  { key: 'pip', placeholder: 'requests, pandas' },
  { key: 'npm', placeholder: 'typescript, tsx' },
  { key: 'cargo', placeholder: 'serde-cli' },
  { key: 'gem', placeholder: 'rake' },
  { key: 'go', placeholder: 'golang.org/x/tools/cmd/goimports' },
];

/** Split a comma/newline separated input into a clean list. */
function parseList(input: string): string[] {
  return Array.from(
    new Set(
      input
        .split(/[,\n]/)
        .map(s => s.trim())
        .filter(Boolean),
    ),
  );
}

export default function EnvironmentCreateForm() {
  const navigate = useNavigate();
  const { t } = useTranslation('environments');

  const [name, setName] = useState('');
  const [tier, setTier] = useState<SandboxResourceTier>('1c2g');
  const [networkingType, setNetworkingType] = useState<'none' | 'limited' | 'unrestricted'>('none');
  const [allowedHosts, setAllowedHosts] = useState('');
  const [allowMcpServers, setAllowMcpServers] = useState(true);
  const [allowPackageManagers, setAllowPackageManagers] = useState(true);
  const [packages, setPackages] = useState<Record<keyof EnvironmentPackages, string>>({
    apt: '',
    pip: '',
    npm: '',
    cargo: '',
    gem: '',
    go: '',
  });
  const [isolation, setIsolation] = useState<SandboxIsolation>('docker');
  const [toolSearchEnabled, setToolSearchEnabled] = useState(false);
  const [toolSearchThreshold, setToolSearchThreshold] = useState(20);
  const [toolSearchMaxResults, setToolSearchMaxResults] = useState(10);
  const [executorNodes, setExecutorNodes] = useState<ExecutorNode[]>([]);
  const [executorNode, setExecutorNode] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [validationErrors, setValidationErrors] = useState<FieldValidationError[]>([]);

  useEffect(() => {
    let cancelled = false;
    apiClient
      .get<ExecutorNode[]>('/v1/executor-nodes')
      .then(nodes => {
        if (!cancelled) setExecutorNodes(nodes);
      })
      .catch(() => {
        // Non-critical: the selector simply stays empty (local execution).
        if (!cancelled) setExecutorNodes([]);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    setValidationErrors([]);
    setSubmitting(true);

    const body: CreateEnvironmentRequest = {
      name,
      config: {
        networking:
          networkingType === 'limited'
            ? {
                type: 'limited',
                allowed_hosts: parseList(allowedHosts),
                allow_mcp_servers: allowMcpServers,
                allow_package_managers: allowPackageManagers,
              }
            : { type: networkingType },
        packages: {
          apt: parseList(packages.apt),
          pip: parseList(packages.pip),
          npm: parseList(packages.npm),
          cargo: parseList(packages.cargo),
          gem: parseList(packages.gem),
          go: parseList(packages.go),
        },
        resources: { tier },
        isolation,
        tool_search: toolSearchEnabled
          ? {
              enabled: true,
              threshold: toolSearchThreshold,
              max_results: toolSearchMaxResults,
            }
          : null,
        executor_node: executorNode || null,
      },
    };

    try {
      await apiClient.post('/v1/environments', body);
      navigate('/environments');
    } catch (err) {
      const apiErr = err as ApiError;
      setValidationErrors(extractValidationErrors(err));
      setError(apiErr.message || t('createFailed'));
      setSubmitting(false);
    }
  };

  // --- Field-level validation error rendering (422) ---
  const KNOWN_TOP_FIELDS = new Set(['name', 'config']);

  const errorsFor = (prefix: string): string[] =>
    validationErrors
      .filter((fe) => fe.path === prefix || fe.path.startsWith(`${prefix}.`))
      .map((fe) => (fe.path === prefix ? fe.msg : `${fe.path.slice(prefix.length + 1)}: ${fe.msg}`));

  const renderFieldErrors = (prefix: string) => {
    const msgs = errorsFor(prefix);
    if (msgs.length === 0) return null;
    return (
      <ul className="field-error" data-testid={`field-error-${prefix.replace(/\./g, '-')}`}>
        {msgs.map((m, i) => (
          <li key={i}>{m}</li>
        ))}
      </ul>
    );
  };

  const unmappedErrors = validationErrors.filter(
    (fe) => !KNOWN_TOP_FIELDS.has(fe.path.split('.')[0]),
  );

  // config.* errors outside the known subsections — shown at the top of the Advanced section
  const CONFIG_SUBSECTIONS = [
    'networking',
    'packages',
    'resources',
    'isolation',
    'tool_search',
    'executor_node',
  ];
  const configOtherErrors = validationErrors
    .filter((fe) => {
      if (!fe.path.startsWith('config.')) return false;
      const rest = fe.path.slice('config.'.length);
      return !CONFIG_SUBSECTIONS.some((k) => rest === k || rest.startsWith(`${k}.`));
    })
    .map((fe) => `${fe.path.slice('config.'.length)}: ${fe.msg}`);

  return (
    <div className="environment-create-form">
      <Link to="/environments" className="back-link">
        ← {t('listTitle')}
      </Link>
      <h1>{t('createTitle')}</h1>

      {error && (
        <div className="form-error" data-testid="form-error">
          <p>
            {validationErrors.length > 0
              ? t('createValidationFailed')
              : error}
          </p>
          {unmappedErrors.length > 0 && (
            <ul className="form-error-list">
              {unmappedErrors.map((fe, i) => (
                <li key={i}>
                  {fe.path}: {fe.msg}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      <form onSubmit={handleSubmit}>
        <section className="form-section">
          <h2>{t('createBasics')}</h2>
          <div className="form-field">
            <label htmlFor="env-name">{t('common:name')}</label>
            <input
              id="env-name"
              type="text"
              value={name}
              onChange={e => setName(e.target.value)}
              required
              placeholder={t('createNamePlaceholder')}
            />
            {renderFieldErrors('name')}
          </div>
        </section>

        <section className="form-section">
          <h2>{t('createResources')}</h2>
          <div className="form-field">
            <label htmlFor="env-tier">{t('createSpecTier')}</label>
            <select
              id="env-tier"
              data-testid="env-tier"
              value={tier}
              onChange={e => setTier(e.target.value as SandboxResourceTier)}
            >
              {TIER_OPTIONS.map(opt => (
                <option key={opt.value} value={opt.value}>
                  {t(opt.label)}
                </option>
              ))}
            </select>
            {renderFieldErrors('config.resources')}
            <p className="field-help">{t('createTierHelp')}</p>
          </div>
        </section>

        <section className="form-section">
          <h2>{t('createNetworking')}</h2>
          {renderFieldErrors('config.networking')}
          <div className="form-field">
            <label htmlFor="networking-type">{t('createNetworkMode')}</label>
            <select
              id="networking-type"
              data-testid="networking-type"
              value={networkingType}
              onChange={e =>
                setNetworkingType(e.target.value as 'none' | 'limited' | 'unrestricted')
              }
            >
              <option value="none">{t('createNetworkingNone')}</option>
              <option value="limited">{t('createNetworkingLimited')}</option>
              <option value="unrestricted">{t('createNetworkingUnrestricted')}</option>
            </select>
          </div>

          {networkingType === 'limited' && (
            <div className="limited-subform" data-testid="limited-subform">
              <div className="form-field">
                <label htmlFor="allowed-hosts">{t('createAllowedHosts')}</label>
                <textarea
                  id="allowed-hosts"
                  data-testid="allowed-hosts"
                  className="allowed-hosts-input"
                  value={allowedHosts}
                  onChange={e => setAllowedHosts(e.target.value)}
                  placeholder={'api.github.com\n*.npmjs.org\nregistry.pypi.org'}
                />
                <p className="field-help">
                  {t('createAllowedHostsHelp1')}
                  <code>*.example.com</code>
                  {t('createAllowedHostsHelp2')}
                </p>
              </div>
              <label className="checkbox-row">
                <input
                  type="checkbox"
                  checked={allowMcpServers}
                  onChange={e => setAllowMcpServers(e.target.checked)}
                />
                <span>
                  {t('createAllowMcpDomains')}
                  <span className="checkbox-hint">
                    {t('createAllowMcpDomainsHint')}
                  </span>
                </span>
              </label>
              <label className="checkbox-row">
                <input
                  type="checkbox"
                  checked={allowPackageManagers}
                  onChange={e => setAllowPackageManagers(e.target.checked)}
                />
                <span>
                  {t('createAllowPackageDomains')}
                  <span className="checkbox-hint">
                    {t('createAllowPackageDomainsHint')}
                  </span>
                </span>
              </label>
            </div>
          )}
        </section>

        <section className="form-section">
          <h2>{t('createPreInstalledPackages')}</h2>
          <p className="field-help packages-lead">
            {t('createPackagesLead')}
          </p>
          {renderFieldErrors('config.packages')}
          <div className="packages-grid">
            {PACKAGE_MANAGERS.map(({ key, placeholder }) => (
              <div className="form-field package-field" key={key}>
                <label htmlFor={`pkg-${key}`}>{key}</label>
                <input
                  id={`pkg-${key}`}
                  type="text"
                  value={packages[key]}
                  onChange={e =>
                    setPackages(prev => ({ ...prev, [key]: e.target.value }))
                  }
                  placeholder={placeholder}
                />
              </div>
            ))}
          </div>
        </section>

        <section className="form-section">
          <h2>{t('createAdvanced')}</h2>
          {configOtherErrors.length > 0 && (
            <ul className="field-error" data-testid="field-error-config">
              {configOtherErrors.map((m, i) => (
                <li key={i}>{m}</li>
              ))}
            </ul>
          )}
          <div className="form-field">
            <label htmlFor="env-isolation">{t('createIsolationRuntime')}</label>
            <select
              id="env-isolation"
              data-testid="env-isolation"
              value={isolation}
              onChange={e => setIsolation(e.target.value as SandboxIsolation)}
            >
              <option value="docker">{t('createIsolationDocker')}</option>
              <option value="microvm">{t('createIsolationMicrovm')}</option>
            </select>
            {renderFieldErrors('config.isolation')}
          </div>

          <div className="form-field">
            <label htmlFor="env-executor-node">{t('createExecutorNode')}</label>
            <select
              id="env-executor-node"
              data-testid="env-executor-node"
              value={executorNode}
              onChange={e => setExecutorNode(e.target.value)}
            >
              <option value="">{t('createLocalExecutor')}</option>
              {executorNodes.map(node => (
                <option key={node.id} value={node.name}>
                  {node.name} ({t(`status:${node.status}`, { defaultValue: String(node.status) })})
                </option>
              ))}
            </select>
            <p className="field-help">
              {t('createExecutorNodeHelp')}
            </p>
            {renderFieldErrors('config.executor_node')}
          </div>

          <label className="checkbox-row" data-testid="tool-search-toggle">
            <input
              type="checkbox"
              checked={toolSearchEnabled}
              onChange={e => setToolSearchEnabled(e.target.checked)}
            />
            <span>
              {t('createToolSearch')}
              <span className="checkbox-hint">
                {t('createToolSearchHint')}
              </span>
            </span>
          </label>
          {renderFieldErrors('config.tool_search')}

          {toolSearchEnabled && (
            <div className="inline-fields">
              <div className="form-field">
                <label htmlFor="ts-threshold">{t('createToolSearchThreshold')}</label>
                <input
                  id="ts-threshold"
                  type="number"
                  min={1}
                  value={toolSearchThreshold}
                  onChange={e => setToolSearchThreshold(Number(e.target.value))}
                />
              </div>
              <div className="form-field">
                <label htmlFor="ts-max-results">{t('createToolSearchMaxResults')}</label>
                <input
                  id="ts-max-results"
                  type="number"
                  min={1}
                  max={50}
                  value={toolSearchMaxResults}
                  onChange={e => setToolSearchMaxResults(Number(e.target.value))}
                />
              </div>
            </div>
          )}
        </section>

        <div className="form-actions">
          <button type="submit" className="btn-submit" disabled={submitting}>
            {submitting ? t('common:creating') : t('createTitle')}
          </button>
        </div>
      </form>
    </div>
  );
}
