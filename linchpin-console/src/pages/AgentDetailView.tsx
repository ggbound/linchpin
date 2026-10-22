import { useCallback, useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { apiClient, ApiError } from '../api/client';
import type { Agent, AgentVersion, ModelConfig, SubagentConfig, UpdateAgentRequest } from '../types';
import LoadingSpinner from '../components/LoadingSpinner';
import ErrorMessage from '../components/ErrorMessage';
import ConfirmDialog from '../components/ConfirmDialog';
import SubagentEditor from '../components/SubagentEditor';
import SkillsPicker from '../components/SkillsPicker';
import './AgentDetailView.css';

const MAX_SKILLS = 8;

export default function AgentDetailView() {
  const { t, i18n } = useTranslation('agents');
  const { id } = useParams<{ id: string }>();
  const [agent, setAgent] = useState<Agent | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<ApiError | null>(null);

  // Edit mode state
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  // Editable field state
  const [editName, setEditName] = useState('');
  const [editDescription, setEditDescription] = useState('');
  const [editProvider, setEditProvider] = useState<ModelConfig['provider']>('openrouter');
  const [editModelId, setEditModelId] = useState('');
  const [editBaseUrl, setEditBaseUrl] = useState('');
  const [editSystem, setEditSystem] = useState('');
  const [editSkills, setEditSkills] = useState<string[]>([]);
  const [editSubagents, setEditSubagents] = useState<SubagentConfig[]>([]);

  // Archive state
  const [showArchiveConfirm, setShowArchiveConfirm] = useState(false);
  const [archiving, setArchiving] = useState(false);
  const [archiveError, setArchiveError] = useState<string | null>(null);

  // Version history
  const [versions, setVersions] = useState<AgentVersion[] | null>(null);
  const [versionsError, setVersionsError] = useState<string | null>(null);

  const fetchAgent = async () => {
    try {
      setError(null);
      const data = await apiClient.get<Agent>(`/v1/agents/${id}`);
      setAgent(data);
    } catch (err) {
      setError(err as ApiError);
    } finally {
      setLoading(false);
    }
  };

  const fetchVersions = useCallback(() => {
    return apiClient
      .get<{ data: AgentVersion[] }>(`/v1/agents/${id}/versions?limit=20`)
      .then((res) => setVersions(res.data))
      .catch(() => setVersionsError(t('detailLoadVersionsFailed')));
  }, [id, t]);

  useEffect(() => {
    setLoading(true);
    setVersions(null);
    fetchAgent();
    // Version history (independent failure handling)
    fetchVersions();
  }, [id, fetchVersions]);

  const enterEditMode = () => {
    if (!agent) return;
    setEditName(agent.name);
    setEditDescription(agent.description || '');
    setEditProvider(agent.model.provider);
    setEditModelId(agent.model.id);
    setEditBaseUrl(agent.model.base_url || '');
    setEditSystem(agent.system);
    setEditSkills([...agent.skills]);
    setEditSubagents(agent.multi_agents.map((s) => ({ ...s })));
    setSaveError(null);
    setEditing(true);
  };

  const cancelEdit = () => {
    setEditing(false);
    setSaveError(null);
  };

  const handleSave = async () => {
    if (!agent) return;
    setSaving(true);
    setSaveError(null);

    // Build patch with only changed fields
    const patch: UpdateAgentRequest = {};

    if (editName !== agent.name) {
      patch.name = editName;
    }

    if (editDescription !== (agent.description || '')) {
      patch.description = editDescription.trim() || null;
    }

    const modelChanged =
      editProvider !== agent.model.provider ||
      editModelId !== agent.model.id ||
      editBaseUrl !== (agent.model.base_url || '');

    if (modelChanged) {
      const model: ModelConfig = { provider: editProvider, id: editModelId };
      if (editProvider === 'ollama' && editBaseUrl.trim()) {
        model.base_url = editBaseUrl.trim();
      }
      patch.model = model;
    }

    if (editSystem !== agent.system) {
      patch.system = editSystem;
    }

    if (JSON.stringify(editSkills) !== JSON.stringify(agent.skills)) {
      patch.skills = editSkills;
    }

    if (JSON.stringify(editSubagents) !== JSON.stringify(agent.multi_agents)) {
      // API SubagentConfig.description is `str = ""` — it rejects null.
      patch.multi_agents = editSubagents.map((s) => ({
        ...s,
        description: s.description ?? '',
      }));
    }

    try {
      const updated = await apiClient.patch<Agent>(`/v1/agents/${id}`, patch);
      setAgent(updated);
      setEditing(false);
      // PATCH snapshots the previous version server-side, so refresh history.
      fetchVersions();
    } catch (err) {
      const apiErr = err as ApiError;
      setSaveError(apiErr.message || t('detailSaveFailed'));
    } finally {
      setSaving(false);
    }
  };

  const handleArchive = async () => {
    if (!agent) return;
    setShowArchiveConfirm(false);
    setArchiving(true);
    setArchiveError(null);
    try {
      const updated = await apiClient.post<Agent>(`/v1/agents/${id}/archive`, {});
      setAgent(updated);
    } catch (err) {
      setArchiveError((err as ApiError).message || t('detailArchiveFailed'));
    } finally {
      setArchiving(false);
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
          fetchAgent();
        }}
      />
    );
  }

  if (!agent) return null;

  const isArchived = Boolean(agent.archived_at);

  return (
    <div className="agent-detail-view" data-testid="agent-detail-view">
      <Link to="/agents" className="back-link" data-testid="back-link">
        {t('detailBackToAgents')}
      </Link>

      <div className="agent-detail-header">
        <div className="agent-detail-title-row">
          {editing ? (
            <input
              className="edit-name-input"
              type="text"
              value={editName}
              onChange={(e) => setEditName(e.target.value)}
              data-testid="edit-name-input"
            />
          ) : (
            <h1 data-testid="agent-name">{agent.name}</h1>
          )}
          {isArchived && (
            <span className="archived-badge" data-testid="archived-badge">
              {t('status:archived')}
            </span>
          )}
          {!editing && !isArchived && (
            <button className="btn-edit" onClick={enterEditMode} data-testid="edit-button">
              {t('common:edit')}
            </button>
          )}
          {!editing && !isArchived && (
            <button
              className="btn-archive"
              onClick={() => setShowArchiveConfirm(true)}
              disabled={archiving}
              data-testid="archive-button"
            >
              {archiving ? t('detailArchiving') : t('detailArchive')}
            </button>
          )}
        </div>
        <div className="agent-detail-meta">
          <span data-testid="agent-id">
            <code>{agent.id}</code>
          </span>
          <span className="meta-separator">·</span>
          <span data-testid="agent-version">{t('detailVersion', { version: agent.version })}</span>
          <span className="meta-separator">·</span>
          <span data-testid="agent-created">
            {t('detailCreatedAt', { time: new Date(agent.created_at).toLocaleString(i18n.language) })}
          </span>
        </div>
      </div>

      {archiveError && (
        <div className="save-error" data-testid="archive-error">
          {archiveError}
        </div>
      )}

      {saveError && (
        <div className="save-error" data-testid="save-error">
          {saveError}
        </div>
      )}

      <section className="detail-section" data-testid="model-config-section">
        <h2>{t('detailModelConfig')}</h2>
        {editing ? (
          <div className="edit-fields">
            <div className="edit-field">
              <label htmlFor="edit-provider">{t('detailProvider')}</label>
              <select
                id="edit-provider"
                value={editProvider}
                onChange={(e) => setEditProvider(e.target.value as ModelConfig['provider'])}
                data-testid="edit-provider-select"
              >
                <option value="openrouter">OpenRouter</option>
                <option value="openai">OpenAI</option>
                <option value="ollama">Ollama</option>
              </select>
            </div>
            <div className="edit-field">
              <label htmlFor="edit-model-id">{t('detailModelId')}</label>
              <input
                id="edit-model-id"
                type="text"
                value={editModelId}
                onChange={(e) => setEditModelId(e.target.value)}
                data-testid="edit-model-id-input"
              />
            </div>
            {(editProvider === 'openai' || editProvider === 'ollama') && (
              <div className="edit-field">
                <label htmlFor="edit-base-url">{t('detailBaseUrl')}</label>
                <input
                  id="edit-base-url"
                  type="text"
                  value={editBaseUrl}
                  onChange={(e) => setEditBaseUrl(e.target.value)}
                  placeholder={editProvider === 'openai' ? 'https://api.openai.com/v1' : 'http://localhost:11434'}
                  data-testid="edit-base-url-input"
                />
              </div>
            )}
          </div>
        ) : (
          <dl className="detail-grid">
            <dt>{t('detailProvider')}</dt>
            <dd data-testid="model-provider">{agent.model.provider}</dd>
            <dt>{t('detailModelId')}</dt>
            <dd data-testid="model-id">{agent.model.id}</dd>
            {agent.model.base_url && (
              <>
                <dt>{t('detailBaseUrl')}</dt>
                <dd data-testid="model-base-url">{agent.model.base_url}</dd>
              </>
            )}
          </dl>
        )}
      </section>

      <section className="detail-section" data-testid="description-section">
        <h2>{t('common:description')}</h2>
        {editing ? (
          <textarea
            className="edit-description-textarea"
            value={editDescription}
            onChange={(e) => setEditDescription(e.target.value)}
            placeholder={t('detailDescriptionPlaceholder')}
            rows={3}
            data-testid="edit-description-input"
          />
        ) : agent.description ? (
          <p className="description-text" data-testid="agent-description">
            {agent.description}
          </p>
        ) : (
          <p className="empty-text">{t('detailNoDescription')}</p>
        )}
      </section>

      <section className="detail-section" data-testid="system-prompt-section">
        <h2>{t('detailSystemPrompt')}</h2>
        {editing ? (
          <textarea
            className="edit-system-textarea"
            value={editSystem}
            onChange={(e) => setEditSystem(e.target.value)}
            data-testid="edit-system-textarea"
          />
        ) : (
          <pre className="system-prompt" data-testid="system-prompt">
            {agent.system}
          </pre>
        )}
      </section>

      {editing && (
        <div className="edit-actions" data-testid="edit-actions">
          <button
            className="btn-save"
            onClick={handleSave}
            disabled={saving}
            data-testid="save-button"
          >
            {saving ? t('common:saving') : t('common:save')}
          </button>
          <button
            className="btn-cancel"
            onClick={cancelEdit}
            disabled={saving}
            data-testid="cancel-button"
          >
            {t('common:cancel')}
          </button>
        </div>
      )}

      <section className="detail-section" data-testid="skills-section">
        <h2>{t('detailSkills')}</h2>
        <p className="section-hint">{t('detailSkillsHint', { max: MAX_SKILLS })}</p>
        {editing ? (
          <SkillsPicker
            value={editSkills}
            onChange={setEditSkills}
            testIdPrefix="skills-editor"
            max={MAX_SKILLS}
          />
        ) : agent.skills.length === 0 ? (
          <p className="empty-text">{t('detailNoSkills')}</p>
        ) : (
          <div className="skill-chip-row" data-testid="skills-list">
            {agent.skills.map((s) => (
              <Link
                key={s}
                to={`/skills?search=${encodeURIComponent(s)}`}
                className="skill-chip skill-chip-link"
                data-testid="skill-chip"
              >
                {s}
              </Link>
            ))}
          </div>
        )}
      </section>

      <section className="detail-section" data-testid="multi-agent-section">
        <h2>{t('detailSubagentsTitle')}</h2>
        {editing ? (
          <>
            <p className="section-hint">{t('detailSubagentsHint')}</p>
            <SubagentEditor
              value={editSubagents}
              onChange={setEditSubagents}
              selfAgentId={agent.id}
            />
          </>
        ) : agent.multi_agents.length === 0 ? (
          <p className="empty-text">{t('detailNoSubagents')}</p>
        ) : (
          <ul className="subagents-list">
            {agent.multi_agents.map((sub) => (
              <li key={sub.name} className="subagent-item" data-testid="subagent-item">
                <span className="subagent-name">{sub.name}</span>
                {sub.agent_id ? (
                  <Link to={`/agents/${sub.agent_id}`} className="subagent-link">
                    {sub.agent_id}
                  </Link>
                ) : (
                  <span className="empty-text">{t('detailNoAgent')}</span>
                )}
                {sub.environment_id && (
                  <Link to={`/environments/${sub.environment_id}`} className="subagent-link">
                    {t('detailEnvLabel', { id: sub.environment_id })}
                  </Link>
                )}
                {sub.description && (
                  <span className="subagent-description">{sub.description}</span>
                )}
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="detail-section" data-testid="tools-section">
        <h2>{t('detailTools')}</h2>
        {agent.tools.length === 0 ? (
          <p className="empty-text">{t('detailNoTools')}</p>
        ) : (
          <ul className="tools-list">
            {agent.tools.map((tool, idx) =>
              tool.type === 'builtin' ? (
                tool.configs.map((cfg) => (
                  <li key={`builtin-${idx}-${cfg.name}`} className="tool-item" data-testid="tool-item">
                    <span className="tool-name">{cfg.name}</span>
                    <span className="tool-type">{t('detailToolBuiltin')}</span>
                    <span
                      className={`tool-permission ${cfg.permission_policy}`}
                      data-testid="tool-permission"
                    >
                      {cfg.permission_policy}
                    </span>
                  </li>
                ))
              ) : (
                <li key={`custom-${idx}-${tool.name}`} className="tool-item" data-testid="tool-item">
                  <span className="tool-name">{tool.name}</span>
                  <span className="tool-type">{t('detailToolCustom')}</span>
                  <span
                    className={`tool-permission ${tool.permission_policy}`}
                    data-testid="tool-permission"
                  >
                    {tool.permission_policy}
                  </span>
                </li>
              ),
            )}
          </ul>
        )}
      </section>

      <section className="detail-section" data-testid="mcp-servers-section">
        <h2>{t('detailMcpServers')}</h2>
        {agent.mcp_servers.length === 0 ? (
          <p className="empty-text">{t('detailNoMcpServers')}</p>
        ) : (
          <div className="mcp-servers-list">
            {agent.mcp_servers.map((server) => (
              <div key={server.name} className="mcp-server-card" data-testid="mcp-server-card">
                <h3 data-testid="mcp-server-name">{server.name}</h3>
                <dl className="detail-grid">
                  <dt>{t('detailCommand')}</dt>
                  <dd data-testid="mcp-server-command">
                    <code>{server.command}</code>
                  </dd>
                  {server.args.length > 0 && (
                    <>
                      <dt>{t('detailArgs')}</dt>
                      <dd data-testid="mcp-server-args">
                        <code>{server.args.join(' ')}</code>
                      </dd>
                    </>
                  )}
                  {Object.keys(server.env).length > 0 && (
                    <>
                      <dt>{t('detailEnv')}</dt>
                      <dd data-testid="mcp-server-env">
                        {Object.entries(server.env).map(([k, v]) => (
                          <div key={k} className="env-entry">
                            <code>
                              {k}={v}
                            </code>
                          </div>
                        ))}
                      </dd>
                    </>
                  )}
                </dl>
              </div>
            ))}
          </div>
        )}
      </section>

      <section className="detail-section" data-testid="version-history-section">
        <h2>{t('detailVersionHistory')}</h2>
        {versionsError ? (
          <p className="empty-text">{versionsError}</p>
        ) : versions === null ? (
          <LoadingSpinner />
        ) : versions.length === 0 ? (
          <p className="empty-text">{t('detailNoVersions')}</p>
        ) : (
          <table className="versions-table">
            <thead>
              <tr>
                <th>{t('detailColVersion')}</th>
                <th>{t('common:name')}</th>
                <th>{t('detailColModel')}</th>
                <th>{t('detailColSnapshotTime')}</th>
              </tr>
            </thead>
            <tbody>
              {versions.map((v) => (
                <tr key={v.version} data-testid="version-row">
                  <td data-testid="version-number">{v.version}</td>
                  <td>{v.name}</td>
                  <td>
                    <code>{v.model.id}</code>
                  </td>
                  <td>{new Date(v.snapshotted_at).toLocaleString(i18n.language)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <ConfirmDialog
        open={showArchiveConfirm}
        title={t('detailArchiveConfirmTitle')}
        message={t('detailArchiveConfirmMessage', { name: agent.name })}
        onConfirm={handleArchive}
        onCancel={() => setShowArchiveConfirm(false)}
      />
    </div>
  );
}
