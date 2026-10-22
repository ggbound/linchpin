# Model gateway (LiteLLM)

Linchpin's model layer ships a small set of provider adapters: `openrouter`
(OpenAI-compatible HTTP), `openai` (direct OpenAI API), and `ollama`. Instead
of adding one adapter per vendor, any other OpenAI-compatible upstream — a
LiteLLM gateway, a self-hosted vLLM/TGI deployment, or your own fine-tuned
model served behind an OpenAI-style API — is reached through the **same
`openrouter` adapter with a `base_url` override**.

```
agent.model = { provider: "openrouter", id: "<model-id>", base_url: "<gateway>" }
```

`OpenRouterProvider` simply POSTs to `<base_url>/chat/completions`
([providers.py](../linchpin-api/app/providers.py), `get_provider`), so any
endpoint that speaks the OpenAI chat-completions wire format works.

## Quick start with the bundled gateway

The repo's [docker-compose.yml](../docker-compose.yml) ships an optional
LiteLLM service behind the `litellm` profile:

```bash
export OPENROUTER_API_KEY=sk-or-...        # upstream credential LiteLLM uses
export LITELLM_MASTER_KEY=sk-linchpin      # key agents present to the gateway
docker compose --profile litellm up -d litellm
```

The gateway uses wildcard routing ([litellm.config.yaml](../litellm.config.yaml)):
whatever model id the agent requests is forwarded verbatim to the OpenRouter
upstream, so no per-model entries are required.

Create an agent that routes through the gateway:

```bash
curl -X POST http://localhost:8000/v1/agents \
  -H "Authorization: Bearer $LINCHPIN_API_KEY" -H "Content-Type: application/json" \
  -d '{
    "name": "my-agent",
    "model": {
      "provider": "openrouter",
      "id": "anthropic/claude-sonnet-4",
      "base_url": "http://litellm:4000/v1"
    }
  }'
```

> **Addressing inside Docker:** the Linchpin API resolves `base_url` from the
> API container, so use the compose service name (`http://litellm:4000/v1`).
> `http://localhost:4000/v1` only works when the API itself runs on your host
> (dev setup against ServBay Postgres, for example).

## API key semantics (unified key name)

Credential resolution is keyed off the **provider string, not the endpoint**
([orchestrator.py](../linchpin-api/app/orchestrator.py), `env_var_map`):

| Channel | `provider` | Env fallback | Vault credential |
|---|---|---|---|
| Direct OpenRouter | `openrouter` | `OPENROUTER_API_KEY` | `provider="openrouter"`, `api_key=<sk-or-...>` |
| Direct OpenAI | `openai` | `OPENAI_API_KEY` | `provider="openai"`, `api_key=<sk-...>` |
| Via LiteLLM gateway | `openrouter` | `OPENROUTER_API_KEY` | `provider="openrouter"`, `api_key=<LITELLM_MASTER_KEY or virtual key>` |
| Direct Ollama | `ollama` | none needed | — |

Key points:

- Walking through the gateway **does not introduce a new key name**. The env
  variable stays `OPENROUTER_API_KEY` — treat it as "the credential for the
  OpenAI-compatible channel".
- Vault credentials likewise match on `provider="openrouter"`; store the
  gateway key (master or a per-agent [virtual key](https://docs.litellm.ai/docs/proxy/virtual_keys))
  in a vault bound to the session and it takes precedence over the env var.
- The gateway authenticates upstream with its own `OPENROUTER_API_KEY`; agent
  keys (`LITELLM_MASTER_KEY` / virtual keys) are only checked against the
  gateway, enabling per-agent budgets and rate limits.

## Self-hosted / custom models

Add an explicit entry to `litellm.config.yaml` and restart the service:

```yaml
model_list:
  - model_name: my-model
    litellm_params:
      model: openai/my-model            # OpenAI-compatible upstream
      api_base: http://vllm:8000/v1     # your vLLM / TGI / custom server
      api_key: os.environ/MY_MODEL_API_KEY

  - model_name: "*"                     # keep the wildcard as a fallback
    litellm_params:
      model: "openrouter/*"
```

Then point the agent at it: `provider: "openrouter"`, `id: "my-model"`,
`base_url: "http://litellm:4000/v1"`. Upstream-specific env vars belong in the
`environment` block of the `litellm` service in docker-compose.yml.

For a single fixed endpoint you may also bypass the gateway entirely: set
`base_url` directly to your server (`http://vllm:8000/v1`) and store its key as
a vault credential with `provider="openrouter"` (or in `OPENROUTER_API_KEY`).

## Direct OpenAI

For the OpenAI API itself there is a dedicated adapter: `provider: "openai"`,
`id: <model-id>` (e.g. `gpt-4o`). The default endpoint is
`https://api.openai.com/v1`; a `base_url` override redirects it to any
OpenAI-style endpoint (proxies, gateways). Credentials resolve from a vault
credential with `provider="openai"` or the `OPENAI_API_KEY` env var — the same
flow as OpenRouter. Unlike `openrouter`, the adapter sends no
`HTTP-Referer` / `X-Title` headers.

## Ollama

Local Ollama needs no gateway: `provider: "ollama"`, `id: <local tag>`,
`base_url: http://<host>:11434` (defaults to `http://localhost:11434`).

## Observability

Model-call tracing (`span.model_request_*` events, OTel spans with
`gen_ai.*` attributes) is emitted by the Linchpin API itself and is unaffected
by which upstream serves the request — see [ATLAS_UPGRADE_PLAN.md](../ATLAS_UPGRADE_PLAN.md)
B15. LiteLLM additionally supports its own `callbacks: ["langfuse"]` in
`litellm_settings` if you want the gateway side traced as well.
