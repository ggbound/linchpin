/**
 * Map an audit resource (type + id) to the console route that shows it.
 * Returns null when the console has no page for that resource type.
 *
 * resource_type values mirror the backend audit writer call sites
 * (app/audit.py): agent, environment, session, deployment, vault,
 * credential, memory_store, memory, memory_version, skill, file,
 * api_key, executor_node, webhook_endpoint, project, git_repository.
 */

/** Resource types with a detail page — link straight to it. */
const DETAIL_ROUTES: Record<string, string> = {
  agent: '/agents',
  environment: '/environments',
  session: '/sessions',
  deployment: '/deployments',
  vault: '/vaults',
  memory_store: '/memory-stores',
  webhook_endpoint: '/webhooks',
  dream: '/dreams',
  skill: '/skills',
};

/** Resource types that only have a list page — link to the list. */
const LIST_ROUTES: Record<string, string> = {
  file: '/files',
  api_key: '/api-keys',
  executor_node: '/executor-nodes',
};

export function resourceHref(resourceType: string, resourceId: string): string | null {
  if (!resourceId) return null;
  const detailBase = DETAIL_ROUTES[resourceType];
  if (detailBase) return `${detailBase}/${resourceId}`;
  return LIST_ROUTES[resourceType] ?? null;
}
