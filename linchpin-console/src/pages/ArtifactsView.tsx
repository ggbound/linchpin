import { useEffect, useState, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { Link } from 'react-router-dom';
import { apiClient, ApiError } from '../api/client';
import type { FileRecord, PaginatedResponse } from '../types';
import LoadingSpinner from '../components/LoadingSpinner';
import ErrorMessage from '../components/ErrorMessage';
import './ArtifactsView.css';

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(1)} GB`;
}

export default function ArtifactsView() {
  const { t, i18n } = useTranslation('artifacts');
  const [artifacts, setArtifacts] = useState<FileRecord[]>([]);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<ApiError | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [downloadingId, setDownloadingId] = useState<string | null>(null);

  const fetchPage = useCallback(async () => {
    const res = await apiClient.get<PaginatedResponse<FileRecord>>('/v1/artifacts?limit=100');
    setArtifacts(res.data);
    setHasMore(!!res.has_more);
  }, []);

  useEffect(() => {
    setLoading(true);
    fetchPage()
      .then(() => setError(null))
      .catch(err => setError(err as ApiError))
      .finally(() => setLoading(false));
  }, [fetchPage]);

  const handleDownload = async (artifact: FileRecord) => {
    setActionError(null);
    setDownloadingId(artifact.id);
    try {
      const blob = await apiClient.download(`/v1/artifacts/${artifact.id}/content`);
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = artifact.filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (err) {
      setActionError((err as ApiError).message || t('downloadFailed'));
    } finally {
      setDownloadingId(null);
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
    <div className="artifacts-view" data-testid="artifacts-view">
      <div className="artifacts-header">
        <div>
          <h1>{t('title')}</h1>
          <p>
            {t('subtitle')}
          </p>
        </div>
      </div>

      {actionError && (
        <div className="artifacts-action-error" role="alert" data-testid="artifacts-action-error">
          {actionError}
        </div>
      )}

      {hasMore && (
        <div className="artifacts-more-note" data-testid="artifacts-more-note">
          {t('moreNote')}
        </div>
      )}

      <div className="artifacts-table-wrapper">
        <table className="artifacts-table" data-testid="artifacts-table">
          <thead>
            <tr>
              <th>{t('colFilename')}</th>
              <th>{t('colContentType')}</th>
              <th>{t('colSize')}</th>
              <th>{t('colSession')}</th>
              <th>{t('common:created')}</th>
              <th>{t('common:actions')}</th>
            </tr>
          </thead>
          <tbody>
            {artifacts.length === 0 ? (
              <tr>
                <td colSpan={6} className="empty-row" data-testid="artifacts-empty">
                  {t('empty')}
                </td>
              </tr>
            ) : (
              artifacts.map(artifact => (
                <tr key={artifact.id} className="artifacts-row" data-testid={`artifacts-row-${artifact.id}`}>
                  <td className="artifacts-cell-filename" data-testid="artifacts-cell-filename">
                    {artifact.filename}
                  </td>
                  <td><code>{artifact.content_type}</code></td>
                  <td data-testid="artifacts-cell-size">{formatBytes(artifact.size_bytes)}</td>
                  <td data-testid="artifacts-cell-session">
                    {artifact.scope_id ? (
                      <Link to={`/sessions/${artifact.scope_id}`} className="artifacts-session-link" data-testid="artifacts-session-link">
                        {artifact.scope_id.slice(0, 8)}
                      </Link>
                    ) : (
                      <span className="artifacts-session-none">—</span>
                    )}
                  </td>
                  <td>{new Date(artifact.created_at).toLocaleString(i18n.language)}</td>
                  <td>
                    {artifact.downloadable && (
                      <button
                        className="btn-sm-secondary"
                        disabled={downloadingId === artifact.id}
                        onClick={() => handleDownload(artifact)}
                        data-testid="artifacts-download-btn"
                      >
                        {downloadingId === artifact.id ? t('downloading') : t('common:download')}
                      </button>
                    )}
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
