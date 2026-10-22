import { createBrowserRouter, Navigate } from 'react-router-dom';
import AppLayout from './components/AppLayout';
import DashboardView from './pages/DashboardView';
import AgentListView from './pages/AgentListView';
import AgentCreateForm from './pages/AgentCreateForm';
import AgentDetailView from './pages/AgentDetailView';
import EnvironmentListView from './pages/EnvironmentListView';
import EnvironmentCreateForm from './pages/EnvironmentCreateForm';
import EnvironmentDetailView from './pages/EnvironmentDetailView';
import SessionListView from './pages/SessionListView';
import SessionDetailView from './pages/SessionDetailView';
import FilesView from './pages/FilesView';
import ArtifactsView from './pages/ArtifactsView';
import VaultListView from './pages/VaultListView';
import VaultDetailView from './pages/VaultDetailView';
import UsageView from './pages/UsageView';
import DeploymentListView from './pages/DeploymentListView';
import DeploymentCreateForm from './pages/DeploymentCreateForm';
import DeploymentDetailView from './pages/DeploymentDetailView';
import WebhookListView from './pages/WebhookListView';
import WebhookCreateForm from './pages/WebhookCreateForm';
import WebhookDetailView from './pages/WebhookDetailView';
import SkillListView from './pages/SkillListView';
import SkillDetailView from './pages/SkillDetailView';
import ApiKeysView from './pages/ApiKeysView';
import MemoryStoreListView from './pages/MemoryStoreListView';
import MemoryStoreDetailView from './pages/MemoryStoreDetailView';
import DreamListView from './pages/DreamListView';
import DreamCreateForm from './pages/DreamCreateForm';
import DreamDetailView from './pages/DreamDetailView';
import AuditView from './pages/AuditView';
import ExecutorNodeListView from './pages/ExecutorNodeListView';

export const router = createBrowserRouter([
  {
    element: <AppLayout />,
    children: [
      { path: '/', element: <DashboardView /> },
      { path: '/dashboard', element: <Navigate to="/" replace /> },
      { path: '/agents', element: <AgentListView /> },
      { path: '/agents/new', element: <AgentCreateForm /> },
      { path: '/agents/:id', element: <AgentDetailView /> },
      { path: '/environments', element: <EnvironmentListView /> },
      { path: '/environments/new', element: <EnvironmentCreateForm /> },
      { path: '/environments/:id', element: <EnvironmentDetailView /> },
      { path: '/sessions', element: <SessionListView /> },
      { path: '/sessions/:id', element: <SessionDetailView /> },
      { path: '/files', element: <FilesView /> },
      { path: '/artifacts', element: <ArtifactsView /> },
      { path: '/vaults', element: <VaultListView /> },
      { path: '/vaults/:id', element: <VaultDetailView /> },
      { path: '/deployments', element: <DeploymentListView /> },
      { path: '/deployments/new', element: <DeploymentCreateForm /> },
      { path: '/deployments/:id', element: <DeploymentDetailView /> },
      { path: '/webhooks', element: <WebhookListView /> },
      { path: '/webhooks/new', element: <WebhookCreateForm /> },
      { path: '/webhooks/:id', element: <WebhookDetailView /> },
      { path: '/skills', element: <SkillListView /> },
      { path: '/skills/:id', element: <SkillDetailView /> },
      { path: '/api-keys', element: <ApiKeysView /> },
      { path: '/memory-stores', element: <MemoryStoreListView /> },
      { path: '/memory-stores/:id', element: <MemoryStoreDetailView /> },
      { path: '/dreams', element: <DreamListView /> },
      { path: '/dreams/new', element: <DreamCreateForm /> },
      { path: '/dreams/:id', element: <DreamDetailView /> },
      { path: '/audit', element: <AuditView /> },
      { path: '/executor-nodes', element: <ExecutorNodeListView /> },
      { path: '/usage', element: <UsageView /> },
    ],
  },
]);
