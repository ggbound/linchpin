import { useCallback, useEffect, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { apiClient } from '../api/client';
import { resourceHref } from '../lib/resourceLinks';
import type {
  Agent,
  Environment,
  Session,
  MemoryStore,
  Vault,
  Deployment,
  WebhookEndpoint,
  ExecutorNode,
  AuditEntry,
  PaginatedResponse,
  SkillListResponse,
  DreamListResponse,
} from '../types';
import LoadingSpinner from '../components/LoadingSpinner';
import StatusBadge from '../components/StatusBadge';
import './DashboardView.css';

interface CountStat {
  count: number;
  hasMore: boolean;
}

interface PairStat {
  total: number;
  primary: number;
}

interface Stats {
  agents?: CountStat;
  sessions?: { total: CountStat; running: number; recent: Session[] };
  environments?: CountStat;
  memoryStores?: CountStat;
  vaults?: CountStat;
  deployments?: PairStat; // primary = active
  dreams?: PairStat; // primary = running
  webhooks?: PairStat; // primary = enabled
  skills?: CountStat;
  executorNodes?: PairStat; // primary = online
  audit?: AuditEntry[];
}

function countStat(res: { data: unknown[]; has_more?: boolean }): CountStat {
  return { count: res.data.length, hasMore: !!res.has_more };
}

function pairStat(total: number, primary: number): PairStat {
  return { total, primary };
}

function fmtTime(iso: string): string {
  return new Date(iso).toLocaleString();
}

export default function DashboardView() {
  const navigate = useNavigate();
  const [loading, setLoading] = useState(true);
  const [stats, setStats] = useState<Stats>({});
  const [agentNames, setAgentNames] = useState<Record<string, string>>({});

  const fetchDashboard = useCallback(async () => {
    setLoading(true);
    const results = await Promise.allSettled([
      apiClient.get<PaginatedResponse<Agent>>('/v1/agents?limit=100'),
      apiClient.get<PaginatedResponse<Session>>('/v1/sessions?limit=100'),
      apiClient.get<PaginatedResponse<Environment>>('/v1/environments?limit=100'),
      apiClient.get<{ data: MemoryStore[] }>('/v1/memory_stores?include_archived=false&limit=100'),
      apiClient.get<PaginatedResponse<Vault>>('/v1/vaults?limit=100'),
      apiClient.get<PaginatedResponse<Deployment>>('/v1/deployments?limit=100'),
      apiClient.get<DreamListResponse>('/v1/dreams?limit=100'),
      apiClient.get<PaginatedResponse<WebhookEndpoint>>('/v1/webhook_endpoints?limit=100'),
      apiClient.get<SkillListResponse>('/v1/skills?limit=100'),
      apiClient.get<ExecutorNode[]>('/v1/executor-nodes'),
      apiClient
        .download('/v1/audit', { limit: '5' })
        .then(blob => blob.text())
        .then(text => text.split('\n').filter(Boolean).map(line => JSON.parse(line) as AuditEntry)),
    ]);
    const [
      agents,
      sessions,
      environments,
      memoryStores,
      vaults,
      deployments,
      dreams,
      webhooks,
      skills,
      nodes,
      audit,
    ] = results;
    const sessionRows = sessions.status === 'fulfilled' ? sessions.value.data : [];
    if (agents.status === 'fulfilled') {
      const names: Record<string, string> = {};
      for (const a of agents.value.data) names[a.id] = a.name;
      setAgentNames(names);
    }
    setStats({
      agents: agents.status === 'fulfilled' ? countStat(agents.value) : undefined,
      sessions:
        sessions.status === 'fulfilled'
          ? {
              total: countStat(sessions.value),
              running: sessionRows.filter(s => s.status === 'running').length,
              recent: sessionRows.slice(0, 8),
            }
          : undefined,
      environments: environments.status === 'fulfilled' ? countStat(environments.value) : undefined,
      memoryStores: memoryStores.status === 'fulfilled' ? countStat(memoryStores.value) : undefined,
      vaults: vaults.status === 'fulfilled' ? countStat(vaults.value) : undefined,
      deployments:
        deployments.status === 'fulfilled'
          ? pairStat(
              deployments.value.data.length,
              deployments.value.data.filter(d => d.status === 'active').length,
            )
          : undefined,
      dreams:
        dreams.status === 'fulfilled'
          ? pairStat(
              dreams.value.data.length,
              dreams.value.data.filter(d => d.status === 'running').length,
            )
          : undefined,
      webhooks:
        webhooks.status === 'fulfilled'
          ? pairStat(
              webhooks.value.data.length,
              webhooks.value.data.filter(w => w.enabled).length,
            )
          : undefined,
      skills: skills.status === 'fulfilled' ? countStat(skills.value) : undefined,
      executorNodes:
        nodes.status === 'fulfilled'
          ? pairStat(
              nodes.value.length,
              nodes.value.filter(n => n.status === 'online').length,
            )
          : undefined,
      audit: audit.status === 'fulfilled' ? audit.value.slice(0, 5) : undefined,
    });
    setLoading(false);
  }, []);

  useEffect(() => {
    fetchDashboard();
  }, [fetchDashboard]);

  if (loading) return <LoadingSpinner />;

  const statCards: Array<{ key: string; label: string; to: string; stat?: CountStat | PairStat; primaryOf?: (s: CountStat | PairStat) => string; sub?: (s: CountStat | PairStat) => string }> = [
    { key: 'agents', label: 'Agents', to: '/agents', stat: stats.agents, primaryOf: s => countLabel(s) },
    { key: 'sessions', label: 'Sessions', to: '/sessions', stat: stats.sessions?.total, primaryOf: s => countLabel(s), sub: () => `${stats.sessions?.running ?? 0} running` },
    { key: 'environments', label: 'Environments', to: '/environments', stat: stats.environments, primaryOf: s => countLabel(s) },
    { key: 'memory-stores', label: 'Memory Stores', to: '/memory-stores', stat: stats.memoryStores, primaryOf: s => countLabel(s) },
    { key: 'vaults', label: 'Credential Vaults', to: '/vaults', stat: stats.vaults, primaryOf: s => countLabel(s) },
    { key: 'deployments', label: 'Active Deployments', to: '/deployments', stat: stats.deployments, primaryOf: s => String((s as PairStat).primary), sub: s => `${(s as PairStat).total} total` },
    { key: 'dreams', label: 'Running Dreams', to: '/dreams', stat: stats.dreams, primaryOf: s => String((s as PairStat).primary), sub: s => `${(s as PairStat).total} total` },
    { key: 'webhooks', label: 'Enabled Webhooks', to: '/webhooks', stat: stats.webhooks, primaryOf: s => String((s as PairStat).primary), sub: s => `${(s as PairStat).total} total` },
    { key: 'skills', label: 'Skills', to: '/skills', stat: stats.skills, primaryOf: s => countLabel(s) },
    { key: 'executor-nodes', label: 'Executor Nodes', to: '/executor-nodes', stat: stats.executorNodes, primaryOf: s => String((s as PairStat).primary), sub: s => `${(s as PairStat).total} total` },
  ];

  return (
    <div className="dashboard-view" data-testid="dashboard-view">
      <div className="dashboard-header">
        <div>
          <h1>Overview</h1>
          <p>At-a-glance view of your Linchpin workspace.</p>
        </div>
      </div>

      <div className="dashboard-stats-grid" data-testid="dashboard-stats">
        {statCards.map(card => (
          <Link
            key={card.key}
            to={card.to}
            className="dashboard-stat-card"
            data-testid={`stat-${card.key}`}
          >
            <span className="dashboard-stat-label">{card.label}</span>
            <span className="dashboard-stat-value">{card.stat ? card.primaryOf!(card.stat) : '—'}</span>
            {card.sub && card.stat && (
              <span className="dashboard-stat-sub">{card.sub(card.stat)}</span>
            )}
          </Link>
        ))}
      </div>

      <div className="dashboard-quick-actions" data-testid="dashboard-quick-actions">
        <h2>Quick actions</h2>
        <div className="dashboard-quick-actions-row">
          <button className="btn-primary" data-testid="quick-action-agent" onClick={() => navigate('/agents/new')}>
            New agent
          </button>
          <button className="btn-secondary" data-testid="quick-action-environment" onClick={() => navigate('/environments/new')}>
            New environment
          </button>
          <button className="btn-secondary" data-testid="quick-action-deployment" onClick={() => navigate('/deployments/new')}>
            New deployment
          </button>
          <button className="btn-secondary" data-testid="quick-action-dream" onClick={() => navigate('/dreams/new')}>
            New dream
          </button>
        </div>
      </div>

      <div className="dashboard-section">
        <div className="dashboard-section-header">
          <h2>Recent sessions</h2>
          <Link to="/sessions" className="dashboard-section-link">View all</Link>
        </div>
        <table className="dashboard-table" data-testid="recent-sessions-table">
          <thead>
            <tr>
              <th>Session</th>
              <th>Agent</th>
              <th>Status</th>
              <th>Created</th>
            </tr>
          </thead>
          <tbody>
            {(stats.sessions?.recent ?? []).length === 0 ? (
              <tr>
                <td colSpan={4} className="dashboard-empty" data-testid="recent-sessions-empty">
                  No sessions yet.
                </td>
              </tr>
            ) : (
              (stats.sessions?.recent ?? []).map(s => (
                <tr
                  key={s.id}
                  className="dashboard-row"
                  data-testid={`recent-sessions-row-${s.id}`}
                  onClick={() => navigate(`/sessions/${s.id}`)}
                  role="link"
                  tabIndex={0}
                  onKeyDown={e => {
                    if (e.key === 'Enter') navigate(`/sessions/${s.id}`);
                  }}
                >
                  <td data-testid="recent-sessions-cell-title">{s.title || s.id.slice(0, 8)}</td>
                  <td data-testid="recent-sessions-cell-agent">
                    <Link
                      to={`/agents/${s.agent_id}`}
                      onClick={e => e.stopPropagation()}
                    >
                      {agentNames[s.agent_id] ?? s.agent_id.slice(0, 8)}
                    </Link>
                  </td>
                  <td>
                    <StatusBadge status={s.status} />
                  </td>
                  <td>{fmtTime(s.created_at)}</td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      <div className="dashboard-section">
        <div className="dashboard-section-header">
          <h2>Recent audit events</h2>
          <Link to="/audit" className="dashboard-section-link">View all</Link>
        </div>
        <table className="dashboard-table" data-testid="recent-audit-table">
          <thead>
            <tr>
              <th>Time</th>
              <th>Actor</th>
              <th>Action</th>
              <th>Resource</th>
            </tr>
          </thead>
          <tbody>
            {(stats.audit ?? []).length === 0 ? (
              <tr>
                <td colSpan={4} className="dashboard-empty" data-testid="recent-audit-empty">
                  No audit events yet.
                </td>
              </tr>
            ) : (
              (stats.audit ?? []).map(e => {
                const href = resourceHref(e.resource_type, e.resource_id);
                const label = `${e.resource_type}${e.resource_id ? ` · ${e.resource_id.slice(0, 8)}` : ''}`;
                return (
                  <tr key={e.id} data-testid={`recent-audit-row-${e.id}`}>
                    <td>{fmtTime(e.at)}</td>
                    <td>{e.actor || '—'}</td>
                    <td data-testid="recent-audit-cell-action"><code>{e.action}</code></td>
                    <td>
                      {href ? (
                        <Link to={href} data-testid="recent-audit-resource-link">{label}</Link>
                      ) : (
                        label
                      )}
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function countLabel(s: CountStat | PairStat): string {
  const stat = s as CountStat;
  return stat.hasMore ? `${stat.count}+` : String(stat.count);
}
