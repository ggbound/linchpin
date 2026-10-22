import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { apiClient, ApiError } from '../api/client';
import type { PostEventsRequest, SessionStatus } from '../types';
import './MessageInput.css';

interface MessageInputProps {
  sessionId: string;
  disabled: boolean;
  sessionStatus?: SessionStatus;
}

export default function MessageInput({ sessionId, disabled, sessionStatus }: MessageInputProps) {
  const { t } = useTranslation('messageInput');
  const [text, setText] = useState('');
  const [sending, setSending] = useState(false);
  const [interrupting, setInterrupting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const isRunning = sessionStatus === 'running';

  const postEvent = async (type: 'user.message' | 'user.steer' | 'user.interrupt', payload: Record<string, unknown>) => {
    const body: PostEventsRequest = { events: [{ type, payload }] };
    await apiClient.post(`/v1/sessions/${sessionId}/events`, body);
  };

  const handleSend = async () => {
    const trimmed = text.trim();
    if (!trimmed || sending || disabled) return;

    setSending(true);
    setError(null);

    try {
      // While the agent is mid-turn the A4 gate only accepts steers
      // (queued and injected at the next safe point), otherwise a normal message.
      await postEvent(isRunning ? 'user.steer' : 'user.message', { content: trimmed });
      setText('');
    } catch (err) {
      const apiErr = err as ApiError;
      setError(apiErr.message || t('sendFailed'));
    } finally {
      setSending(false);
    }
  };

  const handleInterrupt = async () => {
    if (interrupting || disabled) return;

    setInterrupting(true);
    setError(null);

    try {
      await postEvent('user.interrupt', text.trim() ? { reason: text.trim() } : {});
      setText('');
    } catch (err) {
      const apiErr = err as ApiError;
      setError(apiErr.message || t('interruptFailed'));
    } finally {
      setInterrupting(false);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  if (disabled) {
    return (
      <div className="message-input-container" data-testid="message-input-container">
        <div className="message-input-disabled-notice" data-testid="message-input-disabled-notice">
          {t('sessionInactive')}
        </div>
        <div className="message-input-row">
          <textarea
            className="message-input-field"
            disabled
            placeholder={t('sessionEnded')}
            data-testid="message-input-field"
          />
          <button className="message-input-send" disabled data-testid="message-input-send">
            {t('send')}
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="message-input-container" data-testid="message-input-container">
      {isRunning && (
        <div className="message-input-hint" data-testid="message-input-steer-hint">
          {t('steerHint')}
        </div>
      )}
      {error && (
        <div className="message-input-error" data-testid="message-input-error">
          {error}
        </div>
      )}
      <div className="message-input-row">
        <textarea
          className="message-input-field"
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={t('placeholder')}
          disabled={sending}
          rows={1}
          data-testid="message-input-field"
        />
        <button
          className="message-input-send"
          onClick={handleSend}
          disabled={sending || !text.trim()}
          data-testid="message-input-send"
        >
          {sending ? t('sending') : isRunning ? t('steer') : t('send')}
        </button>
        {isRunning && (
          <button
            className="message-input-interrupt"
            onClick={handleInterrupt}
            disabled={interrupting}
            data-testid="message-input-interrupt"
          >
            {interrupting ? t('interrupting') : t('interrupt')}
          </button>
        )}
      </div>
    </div>
  );
}
