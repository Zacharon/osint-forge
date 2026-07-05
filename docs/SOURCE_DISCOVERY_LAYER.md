# Source Discovery Layer

Directory hubs such as `https://start.me/p/L1rEYQ/osint4all`, OSINT Framework trees, and GitHub OSINT lists are source discovery inputs. They are not runtime event feeds.

```text
Source Discovery Layer
  -> disabled registry proposals
  -> reviewed source_registry.json entries
  -> ingestion connectors
  -> event pipeline
```

## What The Layer Does

`osint_forge.ingestion.registry_inference` provides a Source Discovery Engine that:

- extracts outbound links from directory-hub HTML
- preserves hub category context such as "News OSINT" or "Cyber threat intel"
- maps links into the OSINT Forge taxonomy
- estimates ingestion method: `api`, `rss`, `scrape`, or `manual`
- estimates update cadence, trust tier, and legal risk
- emits Source Registry-shaped entries that are disabled by default
- diffs snapshots so daily discovery jobs can detect new or recategorized tools

The output is proposal metadata. It does not enable collection.

## Safety Defaults

Generated proposals always use:

- `enabled: false`
- `collection_mode: disabled`
- `live_enabled_default: false`
- `live_capable: false`
- `operator_approval_required: true`
- `adapter: ManualRegistryReviewAdapter`

Promoting a proposal into `config/source_registry.json` still requires source-specific ToS/license review, rate-limit policy, PII retention policy, credentials handling if needed, and a tested ingestion adapter.

## Taxonomy

The inference engine maps directory links into:

- `news_events`
- `public_social`
- `cyber_threat_intel`
- `geospatial_intel`
- `maritime_aviation`
- `breach_leak_monitoring`
- `dark_web_index`
- `directory_meta`
- `unknown`

High-risk categories such as breach/leak monitoring and dark web indexes are forced to `manual` method proposals with high legal risk.

## CLI Usage

Use a locally captured hub page for deterministic registry bootstrap:

```powershell
python -m osint_forge.main discover-sources --hub-id osint4all --hub-kind startme --hub-url https://start.me/p/L1rEYQ/osint4all --html-file .\workspace\source-discovery\osint4all.html
```

Fetch a hub URL directly only as an operator-approved discovery job:

```powershell
python -m osint_forge.main discover-sources --hub-id osint4all --hub-kind startme --hub-url https://start.me/p/L1rEYQ/osint4all
```

Compare against a previous snapshot for daily discovery drift:

```powershell
python -m osint_forge.main discover-sources --hub-id osint4all --hub-kind startme --hub-url https://start.me/p/L1rEYQ/osint4all --html-file .\workspace\source-discovery\osint4all.html --previous-snapshot .\workspace\source-discovery\previous.json
```

## Architecture Constraint

Start.me pages are not stable APIs and may change structure or block scraping. Use them only for registry generation and enrichment. Do not put them into the live ingestion path.
