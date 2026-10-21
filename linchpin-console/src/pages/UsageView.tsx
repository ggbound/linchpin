import { useEffect, useState, useCallback } from 'react';
import { apiClient, ApiError } from '../api/client';
import type { UsageBucket, UsageResponse } from '../types';
import LoadingSpinner from '../components/LoadingSpinner';
import ErrorMessage from '../components/ErrorMessage';
import './UsageView.css';

type GroupBy = 'day' | 'project';

type TokenField =
  | 'input_tokens'
  | 'output_tokens'
  | 'cache_creation_input_tokens'
  | 'cache_read_input_tokens';

const SEGMENTS: Array<{ key: TokenField; label: string; className: string }> = [
  { key: 'input_tokens', label: 'Input', className: 'usage-seg-input' },
  { key: 'output_tokens', label: 'Output', className: 'usage-seg-output' },
  {
    key: 'cache_creation_input_tokens',
    label: 'Cache write',
    className: 'usage-seg-cache-write',
  },
  {
    key: 'cache_read_input_tokens',
    label: 'Cache read',
    className: 'usage-seg-cache-read',
  },
];

const RANGES = [
  { label: '7D', days: 7 },
  { label: '30D', days: 30 },
  { label: '90D', days: 90 },
] as const;

function bucketTotal(bucket: UsageBucket): number {
  return SEGMENTS.reduce((sum, seg) => sum + bucket[seg.key], 0);
}

function stripZero(value: number): string {
  return value.toFixed(1).replace(/\.0$/, '');
}

function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${stripZero(n / 1_000_000)}M`;
  if (n >= 1_000) return `${stripZero(n / 1_000)}k`;
  return String(n);
}

function isoDaysAgo(days: number): string {
  const d = new Date();
  d.setUTCDate(d.getUTCDate() - days);
  return d.toISOString().slice(0, 10);
}

/** Round up to a 1/2/5 x 10^n grid so axis labels stay readable. */
function niceCeil(v: number): number {
  if (v <= 0) return 1;
  const exp = Math.floor(Math.log10(v));
  const base = Math.pow(10, exp);
  for (const m of [1, 2, 5, 10]) {
    if (m * base >= v) return m * base;
  }
  return 10 * base;
}

function truncate(text: string, max: number): string {
  return text.length > max ? `${text.slice(0, max - 1)}…` : text;
}

function DayChart({ buckets }: { buckets: UsageBucket[] }) {
  const WIDTH = 840;
  const HEIGHT = 280;
  const PAD = { top: 12, right: 12, bottom: 28, left: 56 };
  const innerW = WIDTH - PAD.left - PAD.right;
  const innerH = HEIGHT - PAD.top - PAD.bottom;
  const yMax = niceCeil(Math.max(...buckets.map(bucketTotal), 0));
  const step = buckets.length > 0 ? innerW / buckets.length : innerW;
  const barW = Math.min(step * 0.6, 40);
  const labelStep = Math.max(1, Math.ceil(buckets.length / 12));

  return (
    <svg
      className="usage-chart"
      viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
      role="img"
      aria-label="Token usage by day"
      data-testid="usage-chart"
    >
      {[0, 0.25, 0.5, 0.75, 1].map(f => {
        const y = PAD.top + innerH - f * innerH;
        return (
          <g key={f}>
            <line
              x1={PAD.left}
              x2={WIDTH - PAD.right}
              y1={y}
              y2={y}
              className="usage-gridline"
            />
            <text x={PAD.left - 8} y={y + 3} className="usage-axis-label" textAnchor="end">
              {formatTokens(f * yMax)}
            </text>
          </g>
        );
      })}
      {buckets.map((b, i) => {
        const x = PAD.left + i * step + (step - barW) / 2;
        let acc = 0;
        return (
          <g key={b.bucket}>
            {SEGMENTS.map(seg => {
              const h = (b[seg.key] / yMax) * innerH;
              const y = PAD.top + innerH - acc - h;
              acc += h;
              return b[seg.key] > 0 ? (
                <rect
                  key={seg.key}
                  x={x}
                  y={y}
                  width={barW}
                  height={Math.max(h, 0)}
                  className={seg.className}
                >
                  <title>{`${b.bucket} ${seg.label}: ${b[seg.key]}`}</title>
                </rect>
              ) : null;
            })}
            {i % labelStep === 0 && (
              <text
                x={x + barW / 2}
                y={HEIGHT - 8}
                className="usage-axis-label"
                textAnchor="middle"
              >
                {b.bucket.slice(5)}
              </text>
            )}
          </g>
        );
      })}
    </svg>
  );
}

function ProjectChart({ buckets }: { buckets: UsageBucket[] }) {
  const WIDTH = 840;
  const LABEL_W = 150;
  const VALUE_W = 70;
  const ROW_H = 24;
  const GAP = 8;
  const trackW = WIDTH - LABEL_W - VALUE_W;
  const max = Math.max(...buckets.map(bucketTotal), 0);
  const height = Math.max(buckets.length * (ROW_H + GAP) - GAP, ROW_H) + 8;

  return (
    <svg
      className="usage-chart"
      viewBox={`0 0 ${WIDTH} ${height}`}
      role="img"
      aria-label="Token usage by project"
      data-testid="usage-chart"
    >
      {buckets.map((b, i) => {
        const y = i * (ROW_H + GAP) + 4;
        const t = bucketTotal(b);
        const w = max > 0 ? (t / max) * trackW : 0;
        let acc = 0;
        return (
          <g key={b.bucket}>
            <text x={0} y={y + ROW_H / 2 + 4} className="usage-axis-label" textAnchor="start">
              {truncate(b.bucket, 20)}
            </text>
            {SEGMENTS.map(seg => {
              const segW = t > 0 ? (b[seg.key] / t) * w : 0;
              const x = LABEL_W + acc;
              acc += segW;
              return segW > 0 ? (
                <rect
                  key={seg.key}
                  x={x}
                  y={y}
                  width={segW}
                  height={ROW_H}
                  className={seg.className}
                >
                  <title>{`${b.bucket} ${seg.label}: ${b[seg.key]}`}</title>
                </rect>
              ) : null;
            })}
            <text
              x={LABEL_W + trackW}
              y={y + ROW_H / 2 + 4}
              className="usage-axis-value"
              textAnchor="end"
            >
              {formatTokens(t)}
            </text>
          </g>
        );
      })}
    </svg>
  );
}

export default function UsageView() {
  const [groupBy, setGroupBy] = useState<GroupBy>('day');
  const [fromDate, setFromDate] = useState(() => isoDaysAgo(30));
  const [toDate, setToDate] = useState(() => isoDaysAgo(0));
  const [usage, setUsage] = useState<UsageResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<ApiError | null>(null);

  const fetchUsage = useCallback(async () => {
    try {
      const params: Record<string, string> = { group_by: groupBy };
      if (fromDate) params.from = `${fromDate}T00:00:00Z`;
      if (toDate) params.to = `${toDate}T23:59:59Z`;
      const res = await apiClient.get<UsageResponse>('/v1/usage', params);
      setUsage(res);
      setError(null);
    } catch (err) {
      setError(err as ApiError);
    }
  }, [groupBy, fromDate, toDate]);

  useEffect(() => {
    setLoading(true);
    fetchUsage().finally(() => setLoading(false));
  }, [fetchUsage]);

  const handleRange = (days: number) => {
    setFromDate(isoDaysAgo(days - 1));
    setToDate(isoDaysAgo(0));
  };

  if (loading && !usage) return <LoadingSpinner />;

  if (error && !usage) {
    return (
      <ErrorMessage
        status={error.status}
        message={error.message}
        onRetry={() => {
          setError(null);
          setLoading(true);
          fetchUsage().finally(() => setLoading(false));
        }}
      />
    );
  }

  const total = usage?.total;
  const buckets = usage?.buckets ?? [];

  return (
    <div className="usage-view" data-testid="usage-view">
      <div className="usage-header">
        <h1>Usage</h1>
        <p>Token consumption across sessions.</p>
      </div>

      <div className="usage-toolbar">
        <div className="usage-toolbar-group">
          {RANGES.map(r => (
            <button
              key={r.days}
              type="button"
              className="usage-range-btn"
              data-testid={`usage-range-${r.days}`}
              onClick={() => handleRange(r.days)}
            >
              {r.label}
            </button>
          ))}
        </div>

        <div className="usage-toolbar-group">
          <input
            type="date"
            className="usage-date-input"
            value={fromDate}
            max={toDate}
            onChange={e => setFromDate(e.target.value)}
            aria-label="From date"
            data-testid="usage-from"
          />
          <span className="usage-date-sep">–</span>
          <input
            type="date"
            className="usage-date-input"
            value={toDate}
            min={fromDate}
            onChange={e => setToDate(e.target.value)}
            aria-label="To date"
            data-testid="usage-to"
          />
        </div>

        <div className="usage-group-switch" role="group" aria-label="Group by">
          <button
            type="button"
            className={`usage-group-btn${groupBy === 'day' ? ' active' : ''}`}
            data-testid="usage-group-day"
            onClick={() => setGroupBy('day')}
          >
            Day
          </button>
          <button
            type="button"
            className={`usage-group-btn${groupBy === 'project' ? ' active' : ''}`}
            data-testid="usage-group-project"
            onClick={() => setGroupBy('project')}
          >
            Project
          </button>
        </div>
      </div>

      {error && (
        <div className="usage-inline-error" role="alert">
          <span>{error.message}</span>
          <button
            type="button"
            onClick={() => {
              setError(null);
              fetchUsage();
            }}
            data-testid="usage-inline-retry"
          >
            Retry
          </button>
        </div>
      )}

      {total && (
        <div className="usage-kpis" data-testid="usage-kpis">
          <div className="usage-kpi-card usage-kpi-sessions">
            <span className="usage-kpi-label">Sessions</span>
            <span className="usage-kpi-value" data-testid="usage-kpi-sessions">
              {total.sessions}
            </span>
          </div>
          <div className="usage-kpi-card usage-kpi-input">
            <span className="usage-kpi-label">Input tokens</span>
            <span className="usage-kpi-value" data-testid="usage-kpi-input">
              {formatTokens(total.input_tokens)}
            </span>
          </div>
          <div className="usage-kpi-card usage-kpi-output">
            <span className="usage-kpi-label">Output tokens</span>
            <span className="usage-kpi-value" data-testid="usage-kpi-output">
              {formatTokens(total.output_tokens)}
            </span>
          </div>
          <div className="usage-kpi-card usage-kpi-cache-write">
            <span className="usage-kpi-label">Cache write</span>
            <span className="usage-kpi-value" data-testid="usage-kpi-cache-write">
              {formatTokens(total.cache_creation_input_tokens)}
            </span>
          </div>
          <div className="usage-kpi-card usage-kpi-cache-read">
            <span className="usage-kpi-label">Cache read</span>
            <span className="usage-kpi-value" data-testid="usage-kpi-cache-read">
              {formatTokens(total.cache_read_input_tokens)}
            </span>
          </div>
        </div>
      )}

      <div className="usage-card">
        <div className="usage-card-head">
          <span className="usage-card-title">
            {groupBy === 'day' ? 'Tokens by day' : 'Tokens by project'}
          </span>
          <div className="usage-legend">
            {SEGMENTS.map(seg => (
              <span key={seg.key} className="usage-legend-item">
                <span className={`usage-legend-swatch ${seg.className}`} />
                {seg.label}
              </span>
            ))}
          </div>
        </div>
        {buckets.length === 0 ? (
          <div className="usage-empty" data-testid="usage-empty">
            No usage recorded for this period.
          </div>
        ) : groupBy === 'day' ? (
          <DayChart buckets={buckets} />
        ) : (
          <ProjectChart buckets={buckets} />
        )}
      </div>

      <div className="usage-card">
        <div className="usage-card-head">
          <span className="usage-card-title">
            {groupBy === 'day' ? 'Daily breakdown' : 'Project breakdown'}
          </span>
        </div>
        <div className="usage-table-wrapper">
          <table className="usage-table" data-testid="usage-table">
            <thead>
              <tr>
                <th>{groupBy === 'day' ? 'Date' : 'Project'}</th>
                <th>Sessions</th>
                <th>Input</th>
                <th>Output</th>
                <th>Cache write</th>
                <th>Cache read</th>
              </tr>
            </thead>
            <tbody>
              {buckets.length === 0 ? (
                <tr>
                  <td colSpan={6} className="usage-empty-cell">
                    No usage recorded for this period.
                  </td>
                </tr>
              ) : (
                buckets.map(b => (
                  <tr key={b.bucket} data-testid="usage-row">
                    <td className="usage-cell-bucket">{b.bucket}</td>
                    <td>{b.sessions}</td>
                    <td>{formatTokens(b.input_tokens)}</td>
                    <td>{formatTokens(b.output_tokens)}</td>
                    <td>{formatTokens(b.cache_creation_input_tokens)}</td>
                    <td>{formatTokens(b.cache_read_input_tokens)}</td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
