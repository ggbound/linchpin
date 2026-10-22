import type { SessionStatus } from '../types';
import { useTranslation } from 'react-i18next';
import './StatusBadge.css';

interface StatusBadgeProps {
  status: SessionStatus;
}

export default function StatusBadge({ status }: StatusBadgeProps) {
  const { t } = useTranslation();
  return (
    <span
      className={`status-badge status-badge--${status}`}
      data-testid="status-badge"
    >
      <span className="status-badge-dot" />
      {t(`status.${status}`, { defaultValue: String(status) })}
    </span>
  );
}
