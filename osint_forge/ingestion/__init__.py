"""Fixture-only and production-safe ingestion adapters."""

from osint_forge.ingestion.live_adapters import (
    AdapterHealth,
    GDELTPollingAdapter,
    IngestionAdapter,
    NormalizedOSINTEvent,
    RedditOfficialAPIAdapter,
    TelegramBotWebhookAdapter,
    XFilteredStreamAdapter,
)
from osint_forge.ingestion.live_pipeline import LiveIngestionRequest, LiveMapIngestionService
from osint_forge.ingestion.registry_inference import (
    DirectoryHub,
    RegistryProposal,
    SourceDiscoveryEngine,
    SourceGraphDiff,
    SourceGraphSnapshot,
)
from osint_forge.ingestion.source_registry import SourceRegistry, SourceRegistryEntry

__all__ = [
    "AdapterHealth",
    "DirectoryHub",
    "GDELTPollingAdapter",
    "IngestionAdapter",
    "NormalizedOSINTEvent",
    "RedditOfficialAPIAdapter",
    "RegistryProposal",
    "LiveIngestionRequest",
    "LiveMapIngestionService",
    "SourceRegistry",
    "SourceRegistryEntry",
    "SourceDiscoveryEngine",
    "SourceGraphDiff",
    "SourceGraphSnapshot",
    "TelegramBotWebhookAdapter",
    "XFilteredStreamAdapter",
]
