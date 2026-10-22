import { useCallback, useEffect, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { apiClient, ApiError } from '../api/client';
import type {
  Memory,
  MemoryStore,
  MemoryVersion,
  WriteMemoryRequest,
} from '../types';
import LoadingSpinner from '../components/LoadingSpinner';
import ErrorMessage from '../components/ErrorMessage';
import ConfirmDialog from '../components/ConfirmDialog';
import './MemoryStoreDetailView.css';

interface ListResponse<T> {
  data: T[];
  next_cursor: string | null;
}

function humanBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
}

export default function MemoryStoreDetailView() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const { t, i18n } = useTranslation('memoryStores');

  const [store, setStore] = useState<MemoryStore | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<ApiError | null>(null);

  const [actionError, setActionError] = useState<string | null>(null);
  const [archiving, setArchiving] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [showArchiveConfirm, setShowArchiveConfirm] = useState(false);
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);

  // Description editing
  const [editingDesc, setEditingDesc] = useState(false);
  const [descDraft, setDescDraft] = useState('');
  const [savingDesc, setSavingDesc] = useState(false);

  // Memories
  const [memories, setMemories] = useState<Memory[]>([]);
  const [memoriesLoading, setMemoriesLoading] = useState(true);
  const [createMemoryOpen, setCreateMemoryOpen] = useState(false);
  const [newPath, setNewPath] = useState('');
  const [newContent, setNewContent] = useState('');
  const [creatingMemory, setCreatingMemory] = useState(false);
  const [memoryError, setMemoryError] = useState<string | null>(null);

  // Expanded memory content (head version)
  const [expandedPath, setExpandedPath] = useState<string | null>(null);
  const [memoryContent, setMemoryContent] = useState<string | null>(null);
  const [contentLoading, setContentLoading] = useState(false);
  const [contentNote, setContentNote] = useState<string | null>(null);
  const [editingMemory, setEditingMemory] = useState(false);
  const [editContent, setEditContent] = useState('');
  const [savingMemory, setSavingMemory] = useState(false);

  // Memory deletion
  const [deleteMemoryTarget, setDeleteMemoryTarget] = useState<Memory | null>(null);

  // Versions
  const [versions, setVersions] = useState<MemoryVersion[]>([]);
  const [versionsLoading, setVersionsLoading] = useState(true);
  const [redactTarget, setRedactTarget] = useState<MemoryVersion | null>(null);
  const [redactReason, setRedactReason] = useState('');
  const [redacting, setRedacting] = useState(false);

  const fetchStore = useCallback(async () => {
    try {
      setError(null);
      const data = await apiClient.get<MemoryStore>(`/v1/memory_stores/${id}`);
      setStore(data);
    } catch (err) {
      setError(err as ApiError);
    } finally {
      setLoading(false);
    }
  }, [id]);

  const fetchMemories = useCallback(async () => {
    setMemoriesLoading(true);
    try {
      const res = await apiClient.get<ListResponse<Memory>>(
        `/v1/memory_stores/${id}/memories`,
        { limit: '100' },
      );
      setMemories(res.data);
      setMemoryError(null);
    } catch (err) {
      setMemoryError((err as ApiError).message || t('detailFailedToLoadMemories'));
    } finally {
      setMemoriesLoading(false);
    }
  }, [id]);

  const fetchVersions = useCallback(async () => {
    setVersionsLoading(true);
    try {
      const res = await apiClient.get<ListResponse<MemoryVersion>>(
        `/v1/memory_stores/${id}/memory_versions`,
        { limit: '20' },
      );
      setVersions(res.data);
    } catch {
      // Version list is supplementary — don't block the page on failure.
      setVersions([]);
    } finally {
      setVersionsLoading(false);
    }
  }, [id]);

  useEffect(() => {
    setLoading(true);
    fetchStore();
    fetchMemories();
    fetchVersions();
  }, [fetchStore, fetchMemories, fetchVersions]);

  const handleArchive = async () => {
    if (!store) return;
    setShowArchiveConfirm(false);
    setArchiving(true);
    setActionError(null);
    try {
      const updated = await apiClient.post<MemoryStore>(
        `/v1/memory_stores/${store.id}/archive`,
        {},
      );
      setStore(updated);
    } catch (err) {
      setActionError((err as ApiError).message || t('detailFailedToArchive'));
    } finally {
      setArchiving(false);
    }
  };

  const handleDelete = async () => {
    if (!store) return;
    setShowDeleteConfirm(false);
    setDeleting(true);
    setActionError(null);
    try {
      await apiClient.delete(`/v1/memory_stores/${store.id}`);
      navigate('/memory-stores');
    } catch (err) {
      const apiErr = err as ApiError;
      setActionError(
        apiErr.status === 409
          ? t('detailDeleteConflict')
          : apiErr.message || t('detailFailedToDelete'),
      );
      setDeleting(false);
    }
  };

  const handleSaveDesc = async () => {
    if (!store) return;
    setSavingDesc(true);
    setActionError(null);
    try {
      const updated = await apiClient.patch<MemoryStore>(
        `/v1/memory_stores/${store.id}`,
        { description: descDraft },
      );
      setStore(updated);
      setEditingDesc(false);
    } catch (err) {
      setActionError((err as ApiError).message || t('detailFailedToUpdateDesc'));
    } finally {
      setSavingDesc(false);
    }
  };

  const openMemory = async (mem: Memory) => {
    if (expandedPath === mem.path) {
      setExpandedPath(null);
      setMemoryContent(null);
      setContentNote(null);
      setEditingMemory(false);
      return;
    }
    setExpandedPath(mem.path);
    setMemoryContent(null);
    setContentNote(null);
    setEditingMemory(false);
    setContentLoading(true);
    try {
      const res = await apiClient.get<ListResponse<MemoryVersion>>(
        `/v1/memory_stores/${id}/memory_versions`,
        { memory_id: mem.id, limit: '1' },
      );
      const head = res.data[0];
      if (!head) {
        setContentNote(t('detailNoVersionsForMemory'));
      } else if (head.redacted_at) {
        setContentNote(t('detailContentRedacted'));
      } else if (head.action === 'delete' || head.size_bytes === 0) {
        setContentNote(t('detailEmptyContent'));
      } else {
        const blob = await apiClient.download(
          `/v1/memory_stores/${id}/memory_versions/${head.id}/content`,
        );
        setMemoryContent(await blob.text());
      }
    } catch (err) {
      const apiErr = err as ApiError;
      setContentNote(
        apiErr.status === 404
          ? t('detailContentRedacted')
          : (apiErr.message || t('detailFailedToLoadContent')),
      );
    } finally {
      setContentLoading(false);
    }
  };

  const handleDownloadVersion = async (ver: MemoryVersion) => {
    const mem = memories.find(m => m.id === ver.memory_id);
    const filename = mem
      ? `${mem.path.replace(/^\//, '').replace(/\//g, '_')}.v${ver.seq}`
      : `memory-version-${ver.seq}.txt`;
    setActionError(null);
    try {
      const blob = await apiClient.download(
        `/v1/memory_stores/${id}/memory_versions/${ver.id}/content`,
      );
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (err) {
      setActionError((err as ApiError).message || t('detailFailedToDownloadVersion'));
    }
  };

  const handleCreateMemory = async () => {
    if (!store || !newPath.trim()) return;
    setCreatingMemory(true);
    setMemoryError(null);
    try {
      const body: WriteMemoryRequest = {
        path: newPath.trim(),
        content: newContent,
      };
      await apiClient.post<Memory>(
        `/v1/memory_stores/${store.id}/memories`,
        body,
      );
      setCreateMemoryOpen(false);
      setNewPath('');
      setNewContent('');
      await fetchMemories();
      await fetchVersions();
    } catch (err) {
      const apiErr = err as ApiError;
      setMemoryError(
        apiErr.status === 412
          ? t('detailPreconditionFailed')
          : apiErr.message || t('detailFailedToWrite'),
      );
    } finally {
      setCreatingMemory(false);
    }
  };

  const handleSaveMemory = async () => {
    if (!store || !expandedPath) return;
    setSavingMemory(true);
    setMemoryError(null);
    try {
      await apiClient.post<Memory>(`/v1/memory_stores/${store.id}/memories`, {
        path: expandedPath,
        content: editContent,
      });
      setEditingMemory(false);
      await fetchMemories();
      await fetchVersions();
      // Refresh expanded content to the new head.
      const mem = memories.find(m => m.path === expandedPath);
      if (mem) await openMemory({ ...mem, path: expandedPath });
    } catch (err) {
      setMemoryError((err as ApiError).message || t('detailFailedToUpdateMemory'));
    } finally {
      setSavingMemory(false);
    }
  };

  const handleDeleteMemory = async () => {
    if (!store || !deleteMemoryTarget) return;
    setMemoryError(null);
    try {
      await apiClient.delete(`/v1/memory_stores/${store.id}/memories`, {
        path: deleteMemoryTarget.path,
      });
      if (expandedPath === deleteMemoryTarget.path) {
        setExpandedPath(null);
        setMemoryContent(null);
      }
      setDeleteMemoryTarget(null);
      await fetchMemories();
      await fetchVersions();
    } catch (err) {
      setMemoryError((err as ApiError).message || t('detailFailedToDeleteMemory'));
      setDeleteMemoryTarget(null);
    }
  };

  const handleRedact = async () => {
    if (!store || !redactTarget) return;
    setRedacting(true);
    setMemoryError(null);
    try {
      await apiClient.post<MemoryVersion>(
        `/v1/memory_stores/${store.id}/memory_versions/${redactTarget.id}/redact`,
        { reason: redactReason.trim() || 'redacted via console' },
      );
      setRedactTarget(null);
      setRedactReason('');
      await fetchVersions();
      if (expandedPath) {
        const mem = memories.find(m => m.path === expandedPath);
        if (mem) await openMemory(mem);
      }
    } catch (err) {
      setMemoryError((err as ApiError).message || t('detailFailedToRedact'));
      setRedactTarget(null);
    } finally {
      setRedacting(false);
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
          fetchStore();
          fetchMemories();
          fetchVersions();
        }}
      />
    );
  }

  if (!store) return null;

  const isArchived = store.archived_at !== null;

  return (
    <div className="memory-store-detail-view" data-testid="memory-store-detail-view">
      <Link to="/memory-stores" className="back-link" data-testid="back-link">
        {t('detailBackToStores')}
      </Link>

      <div className="memory-store-detail-header">
        <h1 data-testid="store-name">
          {store.name}
          {isArchived && (
            <span className="archived-badge" data-testid="archived-badge">
              {t('detailArchivedBadge')}
            </span>
          )}
        </h1>
        <div className="memory-store-detail-meta">
          <span data-testid="store-id">
            <code>{store.id}</code>
          </span>
          <span className="meta-separator">·</span>
          <span data-testid="store-created">
            {t('detailCreatedAt', { time: new Date(store.created_at).toLocaleString(i18n.language) })}
          </span>
          <span className="meta-separator">·</span>
          <span data-testid="store-updated">
            {t('detailUpdatedAt', { time: new Date(store.updated_at).toLocaleString(i18n.language) })}
          </span>
          {isArchived && (
            <>
              <span className="meta-separator">·</span>
              <span data-testid="store-archived">
                {t('detailArchivedAt', { time: new Date(store.archived_at as string).toLocaleString(i18n.language) })}
              </span>
            </>
          )}
        </div>
        <div className="memory-store-desc-row">
          {editingDesc ? (
            <div className="memory-store-desc-editor">
              <textarea
                value={descDraft}
                onChange={e => setDescDraft(e.target.value)}
                rows={2}
                data-testid="store-desc-input"
              />
              <div className="memory-store-desc-editor-actions">
                <button
                  onClick={() => setEditingDesc(false)}
                  data-testid="store-desc-cancel"
                >
                  {t('common:cancel')}
                </button>
                <button
                  className="btn-primary"
                  disabled={savingDesc}
                  onClick={handleSaveDesc}
                  data-testid="store-desc-save"
                >
                  {savingDesc ? t('common:saving') : t('common:save')}
                </button>
              </div>
            </div>
          ) : (
            <div className="memory-store-desc-display">
              <span data-testid="store-description">
                {store.description || t('detailNoDescription')}
              </span>
              {!isArchived && (
                <button
                  className="memory-store-desc-edit-btn"
                  onClick={() => {
                    setDescDraft(store.description || '');
                    setEditingDesc(true);
                  }}
                  data-testid="store-desc-edit"
                >
                  {t('common:edit')}
                </button>
              )}
            </div>
          )}
        </div>
      </div>

      {actionError && (
        <div className="form-error" role="alert" data-testid="store-action-error">
          {actionError}
        </div>
      )}

      <section className="detail-section memory-lifecycle-actions" data-testid="lifecycle-actions">
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

      <section className="detail-section" data-testid="memories-section">
        <div className="memory-section-header">
          <h2>{t('detailMemories')}</h2>
          {!isArchived && (
            <button
              className="btn-primary"
              onClick={() => {
                setCreateMemoryOpen(true);
                setMemoryError(null);
              }}
              data-testid="new-memory-btn"
            >
              {t('detailNewMemory')}
            </button>
          )}
        </div>

        {memoryError && (
          <div className="form-error" role="alert" data-testid="memory-error">
            {memoryError}
          </div>
        )}

        {memoriesLoading ? (
          <LoadingSpinner />
        ) : memories.length === 0 ? (
          <p className="empty-text" data-testid="memories-empty">
            {t('detailMemoriesEmpty')}
          </p>
        ) : (
          <table className="memory-entries-table" data-testid="memory-table">
            <thead>
              <tr>
                <th>{t('detailColumnPath')}</th>
                <th>{t('detailColumnSize')}</th>
                <th>{t('common:updated')}</th>
                <th>{t('common:actions')}</th>
              </tr>
            </thead>
            <tbody>
              {memories.map(mem => (
                <MemoryRow
                  key={mem.id}
                  mem={mem}
                  expanded={expandedPath === mem.path}
                  content={expandedPath === mem.path ? memoryContent : null}
                  contentNote={expandedPath === mem.path ? contentNote : null}
                  contentLoading={expandedPath === mem.path ? contentLoading : false}
                  editing={expandedPath === mem.path && editingMemory}
                  editContent={editContent}
                  archived={isArchived}
                  onToggle={() => openMemory(mem)}
                  onEditStart={() => {
                    setEditContent(memoryContent ?? '');
                    setEditingMemory(true);
                  }}
                  onEditCancel={() => setEditingMemory(false)}
                  onEditChange={setEditContent}
                  onEditSave={handleSaveMemory}
                  onDelete={() => setDeleteMemoryTarget(mem)}
                  saving={savingMemory}
                />
              ))}
            </tbody>
          </table>
        )}
      </section>

      <section className="detail-section" data-testid="versions-section">
        <h2>{t('detailRecentVersions')}</h2>
        {versionsLoading ? (
          <LoadingSpinner />
        ) : versions.length === 0 ? (
          <p className="empty-text" data-testid="versions-empty">
            {t('detailNoVersions')}
          </p>
        ) : (
          <table className="memory-versions-table" data-testid="version-table">
            <thead>
              <tr>
                <th>{t('detailColumnSeq')}</th>
                <th>{t('detailColumnAction')}</th>
                <th>{t('detailColumnPath')}</th>
                <th>{t('detailColumnAuthor')}</th>
                <th>{t('detailColumnSize')}</th>
                <th>{t('common:created')}</th>
                <th>{t('detailColumnStatus')}</th>
                <th>{t('common:actions')}</th>
              </tr>
            </thead>
            <tbody>
              {versions.map(ver => {
                const mem = memories.find(m => m.id === ver.memory_id);
                return (
                  <tr key={ver.id} data-testid={`version-row-${ver.id}`}>
                    <td>{ver.seq}</td>
                    <td>
                      <code>{ver.action}</code>
                    </td>
                    <td className="memory-version-path-cell">
                      {mem ? mem.path : <code title={ver.memory_id}>{ver.memory_id.slice(0, 8)}…</code>}
                    </td>
                    <td>{ver.author}</td>
                    <td>{humanBytes(ver.size_bytes)}</td>
                    <td>{new Date(ver.created_at).toLocaleString(i18n.language)}</td>
                    <td>
                      {ver.redacted_at ? (
                        <span className="redacted-badge">{t('detailRedactedBadge')}</span>
                      ) : (
                        <span className="memory-version-status-ok">{t('detailRetainedBadge')}</span>
                      )}
                    </td>
                    <td>
                      <div className="memory-version-actions">
                        {!ver.redacted_at && (
                          <button
                            className="btn-sm-secondary"
                            onClick={() => handleDownloadVersion(ver)}
                            data-testid={`version-download-btn-${ver.id}`}
                          >
                            {t('common:download')}
                          </button>
                        )}
                        {!ver.redacted_at && (
                          <button
                            className="btn-sm-danger"
                            onClick={() => {
                              setRedactTarget(ver);
                              setRedactReason('');
                              setMemoryError(null);
                            }}
                            data-testid={`redact-btn-${ver.id}`}
                          >
                            {t('detailRedact')}
                          </button>
                        )}
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </section>

      {/* Create memory dialog */}
      {createMemoryOpen && (
        <div className="memory-detail-dialog-overlay">
          <div className="memory-detail-dialog" data-testid="memory-create-dialog">
            <h2>{t('detailNewMemory')}</h2>
            <div className="form-field">
              <label htmlFor="memory-create-path">{t('detailFieldPath')}</label>
              <input
                id="memory-create-path"
                value={newPath}
                onChange={e => setNewPath(e.target.value)}
                placeholder="/preferences/formatting.md"
                autoFocus
                data-testid="memory-create-path"
              />
            </div>
            <div className="form-field">
              <label htmlFor="memory-create-content">{t('detailFieldContent')}</label>
              <textarea
                id="memory-create-content"
                value={newContent}
                onChange={e => setNewContent(e.target.value)}
                rows={8}
                data-testid="memory-create-content"
              />
            </div>
            <div className="memory-detail-dialog-actions">
              <button
                onClick={() => setCreateMemoryOpen(false)}
                data-testid="memory-create-cancel"
              >
                {t('common:cancel')}
              </button>
              <button
                className="btn-primary"
                disabled={creatingMemory || !newPath.trim().startsWith('/')}
                onClick={handleCreateMemory}
                data-testid="memory-create-submit"
              >
                {creatingMemory ? t('detailWriting') : t('detailWriteMemory')}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Redact dialog (needs a reason input, so custom) */}
      {redactTarget && (
        <div className="memory-detail-dialog-overlay">
          <div className="memory-detail-dialog" data-testid="redact-dialog">
            <h2>{t('detailRedactVersionTitle', { seq: redactTarget.seq })}</h2>
            <p className="memory-dialog-note">
              {t('detailRedactDescription')}
            </p>
            <div className="form-field">
              <label htmlFor="redact-reason">{t('detailRedactReason')}</label>
              <input
                id="redact-reason"
                value={redactReason}
                onChange={e => setRedactReason(e.target.value)}
                placeholder={t('detailRedactReasonPlaceholder')}
                autoFocus
                data-testid="redact-reason"
              />
            </div>
            <div className="memory-detail-dialog-actions">
              <button
                onClick={() => setRedactTarget(null)}
                data-testid="redact-cancel"
              >
                {t('common:cancel')}
              </button>
              <button
                className="btn-sm-danger"
                disabled={redacting}
                onClick={handleRedact}
                data-testid="redact-confirm"
              >
                {redacting ? t('detailRedacting') : t('detailRedactVersion')}
              </button>
            </div>
          </div>
        </div>
      )}

      <ConfirmDialog
        open={showArchiveConfirm}
        title={t('detailArchiveConfirmTitle')}
        message={t('detailArchiveConfirmMessage', { name: store.name })}
        onConfirm={handleArchive}
        onCancel={() => setShowArchiveConfirm(false)}
      />
      <ConfirmDialog
        open={showDeleteConfirm}
        title={t('detailDeleteConfirmTitle')}
        message={t('detailDeleteConfirmMessage', { name: store.name })}
        onConfirm={handleDelete}
        onCancel={() => setShowDeleteConfirm(false)}
      />
      <ConfirmDialog
        open={deleteMemoryTarget !== null}
        title={t('detailDeleteMemoryTitle')}
        message={
          deleteMemoryTarget
            ? t('detailDeleteMemoryMessage', { path: deleteMemoryTarget.path })
            : ''
        }
        onConfirm={handleDeleteMemory}
        onCancel={() => setDeleteMemoryTarget(null)}
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Memory row with expandable content panel
// ---------------------------------------------------------------------------

interface MemoryRowProps {
  mem: Memory;
  expanded: boolean;
  content: string | null;
  contentNote: string | null;
  contentLoading: boolean;
  editing: boolean;
  editContent: string;
  archived: boolean;
  saving: boolean;
  onToggle: () => void;
  onEditStart: () => void;
  onEditCancel: () => void;
  onEditChange: (v: string) => void;
  onEditSave: () => void;
  onDelete: () => void;
}

function MemoryRow({
  mem,
  expanded,
  content,
  contentNote,
  contentLoading,
  editing,
  editContent,
  archived,
  saving,
  onToggle,
  onEditStart,
  onEditCancel,
  onEditChange,
  onEditSave,
  onDelete,
}: MemoryRowProps) {
  const { t, i18n } = useTranslation('memoryStores');
  return (
    <>
      <tr
        className="memory-entry-row"
        onClick={onToggle}
        data-testid={`memory-row-${mem.path}`}
        role="link"
      >
        <td className="memory-entry-path-cell">
          <span className="memory-entry-caret">{expanded ? '▾' : '▸'}</span>
          <code>{mem.path}</code>
        </td>
        <td>{humanBytes(mem.size_bytes)}</td>
        <td>{new Date(mem.updated_at).toLocaleString(i18n.language)}</td>
        <td>
          {!archived && (
            <button
              className="btn-sm-danger"
              disabled={saving}
              onClick={e => {
                e.stopPropagation();
                onDelete();
              }}
              data-testid={`memory-delete-btn-${mem.path}`}
            >
              {t('common:delete')}
            </button>
          )}
        </td>
      </tr>
      {expanded && (
        <tr className="memory-content-row" data-testid={`memory-content-${mem.path}`}>
          <td colSpan={4}>
            {contentLoading ? (
              <LoadingSpinner />
            ) : editing ? (
              <div className="memory-content-editor">
                <textarea
                  value={editContent}
                  onChange={e => onEditChange(e.target.value)}
                  rows={10}
                  data-testid={`memory-edit-content`}
                />
                <div className="memory-content-editor-actions">
                  <button onClick={onEditCancel} data-testid="memory-edit-cancel">
                    {t('common:cancel')}
                  </button>
                  <button
                    className="btn-primary"
                    disabled={saving}
                    onClick={onEditSave}
                    data-testid="memory-edit-save"
                  >
                    {saving ? t('common:saving') : t('common:save')}
                  </button>
                </div>
              </div>
            ) : contentNote ? (
              <p className="memory-content-note">{contentNote}</p>
            ) : (
              <div className="memory-content-panel">
                <pre data-testid="memory-content">{content}</pre>
                <div className="memory-content-editor-actions">
                  <button onClick={onEditStart} data-testid="memory-edit-btn">
                    {t('common:edit')}
                  </button>
                </div>
              </div>
            )}
          </td>
        </tr>
      )}
    </>
  );
}
