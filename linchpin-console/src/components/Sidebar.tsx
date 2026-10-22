import { useEffect, useState, useCallback } from 'react';
import { NavLink, useNavigate } from 'react-router-dom';
import { clearApiKey } from '../api/client';
import './Sidebar.css';

type HealthStatus = 'checking' | 'healthy' | 'unhealthy';

const API_BASE =
  (typeof import.meta !== 'undefined' && import.meta.env?.VITE_API_URL) ||
  'http://localhost:8000';

export default function Sidebar() {
  const navigate = useNavigate();
  const [health, setHealth] = useState<HealthStatus>('checking');

  const checkHealth = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/health`);
      setHealth(res.ok ? 'healthy' : 'unhealthy');
    } catch {
      setHealth('unhealthy');
    }
  }, []);

  useEffect(() => {
    checkHealth();
    const id = setInterval(checkHealth, 30_000);
    return () => clearInterval(id);
  }, [checkHealth]);

  const handleClearKey = () => {
    clearApiKey();
    navigate('/', { replace: true });
    window.location.reload();
  };

  return (
    <aside className="sidebar" data-testid="sidebar">
      <div className="sidebar-header">
        <svg className="sidebar-mark" viewBox="0 0 64 64" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="9" strokeLinecap="square">
          <path d="M18 8 L18 48" />
          <path d="M18 48 L40 48" />
          <circle cx="46" cy="48" r="7" fill="currentColor" stroke="none" />
        </svg>
        <span className="sidebar-logo">Linchpin</span>
      </div>

      <nav className="sidebar-nav">
        <NavLink
          to="/"
          end
          className={({ isActive }) =>
            `sidebar-nav-link${isActive ? ' active' : ''}`
          }
        >
          <svg className="sidebar-nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <rect x="3" y="3" width="7" height="9" />
            <rect x="14" y="3" width="7" height="5" />
            <rect x="14" y="12" width="7" height="9" />
            <rect x="3" y="16" width="7" height="5" />
          </svg>
          Overview
        </NavLink>

        <NavLink
          to="/agents"
          className={({ isActive }) =>
            `sidebar-nav-link${isActive ? ' active' : ''}`
          }
        >
          <svg className="sidebar-nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <circle cx="12" cy="12" r="3" />
            <path d="M12 1v4M12 19v4M4.22 4.22l2.83 2.83M16.95 16.95l2.83 2.83M1 12h4M19 12h4M4.22 19.78l2.83-2.83M16.95 7.05l2.83-2.83" />
          </svg>
          Agents
        </NavLink>

        <NavLink
          to="/sessions"
          className={({ isActive }) =>
            `sidebar-nav-link${isActive ? ' active' : ''}`
          }
        >
          <svg className="sidebar-nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
          </svg>
          Sessions
        </NavLink>

        <NavLink
          to="/files"
          className={({ isActive }) =>
            `sidebar-nav-link${isActive ? ' active' : ''}`
          }
        >
          <svg className="sidebar-nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M13 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z" />
            <polyline points="13 2 13 9 20 9" />
          </svg>
          Files
        </NavLink>

        <NavLink
          to="/artifacts"
          className={({ isActive }) =>
            `sidebar-nav-link${isActive ? ' active' : ''}`
          }
        >
          <svg className="sidebar-nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <line x1="16.5" y1="9.4" x2="7.5" y2="4.21" />
            <path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z" />
            <polyline points="3.27 6.96 12 12.01 20.73 6.96" />
            <line x1="12" y1="22.08" x2="12" y2="12" />
          </svg>
          Artifacts
        </NavLink>

        <NavLink
          to="/environments"
          className={({ isActive }) =>
            `sidebar-nav-link${isActive ? ' active' : ''}`
          }
        >
          <svg className="sidebar-nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <rect x="2" y="2" width="20" height="8" rx="2" ry="2" />
            <rect x="2" y="14" width="20" height="8" rx="2" ry="2" />
            <line x1="6" y1="6" x2="6.01" y2="6" />
            <line x1="6" y1="18" x2="6.01" y2="18" />
          </svg>
          Environments
        </NavLink>

        <NavLink
          to="/vaults"
          className={({ isActive }) =>
            `sidebar-nav-link${isActive ? ' active' : ''}`
          }
        >
          <svg className="sidebar-nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <rect x="3" y="11" width="18" height="11" rx="2" ry="2" />
            <path d="M7 11V7a5 5 0 0 1 10 0v4" />
          </svg>
          Credential Vaults
        </NavLink>

        <NavLink
          to="/memory-stores"
          className={({ isActive }) =>
            `sidebar-nav-link${isActive ? ' active' : ''}`
          }
        >
          <svg className="sidebar-nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <ellipse cx="12" cy="5" rx="9" ry="3" />
            <path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3" />
            <path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5" />
          </svg>
          Memory Stores
        </NavLink>

        <NavLink
          to="/dreams"
          className={({ isActive }) =>
            `sidebar-nav-link${isActive ? ' active' : ''}`
          }
        >
          <svg className="sidebar-nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M18 10h-1.26A8 8 0 1 0 9 20h9a5 5 0 0 0 0-10z" />
          </svg>
          Dreams
        </NavLink>

        <NavLink
          to="/deployments"
          className={({ isActive }) =>
            `sidebar-nav-link${isActive ? ' active' : ''}`
          }
        >
          <svg className="sidebar-nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <circle cx="12" cy="12" r="10" />
            <polyline points="12 6 12 12 16 14" />
          </svg>
          Deployments
        </NavLink>

        <NavLink
          to="/webhooks"
          className={({ isActive }) =>
            `sidebar-nav-link${isActive ? ' active' : ''}`
          }
        >
          <svg className="sidebar-nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z" />
          </svg>
          Webhooks
        </NavLink>

        <NavLink
          to="/skills"
          className={({ isActive }) =>
            `sidebar-nav-link${isActive ? ' active' : ''}`
          }
        >
          <svg className="sidebar-nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <polygon points="12 2 2 7 12 12 22 7 12 2" />
            <polyline points="2 17 12 22 22 17" />
            <polyline points="2 12 12 17 22 12" />
          </svg>
          Skills
        </NavLink>

        <NavLink
          to="/executor-nodes"
          className={({ isActive }) =>
            `sidebar-nav-link${isActive ? ' active' : ''}`
          }
        >
          <svg className="sidebar-nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <rect x="4" y="4" width="16" height="16" rx="2" ry="2" />
            <rect x="9" y="9" width="6" height="6" />
            <line x1="9" y1="1" x2="9" y2="4" />
            <line x1="15" y1="1" x2="15" y2="4" />
            <line x1="9" y1="20" x2="9" y2="23" />
            <line x1="15" y1="20" x2="15" y2="23" />
            <line x1="20" y1="9" x2="23" y2="9" />
            <line x1="20" y1="14" x2="23" y2="14" />
            <line x1="1" y1="9" x2="4" y2="9" />
            <line x1="1" y1="14" x2="4" y2="14" />
          </svg>
          Executor Nodes
        </NavLink>

        <NavLink
          to="/api-keys"
          className={({ isActive }) =>
            `sidebar-nav-link${isActive ? ' active' : ''}`
          }
        >
          <svg className="sidebar-nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M21 2l-2 2m-7.61 7.61a5.5 5.5 0 1 1-7.778 7.778 5.5 5.5 0 0 1 7.777-7.777zm0 0L15.5 7.5m0 0l3 3L22 7l-3-3m-3.5 3.5L19 4" />
          </svg>
          API Keys
        </NavLink>

        <NavLink
          to="/audit"
          className={({ isActive }) =>
            `sidebar-nav-link${isActive ? ' active' : ''}`
          }
        >
          <svg className="sidebar-nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
            <polyline points="14 2 14 8 20 8" />
            <line x1="16" y1="13" x2="8" y2="13" />
            <line x1="16" y1="17" x2="8" y2="17" />
            <polyline points="10 9 9 10 8 9" />
          </svg>
          Audit
        </NavLink>

        <NavLink
          to="/usage"
          className={({ isActive }) =>
            `sidebar-nav-link${isActive ? ' active' : ''}`
          }
        >
          <svg className="sidebar-nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <line x1="18" y1="20" x2="18" y2="10" />
            <line x1="12" y1="20" x2="12" y2="4" />
            <line x1="6" y1="20" x2="6" y2="14" />
          </svg>
          Usage
        </NavLink>
      </nav>

      <div className="sidebar-footer">
        <div className="sidebar-health" data-testid="health-indicator">
          <span className={`sidebar-health-dot ${health}`} />
          <span>
            API {health === 'checking' ? 'checking…' : health === 'healthy' ? 'connected' : 'unreachable'}
          </span>
        </div>
        <button
          className="sidebar-clear-key"
          onClick={handleClearKey}
          data-testid="clear-api-key"
        >
          Clear API key
        </button>
      </div>
    </aside>
  );
}
