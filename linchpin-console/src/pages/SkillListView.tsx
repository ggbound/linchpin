import { useCallback, useEffect, useRef, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
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

function uploadErrorMessage(err: unknown): string {
  const apiErr = err as ApiError;
  if (apiErr.status === 413) {
    return 'Bundle exceeds the 10 MB size limit.';
  }
  if (apiErr.status === 422) {
    return (
      'Invalid skill bundle — the archive must contain a SKILL.md with valid frontmatter (name, description).'
    );
  }
  return apiErr.message || 'Failed to upload skill bundle';
}

export default function SkillListView() {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();

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
          ? `Skill "${created.name}" updated — existing bundle with the same name was replaced.`
          : `Skill "${created.name}" uploaded.`,
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
      setActionError(
        `Bundle is ${humanBytes(file.size)} — exceeds the 10 MB limit.`,
      );
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
      setActionError(apiErr.message || 'Failed to delete skill');
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
          <h1>Skills</h1>
          <p>
            Reusable agent capabilities. Upload a bundle (zip archive containing a
            SKILL.md) to make it available to agents.
          </p>
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
            {uploading ? 'Uploading…' : 'Upload skill bundle'}
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
          placeholder="Filter by name or description"
          value={query}
          onChange={e => setQuery(e.target.value)}
          data-testid="skill-search-input"
          aria-label="Filter skills"
        />
      )}

      {skills.length === 0 ? (
        <div className="skill-empty" data-testid="skill-empty">
          <p className="empty-text">
            No skills installed yet. Upload a bundle to get started.
          </p>
        </div>
      ) : visible.length === 0 ? (
        <p className="empty-text" data-testid="skill-filter-empty">
          No skills match your filter.
        </p>
      ) : (
        <div className="skill-table-wrapper">
          <table className="skill-table" data-testid="skill-table">
            <thead>
              <tr>
                <th>Name</th>
                <th>Version</th>
                <th>Description</th>
                <th>SHA-256</th>
                <th>Size</th>
                <th>Created</th>
                <th>Updated</th>
                <th>Actions</th>
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
                    {skill.archived_at && <span className="archived-badge">Archived</span>}
                  </td>
                  <td className="skill-version-cell">{skill.version || '—'}</td>
                  <td className="skill-desc-cell">{skill.description}</td>
                  <td>
                    <code title={skill.bundle_sha256}>
                      {skill.bundle_sha256.slice(0, 12)}…
                    </code>
                  </td>
                  <td>{humanBytes(skill.bundle_size)}</td>
                  <td>{new Date(skill.created_at).toLocaleString()}</td>
                  <td>{new Date(skill.updated_at).toLocaleString()}</td>
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
                      Delete
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
        title="Delete Skill"
        message={
          deleteTarget
            ? `Are you sure you want to delete "${deleteTarget.name}"? Agents will no longer be able to use it.`
            : ''
        }
        onConfirm={handleDelete}
        onCancel={() => setDeleteTarget(null)}
      />

      <ConfirmDialog
        open={replaceCandidate !== null}
        title="Replace Skill"
        message={
          replaceCandidate
            ? `A skill named "${replaceCandidate.pre.name}" already exists${
                replaceCandidate.pre.replaces?.version
                  ? ` (version ${replaceCandidate.pre.replaces.version})`
                  : ''
              }. Uploading this bundle will replace it. Continue?`
            : ''
        }
        onConfirm={handleReplaceConfirm}
        onCancel={() => setReplaceCandidate(null)}
      />
    </div>
  );
}
