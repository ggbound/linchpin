import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link } from 'react-router-dom';
import { apiClient, ApiError } from '../api/client';
import { resourceHref } from '../lib/resourceLinks';
import type { AuditEntry } from '../types';
import LoadingSpinner from '../components/LoadingSpinner';
import ErrorMessage from '../components/ErrorMessage';
import './AuditView.css';

const DEFAULT_LIMIT = 500;
const MAX_LIMIT = 50000;

interface AuditFilters {
  from: string; // datetime-local value
  to: string;
  resource_type: string;
  action: string;
  limit: string;
}

const EMPTY_FILTERS: AuditFilters = {
  from: '',
  to: '',
  resource_type: '',
  action: '',
  limit: String(DEFAULT_LIMIT),
};

function buildParams(f: AuditFilters): Record<string, string> {
  const params: Record<string, string> = {};
  if (f.from) params.from = new Date(f.from).toISOString();
  if (f.to) params.to = new Date(f.to).toISOString();
  if (f.resource_type.trim()) params.resource_type = f.resource_type.trim();
  if (f.action.trim()) params.action = f.action.trim();
  const parsed = parseInt(f.limit, 10);
  const n = Number.isFinite(parsed) ? Math.max(1, Math.min(MAX_LIMIT, parsed)) : DEFAULT_LIMIT;
  params.limit = String(n);
  return params;
}

function detailSummary(detail: Record<string, unknown>): string {
  if (!detail || Object.keys(detail).length === 0) return '—';
  const json = JSON.stringify(detail);
  return json.length > 140 ? `${json.slice(0, 137)}…` : json;
}

function detailFull(detail: Record<string, unknown>): string {
  if (!detail || Object.keys(detail).length === 0) return '';
  return JSON.stringify(detail, null, 2);
}

export default function AuditView() {
  const { t, i18n } = useTranslation('audit');
  const [draft, setDraft] = useState<AuditFilters>(EMPTY_FILTERS);
  const [applied, setApplied] = useState<AuditFilters>(EMPTY_FILTERS);
  const [entries, setEntries] = useState<AuditEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<ApiError | null>(null);
  const [downloadError, setDownloadError] = useState<string | null>(null);

  const fetchAudit = useCallback(async (f: AuditFilters) => {
    setLoading(true);
    setError(null);
    try {
      const blob = await apiClient.download('/v1/audit', buildParams(f));
      const text = await blob.text();
      const rows = text
        .split('\n')
        .filter(Boolean)
        .map(line => JSON.parse(line) as AuditEntry);
      setEntries(rows);
    } catch (err) {
      setError(err as ApiError);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchAudit(applied);
  }, [applied, fetchAudit]);

  const handleApply = () => {
    setApplied({ ...draft });
  };

  const handleReset = () => {
    setDraft(EMPTY_FILTERS);
    setApplied(EMPTY_FILTERS);
  };

  const handleDownload = async () => {
    setDownloadError(null);
    try {
      const blob = await apiClient.download('/v1/audit', buildParams(applied));
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `audit-${new Date().toISOString().replace(/[:.]/g, '-')}.ndjson`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (err) {
      setDownloadError((err as ApiError).message || t('downloadFailed'));
    }
  };

  const updateDraft = (patch: Partial<AuditFilters>) => {
    setDraft(prev => ({ ...prev, ...patch }));
  };

  return (
    <div className="audit-view" data-testid="audit-view">
      <div className="audit-header">
        <h1>{t('title')}</h1>
        <p>
          {t('subtitle')}
        </p>
      </div>

      <div className="audit-filters" data-testid="audit-filters">
        <div className="audit-filter-field">
          <label htmlFor="audit-from">{t('from')}</label>
          <input
            id="audit-from"
            type="datetime-local"
            value={draft.from}
            onChange={e => updateDraft({ from: e.target.value })}
            data-testid="audit-from"
          />
        </div>
        <div className="audit-filter-field">
          <label htmlFor="audit-to">{t('to')}</label>
          <input
            id="audit-to"
            type="datetime-local"
            value={draft.to}
            onChange={e => updateDraft({ to: e.target.value })}
            data-testid="audit-to"
          />
        </div>
        <div className="audit-filter-field">
          <label htmlFor="audit-resource-type">{t('resourceType')}</label>
          <input
            id="audit-resource-type"
            value={draft.resource_type}
            onChange={e => updateDraft({ resource_type: e.target.value })}
            placeholder={t('resourceTypePlaceholder')}
            data-testid="audit-resource-type"
          />
        </div>
        <div className="audit-filter-field">
          <label htmlFor="audit-action">{t('action')}</label>
          <input
            id="audit-action"
            value={draft.action}
            onChange={e => updateDraft({ action: e.target.value })}
            placeholder={t('actionPlaceholder')}
            data-testid="audit-action"
          />
        </div>
        <div className="audit-filter-field audit-filter-limit">
          <label htmlFor="audit-limit">{t('limit')}</label>
          <input
            id="audit-limit"
            type="number"
            min={1}
            max={MAX_LIMIT}
            value={draft.limit}
            onChange={e => updateDraft({ limit: e.target.value })}
            data-testid="audit-limit"
          />
        </div>
        <div className="audit-filter-actions">
          <button className="btn-primary" onClick={handleApply} data-testid="audit-apply">
            {t('apply')}
          </button>
          <button onClick={handleReset} data-testid="audit-reset">
            {t('reset')}
          </button>
          <button onClick={handleDownload} data-testid="audit-download">
            {t('downloadNdjson')}
          </button>
        </div>
      </div>

      {downloadError && (
        <div className="form-error" role="alert" data-testid="audit-download-error">
          {downloadError}
        </div>
      )}

      {loading ? (
        <LoadingSpinner />
      ) : error ? (
        <ErrorMessage
          status={error.status}
          message={error.message}
          onRetry={() => fetchAudit(applied)}
        />
      ) : entries.length === 0 ? (
        <p className="empty-text" data-testid="audit-empty">
          {t('empty')}
        </p>
      ) : (
        <div className="audit-table-wrapper">
          <div className="audit-meta-line" data-testid="audit-count">
            {t('entryCount', { count: entries.length })}
          </div>
          <table className="audit-table" data-testid="audit-table">
            <thead>
              <tr>
                <th>{t('colTime')}</th>
                <th>{t('colAction')}</th>
                <th>{t('colResourceType')}</th>
                <th>{t('colResourceId')}</th>
                <th>{t('colActor')}</th>
                <th>{t('colDetail')}</th>
              </tr>
            </thead>
            <tbody>
              {entries.map(entry => {
                const href = resourceHref(entry.resource_type, entry.resource_id);
                return (
                  <tr key={entry.id} data-testid={`audit-row-${entry.id}`}>
                    <td
                      className="audit-time-cell"
                      title={entry.at}
                    >
                      {new Date(entry.at).toLocaleString(i18n.language)}
                    </td>
                    <td>
                      <code>{entry.action}</code>
                    </td>
                    <td>{entry.resource_type}</td>
                    <td className="audit-resource-id-cell" title={entry.resource_id}>
                      {href ? (
                        <Link to={href} data-testid="audit-resource-link">
                          {entry.resource_id}
                        </Link>
                      ) : (
                        entry.resource_id
                      )}
                    </td>
                    <td>{entry.actor || '—'}</td>
                    <td
                      className="audit-detail-cell"
                      title={detailFull(entry.detail)}
                    >
                      {detailSummary(entry.detail)}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
