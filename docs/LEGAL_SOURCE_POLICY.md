# Legal Source Policy

OSINT Forge does not provide a general scraper. Every source must be governed by `config/source_registry.json`.

Directory hubs such as Start.me OSINT4ALL, OSINT Framework, and GitHub OSINT lists are governed separately as source discovery inputs. They may seed disabled registry proposals, but they are not live ingestion feeds and must not bypass `source_registry.json`.

Each registry entry declares:

- `source_id`
- `display_name`
- `adapter`
- `api_docs_url`
- `auth_required`
- `enabled`
- `legal_basis`
- `collection_mode`
- `rate_limit`
- `rate_limit_policy`
- `allowed_use`
- `pii_risk`
- `pii_policy`
- `license_notes`
- `storage_policy`
- `live_enabled_default`
- `live_capable`
- `operator_approval_required`
- `last_verified_at`
- `raw_payload_retention`
- `notes`

## Guardrails

- No private groups.
- No credential bypass.
- No scraping when official API or licensed access is required.
- No treating directory hubs as event feeds.
- Obey robots, ToS, API scopes, and rate limits.
- No doxxing.
- No individual targeting.
- No storing unnecessary PII.
- All live collection must be logged.
- Dashboard endpoints expose sanitized normalized events only.

## Disabled Sources

Reddit, Telegram, and X are registered as disabled scaffolds. Invoking them through live ingestion fails closed and logs `LIVE_DISABLED_SOURCE_ATTEMPT`.

Before enabling any disabled source, add a reviewed fetcher/client, credentials handling, source-specific quota policy, retention policy, and tests.

## Source Discovery Proposals

`osint_forge.ingestion.registry_inference` can generate disabled registry proposals from curated directory hubs. Generated entries are review artifacts only: `enabled`, `live_enabled_default`, and `live_capable` remain false, and `collection_mode` remains `disabled`.
