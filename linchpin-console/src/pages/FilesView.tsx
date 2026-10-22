import { useEffect, useState, useCallback, type ChangeEvent } from 'react';
import { useTranslation } from 'react-i18next';
import { apiClient, ApiError } from '../api/client';
import type { FileRecord, PaginatedResponse } from '../types';
import LoadingSpinner from '../components/LoadingSpinner';
import ErrorMessage from '../components/ErrorMessage';
import ConfirmDialog from '../components/ConfirmDialog';
import './FilesView.css';

const SOURCE_LABELS: Record<string, string> = {
  upload: 'sourceUpload',
  deliverable: 'sourceDeliverable',
  artifact: 'sourceArtifact',
};

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(1)} GB`;
}

export default function FilesView() {
  const { t, i18n } = useTranslation('files');
  const [files, setFiles] = useState<FileRecord[]>([]);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<ApiError | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<FileRecord | null>(null);

  const fetchPage = useCallback(async () => {
    const res = await apiClient.get<PaginatedResponse<FileRecord>>('/v1/files?limit=100');
    setFiles(res.data);
    setHasMore(!!res.has_more);
  }, []);

  useEffect(() => {
    setLoading(true);
    fetchPage()
      .then(() => setError(null))
      .catch(err => setError(err as ApiError))
      .finally(() => setLoading(false));
  }, [fetchPage]);

  const handleUpload = async (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = '';
    if (!file) return;
    setActionError(null);
    setUploading(true);
    try {
      await apiClient.upload<FileRecord>('/v1/files', file, 'file');
      await fetchPage();
    } catch (err) {
      const apiErr = err as ApiError;
      setActionError(
        apiErr.status === 413
          ? t('fileTooLarge')
          : apiErr.message || t('uploadFailed')
      );
    } finally {
      setUploading(false);
    }
  };

  const handleDownload = async (file: FileRecord) => {
    setActionError(null);
    try {
      const blob = await apiClient.download(`/v1/files/${file.id}/content`);
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = file.filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (err) {
      setActionError((err as ApiError).message || t('downloadFailed'));
    }
  };

  const handleDelete = async () => {
    if (!deleteTarget) return;
    setActionError(null);
    setDeletingId(deleteTarget.id);
    try {
      await apiClient.delete(`/v1/files/${deleteTarget.id}`);
      setDeleteTarget(null);
      await fetchPage();
    } catch (err) {
      const apiErr = err as ApiError;
      setActionError(apiErr.message || t('deleteFailed'));
      setDeleteTarget(null);
    } finally {
      setDeletingId(null);
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
          fetchPage()
            .then(() => setError(null))
            .catch(err => setError(err as ApiError))
            .finally(() => setLoading(false));
        }}
      />
    );
  }

  return (
    <div className="files-view" data-testid="files-view">
      <div className="files-header">
        <div>
          <h1>{t('title')}</h1>
          <p>
            {t('subtitle')}
          </p>
        </div>
        <div className="files-upload">
          <input
            type="file"
            id="files-upload-input"
            className="files-upload-input"
            data-testid="files-upload-input"
            onChange={handleUpload}
            disabled={uploading}
          />
          <label htmlFor="files-upload-input" className="btn-primary files-upload-btn" data-testid="files-upload-btn">
            {uploading ? t('uploading') : t('uploadFile')}
          </label>
        </div>
      </div>

      {actionError && (
        <div className="files-action-error" role="alert" data-testid="files-action-error">
          {actionError}
        </div>
      )}

      {hasMore && (
        <div className="files-more-note">
          {t('moreNote')}
        </div>
      )}

      <div className="files-table-wrapper">
        <table className="files-table" data-testid="files-table">
          <thead>
            <tr>
              <th>{t('colFilename')}</th>
              <th>{t('colContentType')}</th>
              <th>{t('colSize')}</th>
              <th>{t('colSource')}</th>
              <th>{t('colDownloadable')}</th>
              <th>{t('common:created')}</th>
              <th>{t('common:actions')}</th>
            </tr>
          </thead>
          <tbody>
            {files.length === 0 ? (
              <tr>
                <td colSpan={7} className="empty-row" data-testid="files-empty">
                  {t('empty')}
                </td>
              </tr>
            ) : (
              files.map(file => (
                <tr key={file.id} className="files-row" data-testid={`files-row-${file.id}`}>
                  <td className="files-cell-filename" data-testid="files-cell-filename">
                    {file.filename}
                  </td>
                  <td><code>{file.content_type}</code></td>
                  <td data-testid="files-cell-size">{formatBytes(file.size_bytes)}</td>
                  <td>
                    <span className={`files-source files-source--${file.source}`}>
                      {SOURCE_LABELS[file.source]
                        ? t(SOURCE_LABELS[file.source])
                        : file.source}
                    </span>
                  </td>
                  <td data-testid="files-cell-downloadable">
                    {file.downloadable ? t('common:yes') : t('common:no')}
                  </td>
                  <td>{new Date(file.created_at).toLocaleString(i18n.language)}</td>
                  <td>
                    <div className="files-actions-cell">
                      {file.downloadable && (
                        <button
                          className="btn-sm-secondary"
                          disabled={deletingId === file.id}
                          onClick={() => handleDownload(file)}
                          data-testid="files-download-btn"
                        >
                          {t('common:download')}
                        </button>
                      )}
                      <button
                        className="btn-sm-danger"
                        disabled={deletingId === file.id || uploading}
                        onClick={() => setDeleteTarget(file)}
                        data-testid="files-delete-btn"
                        >
                          {t('common:delete')}
                        </button>
                    </div>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      <ConfirmDialog
        open={deleteTarget !== null}
        title={t('deleteTitle')}
        message={t('deleteMessage', { filename: deleteTarget?.filename ?? '' })}
        onConfirm={handleDelete}
        onCancel={() => setDeleteTarget(null)}
      />
    </div>
  );
}
