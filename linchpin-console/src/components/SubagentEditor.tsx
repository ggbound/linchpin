import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { apiClient } from '../api/client';
import type { Agent, Environment, SubagentConfig } from '../types';
import './SubagentEditor.css';

/** Server-side subagent name rule (models._validate_subagent_list). */
export const SUBAGENT_NAME_RE = /^[a-z][a-z0-9_-]{0,63}$/;
export const MAX_SUBAGENTS = 16;

interface SubagentEditorProps {
  value: SubagentConfig[];
  onChange: (next: SubagentConfig[]) => void;
  /** Exclude this agent from the picker (the parent itself). */
  selfAgentId?: string;
}

/**
 * Edits the multi_agents (subagent) list: name + target agent +
 * optional dedicated environment + description.
 */
export default function SubagentEditor({ value, onChange, selfAgentId }: SubagentEditorProps) {
  const { t } = useTranslation('subagentEditor');
  const [agents, setAgents] = useState<Agent[]>([]);
  const [environments, setEnvironments] = useState<Environment[]>([]);

  useEffect(() => {
    let cancelled = false;
    apiClient
      .get<{ data: Agent[] }>('/v1/agents', { limit: '100' })
      .then((res) => {
        if (!cancelled) setAgents(res.data ?? []);
      })
      .catch(() => {
        /* picker just stays empty */
      });
    apiClient
      .get<{ data: Environment[] }>('/v1/environments', { limit: '100' })
      .then((res) => {
        if (!cancelled) setEnvironments(res.data ?? []);
      })
      .catch(() => {
        /* picker just stays empty */
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const update = (idx: number, field: keyof SubagentConfig, val: string) => {
    onChange(
      value.map((s, i) =>
        i === idx
          ? {
              ...s,
              [field]:
                field === 'environment_id' || field === 'description'
                  ? val === ''
                    ? null
                    : val
                  : val,
            }
          : s,
      ),
    );
  };

  const addRow = () => {
    onChange([...value, { name: '', agent_id: '', environment_id: null, description: null }]);
  };

  const removeRow = (idx: number) => {
    onChange(value.filter((_, i) => i !== idx));
  };

  const atCapacity = value.length >= MAX_SUBAGENTS;

  return (
    <div className="subagent-editor" data-testid="subagent-editor">
      {value.map((sub, idx) => {
        const nameTaken =
          value.some((s, i) => i !== idx && s.name && s.name === sub.name);
        const nameInvalid = sub.name !== '' && !SUBAGENT_NAME_RE.test(sub.name);
        const selected = agents.find((a) => a.id === sub.agent_id);
        return (
          <div key={idx} className="subagent-row" data-testid="subagent-row">
            <div className="subagent-fields">
              <div className="form-field">
                <label>{t('common:name')}</label>
                <input
                  type="text"
                  value={sub.name}
                  onChange={(e) => update(idx, 'name', e.target.value)}
                  placeholder="researcher"
                  data-testid="subagent-name"
                />
                {nameInvalid && (
                  <span className="subagent-hint subagent-hint--error">
                    {t('nameRuleHint')}
                  </span>
                )}
                {nameTaken && (
                  <span className="subagent-hint subagent-hint--error">
                    {t('nameUnique')}
                  </span>
                )}
              </div>
              <div className="form-field">
                <label>{t('agent')}</label>
                <select
                  value={sub.agent_id}
                  onChange={(e) => update(idx, 'agent_id', e.target.value)}
                  data-testid="subagent-agent"
                >
                  <option value="">{t('selectAgent')}</option>
                  {agents
                    .filter((a) => a.id !== selfAgentId && !a.archived_at)
                    .map((a) => (
                      <option key={a.id} value={a.id}>
                        {a.name}
                      </option>
                    ))}
                </select>
                {selected && (
                  <span className="subagent-hint">{t('model', { model: selected.model.id })}</span>
                )}
              </div>
              <div className="form-field">
                <label>{t('environmentOptional')}</label>
                <select
                  value={sub.environment_id ?? ''}
                  onChange={(e) => update(idx, 'environment_id', e.target.value)}
                  data-testid="subagent-environment"
                >
                  <option value="">{t('inheritSessionEnvironment')}</option>
                  {environments
                    .filter((env) => !env.archived_at)
                    .map((env) => (
                      <option key={env.id} value={env.id}>
                        {env.name}
                      </option>
                    ))}
                </select>
              </div>
              <div className="form-field subagent-desc">
                <label>{t('common:description')}</label>
                <input
                  type="text"
                  value={sub.description ?? ''}
                  onChange={(e) => update(idx, 'description', e.target.value)}
                  placeholder={t('descriptionPlaceholder')}
                  data-testid="subagent-description"
                />
              </div>
            </div>
            <button
              type="button"
              className="subagent-remove"
              onClick={() => removeRow(idx)}
              data-testid="subagent-remove"
            >
              {t('remove')}
            </button>
          </div>
        );
      })}
      <button
        type="button"
        className="subagent-add"
        onClick={addRow}
        disabled={atCapacity}
        data-testid="subagent-add"
      >
        {t('addSubagent')}
      </button>
      {atCapacity && (
        <span className="subagent-hint">{t('maxSubagents', { count: MAX_SUBAGENTS })}</span>
      )}
    </div>
  );
}
