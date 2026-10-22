# Requirements Document

## Introduction

Linchpin Console is a web-based management UI for the Linchpin managed-agent runtime. It provides a single-page application (SPA) that communicates with the existing Linchpin API (port 8000) to let developers create and manage agents, environments, and sessions, and view real-time session events via SSE. The console is deployed as a new service in the existing docker-compose setup and is inspired by Anthropic's Claude Console for managed agents. The console targets developers who prefer a visual interface over raw API calls for day-to-day agent management.

## Glossary

- **Console**: The React-based single-page application that serves as the web frontend for Linchpin.
- **Linchpin_API**: The existing FastAPI backend service exposing REST and SSE endpoints on port 8000.
- **Sidebar**: The persistent left-hand navigation panel present on all Console pages, containing links to all major sections.
- **Agent_List_View**: The page displaying a filterable, sortable table of all Agent resources.
- **Agent_Detail_View**: The page displaying the full configuration of a single Agent resource.
- **Agent_Create_Form**: The form or wizard used to create a new Agent resource.
- **Environment_List_View**: The page displaying a filterable table of all Environment resources.
- **Environment_Create_Form**: The form used to create a new Environment resource.
- **Environment_Detail_View**: The page displaying the full configuration of a single Environment resource, including resource spec, networking, packages, and advanced settings.
- **Session_List_View**: The page displaying a filterable, sortable table of all Session resources.
- **Session_Detail_View**: The page displaying a single session's metadata, status, and real-time event stream.
- **Event_Stream_Panel**: The component within Session_Detail_View that renders session events in real time via SSE.
- **Status_Badge**: A visual indicator (colored label) representing the current status of a resource (e.g., running, idle, terminated, failed, active).
- **API_Client**: The frontend HTTP client module that handles all communication with Linchpin_API, including authentication headers and error handling.
- **SSE_Client**: The frontend module that manages Server-Sent Events connections for real-time session event streaming.

## Requirements

### Requirement 1: Console Service Deployment

**User Story:** As a developer, I want the console to be deployable alongside the existing Linchpin services via docker-compose, so that I can run the full stack with a single command.

#### Acceptance Criteria

1. THE Console SHALL be defined as a new service named `linchpin-console` in the existing docker-compose.yml file.
2. THE Console SHALL be served on port 3000 and accessible at `http://localhost:3000`.
3. THE Console SHALL be built as a static SPA using React with TypeScript and a modern build tool (Vite).
4. THE Console SHALL communicate with the Linchpin_API via a configurable API base URL environment variable (`VITE_API_URL`), defaulting to `http://localhost:8000`.
5. WHEN docker-compose up is executed, THE Console service SHALL start after the linchpin-api service is available.

### Requirement 2: API Client and Authentication

**User Story:** As a developer, I want the console to authenticate with the Linchpin API using my API key, so that I can securely manage my resources.

#### Acceptance Criteria

1. THE Console SHALL provide an API key input on first load when no API key is stored, allowing the developer to enter the Linchpin API key.
2. WHEN an API key is provided, THE API_Client SHALL include the key as a Bearer token in the Authorization header of every request to Linchpin_API.
3. THE Console SHALL persist the API key in browser localStorage so the developer does not need to re-enter the key on subsequent visits.
4. IF the Linchpin_API returns an HTTP 401 response, THEN THE Console SHALL clear the stored API key and redirect the developer to the API key input.
5. THE Console SHALL provide a mechanism in the Sidebar or settings to clear the stored API key and enter a new one.

### Requirement 3: Sidebar Navigation

**User Story:** As a developer, I want a persistent sidebar for navigating between console sections, so that I can quickly switch between managing agents, environments, and sessions.

#### Acceptance Criteria

1. THE Sidebar SHALL be visible on all Console pages.
2. THE Sidebar SHALL contain navigation links to the following sections: Agents, Sessions, Environments.
3. THE Sidebar SHALL visually indicate the currently active section by highlighting the corresponding navigation link.
4. THE Sidebar SHALL display the Linchpin product name or logo at the top.
5. THE Sidebar SHALL include a link to the Linchpin API health endpoint or a visual health status indicator.

### Requirement 4: Agent List View

**User Story:** As a developer, I want to see a table of all my agents with key details, so that I can browse and manage my agent configurations.

#### Acceptance Criteria

1. WHEN the developer navigates to the Agents section, THE Agent_List_View SHALL fetch and display a table of Agent resources from `GET /v1/agents`.
2. THE Agent_List_View SHALL display the following columns for each agent: ID, Name, Model (provider and model id), Created (timestamp).
3. THE Agent_List_View SHALL provide a search input that filters agents by ID.
4. THE Agent_List_View SHALL provide a "New agent" button that navigates to the Agent_Create_Form.
5. WHEN the developer clicks on an agent row, THE Console SHALL navigate to the Agent_Detail_View for that agent.
6. WHEN the agent list exceeds one page, THE Agent_List_View SHALL support pagination by fetching additional pages from the API.

### Requirement 5: Agent Detail View

**User Story:** As a developer, I want to view the full configuration of an agent, so that I can inspect its model, system prompt, tools, and MCP servers.

#### Acceptance Criteria

1. WHEN the developer navigates to an agent detail page, THE Agent_Detail_View SHALL fetch and display the full Agent resource from `GET /v1/agents/{id}`.
2. THE Agent_Detail_View SHALL display the agent's name, ID, version, model configuration (provider, model id, base_url if present), system prompt, tools list with permissions, MCP server configurations, and created_at timestamp.
3. THE Agent_Detail_View SHALL provide a back navigation link to the Agent_List_View.
4. IF the requested agent ID does not exist, THEN THE Agent_Detail_View SHALL display a "not found" error message.

### Requirement 6: Agent Creation

**User Story:** As a developer, I want to create a new agent through the console, so that I can define agent configurations without using the API directly.

#### Acceptance Criteria

1. THE Agent_Create_Form SHALL provide input fields for: name (text), model provider (select: anthropic, openai, ollama), model id (text), base_url (text, shown only when provider is ollama), and system prompt (textarea).
2. THE Agent_Create_Form SHALL allow adding one or more tool entries, each with a type (builtin or custom), name, and permission policy (always_allow or always_ask).
3. THE Agent_Create_Form SHALL allow adding one or more MCP server entries, each with name, command, args, and env key-value pairs.
4. WHEN the developer submits a valid agent creation form, THE Console SHALL send a `POST /v1/agents` request and navigate to the Agent_Detail_View for the newly created agent.
5. IF the Linchpin_API returns a validation error (HTTP 422), THEN THE Agent_Create_Form SHALL display the error details inline near the relevant fields.
6. THE Agent_Create_Form SHALL disable the submit button while a creation request is in flight to prevent duplicate submissions.

### Requirement 7: Environment List View

**User Story:** As a developer, I want to see a table of all my environments, so that I can browse and manage my container templates.

#### Acceptance Criteria

1. WHEN the developer navigates to the Environments section, THE Environment_List_View SHALL fetch and display a table of Environment resources from `GET /v1/environments`.
2. THE Environment_List_View SHALL display the following columns for each environment: ID, Name, Spec tier (1c2g, 2c4g, or 4c8g), Networking mode (none, limited, or unrestricted), Created (timestamp).
3. THE Environment_List_View SHALL provide a "New environment" button that navigates to the Environment_Create_Form.
4. WHEN the developer clicks on an environment row, THE Console SHALL navigate to a detail view displaying the full Environment resource.
5. WHEN the environment list exceeds one page, THE Environment_List_View SHALL support pagination by fetching additional pages from the API.
6. THE Environment_Detail_View SHALL display the resource spec tier, networking mode (including the allowed-hosts list and curated-domain flags when networking is limited), pre-installed packages per package manager, isolation runtime, tool search settings, and executor node (or "Local" when unset).
7. THE Environment_Detail_View SHALL provide an "Archive" action behind a confirmation dialog that sends `POST /v1/environments/{id}/archive`; WHEN the archive succeeds, THE Environment_Detail_View SHALL display an "Archived" badge and the archived timestamp, and SHALL hide the Archive action.
8. THE Environment_Detail_View SHALL provide a "Delete" action behind a confirmation dialog that sends `DELETE /v1/environments/{id}` and navigates to the Environment_List_View on success; IF the API returns HTTP 409 because non-terminated sessions still reference the environment, THEN THE Console SHALL display an error instructing the developer to terminate dependent sessions or archive the environment instead.

Depends on the Atlas upgrade (FR-4.3/FR-4.5/FR-4.6, v0.2.0 item #3/#4): the environment configuration surface is the full `EnvironmentConfig` payload — networking (none / limited with `allowed_hosts`, `allow_mcp_servers`, `allow_package_managers` / unrestricted), resources (`tier`), packages (apt, pip, npm, cargo, gem, go), isolation (docker, microvm), tool search, and executor node. Environments are immutable after creation (no PATCH endpoint), so configuration happens at creation time and the configuration surface of the detail view is read-only; lifecycle actions (archive, delete) remain available.

### Requirement 8: Environment Creation

**User Story:** As a developer, I want to create a new environment through the console, so that I can define container templates without using the API directly.

#### Acceptance Criteria

1. THE Environment_Create_Form SHALL provide input fields for: name (text), resources spec tier (select: 1c2g "Small — 1 vCPU / 2 GB RAM", 2c4g "Medium — 2 vCPU / 4 GB RAM", 4c8g "Large — 4 vCPU / 8 GB RAM"), and networking mode (select: none, limited, unrestricted).
2. WHEN the developer selects the limited networking mode, THE Environment_Create_Form SHALL additionally provide: allowed_hosts (textarea, one host per line, supporting wildcard subdomains), allow_mcp_servers (checkbox), and allow_package_managers (checkbox).
3. THE Environment_Create_Form SHALL provide pre-installed package inputs for each package manager (apt, pip, npm, cargo, gem, go) as comma-separated lists.
4. THE Environment_Create_Form SHALL provide advanced fields: isolation runtime (select: docker, microvm), tool search (checkbox with threshold and max-results numeric inputs when enabled), and executor node (select populated from `GET /v1/executor-nodes`, defaulting to local execution).
5. WHEN the developer submits a valid environment creation form, THE Console SHALL send a `POST /v1/environments` request containing the full `EnvironmentConfig` and navigate to the Environment_List_View.
6. IF the Linchpin_API returns a validation error (HTTP 422), THEN THE Environment_Create_Form SHALL display the error details to the developer.
7. THE Environment_Create_Form SHALL disable the submit button while a creation request is in flight to prevent duplicate submissions.

### Requirement 9: Session List View

**User Story:** As a developer, I want to see a table of all my sessions with filtering options, so that I can monitor and manage active and past sessions.

#### Acceptance Criteria

1. WHEN the developer navigates to the Sessions section, THE Session_List_View SHALL fetch and display a table of Session resources from `GET /v1/sessions`.
2. THE Session_List_View SHALL display the following columns for each session: ID, Title (or "Untitled" if null), Status (as a Status_Badge), Agent (agent_id), Created (timestamp).
3. THE Session_List_View SHALL provide a search input that filters sessions by ID.
4. THE Session_List_View SHALL provide a dropdown filter for agent_id that filters sessions by the selected agent using the `agent_id` query parameter on `GET /v1/sessions`.
5. THE Session_List_View SHALL provide a toggle to show or hide archived sessions.
6. THE Session_List_View SHALL provide a "New session" button that opens a session creation dialog or form.
7. WHEN the developer clicks on a session row, THE Console SHALL navigate to the Session_Detail_View for that session.
8. WHEN the session list exceeds one page, THE Session_List_View SHALL support pagination by fetching additional pages from the API.

### Requirement 10: Session Creation

**User Story:** As a developer, I want to create a new session through the console by selecting an agent and environment, so that I can start an agent interaction visually.

#### Acceptance Criteria

1. THE session creation form SHALL provide a dropdown to select an existing Agent (fetched from `GET /v1/agents`).
2. THE session creation form SHALL provide a dropdown to select an existing Environment (fetched from `GET /v1/environments`).
3. THE session creation form SHALL provide optional input fields for: title (text) and TTL in seconds (number).
4. WHEN the developer submits a valid session creation form, THE Console SHALL send a `POST /v1/sessions` request and navigate to the Session_Detail_View for the newly created session.
5. IF the Linchpin_API returns an error, THEN THE session creation form SHALL display the error message to the developer.

### Requirement 11: Session Detail View

**User Story:** As a developer, I want to view a session's details and interact with it in real time, so that I can monitor agent behavior and send messages.

#### Acceptance Criteria

1. WHEN the developer navigates to a session detail page, THE Session_Detail_View SHALL fetch and display the Session resource from `GET /v1/sessions/{id}`.
2. THE Session_Detail_View SHALL display the session's ID, title, status (as a Status_Badge), agent_id, environment_id, created_at, updated_at, container_id, stats (total_events, tool_calls, model_turns), and usage (input_tokens, output_tokens).
3. THE Session_Detail_View SHALL include the Event_Stream_Panel showing the session's events.
4. THE Session_Detail_View SHALL provide a back navigation link to the Session_List_View.
5. IF the requested session ID does not exist, THEN THE Session_Detail_View SHALL display a "not found" error message.

### Requirement 12: Real-Time Event Streaming

**User Story:** As a developer, I want to see session events appear in real time, so that I can observe the agent's reasoning, tool calls, and responses as they happen.

#### Acceptance Criteria

1. WHEN the developer opens a Session_Detail_View for a non-terminated session, THE SSE_Client SHALL open an SSE connection to `GET /v1/sessions/{id}/stream`.
2. WHEN the SSE_Client receives an event, THE Event_Stream_Panel SHALL append the event to the displayed event list in chronological order.
3. THE Event_Stream_Panel SHALL visually distinguish between event types: user messages, agent messages, agent thinking, tool use, tool results, session status changes, and errors.
4. WHEN the developer navigates away from the Session_Detail_View, THE SSE_Client SHALL close the SSE connection.
5. IF the SSE connection is lost, THEN THE SSE_Client SHALL attempt to reconnect using the last received cursor for seamless replay.
6. THE Event_Stream_Panel SHALL auto-scroll to the latest event as new events arrive, unless the developer has manually scrolled up.

### Requirement 13: Sending Messages to a Session

**User Story:** As a developer, I want to send messages to a running session from the console, so that I can interact with the agent through the UI.

#### Acceptance Criteria

1. THE Session_Detail_View SHALL provide a text input and send button for composing messages to the session.
2. WHEN the developer submits a message, THE Console SHALL send a `POST /v1/sessions/{id}/events` request with a `user.message` event containing the message text.
3. WHILE a message is being sent, THE Console SHALL disable the send button to prevent duplicate submissions.
4. IF the Linchpin_API returns an error when posting an event, THEN THE Console SHALL display the error message near the input field.
5. WHEN a session is in terminated or failed status, THE message input SHALL be disabled and THE Console SHALL display a message indicating the session is no longer active.

### Requirement 14: Session Lifecycle Actions

**User Story:** As a developer, I want to terminate and archive sessions from the console, so that I can manage session lifecycle without using the API directly.

#### Acceptance Criteria

1. THE Session_Detail_View SHALL provide a "Terminate" button for sessions that are not in terminated or failed status.
2. WHEN the developer clicks the "Terminate" button, THE Console SHALL send a `DELETE /v1/sessions/{id}` request and update the displayed session status to terminated.
3. THE Session_Detail_View SHALL provide an "Archive" button for sessions that are not already archived.
4. WHEN the developer clicks the "Archive" button, THE Console SHALL send a `POST /v1/sessions/{id}/archive` request and update the displayed session to show the archived state.
5. WHEN a destructive action (terminate) is requested, THE Console SHALL display a confirmation dialog before executing the action.

### Requirement 15: Tool Confirmation Handling

**User Story:** As a developer, I want to approve or reject tool calls that require confirmation, so that I can control agent actions that have the always_ask permission policy.

#### Acceptance Criteria

1. WHEN the Event_Stream_Panel receives a `session.requires_action` event, THE Console SHALL display the pending tool call details (tool name, arguments) with "Approve" and "Reject" buttons.
2. WHEN the developer clicks "Approve", THE Console SHALL send a `POST /v1/sessions/{id}/events` request with a `user.tool_confirmation` event containing an approval payload.
3. WHEN the developer clicks "Reject", THE Console SHALL send a `POST /v1/sessions/{id}/events` request with a `user.tool_confirmation` event containing a rejection payload.
4. WHILE a tool confirmation is pending, THE Console SHALL visually highlight the pending action in the Event_Stream_Panel.

### Requirement 16: Event History Loading

**User Story:** As a developer, I want to view the full event history of a session, so that I can review past interactions even after reconnecting.

#### Acceptance Criteria

1. WHEN the developer opens a Session_Detail_View, THE Console SHALL fetch existing events from `GET /v1/sessions/{id}/events` and display them in the Event_Stream_Panel before connecting to the SSE stream.
2. WHEN the event history exceeds one page, THE Event_Stream_Panel SHALL support loading older events by fetching additional pages using cursor-based pagination.
3. THE Event_Stream_Panel SHALL merge historical events and live SSE events into a single chronological list without duplicates.

### Requirement 17: Status Badges and Visual Indicators

**User Story:** As a developer, I want clear visual indicators for resource statuses, so that I can quickly assess the state of my agents, environments, and sessions.

#### Acceptance Criteria

1. THE Status_Badge component SHALL render distinct visual styles (color and label) for each session status: running (green), idle (blue), rescheduling (yellow), terminated (gray), failed (red).
2. THE Status_Badge component SHALL be used consistently across Session_List_View and Session_Detail_View.
3. THE Console SHALL display loading spinners or skeleton placeholders while data is being fetched from the API.
4. THE Console SHALL display user-friendly error messages when API requests fail, including the HTTP status code and error detail from the API response.

### Requirement 18: Responsive Layout

**User Story:** As a developer, I want the console to be usable on different screen sizes, so that I can manage agents from various devices.

#### Acceptance Criteria

1. THE Console SHALL use a responsive layout that adapts to viewport widths from 1024px to 1920px.
2. WHILE the viewport width is below 1024px, THE Sidebar SHALL collapse to an icon-only mode or a hamburger menu.
3. THE Console SHALL use a consistent design system with a neutral color palette, readable typography, and adequate spacing between interactive elements.

### Requirement 19: Error Handling and Loading States

**User Story:** As a developer, I want the console to handle errors gracefully and show loading states, so that I always understand what is happening.

#### Acceptance Criteria

1. WHEN an API request is in progress, THE Console SHALL display a loading indicator appropriate to the context (spinner for page loads, inline indicator for form submissions).
2. IF an API request fails with a network error, THEN THE Console SHALL display a "Connection error" message with a retry option.
3. IF an API request fails with an HTTP error, THEN THE Console SHALL display the error message from the API response body.
4. THE Console SHALL not display raw stack traces or internal error details to the developer.

### Requirement 20: CORS Configuration

**User Story:** As a developer, I want the Linchpin API to accept requests from the console origin, so that the browser does not block API calls.

#### Acceptance Criteria

1. THE Linchpin_API SHALL be configured with CORS middleware that allows requests from the Console origin (default `http://localhost:3000`).
2. THE Linchpin_API CORS configuration SHALL allow the Authorization header in cross-origin requests.
3. THE Linchpin_API CORS configuration SHALL be configurable via an environment variable (`CORS_ALLOWED_ORIGINS`) to support custom deployment URLs.

### Requirement 21: Deployment Management

**User Story:** As a developer, I want to manage scheduled deployments (cron-driven agent runs) through the console, so that I can create, pause, and inspect recurring agent tasks without using the API directly.

#### Acceptance Criteria

1. WHEN the developer navigates to the Deployments section, THE Deployment_List_View SHALL fetch and display deployments from `GET /v1/deployments`, showing for each: agent, environment, cron expression, timezone, status (as a Status_Badge), and next run time.
2. THE Deployment_List_View SHALL provide a "New deployment" button that navigates to the Deployment_Create_Form, and each deployment row SHALL navigate to the Deployment_Detail_View when clicked.
3. THE Deployment_Create_Form SHALL provide: an agent select (populated from `GET /v1/agents`), an environment select (populated from `GET /v1/environments`), a cron expression input, a timezone input, and a repeatable group of initial events where each entry has a type and JSON content.
4. THE Deployment_Create_Form SHALL disable the submit button until an agent, an environment, and a valid cron expression are provided, and SHALL navigate to the Deployment_Detail_View for the newly created deployment on success.
5. THE Deployment_Detail_View SHALL display the deployment's full configuration and its run history from `GET /v1/deployments/{id}/runs`.
6. THE Deployment_Detail_View SHALL provide Pause and Resume actions that toggle the deployment via the API and update the displayed status.
7. THE Deployment_Detail_View SHALL provide an "Archive" action behind a confirmation dialog that archives the deployment and displays an "Archived" badge.

### Requirement 22: Webhook Management

**User Story:** As a developer, I want to manage webhook endpoints through the console, so that I can subscribe to Linchpin events and inspect deliveries without using the API directly.

#### Acceptance Criteria

1. WHEN the developer navigates to the Webhooks section, THE Webhook_List_View SHALL fetch and display webhook endpoints from `GET /v1/webhook_endpoints`, showing for each: URL, subscribed event count, enabled/disabled status, and created timestamp.
2. THE Webhook_Create_Form SHALL provide a URL input and a checkbox group of subscribable event types, and SHALL display the signing secret in plain text exactly once after creation succeeds.
3. THE Webhook_Detail_View SHALL display the endpoint's configuration, subscribed events, and status, and SHALL allow dismissing the one-time secret banner; the secret SHALL NOT be retrievable from the API afterwards.
4. THE Webhook_Detail_View SHALL provide a "Rotate secret" action that sends a PATCH with `rotate_secret: true` and displays the new one-time secret in plain text.
5. THE Webhook_Detail_View SHALL provide Enable/Disable toggle actions and a "Delete" action behind a confirmation dialog that sends `DELETE /v1/webhook_endpoints/{id}`.
6. THE Webhook_Detail_View SHALL fetch and display recent deliveries from `GET /v1/webhook_endpoints/{id}/deliveries` with a manual refresh control.

### Requirement 23: Skills Management

**User Story:** As a developer, I want to manage agent skill bundles through the console, so that I can upload, inspect, and remove skills without using the API directly.

#### Acceptance Criteria

1. WHEN the developer navigates to the Skills section, THE Skills_View SHALL fetch and display installed skills from `GET /v1/skills`, showing for each: name, description, version, and created timestamp.
2. THE Skills_View SHALL provide an upload control that sends the selected skill bundle as a multipart upload to the API.
3. WHEN an uploaded skill has the same name as an existing skill, THE Console SHALL inform the developer that the existing skill will be replaced (upsert) before the upload proceeds.
4. THE Skills_View SHALL provide a "Delete" action behind a confirmation dialog for each installed skill.

### Requirement 24: API Keys and Project Quota Management

**User Story:** As a developer, I want to manage API keys and project quotas through the console, so that I can control access to the Linchpin API without using the API directly.

#### Acceptance Criteria

1. WHEN the developer navigates to the API Keys section, THE API_Keys_View SHALL fetch and display API keys from `GET /v1/api-keys`, showing for each: name, key prefix, status, created timestamp, and last used timestamp.
2. THE API_Keys_View SHALL provide a creation form (name and scopes) and SHALL display the full key in plain text exactly once after creation succeeds; the key SHALL NOT be retrievable from the API afterwards.
3. THE API_Keys_View SHALL provide a "Revoke" action behind a confirmation dialog that revokes the key and updates the displayed status to revoked.
4. THE API_Keys_View SHALL display and allow editing the project's request quota; WHEN the quota field is submitted empty, THE Console SHALL send an explicit null to clear the quota, and the UI SHALL confirm the update.

### Requirement 25: Memory Store Management

**User Story:** As a developer, I want to create and manage memory stores and their memories through the console, so that I can inspect, edit, redact, and clean up agent memory without using the API directly.

#### Acceptance Criteria

1. WHEN the developer navigates to the Memory Stores section, THE Memory_Stores_View SHALL fetch and display memory stores from `GET /v1/memory-stores`, showing for each: name, description, created timestamp, and archived state; BY DEFAULT archived stores SHALL be hidden, WITH an "Show archived" toggle that reveals them.
2. THE Memory_Stores_View SHALL provide a creation form (name required, optional description) and SHALL list the new store after `POST /v1/memory-stores` succeeds.
3. WHEN the developer opens a store, THE Store_Detail_View SHALL show the store metadata (name, id, created, updated, archived-at when archived), an editable description with explicit Save/Cancel actions, and an "Archive" action behind a confirmation dialog; WHEN archived, THE view SHALL display an Archived badge and hide the edit and archive actions.
4. THE Store_Detail_View SHALL list the store's memories from `GET /v1/memory-stores/{id}/memories` in a table (path, size, updated timestamp) and SHALL provide a "New memory" form (path and content, both required with the submit disabled while empty).
5. WHEN the developer expands a memory row, THE Store_Detail_View SHALL show the stored content and provide an inline editor whose Save issues `PUT /v1/memory-stores/{id}/memories/{path}`; the refreshed row and version history SHALL reflect the new size and a new version entry.
6. THE Store_Detail_View SHALL list the memory's version history (sequence, action, size, author) and SHALL provide a "Redact" action per version behind a dialog that records a reason; WHEN a version is redacted, THE row SHALL display a Redacted state with zero size and no further actions, and the memory entry SHALL disappear from the store's memory list.
7. THE Store_Detail_View SHALL provide a "Delete" action per memory behind a confirmation dialog that performs a soft delete (a delete version is recorded and the memory leaves the list), and a "Delete store" action behind a confirmation dialog that states the deletion is permanent and irreversible and navigates back to the list on success.

### Requirement 26: Audit Log View

**User Story:** As a developer, I want to browse and export the project's audit log through the console, so that I can trace every mutation performed through the API without querying the database directly.

#### Acceptance Criteria

1. WHEN the developer navigates to the Audit section, THE Audit_View SHALL fetch entries from `GET /v1/audit` and SHALL display them newest first in a table showing Time, Action, Resource type, Resource ID, Actor, and Detail, with the entry count rendered above the table.
2. THE Audit_View SHALL provide filters for from/to timestamps, resource type, action, and result limit (1–50000, default 500), applied only when the developer activates Apply, and a Reset action that restores the default filters and reloads the unfiltered view.
3. WHEN no entries match the active filters, THE Audit_View SHALL display an explicit empty state.
4. THE Audit_View SHALL provide a "Download NDJSON" action that exports the current filtered view as a newline-delimited JSON file download; WHEN the download fails, THE view SHALL display an error message.

### Requirement 27: Executor Node Management

**User Story:** As a developer, I want to register and remove executor nodes through the console, so that I can run sessions on my own infrastructure (BYO compute) without using the API directly.

#### Acceptance Criteria

1. WHEN the developer navigates to the Executor Nodes section, THE Executor_Nodes_View SHALL fetch and display nodes from `GET /v1/executor-nodes`, showing for each: name, URL, status, max concurrent sessions, token prefix, last heartbeat, and created timestamp; WHEN no nodes exist, THE view SHALL display an explicit empty state.
2. THE Executor_Nodes_View SHALL provide a registration form (name and daemon URL required with the submit disabled while either is empty, max concurrent sessions defaulting to 5); on success the view SHALL display a banner containing the daemon token in plain text exactly once, with a dismiss control, and the token SHALL NOT be retrievable afterwards.
3. THE Executor_Nodes_View SHALL provide a "Delete" action per node behind a confirmation dialog that states the daemon token becomes unusable; on confirmation the node SHALL be removed via `DELETE /v1/executor-nodes/{id}` and the list refreshed.
