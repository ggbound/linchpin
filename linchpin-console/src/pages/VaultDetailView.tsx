import { useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { apiClient, ApiError } from '../api/client';
import type {
  Vault,
  CredentialResponse,
  CredentialType,
  ProviderName,
  PaginatedResponse,
  UpdateCredentialRequest,
} from '../types';
import LoadingSpinner from '../components/LoadingSpinner';
import ErrorMessage from '../components/ErrorMessage';
import ConfirmDialog from '../components/ConfirmDialog';
import './VaultDetailView.css';

/** Secret fields editable per credential type (aligned with backend secrets schema). */
const SECRET_FIELDS: Record<CredentialType, { key: keyof UpdateCredentialRequest; label: string; required?: boolean }[]> = {
  bearer_token: [{ key: 'token', label: 'detailToken', required: true }],
  api_key: [{ key: 'api_key', label: 'detailApiKey', required: true }],
  oauth: [
    { key: 'access_token', label: 'detailAccessToken', required: true },
    { key: 'refresh_token', label: 'detailRefreshToken' },
    { key: 'client_secret', label: 'detailClientSecret' },
  ],
  byok_kek: [{ key: 'kek', label: 'detailKek', required: true }],
};

/** i18n keys for credential type display names. */
const CRED_TYPE_LABELS: Record<CredentialType, string> = {
  bearer_token: 'detailCredTypeBearer',
  api_key: 'detailCredTypeApiKey',
  oauth: 'detailCredTypeOauth',
  byok_kek: 'detailCredTypeByokKek',
};

export default function VaultDetailView() {
  const { id } = useParams<{ id: string }>();
  const { t, i18n } = useTranslation('vaults');
  const [vault, setVault] = useState<Vault | null>(null);
  const [credentials, setCredentials] = useState<CredentialResponse[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<ApiError | null>(null);

  // Add credential form
  const [showAddForm, setShowAddForm] = useState(false);
  const [credType, setCredType] = useState<CredentialType>('bearer_token');
  const [mcpUrl, setMcpUrl] = useState('');
  const [provider, setProvider] = useState<ProviderName>('openrouter');
  const [tokenVal, setTokenVal] = useState('');
  const [apiKeyVal, setApiKeyVal] = useState('');
  const [accessTokenVal, setAccessTokenVal] = useState('');
  const [refreshTokenVal, setRefreshTokenVal] = useState('');
  const [clientIdVal, setClientIdVal] = useState('');
  const [clientSecretVal, setClientSecretVal] = useState('');
  const [kekVal, setKekVal] = useState('');
  const [addingCred, setAddingCred] = useState(false);
  const [addCredError, setAddCredError] = useState<string | null>(null);

  // Edit secrets (PATCH)
  const [editCredId, setEditCredId] = useState<string | null>(null);
  const [editSecrets, setEditSecrets] = useState<Record<string, string>>({});
  const [savingSecrets, setSavingSecrets] = useState(false);
  const [editSecretsError, setEditSecretsError] = useState<string | null>(null);

  // Confirm dialogs
  const [deleteCredTarget, setDeleteCredTarget] = useState<CredentialResponse | null>(null);
  const [archiveCredTarget, setArchiveCredTarget] = useState<CredentialResponse | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const fetchData = async () => {
    try {
      setError(null);
      const [vaultData, credData] = await Promise.all([
        apiClient.get<Vault>(`/v1/vaults/${id}`),
        apiClient.get<PaginatedResponse<CredentialResponse>>(`/v1/vaults/${id}/credentials`),
      ]);
      setVault(vaultData);
      setCredentials(credData.data);
    } catch (err) {
      setError(err as ApiError);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    setLoading(true);
    fetchData();
  }, [id]);

  const resetAddForm = () => {
    setCredType('bearer_token');
    setMcpUrl('');
    setProvider('openrouter');
    setTokenVal('');
    setApiKeyVal('');
    setAccessTokenVal('');
    setRefreshTokenVal('');
    setClientIdVal('');
    setClientSecretVal('');
    setKekVal('');
    setAddCredError(null);
  };

  const handleAddCredential = async (e: React.FormEvent) => {
    e.preventDefault();
    setAddingCred(true);
    setAddCredError(null);

    try {
      let body: Record<string, unknown>;
      if (credType === 'bearer_token') {
        body = { credential_type: 'bearer_token', mcp_server_url: mcpUrl, token: tokenVal };
      } else if (credType === 'api_key') {
        body = { credential_type: 'api_key', provider, api_key: apiKeyVal };
      } else if (credType === 'byok_kek') {
        body = { credential_type: 'byok_kek', kek: kekVal };
      } else {
        body = {
          credential_type: 'oauth',
          mcp_server_url: mcpUrl,
          access_token: accessTokenVal,
          ...(refreshTokenVal && { refresh_token: refreshTokenVal }),
          ...(clientIdVal && { client_id: clientIdVal }),
          ...(clientSecretVal && { client_secret: clientSecretVal }),
        };
      }

      const created = await apiClient.post<CredentialResponse>(
        `/v1/vaults/${id}/credentials`,
        body,
      );
      setCredentials(prev => [...prev, created]);
      resetAddForm();
      setShowAddForm(false);
    } catch (err) {
      const apiErr = err as ApiError;
      setAddCredError(apiErr.message || t('detailAddCredFailed'));
    } finally {
      setAddingCred(false);
    }
  };

  const startEditSecrets = (cred: CredentialResponse) => {
    setEditCredId(cred.id);
    setEditSecrets({});
    setEditSecretsError(null);
  };

  const handleSaveSecrets = async (cred: CredentialResponse) => {
    setSavingSecrets(true);
    setEditSecretsError(null);

    try {
      const body: UpdateCredentialRequest = {};
      for (const field of SECRET_FIELDS[cred.credential_type]) {
        const val = (editSecrets[field.key] || '').trim();
        if (val) {
          (body as Record<string, unknown>)[field.key] = val;
        }
      }
      if (Object.keys(body).length === 0) {
        setEditSecretsError(t('detailProvideSecret'));
        return;
      }

      const updated = await apiClient.patch<CredentialResponse>(
        `/v1/vaults/${id}/credentials/${cred.id}`,
        body,
      );
      setCredentials(prev => prev.map(c => (c.id === cred.id ? updated : c)));
      setEditCredId(null);
      setEditSecrets({});
    } catch (err) {
      const apiErr = err as ApiError;
      setEditSecretsError(apiErr.message || t('detailUpdateSecretsFailed'));
    } finally {
      setSavingSecrets(false);
    }
  };

  const handleDeleteCred = async () => {
    if (!deleteCredTarget) return;
    setActionError(null);
    try {
      await apiClient.delete(`/v1/vaults/${id}/credentials/${deleteCredTarget.id}`);
      setCredentials(prev => prev.filter(c => c.id !== deleteCredTarget.id));
    } catch (err) {
      const apiErr = err as ApiError;
      setActionError(apiErr.message || t('detailDeleteCredFailed'));
    } finally {
      setDeleteCredTarget(null);
    }
  };

  const handleArchiveCred = async () => {
    if (!archiveCredTarget) return;
    setActionError(null);
    try {
      await apiClient.post(`/v1/vaults/${id}/credentials/${archiveCredTarget.id}/archive`, {});
      setCredentials(prev =>
        prev.map(c =>
          c.id === archiveCredTarget.id ? { ...c, archived_at: new Date().toISOString() } : c,
        ),
      );
    } catch (err) {
      const apiErr = err as ApiError;
      setActionError(apiErr.message || t('detailArchiveCredFailed'));
    } finally {
      setArchiveCredTarget(null);
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
          fetchData();
        }}
      />
    );
  }

  if (!vault) return null;

  const isArchived = !!vault.archived_at;

  return (
    <div className="vault-detail-view" data-testid="vault-detail-view">
      <Link to="/vaults" className="back-link" data-testid="back-link">
        ← {t('detailBackToVaults')}
      </Link>

      <div className="vault-detail-header">
        <div className="vault-detail-title-row">
          <h1 data-testid="vault-name">{vault.display_name}</h1>
          {isArchived && (
            <span className="archived-badge" data-testid="archived-badge">{t('status:archived')}</span>
          )}
        </div>
        <div className="vault-detail-meta">
          <span data-testid="vault-id"><code>{vault.id}</code></span>
          <span className="meta-separator">·</span>
          <span data-testid="vault-created">
            {t('detailCreatedAt', { time: new Date(vault.created_at).toLocaleString(i18n.language) })}
          </span>
          {vault.archived_at && (
            <>
              <span className="meta-separator">·</span>
              <span data-testid="vault-archived-at">
                {t('detailArchivedAt', { time: new Date(vault.archived_at).toLocaleString(i18n.language) })}
              </span>
            </>
          )}
        </div>
      </div>

      {actionError && (
        <div className="form-error" role="alert" data-testid="action-error">{actionError}</div>
      )}

      <section className="detail-section" data-testid="credentials-section">
        <h2>
          {t('detailCredentialsCount', { total: credentials.length })}
          {!isArchived && (
            <button
              className="btn-primary"
              style={{ marginLeft: 'var(--spacing-md)', fontSize: '0.75rem', padding: '2px 10px' }}
              onClick={() => { setShowAddForm(s => !s); resetAddForm(); }}
              data-testid="add-credential-btn"
            >
              {showAddForm ? t('common:cancel') : t('detailAddCredential')}
            </button>
          )}
        </h2>

        {showAddForm && (
          <form className="add-credential-form" onSubmit={handleAddCredential} data-testid="add-credential-form">
            <div className="form-row">
              <div className="form-field">
                <label htmlFor="cred-type">{t('common:type')}</label>
                <select
                  id="cred-type"
                  value={credType}
                  onChange={e => setCredType(e.target.value as CredentialType)}
                  data-testid="cred-type-select"
                >
                  <option value="bearer_token">{t(CRED_TYPE_LABELS.bearer_token)}</option>
                  <option value="api_key">{t(CRED_TYPE_LABELS.api_key)}</option>
                  <option value="oauth">{t(CRED_TYPE_LABELS.oauth)}</option>
                  <option value="byok_kek">{t(CRED_TYPE_LABELS.byok_kek)}</option>
                </select>
                {credType === 'byok_kek' && (
                  <p className="section-hint" data-testid="kek-hint">
                    {t('detailKekHint')}
                  </p>
                )}
              </div>
            </div>

            {credType === 'bearer_token' && (
              <div className="form-row">
                <div className="form-field">
                  <label htmlFor="cred-mcp-url">{t('detailMcpUrlLabel')}</label>
                  <input
                    id="cred-mcp-url"
                    type="text"
                    value={mcpUrl}
                    onChange={e => setMcpUrl(e.target.value)}
                    placeholder="https://mcp.example.com"
                    required
                    data-testid="cred-mcp-url-input"
                  />
                </div>
                <div className="form-field">
                  <label htmlFor="cred-token">{t('detailToken')}</label>
                  <input
                    id="cred-token"
                    type="password"
                    value={tokenVal}
                    onChange={e => setTokenVal(e.target.value)}
                    placeholder={t('detailTokenPlaceholder')}
                    required
                    data-testid="cred-token-input"
                  />
                </div>
              </div>
            )}

            {credType === 'api_key' && (
              <div className="form-row">
                <div className="form-field">
                  <label htmlFor="cred-provider">{t('detailProvider')}</label>
                  <select
                    id="cred-provider"
                    value={provider}
                    onChange={e => setProvider(e.target.value as ProviderName)}
                    data-testid="cred-provider-select"
                  >
                    <option value="openrouter">OpenRouter</option>
                    <option value="ollama">Ollama</option>
                  </select>
                </div>
                <div className="form-field">
                  <label htmlFor="cred-api-key">{t('detailApiKey')}</label>
                  <input
                    id="cred-api-key"
                    type="password"
                    value={apiKeyVal}
                    onChange={e => setApiKeyVal(e.target.value)}
                    placeholder={t('detailApiKeyPlaceholder')}
                    required
                    data-testid="cred-api-key-input"
                  />
                </div>
              </div>
            )}

            {credType === 'byok_kek' && (
              <div className="form-row">
                <div className="form-field">
                  <label htmlFor="cred-kek">{t('detailKek')}</label>
                  <input
                    id="cred-kek"
                    type="password"
                    value={kekVal}
                    onChange={e => setKekVal(e.target.value)}
                    placeholder={t('detailKekPlaceholder')}
                    required
                    data-testid="cred-kek-input"
                  />
                </div>
              </div>
            )}

            {credType === 'oauth' && (
              <>
                <div className="form-row">
                  <div className="form-field">
                    <label htmlFor="cred-oauth-url">{t('detailMcpUrlLabel')}</label>
                    <input
                      id="cred-oauth-url"
                      type="text"
                      value={mcpUrl}
                      onChange={e => setMcpUrl(e.target.value)}
                      placeholder="https://mcp.example.com"
                      required
                      data-testid="cred-oauth-url-input"
                    />
                  </div>
                  <div className="form-field">
                    <label htmlFor="cred-access-token">{t('detailAccessToken')}</label>
                    <input
                      id="cred-access-token"
                      type="password"
                      value={accessTokenVal}
                      onChange={e => setAccessTokenVal(e.target.value)}
                      placeholder={t('detailAccessToken')}
                      required
                      data-testid="cred-access-token-input"
                    />
                  </div>
                </div>
                <div className="form-row">
                  <div className="form-field">
                    <label htmlFor="cred-refresh-token">{t('detailRefreshToken')}{t('detailOptionalSuffix')}</label>
                    <input
                      id="cred-refresh-token"
                      type="password"
                      value={refreshTokenVal}
                      onChange={e => setRefreshTokenVal(e.target.value)}
                      placeholder={t('detailRefreshToken')}
                      data-testid="cred-refresh-token-input"
                    />
                  </div>
                  <div className="form-field">
                    <label htmlFor="cred-client-id">{t('detailClientId')}{t('detailOptionalSuffix')}</label>
                    <input
                      id="cred-client-id"
                      type="text"
                      value={clientIdVal}
                      onChange={e => setClientIdVal(e.target.value)}
                      placeholder={t('detailClientId')}
                      data-testid="cred-client-id-input"
                    />
                  </div>
                  <div className="form-field">
                    <label htmlFor="cred-client-secret">{t('detailClientSecret')}{t('detailOptionalSuffix')}</label>
                    <input
                      id="cred-client-secret"
                      type="password"
                      value={clientSecretVal}
                      onChange={e => setClientSecretVal(e.target.value)}
                      placeholder={t('detailClientSecretPlaceholder')}
                      data-testid="cred-client-secret-input"
                    />
                  </div>
                </div>
              </>
            )}

            {addCredError && <div className="form-error" role="alert">{addCredError}</div>}

            <div className="add-credential-form-actions">
              <button
                type="submit"
                className="btn-primary"
                disabled={addingCred}
                data-testid="add-credential-submit"
              >
                {addingCred ? t('detailAdding') : t('detailAddCredential')}
              </button>
            </div>
          </form>
        )}

        {credentials.length === 0 ? (
          <p className="empty-text">{t('detailEmptyCredentials')}</p>
        ) : (
          <div className="credential-list">
            {credentials.map(cred => (
              <div key={cred.id} className="credential-card-wrapper" data-testid={`credential-card-${cred.id}`}>
                <div className="credential-card">
                  <div className="credential-card-info">
                    <div className="credential-card-type">{t(CRED_TYPE_LABELS[cred.credential_type])}</div>
                    <div className="credential-card-target">
                      {cred.mcp_server_url || cred.provider || (cred.credential_type === 'byok_kek' ? t('detailCustomerManagedKek') : '—')}
                    </div>
                    <div className="credential-card-meta">
                      {t('detailCreatedAt', { time: new Date(cred.created_at).toLocaleString(i18n.language) })}
                      {cred.updated_at !== cred.created_at &&
                        ` · ${t('detailUpdatedAt', { time: new Date(cred.updated_at).toLocaleString(i18n.language) })}`}
                      {cred.archived_at && ` · ${t('status:archived')}`}
                    </div>
                  </div>
                  <div className="credential-card-secret" data-testid="credential-secret-mask">
                    ••••••••
                  </div>
                  <div className="credential-card-actions">
                    {!cred.archived_at && !isArchived && (
                      <button
                        className="btn-sm-secondary"
                        onClick={() =>
                          editCredId === cred.id
                            ? setEditCredId(null)
                            : startEditSecrets(cred)
                        }
                        data-testid="cred-edit-btn"
                      >
                        {editCredId === cred.id ? t('common:cancel') : t('detailEditSecrets')}
                      </button>
                    )}
                    {!cred.archived_at && (
                      <button
                        className="btn-sm-secondary"
                        onClick={() => setArchiveCredTarget(cred)}
                        data-testid="cred-archive-btn"
                      >
                        {t('detailArchive')}
                      </button>
                    )}
                    <button
                      className="btn-sm-danger"
                      onClick={() => setDeleteCredTarget(cred)}
                      data-testid="cred-delete-btn"
                    >
                      {t('common:delete')}
                    </button>
                  </div>
                </div>

                {editCredId === cred.id && (
                  <div className="edit-secrets-panel" data-testid="edit-secrets-panel">
                    {SECRET_FIELDS[cred.credential_type].map(field => (
                      <div className="edit-secrets-field" key={field.key}>
                        <label htmlFor={`edit-${cred.id}-${field.key}`}>
                          {t(field.label)}
                          {field.required ? '' : t('detailOptionalSuffix')}
                        </label>
                        <input
                          id={`edit-${cred.id}-${field.key}`}
                          type="password"
                          value={editSecrets[field.key] || ''}
                          onChange={e =>
                            setEditSecrets(prev => ({ ...prev, [field.key]: e.target.value }))
                          }
                          placeholder={field.required ? t('detailNewValuePlaceholder') : t('detailLeaveEmptyPlaceholder')}
                          data-testid={`edit-secret-${field.key}`}
                        />
                      </div>
                    ))}
                    <p className="edit-secrets-hint">
                      {t('detailReplaceHint')}
                    </p>
                    {editSecretsError && (
                      <div className="form-error" role="alert" data-testid="edit-secrets-error">
                        {editSecretsError}
                      </div>
                    )}
                    <div className="edit-secrets-actions">
                      <button
                        className="btn-primary"
                        disabled={savingSecrets}
                        onClick={() => handleSaveSecrets(cred)}
                        data-testid="edit-secrets-save"
                      >
                        {savingSecrets ? t('common:saving') : t('detailSaveSecrets')}
                      </button>
                      <button
                        className="btn-sm-secondary"
                        onClick={() => setEditCredId(null)}
                        data-testid="edit-secrets-cancel"
                      >
                        {t('common:cancel')}
                      </button>
                    </div>
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </section>

      <ConfirmDialog
        open={!!deleteCredTarget}
        title={t('detailDeleteCredTitle')}
        message={t('detailDeleteCredMessage')}
        onConfirm={handleDeleteCred}
        onCancel={() => setDeleteCredTarget(null)}
      />

      <ConfirmDialog
        open={!!archiveCredTarget}
        title={t('detailArchiveCredTitle')}
        message={t('detailArchiveCredMessage')}
        onConfirm={handleArchiveCred}
        onCancel={() => setArchiveCredTarget(null)}
      />
    </div>
  );
}
