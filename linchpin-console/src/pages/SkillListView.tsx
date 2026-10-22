import { useCallback, useEffect, useRef, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { apiClient, ApiError } from '../api/client';
import type { Skill, SkillListResponse, SkillPreflightResponse } from '../types';
import LoadingSpinner from '../components/LoadingSpinner';
import ErrorMessage from '../components/ErrorMessage';
import ConfirmDialog from '../components/ConfirmDialog';
import './SkillListView.css';

const SKILL_BUNDLE_MAX_BYTES = 10 * 1024 * 1024; // 10 MB — matches server limit

function humanBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
}

export default function SkillListView() {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const { t, i18n } = useTranslation('skills');

  const [skills, setSkills] = useState<Skill[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<ApiError | null>(null);
  const [query, setQuery] = useState(searchParams.get('search') ?? '');

  const [uploading, setUploading] = useState(false);
  const [uploadNotice, setUploadNotice] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  // A preflighted upload that would replace an existing same-name skill
  // — held (with its File) until the developer confirms or cancels.
  const [replaceCandidate, setReplaceCandidate] = useState<{
    file: File;
    pre: SkillPreflightResponse;
  } | null>(null);

  const [deleteTarget, setDeleteTarget] = useState<Skill | null>(null);
  const [deleting, setDeleting] = useState(false);

  const uploadErrorMessage = (err: unknown): string => {
    const apiErr = err as ApiError;
    if (apiErr.status === 413) {
      return t('listBundleTooLarge');
    }
    if (apiErr.status === 422) {
      return t('listBundleInvalid');
    }
    return apiErr.message || t('listUploadFailed');
  };

  const fetchSkills = useCallback(async () => {
    setLoading(true);
    try {
      const res = await apiClient.get<SkillListResponse>('/v1/skills?limit=100');
      setSkills(res.data);
      setError(null);
    } catch (err) {
      setError(err as ApiError);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchSkills();
  }, [fetchSkills]);

  const performUpload = async (file: File) => {
    setUploading(true);
    try {
      const created = await apiClient.upload<Skill>('/v1/skills', file, 'bundle');
      const isUpdate = created.updated_at !== created.created_at;
      setUploadNotice(
        isUpdate
          ? t('listReplaceNotice', { name: created.name })
          : t('listUploadNotice', { name: created.name }),
      );
      setSkills(prev => {
        const idx = prev.findIndex(s => s.id === created.id);
        if (idx >= 0) {
          const next = [...prev];
          next[idx] = created;
          return next;
        }
        return [created, ...prev];
      });
    } catch (err) {
      setActionError(uploadErrorMessage(err));
    } finally {
      setUploading(false);
    }
  };

  const handleFileSelected = async (file: File) => {
    setUploadNotice(null);
    setActionError(null);
    // Reset immediately so re-selecting the same file re-fires change.
    if (fileInputRef.current) fileInputRef.current.value = '';

    if (file.size > SKILL_BUNDLE_MAX_BYTES) {
      setActionError(t('listBundleTooLargeWithSize', { size: humanBytes(file.size) }));
      return;
    }

    // Preflight: parse server-side and check for a same-name skill so
    // the developer is warned *before* the upload replaces it.
    setUploading(true);
    let pre: SkillPreflightResponse;
    try {
      pre = await apiClient.upload<SkillPreflightResponse>(
        '/v1/skills/preflight',
        file,
        'bundle',
      );
    } catch (err) {
      setActionError(uploadErrorMessage(err));
      setUploading(false);
      return;
    }
    setUploading(false);

    if (pre.replaces) {
      setReplaceCandidate({ file, pre });
      return;
    }
    await performUpload(file);
  };

  const handleReplaceConfirm = async () => {
    if (!replaceCandidate) return;
    const { file } = replaceCandidate;
    setReplaceCandidate(null);
    await performUpload(file);
  };

  const handleDelete = async () => {
    if (!deleteTarget) return;
    setDeleting(true);
    setActionError(null);
    try {
      await apiClient.delete<Skill>(`/v1/skills/${deleteTarget.id}`);
      setSkills(prev => prev.filter(s => s.id !== deleteTarget.id));
      setDeleteTarget(null);
    } catch (err) {
      const apiErr = err as ApiError;
      setActionError(apiErr.message || t('listDeleteFailed'));
      setDeleteTarget(null);
    } finally {
      setDeleting(false);
    }
  };

  // Client-side filter driven by the search box (initialized from ?search=).
  const q = query.trim().toLowerCase();
  const visible = q
    ? skills.filter(
        (s) =>
          s.name.toLowerCase().includes(q) ||
          (s.description ?? '').toLowerCase().includes(q),
      )
    : skills;

  if (loading) return <LoadingSpinner />;

  if (error) {
    return (
      <ErrorMessage
        status={error.status}
        message={error.message}
        onRetry={fetchSkills}
      />
    );
  }

  return (
    <div className="skill-list-view" data-testid="skill-list-view">
      <div className="skill-list-header">
        <div>
          <h1>{t('listTitle')}</h1>
          <p>{t('listSubtitle')}</p>
        </div>
        <div className="skill-upload-area">
          <input
            ref={fileInputRef}
            type="file"
            accept=".zip,application/zip,application/x-zip-compressed"
            className="skill-file-input"
            onChange={e => {
              const file = e.target.files?.[0];
              if (file) handleFileSelected(file);
            }}
            data-testid="skill-file-input"
          />
          <button
            className="btn-primary"
            disabled={uploading}
            onClick={() => fileInputRef.current?.click()}
            data-testid="upload-skill-btn"
          >
            {uploading ? t('listUploading') : t('listUploadBundle')}
          </button>
        </div>
      </div>

      {uploadNotice && (
        <div className="skill-notice" role="status" data-testid="skill-upload-notice">
          {uploadNotice}
        </div>
      )}

      {actionError && (
        <div className="form-error" role="alert" data-testid="skill-action-error">
          {actionError}
        </div>
      )}

      {skills.length > 0 && (
        <input
          type="text"
          className="skill-search-input"
          placeholder={t('listSearchPlaceholder')}
          value={query}
          onChange={e => setQuery(e.target.value)}
          data-testid="skill-search-input"
          aria-label={t('listSearchAriaLabel')}
        />
      )}

      {skills.length === 0 ? (
        <div className="skill-empty" data-testid="skill-empty">
          <p className="empty-text">{t('listEmpty')}</p>
        </div>
      ) : visible.length === 0 ? (
        <p className="empty-text" data-testid="skill-filter-empty">
          {t('listNoFilterResults')}
        </p>
      ) : (
        <div className="skill-table-wrapper">
          <table className="skill-table" data-testid="skill-table">
            <thead>
              <tr>
                <th>{t('common:name')}</th>
                <th>{t('listColumnVersion')}</th>
                <th>{t('common:description')}</th>
                <th>{t('listColumnSha256')}</th>
                <th>{t('listColumnSize')}</th>
                <th>{t('common:created')}</th>
                <th>{t('common:updated')}</th>
                <th>{t('common:actions')}</th>
              </tr>
            </thead>
            <tbody>
              {visible.map(skill => (
                <tr
                  key={skill.id}
                  className="skill-row"
                  onClick={() => navigate(`/skills/${skill.id}`)}
                  onKeyDown={e => {
                    if (e.key === 'Enter') navigate(`/skills/${skill.id}`);
                  }}
                  role="link"
                  tabIndex={0}
                  data-testid="skill-row"
                >
                  <td className="skill-name-cell">
                    {skill.name}
                    {skill.archived_at && (
                      <span className="archived-badge">{t('listArchivedBadge')}</span>
                    )}
                  </td>
                  <td className="skill-version-cell">{skill.version || '—'}</td>
                  <td className="skill-desc-cell">{skill.description}</td>
                  <td>
                    <code title={skill.bundle_sha256}>
                      {skill.bundle_sha256.slice(0, 12)}…
                    </code>
                  </td>
                  <td>{humanBytes(skill.bundle_size)}</td>
                  <td>{new Date(skill.created_at).toLocaleString(i18n.language)}</td>
                  <td>{new Date(skill.updated_at).toLocaleString(i18n.language)}</td>
                  <td>
                    <button
                      className="btn-sm-danger"
                      disabled={deleting}
                      onClick={e => {
                        e.stopPropagation();
                        setDeleteTarget(skill);
                      }}
                      data-testid="skill-delete-btn"
                    >
                      {t('common:delete')}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <ConfirmDialog
        open={deleteTarget !== null}
        title={t('listDeleteConfirmTitle')}
        message={
          deleteTarget
            ? t('listDeleteConfirmMessage', { name: deleteTarget.name })
            : ''
        }
        onConfirm={handleDelete}
        onCancel={() => setDeleteTarget(null)}
      />

      <ConfirmDialog
        open={replaceCandidate !== null}
        title={t('listReplaceConfirmTitle')}
        message={
          replaceCandidate
            ? replaceCandidate.pre.replaces?.version
              ? t('listReplaceConfirmMessageWithVersion', {
                  name: replaceCandidate.pre.name,
                  version: replaceCandidate.pre.replaces.version,
                })
              : t('listReplaceConfirmMessage', { name: replaceCandidate.pre.name })
            : ''
        }
        onConfirm={handleReplaceConfirm}
        onCancel={() => setReplaceCandidate(null)}
      />
    </div>
  );
}
