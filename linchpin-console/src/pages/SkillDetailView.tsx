import { useCallback, useEffect, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { apiClient, ApiError } from '../api/client';
import type { Skill } from '../types';
import LoadingSpinner from '../components/LoadingSpinner';
import ErrorMessage from '../components/ErrorMessage';
import ConfirmDialog from '../components/ConfirmDialog';
import './SkillDetailView.css';

function humanBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
}

export default function SkillDetailView() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();

  const [skill, setSkill] = useState<Skill | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<ApiError | null>(null);

  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  const fetchSkill = useCallback(async () => {
    try {
      setError(null);
      const data = await apiClient.get<Skill>(`/v1/skills/${id}`);
      setSkill(data);
    } catch (err) {
      setError(err as ApiError);
    } finally {
      setLoading(false);
    }
  }, [id]);

  useEffect(() => {
    setLoading(true);
    fetchSkill();
  }, [fetchSkill]);

  const handleDelete = async () => {
    if (!skill) return;
    setShowDeleteConfirm(false);
    setDeleting(true);
    setActionError(null);
    try {
      await apiClient.delete<Skill>(`/v1/skills/${skill.id}`);
      navigate('/skills');
    } catch (err) {
      setActionError((err as ApiError).message || 'Failed to delete skill');
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
          fetchSkill();
        }}
      />
    );
  }

  if (!skill) return null;

  const isArchived = skill.archived_at !== null;

  return (
    <div className="skill-detail-view" data-testid="skill-detail-view">
      <Link to="/skills" className="back-link" data-testid="back-link">
        ← Skills
      </Link>

      <div className="skill-detail-header">
        <h1 data-testid="skill-name">
          {skill.name}
          {isArchived && (
            <span className="archived-badge" data-testid="archived-badge">
              Archived
            </span>
          )}
        </h1>
        <div className="skill-detail-meta">
          <span data-testid="skill-id">
            <code>{skill.id}</code>
          </span>
          <span className="meta-separator">·</span>
          <span data-testid="skill-created">
            Created {new Date(skill.created_at).toLocaleString()}
          </span>
          <span className="meta-separator">·</span>
          <span data-testid="skill-updated">
            Updated {new Date(skill.updated_at).toLocaleString()}
          </span>
          {isArchived && (
            <>
              <span className="meta-separator">·</span>
              <span data-testid="skill-archived">
                Archived {new Date(skill.archived_at as string).toLocaleString()}
              </span>
            </>
          )}
        </div>
        <p className="skill-detail-desc" data-testid="skill-description">
          {skill.description || 'No description'}
        </p>
      </div>

      {actionError && (
        <div className="form-error" role="alert" data-testid="skill-action-error">
          {actionError}
        </div>
      )}

      <section className="detail-section skill-lifecycle-actions" data-testid="skill-lifecycle-actions">
        <button
          className="btn-sm-danger"
          disabled={deleting}
          onClick={() => setShowDeleteConfirm(true)}
          data-testid="delete-button"
        >
          {deleting ? 'Deleting…' : 'Delete'}
        </button>
      </section>

      <section className="detail-section" data-testid="skill-bundle-section">
        <h2>Bundle</h2>
        <dl className="detail-grid">
          <dt>Version</dt>
          <dd data-testid="skill-version">{skill.version || '—'}</dd>
          <dt>Size</dt>
          <dd data-testid="skill-size">{humanBytes(skill.bundle_size)}</dd>
          <dt>SHA-256</dt>
          <dd data-testid="skill-sha256">
            <code className="skill-sha256-code">{skill.bundle_sha256}</code>
          </dd>
        </dl>
      </section>

      <ConfirmDialog
        open={showDeleteConfirm}
        title="Delete Skill"
        message={`Are you sure you want to delete "${skill.name}"? Agents will no longer be able to use it.`}
        onConfirm={handleDelete}
        onCancel={() => setShowDeleteConfirm(false)}
      />
    </div>
  );
}
