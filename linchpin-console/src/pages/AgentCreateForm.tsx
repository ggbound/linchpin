import { useState, FormEvent } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { apiClient, ApiError, extractValidationErrors, FieldValidationError } from '../api/client';
import type { Agent, CreateAgentRequest, ModelConfig, MCPServerConfig, SubagentConfig } from '../types';
import SubagentEditor from '../components/SubagentEditor';
import './AgentCreateForm.css';

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
  const navigate = useNavigate();

  // Basic info
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [provider, setProvider] = useState<ModelConfig['provider']>('openrouter');
  const [modelId, setModelId] = useState('');
  const [baseUrl, setBaseUrl] = useState('');
  const [systemPrompt, setSystemPrompt] = useState('');

  // Skills (max 8, unique — MAX_SKILLS_PER_AGENT)
  const [skillInput, setSkillInput] = useState('');
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

  // --- Skill helpers ---
  const MAX_SKILLS = 8;

  const addSkill = () => {
    const s = skillInput.trim();
    if (!s || skills.includes(s) || skills.length >= MAX_SKILLS) return;
    setSkills([...skills, s]);
    setSkillInput('');
  };

  const removeSkill = (s: string) => {
    setSkills(skills.filter((x) => x !== s));
  };

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
      setError(apiErr.message || 'Failed to create agent');
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
        ← Agents
      </Link>
      <h1>Create Agent</h1>

      {error && (
        <div className="form-error" data-testid="form-error">
          <p>
            {validationErrors.length > 0
              ? 'Request validation failed — fix the highlighted fields below.'
              : error}
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
          <h2>Basic Info</h2>
          <div className="form-field">
            <label htmlFor="agent-name">Name</label>
            <input
              id="agent-name"
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              required
              placeholder="My Agent"
            />
            {renderFieldErrors('name')}
          </div>
          <div className="form-field">
            <label htmlFor="agent-description">Description</label>
            <input
              id="agent-description"
              type="text"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="What this agent does"
            />
            {renderFieldErrors('description')}
          </div>
        </section>

        {/* Model Configuration */}
        <section className="form-section">
          <h2>Model Configuration</h2>
          {modelOtherErrors.length > 0 && (
            <ul className="field-error" data-testid="field-error-model">
              {modelOtherErrors.map((m, i) => (
                <li key={i}>{m}</li>
              ))}
            </ul>
          )}
          <div className="form-field">
            <label htmlFor="model-provider">Provider</label>
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
            <label htmlFor="model-id">Model ID</label>
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
              <label htmlFor="base-url">Base URL</label>
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
          <h2>System Prompt</h2>
          <div className="form-field">
            <label htmlFor="system-prompt">Prompt</label>
            <textarea
              id="system-prompt"
              value={systemPrompt}
              onChange={(e) => setSystemPrompt(e.target.value)}
              placeholder="You are a helpful assistant..."
            />
            {renderFieldErrors('system')}
          </div>
        </section>

        {/* Skills */}
        <section className="form-section">
          <h2>Skills</h2>
          <p className="section-hint">
            Skill bundle names installed on this agent (upload bundles on the Skills page). Max {MAX_SKILLS}.
          </p>
          {renderFieldErrors('skills')}
          <div className="skill-chip-row" data-testid="skills-editor">
            {skills.map((s) => (
              <span key={s} className="skill-chip">
                {s}
                <button
                  type="button"
                  className="skill-chip-remove"
                  onClick={() => removeSkill(s)}
                  aria-label={`Remove ${s}`}
                >
                  ✕
                </button>
              </span>
            ))}
          </div>
          <div className="skill-input-row">
            <input
              type="text"
              value={skillInput}
              onChange={(e) => setSkillInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') {
                  e.preventDefault();
                  addSkill();
                }
              }}
              placeholder="skill-name"
              disabled={skills.length >= MAX_SKILLS}
            />
            <button
              type="button"
              className="btn-add-sm"
              onClick={addSkill}
              disabled={!skillInput.trim() || skills.includes(skillInput.trim()) || skills.length >= MAX_SKILLS}
            >
              + Add Skill
            </button>
          </div>
        </section>

        {/* Tools */}
        <section className="form-section">
          <h2>Tools</h2>
          {renderFieldErrors('tools')}
          <div className="dynamic-entries">
            {tools.map((tool, idx) => (
              <div key={idx} className="dynamic-entry">
                <div className="entry-fields">
                  <div className="form-field">
                    <label>Type</label>
                    <select
                      value={tool.type}
                      onChange={(e) => updateTool(idx, 'type', e.target.value)}
                    >
                      <option value="builtin">Builtin</option>
                      <option value="custom">Custom</option>
                    </select>
                  </div>
                  <div className="form-field">
                    <label>Name</label>
                    <input
                      type="text"
                      value={tool.name}
                      onChange={(e) => updateTool(idx, 'name', e.target.value)}
                      placeholder="Tool name"
                    />
                  </div>
                  <div className="form-field">
                    <label>Permission</label>
                    <select
                      value={tool.permission_policy}
                      onChange={(e) => updateTool(idx, 'permission_policy', e.target.value)}
                    >
                      <option value="always_allow">Always Allow</option>
                      <option value="always_ask">Always Ask</option>
                    </select>
                  </div>
                </div>
                <button type="button" className="btn-remove" onClick={() => removeTool(idx)}>
                  Remove
                </button>
              </div>
            ))}
          </div>
          <button type="button" className="btn-add" onClick={addTool}>
            + Add Tool
          </button>
        </section>

        {/* MCP Servers */}
        <section className="form-section">
          <h2>MCP Servers</h2>
          {renderFieldErrors('mcp_servers')}
          <div className="dynamic-entries">
            {mcpServers.map((server, idx) => (
              <div key={idx} className="dynamic-entry">
                <div className="entry-fields">
                  <div className="form-field">
                    <label>Name</label>
                    <input
                      type="text"
                      value={server.name}
                      onChange={(e) => updateMcpServer(idx, 'name', e.target.value)}
                      placeholder="Server name"
                    />
                  </div>
                  <div className="form-field">
                    <label>Command</label>
                    <input
                      type="text"
                      value={server.command}
                      onChange={(e) => updateMcpServer(idx, 'command', e.target.value)}
                      placeholder="npx"
                    />
                  </div>
                  <div className="form-field full-width">
                    <label>Args (comma-separated)</label>
                    <input
                      type="text"
                      value={server.args}
                      onChange={(e) => updateMcpServer(idx, 'args', e.target.value)}
                      placeholder="-y, @modelcontextprotocol/server-filesystem"
                    />
                  </div>
                  <div className="form-field full-width">
                    <label>Environment Variables</label>
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
                        + Add env var
                      </button>
                    </div>
                  </div>
                </div>
                <button type="button" className="btn-remove" onClick={() => removeMcpServer(idx)}>
                  Remove
                </button>
              </div>
            ))}
          </div>
          <button type="button" className="btn-add" onClick={addMcpServer}>
            + Add MCP Server
          </button>
        </section>

        {/* Multi-Agent */}
        <section className="form-section">
          <h2>Multi-Agent Subagents</h2>
          <p className="section-hint">
            Sub-agents this agent can spawn via the multi-agent tool (max 16, unique names).
          </p>
          {renderFieldErrors('multi_agents')}
          <SubagentEditor value={subagents} onChange={setSubagents} />
        </section>

        {/* Submit */}
        <div className="form-actions">
          <button type="submit" className="btn-submit" disabled={submitting}>
            {submitting ? 'Creating…' : 'Create Agent'}
          </button>
        </div>
      </form>
    </div>
  );
}
