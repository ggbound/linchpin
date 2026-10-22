import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';
import { apiClient, ApiError } from '../api/client';
import type { MemoryStore } from '../types';
import LoadingSpinner from '../components/LoadingSpinner';
import ErrorMessage from '../components/ErrorMessage';
import './MemoryStoreListView.css';

interface StoreListResponse {
  data: MemoryStore[];
  next_cursor: string | null;
}

export default function MemoryStoreListView() {
  const navigate = useNavigate();
  const { t, i18n } = useTranslation('memoryStores');

  const [stores, setStores] = useState<MemoryStore[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<ApiError | null>(null);

  const [includeArchived, setIncludeArchived] = useState(false);

  const [showCreate, setShowCreate] = useState(false);
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [creating, setCreating] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const fetchStores = useCallback(async () => {
    setLoading(true);
    try {
      const res = await apiClient.get<StoreListResponse>('/v1/memory_stores', {
        include_archived: includeArchived ? 'true' : 'false',
        limit: '100',
      });
      setStores(res.data);
      setError(null);
    } catch (err) {
      setError(err as ApiError);
    } finally {
      setLoading(false);
    }
  }, [includeArchived]);

  useEffect(() => {
    fetchStores();
  }, [fetchStores]);

  const handleCreate = async () => {
    if (!name.trim()) return;
    setCreating(true);
    setActionError(null);
    try {
      const created = await apiClient.post<MemoryStore>('/v1/memory_stores', {
        name: name.trim(),
        description: description.trim() || null,
      });
      setShowCreate(false);
      setName('');
      setDescription('');
      setNotice(t('listCreatedNotice', { name: created.name }));
      setStores(prev => [created, ...prev]);
      navigate(`/memory-stores/${created.id}`);
    } catch (err) {
      const apiErr = err as ApiError;
      setActionError(
        apiErr.status === 409
          ? t('listNameConflict', { name: name.trim() })
          : apiErr.message || t('listCreateFailed'),
      );
    } finally {
      setCreating(false);
    }
  };

  if (loading) return <LoadingSpinner />;

  if (error) {
    return (
      <ErrorMessage
        status={error.status}
        message={error.message}
        onRetry={fetchStores}
      />
    );
  }

  return (
    <div className="memory-store-list-view" data-testid="memory-store-list-view">
      <div className="memory-store-list-header">
        <div>
          <h1>{t('listTitle')}</h1>
          <p>{t('listSubtitle')}</p>
        </div>
        <div className="memory-store-header-actions">
          <label className="memory-store-toggle">
            <input
              type="checkbox"
              checked={includeArchived}
              onChange={e => setIncludeArchived(e.target.checked)}
              data-testid="include-archived-toggle"
            />
            {t('listShowArchived')}
          </label>
          <button
            className="btn-primary"
            onClick={() => {
              setShowCreate(true);
              setActionError(null);
            }}
            data-testid="new-store-btn"
          >
            {t('listNewStore')}
          </button>
        </div>
      </div>

      {notice && (
        <div className="memory-store-notice" role="status" data-testid="store-notice">
          {notice}
        </div>
      )}

      {actionError && !showCreate && (
        <div className="form-error" role="alert" data-testid="store-action-error">
          {actionError}
        </div>
      )}

      {stores.length === 0 ? (
        <div className="memory-store-empty" data-testid="store-empty">
          <p className="empty-text">
            {t('listEmpty')}
          </p>
        </div>
      ) : (
        <div className="memory-store-table-wrapper">
          <table className="memory-store-table" data-testid="store-table">
            <thead>
              <tr>
                <th>{t('common:name')}</th>
                <th>{t('common:description')}</th>
                <th>{t('common:created')}</th>
                <th>{t('common:updated')}</th>
              </tr>
            </thead>
            <tbody>
              {stores.map(store => (
                <tr
                  key={store.id}
                  className="memory-store-row"
                  onClick={() => navigate(`/memory-stores/${store.id}`)}
                  data-testid={`memory-store-row-${store.id}`}
                  role="link"
                >
                  <td className="memory-store-name-cell">
                    {store.name}
                    {store.archived_at && (
                      <span className="archived-badge">{t('listArchivedBadge')}</span>
                    )}
                  </td>
                  <td className="memory-store-desc-cell">
                    {store.description || '—'}
                  </td>
                  <td>{new Date(store.created_at).toLocaleString(i18n.language)}</td>
                  <td>{new Date(store.updated_at).toLocaleString(i18n.language)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {showCreate && (
        <div className="memory-store-dialog-overlay">
          <div className="memory-store-dialog" data-testid="store-create-dialog">
            <h2>{t('listNewStore')}</h2>
            <div className="form-field">
              <label htmlFor="store-create-name">{t('common:name')}</label>
              <input
                id="store-create-name"
                value={name}
                onChange={e => setName(e.target.value)}
                placeholder="project-knowledge"
                autoFocus
                data-testid="store-create-name"
              />
            </div>
            <div className="form-field">
              <label htmlFor="store-create-desc">{t('listDescriptionOptional')}</label>
              <textarea
                id="store-create-desc"
                value={description}
                onChange={e => setDescription(e.target.value)}
                rows={3}
                placeholder={t('listDescPlaceholder')}
                data-testid="store-create-desc"
              />
            </div>
            {actionError && (
              <div className="form-error" role="alert" data-testid="store-create-error">
                {actionError}
              </div>
            )}
            <div className="memory-store-dialog-actions">
              <button
                onClick={() => {
                  setShowCreate(false);
                  setActionError(null);
                }}
                data-testid="store-create-cancel"
              >
                {t('common:cancel')}
              </button>
              <button
                className="btn-primary"
                disabled={creating || !name.trim()}
                onClick={handleCreate}
                data-testid="store-create-submit"
              >
                {creating ? t('common:creating') : t('listCreateSubmit')}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
