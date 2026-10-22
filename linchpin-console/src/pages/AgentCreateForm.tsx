import { useState, FormEvent } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { apiClient, ApiError, extractValidationErrors, FieldValidationError } from '../api/client';
import type { Agent, CreateAgentRequest, ModelConfig, MCPServerConfig, SubagentConfig } from '../types';
import SubagentEditor from '../components/SubagentEditor';
import SkillsPicker from '../components/SkillsPicker';
import './AgentCreateForm.css';

// Builtin tool names accepted by the orchestrator (see linchpin-api tools registry).
const BUILTIN_TOOL_NAMES = [
  'bash',
  'read',
  'write',
  'edit',
  'glob',
  'grep',
  'web_fetch',
  'web_search',
] as const;

const MAX_SKILLS = 8; // MAX_SKILLS_PER_AGENT

interface ToolEntry {
  type: 'builtin' | 'custom';
  name: string;
  permission_policy: 'always_allow' | 'always_ask';
}

interface MCPEntry {
  name: string;
  command: string;
  args: string;
  env: Array<{ key: string; value: string }>;
}

export default function AgentCreateForm() {
  const { t } = useTranslation('agents');
  const navigate = useNavigate();

  // Basic info
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [provider, setProvider] = useState<ModelConfig['provider']>('openrouter');
  const [modelId, setModelId] = useState('');
  const [baseUrl, setBaseUrl] = useState('');
  const [systemPrompt, setSystemPrompt] = useState('');

  // Skills (max 8, unique — MAX_SKILLS_PER_AGENT)
  const [skills, setSkills] = useState<string[]>([]);

  // Dynamic tools
  const [tools, setTools] = useState<ToolEntry[]>([]);

  // Dynamic MCP servers
  const [mcpServers, setMcpServers] = useState<MCPEntry[]>([]);

  // Multi-agent subagents
  const [subagents, setSubagents] = useState<SubagentConfig[]>([]);

  // Form state
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [validationErrors, setValidationErrors] = useState<FieldValidationError[]>([]);

  // --- Tool helpers ---
  const addTool = () => {
    setTools([...tools, { type: 'builtin', name: '', permission_policy: 'always_allow' }]);
  };

  const removeTool = (index: number) => {
    setTools(tools.filter((_, i) => i !== index));
  };

  const updateTool = (index: number, field: keyof ToolEntry, value: string) => {
    setTools(tools.map((t, i) => (i === index ? { ...t, [field]: value } : t)));
  };

  // --- MCP server helpers ---
  const addMcpServer = () => {
    setMcpServers([...mcpServers, { name: '', command: '', args: '', env: [] }]);
  };

  const removeMcpServer = (index: number) => {
    setMcpServers(mcpServers.filter((_, i) => i !== index));
  };

  const updateMcpServer = (index: number, field: keyof MCPEntry, value: string) => {
    setMcpServers(mcpServers.map((s, i) => (i === index ? { ...s, [field]: value } : s)));
  };

  const addEnvPair = (serverIndex: number) => {
    setMcpServers(
      mcpServers.map((s, i) =>
        i === serverIndex ? { ...s, env: [...s.env, { key: '', value: '' }] } : s,
      ),
    );
  };

  const removeEnvPair = (serverIndex: number, envIndex: number) => {
    setMcpServers(
      mcpServers.map((s, i) =>
        i === serverIndex ? { ...s, env: s.env.filter((_, ei) => ei !== envIndex) } : s,
      ),
    );
  };

  const updateEnvPair = (
    serverIndex: number,
    envIndex: number,
    field: 'key' | 'value',
    value: string,
  ) => {
    setMcpServers(
      mcpServers.map((s, i) =>
        i === serverIndex
          ? {
              ...s,
              env: s.env.map((e, ei) => (ei === envIndex ? { ...e, [field]: value } : e)),
            }
          : s,
      ),
    );
  };

  // --- Build request body ---
  const buildRequest = (): CreateAgentRequest => {
    const model: ModelConfig = { provider, id: modelId };
    if (provider === 'ollama' && baseUrl.trim()) {
      model.base_url = baseUrl.trim();
    }

    const toolConfigs = tools.map((t) => {
      if (t.type === 'builtin') {
        return {
          type: 'builtin' as const,
          default_config: {},
          configs: [{ name: t.name, permission_policy: t.permission_policy, enabled: true }],
        };
      }
      return {
        type: 'custom' as const,
        name: t.name,
        description: '',
        input_schema: {},
        permission_policy: t.permission_policy,
      };
    });

    const mcpConfigs: MCPServerConfig[] = mcpServers.map((s) => ({
      name: s.name,
      command: s.command,
      args: s.args
        .split(',')
        .map((a) => a.trim())
        .filter(Boolean),
      env: Object.fromEntries(s.env.filter((e) => e.key.trim()).map((e) => [e.key, e.value])),
    }));

    const validSubagents = subagents
      .filter((s) => s.name && s.agent_id && !s.name.startsWith('-'))
      // API SubagentConfig.description is `str = ""` — it rejects null.
      .map((s) => ({ ...s, description: s.description ?? '' }));

    return {
      name,
      model,
      system: systemPrompt,
      tools: toolConfigs,
      mcp_servers: mcpConfigs,
      skills,
      multi_agents: validSubagents,
      description: description.trim() || null,
    };
  };

  // --- Submit ---
  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    setValidationErrors([]);
    setSubmitting(true);

    try {
      const agent = await apiClient.post<Agent>('/v1/agents', buildRequest());
      navigate(`/agents/${agent.id}`);
    } catch (err) {
      const apiErr = err as ApiError;
      setValidationErrors(extractValidationErrors(err));
      setError(apiErr.message || t('createFailed'));
      setSubmitting(false);
    }
  };

  // --- Field-level validation error rendering (422) ---
  const KNOWN_TOP_FIELDS = new Set([
    'name',
    'description',
    'model',
    'system',
    'tools',
    'mcp_servers',
    'skills',
    'multi_agents',
  ]);

  const errorsFor = (prefix: string): string[] =>
    validationErrors
      .filter((fe) => fe.path === prefix || fe.path.startsWith(`${prefix}.`))
      .map((fe) => (fe.path === prefix ? fe.msg : `${fe.path.slice(prefix.length + 1)}: ${fe.msg}`));

  const renderFieldErrors = (prefix: string) => {
    const msgs = errorsFor(prefix);
    if (msgs.length === 0) return null;
    return (
      <ul className="field-error" data-testid={`field-error-${prefix.replace(/\./g, '-')}`}>
        {msgs.map((m, i) => (
          <li key={i}>{m}</li>
        ))}
      </ul>
    );
  };

  const unmappedErrors = validationErrors.filter(
    (fe) => !KNOWN_TOP_FIELDS.has(fe.path.split('.')[0]),
  );

  // model.* errors other than id/base_url — shown at the top of the model section
  const modelOtherErrors = validationErrors
    .filter(
      (fe) =>
        fe.path.startsWith('model.') &&
        !fe.path.startsWith('model.id') &&
        !fe.path.startsWith('model.base_url'),
    )
    .map((fe) => `${fe.path.slice('model.'.length)}: ${fe.msg}`);

  return (
    <div className="agent-create-form">
      <Link to="/agents" className="back-link">
        {t('createBackToAgents')}
      </Link>
      <h1>{t('createTitle')}</h1>

      {error && (
        <div className="form-error" data-testid="form-error">
          <p>
            {validationErrors.length > 0 ? t('createValidationError') : error}
          </p>
          {unmappedErrors.length > 0 && (
            <ul className="form-error-list">
              {unmappedErrors.map((fe, i) => (
                <li key={i}>
                  {fe.path}: {fe.msg}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      <form onSubmit={handleSubmit}>
        {/* Basic Info */}
        <section className="form-section">
          <h2>{t('createBasicInfo')}</h2>
          <div className="form-field">
            <label htmlFor="agent-name">{t('common:name')}</label>
            <input
              id="agent-name"
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              required
              placeholder={t('createNamePlaceholder')}
            />
            {renderFieldErrors('name')}
          </div>
          <div className="form-field">
            <label htmlFor="agent-description">{t('common:description')}</label>
            <input
              id="agent-description"
              type="text"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder={t('createDescriptionPlaceholder')}
            />
            {renderFieldErrors('description')}
          </div>
        </section>

        {/* Model Configuration */}
        <section className="form-section">
          <h2>{t('createModelConfig')}</h2>
          {modelOtherErrors.length > 0 && (
            <ul className="field-error" data-testid="field-error-model">
              {modelOtherErrors.map((m, i) => (
                <li key={i}>{m}</li>
              ))}
            </ul>
          )}
          <div className="form-field">
            <label htmlFor="model-provider">{t('createProvider')}</label>
            <select
              id="model-provider"
              value={provider}
              onChange={(e) => setProvider(e.target.value as ModelConfig['provider'])}
            >
              <option value="openrouter">OpenRouter</option>
              <option value="ollama">Ollama</option>
            </select>
          </div>
          <div className="form-field">
            <label htmlFor="model-id">{t('createModelId')}</label>
            <input
              id="model-id"
              type="text"
              value={modelId}
              onChange={(e) => setModelId(e.target.value)}
              required
              placeholder="claude-sonnet-4-20250514"
            />
            {renderFieldErrors('model.id')}
          </div>
          {provider === 'ollama' && (
            <div className="form-field">
              <label htmlFor="base-url">{t('createBaseUrl')}</label>
              <input
                id="base-url"
                type="text"
                value={baseUrl}
                onChange={(e) => setBaseUrl(e.target.value)}
                placeholder="http://localhost:11434"
              />
              {renderFieldErrors('model.base_url')}
            </div>
          )}
        </section>

        {/* System Prompt */}
        <section className="form-section">
          <h2>{t('createSystemPrompt')}</h2>
          <div className="form-field">
            <label htmlFor="system-prompt">{t('createPromptLabel')}</label>
            <textarea
              id="system-prompt"
              value={systemPrompt}
              onChange={(e) => setSystemPrompt(e.target.value)}
              placeholder={t('createPromptPlaceholder')}
            />
            {renderFieldErrors('system')}
          </div>
        </section>

        {/* Skills */}
        <section className="form-section">
          <h2>{t('createSkills')}</h2>
          <p className="section-hint">{t('createSkillsHint', { max: MAX_SKILLS })}</p>
          {renderFieldErrors('skills')}
          <SkillsPicker value={skills} onChange={setSkills} testIdPrefix="skills-editor" max={MAX_SKILLS} />
        </section>

        {/* Tools */}
        <section className="form-section">
          <h2>{t('createTools')}</h2>
          {renderFieldErrors('tools')}
          <div className="dynamic-entries">
            {tools.map((tool, idx) => (
              <div key={idx} className="dynamic-entry">
                <div className="entry-fields">
                  <div className="form-field">
                    <label>{t('common:type')}</label>
                    <select
                      value={tool.type}
                      onChange={(e) => updateTool(idx, 'type', e.target.value)}
                    >
                      <option value="builtin">{t('createToolBuiltin')}</option>
                      <option value="custom">{t('createToolCustom')}</option>
                    </select>
                  </div>
                  <div className="form-field">
                    <label>{t('common:name')}</label>
                    {tool.type === 'builtin' ? (
                      <select
                        value={tool.name}
                        onChange={(e) => updateTool(idx, 'name', e.target.value)}
                      >
                        <option value="">{t('createSelectTool')}</option>
                        {BUILTIN_TOOL_NAMES.map((n) => (
                          <option key={n} value={n}>
                            {n}
                          </option>
                        ))}
                      </select>
                    ) : (
                      <input
                        type="text"
                        value={tool.name}
                        onChange={(e) => updateTool(idx, 'name', e.target.value)}
                        placeholder={t('createToolNamePlaceholder')}
                      />
                    )}
                  </div>
                  <div className="form-field">
                    <label>{t('createPermission')}</label>
                    <select
                      value={tool.permission_policy}
                      onChange={(e) => updateTool(idx, 'permission_policy', e.target.value)}
                    >
                      <option value="always_allow">{t('createPermAlwaysAllow')}</option>
                      <option value="always_ask">{t('createPermAlwaysAsk')}</option>
                    </select>
                  </div>
                </div>
                <button type="button" className="btn-remove" onClick={() => removeTool(idx)}>
                  {t('createRemove')}
                </button>
              </div>
            ))}
          </div>
          <button type="button" className="btn-add" onClick={addTool}>
            {t('createAddTool')}
          </button>
        </section>

        {/* MCP Servers */}
        <section className="form-section">
          <h2>{t('createMcpServers')}</h2>
          {renderFieldErrors('mcp_servers')}
          <div className="dynamic-entries">
            {mcpServers.map((server, idx) => (
              <div key={idx} className="dynamic-entry">
                <div className="entry-fields">
                  <div className="form-field">
                    <label>{t('common:name')}</label>
                    <input
                      type="text"
                      value={server.name}
                      onChange={(e) => updateMcpServer(idx, 'name', e.target.value)}
                      placeholder={t('createServerNamePlaceholder')}
                    />
                  </div>
                  <div className="form-field">
                    <label>{t('createCommand')}</label>
                    <input
                      type="text"
                      value={server.command}
                      onChange={(e) => updateMcpServer(idx, 'command', e.target.value)}
                      placeholder="npx"
                    />
                  </div>
                  <div className="form-field full-width">
                    <label>{t('createArgs')}</label>
                    <input
                      type="text"
                      value={server.args}
                      onChange={(e) => updateMcpServer(idx, 'args', e.target.value)}
                      placeholder="-y, @modelcontextprotocol/server-filesystem"
                    />
                  </div>
                  <div className="form-field full-width">
                    <label>{t('createEnvVars')}</label>
                    <div className="env-pairs">
                      {server.env.map((envPair, ei) => (
                        <div key={ei} className="env-pair">
                          <input
                            type="text"
                            value={envPair.key}
                            onChange={(e) => updateEnvPair(idx, ei, 'key', e.target.value)}
                            placeholder="KEY"
                          />
                          <input
                            type="text"
                            value={envPair.value}
                            onChange={(e) => updateEnvPair(idx, ei, 'value', e.target.value)}
                            placeholder="value"
                          />
                          <button
                            type="button"
                            className="btn-remove-sm"
                            onClick={() => removeEnvPair(idx, ei)}
                          >
                            ✕
                          </button>
                        </div>
                      ))}
                      <button type="button" className="btn-add-sm" onClick={() => addEnvPair(idx)}>
                        {t('createAddEnvVar')}
                      </button>
                    </div>
                  </div>
                </div>
                <button type="button" className="btn-remove" onClick={() => removeMcpServer(idx)}>
                  {t('createRemove')}
                </button>
              </div>
            ))}
          </div>
          <button type="button" className="btn-add" onClick={addMcpServer}>
            {t('createAddMcpServer')}
          </button>
        </section>

        {/* Multi-Agent */}
        <section className="form-section">
          <h2>{t('createSubagentsTitle')}</h2>
          <p className="section-hint">{t('createSubagentsHint')}</p>
          {renderFieldErrors('multi_agents')}
          <SubagentEditor value={subagents} onChange={setSubagents} />
        </section>

        {/* Submit */}
        <div className="form-actions">
          <button type="submit" className="btn-submit" disabled={submitting}>
            {submitting ? t('common:creating') : t('createSubmitButton')}
          </button>
        </div>
      </form>
    </div>
  );
}
