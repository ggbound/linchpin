import { useState, type FormEvent } from 'react';
import { useTranslation } from 'react-i18next';
import { setApiKey } from '../api/client';
import './ApiKeyInput.css';

interface ApiKeyInputProps {
  onKeySet: () => void;
}

export default function ApiKeyInput({ onKeySet }: ApiKeyInputProps) {
  const { t } = useTranslation('apiKeyInput');
  const [key, setKey] = useState('');

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault();
    const trimmed = key.trim();
    if (!trimmed) return;
    setApiKey(trimmed);
    onKeySet();
  };

  return (
    <div className="api-key-screen">
      <form className="api-key-card" onSubmit={handleSubmit}>
        <h1>{t('consoleTitle')}</h1>
        <p>{t('subtitle')}</p>
        <label htmlFor="api-key-input">{t('apiKey')}</label>
        <input
          id="api-key-input"
          type="password"
          value={key}
          onChange={(e) => setKey(e.target.value)}
          placeholder="lp_key_..."
          autoFocus
        />
        <button type="submit" disabled={!key.trim()}>
          {t('connect')}
        </button>
      </form>
    </div>
  );
}
