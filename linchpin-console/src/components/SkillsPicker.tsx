import { useEffect, useState } from 'react';
import { apiClient, ApiError } from '../api/client';
import type { Skill, SkillListResponse } from '../types';

interface SkillsPickerProps {
  /** Currently selected skill names (skill bundle names, not ids). */
  value: string[];
  onChange: (next: string[]) => void;
  /** Prefix for data-testid attributes (default "skills-editor"). */
  testIdPrefix?: string;
  max?: number;
}

/**
 * Multi-select picker for agent skills, sourced from the skill library
 * (GET /v1/skills). Selected skills render as removable chips; new ones are
 * added via a dropdown of library skills not yet selected. Skills previously
 * attached to an agent but no longer present in the library still render as
 * removable chips so they can be kept or dropped explicitly.
 */
export default function SkillsPicker({
  value,
  onChange,
  testIdPrefix = 'skills-editor',
  max = 8,
}: SkillsPickerProps) {
  const [skills, setSkills] = useState<Skill[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [picked, setPicked] = useState('');

  useEffect(() => {
    let cancelled = false;
    apiClient
      .get<SkillListResponse>('/v1/skills?limit=100')
      .then((res) => {
        if (cancelled) return;
        setSkills(res.data.filter((s) => !s.archived_at));
        setLoadError(null);
      })
      .catch((err: ApiError) => {
        if (cancelled) return;
        setLoadError(err.message || 'Failed to load skill library');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const selected = new Set(value);
  const options = skills.filter((s) => !selected.has(s.name));
  const atMax = value.length >= max;

  const addPicked = () => {
    if (!picked || selected.has(picked) || atMax) return;
    onChange([...value, picked]);
    setPicked('');
  };

  const removeSkill = (name: string) => {
    onChange(value.filter((s) => s !== name));
  };

  return (
    <div data-testid={testIdPrefix}>
      {value.length > 0 && (
        <div className="skill-chip-row">
          {value.map((name) => (
            <span
              key={name}
              className="skill-chip"
              title={skills.some((s) => s.name === name) ? name : `${name} (not in skill library)`}
            >
              {name}
              <button
                type="button"
                className="skill-chip-remove"
                onClick={() => removeSkill(name)}
                aria-label={`Remove ${name}`}
                data-testid={`${testIdPrefix}-remove`}
              >
                ✕
              </button>
            </span>
          ))}
        </div>
      )}
      {loading ? (
        <p className="section-hint">Loading skill library…</p>
      ) : loadError ? (
        <p className="section-hint">Failed to load skill library — {loadError}</p>
      ) : skills.length === 0 ? (
        <p className="section-hint">
          No skills in the library yet — upload bundles on the Skills page first.
        </p>
      ) : (
        <div className="skill-input-row">
          <select
            value={picked}
            onChange={(e) => setPicked(e.target.value)}
            disabled={atMax}
            aria-label="Select a skill to add"
            data-testid={`${testIdPrefix}-select`}
          >
            <option value="">{atMax ? `Max ${max} skills selected` : 'Select a skill…'}</option>
            {options.map((s) => (
              <option key={s.name} value={s.name}>
                {s.name}
                {s.version ? ` (v${s.version})` : ''}
              </option>
            ))}
          </select>
          <button
            type="button"
            className="btn-add-sm"
            onClick={addPicked}
            disabled={!picked || atMax}
            data-testid={`${testIdPrefix}-add`}
          >
            + Add
          </button>
        </div>
      )}
      {atMax && <p className="section-hint">Maximum of {max} skills per agent.</p>}
    </div>
  );
}
