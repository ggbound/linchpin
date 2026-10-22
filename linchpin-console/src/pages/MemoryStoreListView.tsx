import { useCallback, useEffect, useState } from 'react';
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
      setNotice(`Memory store "${created.name}" created.`);
      setStores(prev => [created, ...prev]);
      navigate(`/memory-stores/${created.id}`);
    } catch (err) {
      const apiErr = err as ApiError;
      setActionError(
        apiErr.status === 409
          ? `A memory store named "${name.trim()}" already exists.`
          : apiErr.message || 'Failed to create memory store',
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
          <h1>Memory Stores</h1>
          <p>
            Persistent, path-addressed memory that sessions mount and read/write.
            Entries are versioned and retained for 30 days.
          </p>
        </div>
        <div className="memory-store-header-actions">
          <label className="memory-store-toggle">
            <input
              type="checkbox"
              checked={includeArchived}
              onChange={e => setIncludeArchived(e.target.checked)}
              data-testid="include-archived-toggle"
            />
            Show archived
          </label>
          <button
            className="btn-primary"
            onClick={() => {
              setShowCreate(true);
              setActionError(null);
            }}
            data-testid="new-store-btn"
          >
            New memory store
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
            No memory stores yet. Create one to give agents persistent memory.
          </p>
        </div>
      ) : (
        <div className="memory-store-table-wrapper">
          <table className="memory-store-table" data-testid="store-table">
            <thead>
              <tr>
                <th>Name</th>
                <th>Description</th>
                <th>Created</th>
                <th>Updated</th>
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
                      <span className="archived-badge">Archived</span>
                    )}
                  </td>
                  <td className="memory-store-desc-cell">
                    {store.description || '—'}
                  </td>
                  <td>{new Date(store.created_at).toLocaleString()}</td>
                  <td>{new Date(store.updated_at).toLocaleString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {showCreate && (
        <div className="memory-store-dialog-overlay">
          <div className="memory-store-dialog" data-testid="store-create-dialog">
            <h2>New memory store</h2>
            <div className="form-field">
              <label htmlFor="store-create-name">Name</label>
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
              <label htmlFor="store-create-desc">Description (optional)</label>
              <textarea
                id="store-create-desc"
                value={description}
                onChange={e => setDescription(e.target.value)}
                rows={3}
                placeholder="What this store is for…"
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
                Cancel
              </button>
              <button
                className="btn-primary"
                disabled={creating || !name.trim()}
                onClick={handleCreate}
                data-testid="store-create-submit"
              >
                {creating ? 'Creating…' : 'Create store'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
