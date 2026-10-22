import { useTranslation } from 'react-i18next';
import './ErrorMessage.css';

interface ErrorMessageProps {
  status: number;
  message: string;
  onRetry?: () => void;
}

export default function ErrorMessage({ status, message, onRetry }: ErrorMessageProps) {
  const { t } = useTranslation();
  return (
    <div className="error-message" data-testid="error-message" role="alert">
      <span className="error-message-status" data-testid="error-status">
        {status ? t('common:errorWithStatus', { status }) : t('common:connectionError')}
      </span>
      <span className="error-message-text" data-testid="error-text">
        {message}
      </span>
      {onRetry && (
        <button
          className="error-message-retry"
          onClick={onRetry}
          data-testid="error-retry"
        >
          {t('common:retry')}
        </button>
      )}
    </div>
  );
}
