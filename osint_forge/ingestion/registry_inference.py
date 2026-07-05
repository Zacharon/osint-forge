"""Source discovery and registry proposal inference.

Directory hubs such as Start.me OSINT pages and OSINT Framework trees are
source discovery inputs, not live feeds. This module turns those pointer graphs
into disabled registry proposals that still require human/legal review before
any ingestion adapter can use them.
"""

from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import date
from html.parser import HTMLParser
from typing import Any, Literal


HubKind = Literal["startme", "osint_framework", "github_list", "generic_directory"]
IngestionMethod = Literal["api", "rss", "scrape", "manual"]
LegalRisk = Literal["low", "medium", "high"]
OSINTDomain = Literal[
    "news_events",
    "public_social",
    "cyber_threat_intel",
    "geospatial_intel",
    "maritime_aviation",
    "breach_leak_monitoring",
    "dark_web_index",
    "directory_meta",
    "unknown",
]


@dataclass(frozen=True)
class DirectoryHub:
    hub_id: str
    url: str
    kind: HubKind = "generic_directory"
    trust_tier: int = 3
    runtime_feed_allowed: bool = False
    notes: str = "Human-curated source discovery hub; not a runtime event feed."

    def to_dict(self) -> dict[str, Any]:
        return {
            "hub_id": self.hub_id,
            "url": self.url,
            "kind": self.kind,
            "trust_tier": self.trust_tier,
            "runtime_feed_allowed": self.runtime_feed_allowed,
            "notes": self.notes,
        }


@dataclass(frozen=True)
class ExtractedSourceLink:
    title: str
    url: str
    hub_id: str
    hub_url: str
    category: str = "Uncategorized"
    context: str = ""

    @property
    def domain(self) -> str:
        return urllib.parse.urlparse(self.url).netloc.lower().removeprefix("www.")

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "url": self.url,
            "domain": self.domain,
            "hub_id": self.hub_id,
            "hub_url": self.hub_url,
            "category": self.category,
            "context": self.context,
        }


@dataclass(frozen=True)
class RegistryProposal:
    source_id: str
    display_name: str
    url: str
    source_type: OSINTDomain
    method: IngestionMethod
    update_interval_sec: int
    trust_tier: int
    legal_risk: LegalRisk
    legal: dict[str, Any]
    provenance: dict[str, Any]
    adapter: str = "ManualRegistryReviewAdapter"
    enabled: bool = False
    live_capable: bool = False
    review_required: bool = True
    rationale: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "display_name": self.display_name,
            "url": self.url,
            "source_type": self.source_type,
            "method": self.method,
            "update_interval_sec": self.update_interval_sec,
            "trust_tier": self.trust_tier,
            "legal_risk": self.legal_risk,
            "legal": dict(self.legal),
            "provenance": dict(self.provenance),
            "adapter": self.adapter,
            "enabled": self.enabled,
            "live_capable": self.live_capable,
            "review_required": self.review_required,
            "rationale": list(self.rationale),
        }

    def to_disabled_source_registry_entry(self, *, verified_at: str | None = None) -> dict[str, Any]:
        """Return a SourceRegistry-shaped disabled entry for review.

        The existing source registry is the enforcement boundary for live
        ingestion. Inferred entries deliberately fail closed until an operator
        replaces the manual adapter with a reviewed connector and enables it.
        """

        checked_at = verified_at or date.today().isoformat()
        auth_required = bool(self.legal.get("requires_api_key"))
        pii_risk = "high" if self.legal_risk == "high" else "medium" if self.source_type in {"public_social", "breach_leak_monitoring"} else "low"
        return {
            "source_id": self.source_id,
            "display_name": self.display_name,
            "adapter": self.adapter,
            "api_docs_url": self.url,
            "auth_required": auth_required,
            "enabled": False,
            "legal_basis": "Registry proposal only; disabled pending source-specific ToS, license, and allowed-use review.",
            "collection_mode": "disabled",
            "rate_limit": "Disabled; no collection.",
            "rate_limit_policy": _rate_limit_policy(self.method),
            "allowed_use": _allowed_use(self.source_type),
            "pii_risk": pii_risk,
            "pii_policy": "Proposal only; before enablement define minimization, retention, and dashboard exposure rules.",
            "license_notes": str(self.legal.get("toS_notes") or "Terms and license must be reviewed before enablement."),
            "storage_policy": "Proposal metadata only; do not store raw source payloads until a reviewed connector exists.",
            "live_enabled_default": False,
            "live_capable": False,
            "operator_approval_required": True,
            "last_verified_at": checked_at,
            "raw_payload_retention": "proposal only; no live payload collection",
            "notes": "Generated by Source Discovery Engine from a directory hub; not treated as a feed.",
        }


@dataclass(frozen=True)
class SourceGraphSnapshot:
    hub: DirectoryHub
    captured_at: str
    links: list[ExtractedSourceLink]
    proposals: list[RegistryProposal]

    def to_dict(self) -> dict[str, Any]:
        return {
            "hub": self.hub.to_dict(),
            "captured_at": self.captured_at,
            "links": [item.to_dict() for item in self.links],
            "proposals": [item.to_dict() for item in self.proposals],
            "registry_entries": [item.to_disabled_source_registry_entry() for item in self.proposals],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=True)


@dataclass(frozen=True)
class SourceGraphDiff:
    added_urls: list[str]
    removed_urls: list[str]
    changed_categories: list[dict[str, str]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "added_urls": list(self.added_urls),
            "removed_urls": list(self.removed_urls),
            "changed_categories": [dict(item) for item in self.changed_categories],
        }


class SourceDiscoveryError(Exception):
    """Raised when a directory hub cannot be fetched or parsed."""


class SourceDiscoveryEngine:
    def discover_from_html(self, hub: DirectoryHub, html: str, *, captured_at: str | None = None) -> SourceGraphSnapshot:
        links = extract_links_from_html(html, hub=hub, skip_same_host=hub.kind != "github_list")
        proposals = infer_registry_proposals(links)
        return SourceGraphSnapshot(hub=hub, captured_at=captured_at or date.today().isoformat(), links=links, proposals=proposals)

    def discover_from_url(self, hub: DirectoryHub, *, timeout_seconds: float = 10.0) -> SourceGraphSnapshot:
        request = urllib.request.Request(hub.url, headers={"User-Agent": "OSINT-Forge-SourceDiscovery/1.0"})
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                charset = response.headers.get_content_charset() or "utf-8"
                html = response.read().decode(charset, errors="replace")
        except Exception as exc:  # pragma: no cover - exercised by integration runs.
            raise SourceDiscoveryError(f"failed to fetch source discovery hub {hub.url}: {exc}") from exc
        return self.discover_from_html(hub, html)

    @staticmethod
    def diff_snapshots(previous: SourceGraphSnapshot, current: SourceGraphSnapshot) -> SourceGraphDiff:
        previous_by_url = {link.url: link for link in previous.links}
        current_by_url = {link.url: link for link in current.links}
        added = sorted(set(current_by_url) - set(previous_by_url))
        removed = sorted(set(previous_by_url) - set(current_by_url))
        changed: list[dict[str, str]] = []
        for url in sorted(set(previous_by_url) & set(current_by_url)):
            old = previous_by_url[url].category
            new = current_by_url[url].category
            if old != new:
                changed.append({"url": url, "previous_category": old, "current_category": new})
        return SourceGraphDiff(added_urls=added, removed_urls=removed, changed_categories=changed)


class _DirectoryLinkParser(HTMLParser):
    def __init__(self, hub: DirectoryHub, *, skip_same_host: bool) -> None:
        super().__init__(convert_charrefs=True)
        self.hub = hub
        self.skip_same_host = skip_same_host
        self.current_category = "Uncategorized"
        self._heading_tag: str | None = None
        self._heading_parts: list[str] = []
        self._anchor_href: str | None = None
        self._anchor_parts: list[str] = []
        self.links: list[ExtractedSourceLink] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = {name.lower(): value or "" for name, value in attrs}
        if tag in {"h1", "h2", "h3", "h4", "summary"}:
            self._heading_tag = tag
            self._heading_parts = []
        if tag == "a" and attrs_dict.get("href"):
            self._anchor_href = attrs_dict["href"]
            self._anchor_parts = []

    def handle_data(self, data: str) -> None:
        text = " ".join(data.split())
        if not text:
            return
        if self._heading_tag:
            self._heading_parts.append(text)
        if self._anchor_href:
            self._anchor_parts.append(text)

    def handle_endtag(self, tag: str) -> None:
        if self._heading_tag == tag:
            heading = " ".join(self._heading_parts).strip()
            if heading:
                self.current_category = heading[:120]
            self._heading_tag = None
            self._heading_parts = []
        if tag == "a" and self._anchor_href:
            self._append_anchor()
            self._anchor_href = None
            self._anchor_parts = []

    def _append_anchor(self) -> None:
        assert self._anchor_href is not None
        url = urllib.parse.urljoin(self.hub.url, self._anchor_href)
        url = _unwrap_directory_redirect(url, self.hub)
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            return
        if self.skip_same_host and parsed.netloc.lower() == urllib.parse.urlparse(self.hub.url).netloc.lower():
            return
        title = " ".join(self._anchor_parts).strip() or parsed.netloc
        self.links.append(
            ExtractedSourceLink(
                title=title[:160],
                url=url,
                hub_id=self.hub.hub_id,
                hub_url=self.hub.url,
                category=self.current_category,
                context=f"{self.current_category} {title}".strip(),
            )
        )


def extract_links_from_html(html: str, *, hub: DirectoryHub, skip_same_host: bool = True) -> list[ExtractedSourceLink]:
    parser = _DirectoryLinkParser(hub, skip_same_host=skip_same_host)
    parser.feed(html)
    deduped: dict[str, ExtractedSourceLink] = {}
    for link in parser.links:
        deduped.setdefault(_canonical_url(link.url), link)
    return [deduped[url] for url in sorted(deduped)]


def infer_registry_proposals(links: list[ExtractedSourceLink]) -> list[RegistryProposal]:
    proposals: list[RegistryProposal] = []
    used_ids: set[str] = set()
    for link in links:
        source_type, type_reason = infer_source_type(link)
        method, method_reason = infer_ingestion_method(link, source_type)
        legal_risk, legal = infer_legal(link, source_type, method)
        trust_tier, trust_reason = infer_trust_tier(link, source_type, legal_risk)
        source_id = _unique_source_id(link, used_ids)
        proposals.append(
            RegistryProposal(
                source_id=source_id,
                display_name=link.title,
                url=link.url,
                source_type=source_type,
                method=method,
                update_interval_sec=infer_update_interval_sec(source_type, method),
                trust_tier=trust_tier,
                legal_risk=legal_risk,
                legal=legal,
                provenance={
                    "discovered_from": link.hub_id,
                    "hub_url": link.hub_url,
                    "hub_category": link.category,
                    "source_url": link.url,
                    "source_domain": link.domain,
                },
                rationale=[type_reason, method_reason, trust_reason],
            )
        )
    return proposals


def infer_source_type(link: ExtractedSourceLink) -> tuple[OSINTDomain, str]:
    text = _fingerprint_text(link)
    for source_type, keywords in _DOMAIN_KEYWORDS:
        if any(keyword in text for keyword in keywords):
            return source_type, f"matched {source_type} keyword"
    return "unknown", "no strong taxonomy keyword matched"


def infer_ingestion_method(link: ExtractedSourceLink, source_type: OSINTDomain) -> tuple[IngestionMethod, str]:
    text = _fingerprint_text(link)
    if any(token in text for token in ("rss", "atom", "/feed", "feed.")):
        return "rss", "feed endpoint or RSS keyword detected"
    if any(token in text for token in (" api", "/api", "api.", "developer", "docs.")):
        return "api", "API or developer documentation keyword detected"
    if source_type in {"public_social", "dark_web_index", "directory_meta", "unknown"}:
        return "manual", f"{source_type} requires review before connector selection"
    if source_type == "breach_leak_monitoring":
        return "manual", "breach/leak sources require legal review before collection"
    return "scrape", "web source with no API/feed signal; proposal only"


def infer_legal(link: ExtractedSourceLink, source_type: OSINTDomain, method: IngestionMethod) -> tuple[LegalRisk, dict[str, Any]]:
    domain = link.domain
    requires_api_key = method == "api" or any(token in domain for token in ("shodan", "censys", "virustotal", "urlscan", "reddit", "x.com"))
    scraping_allowed = method in {"rss", "api"} and source_type not in {"public_social", "breach_leak_monitoring", "dark_web_index"}
    if source_type in {"dark_web_index", "breach_leak_monitoring"}:
        risk: LegalRisk = "high"
        notes = "High-risk proposal; only legal/public mirrors and explicit authorization may be considered."
    elif source_type == "public_social" or method == "scrape":
        risk = "medium"
        notes = "Use official API or licensed access where required; do not scrape private or restricted content."
    else:
        risk = "low"
        notes = "Public-source candidate; verify ToS, robots, license, rate limits, and allowed use before enabling."
    return risk, {
        "scraping_allowed": scraping_allowed,
        "requires_api_key": requires_api_key,
        "toS_notes": notes,
    }


def infer_trust_tier(link: ExtractedSourceLink, source_type: OSINTDomain, legal_risk: LegalRisk) -> tuple[int, str]:
    domain = link.domain
    if domain.endswith((".gov", ".mil")) or any(token in domain for token in ("nasa.gov", "usgs.gov", "noaa.gov", "mitre.org", "nist.gov")):
        return 1, "official government or standards source"
    if source_type in {"news_events", "geospatial_intel", "cyber_threat_intel", "maritime_aviation"} and legal_risk != "high":
        return 2, "established public-source category"
    if legal_risk == "high":
        return 4, "high legal risk source requires stronger review"
    return 3, "community or unverified directory candidate"


def infer_update_interval_sec(source_type: OSINTDomain, method: IngestionMethod) -> int:
    if method == "manual":
        return 86400
    return {
        "news_events": 300,
        "public_social": 300,
        "cyber_threat_intel": 900,
        "geospatial_intel": 3600,
        "maritime_aviation": 60,
        "breach_leak_monitoring": 3600,
        "dark_web_index": 86400,
        "directory_meta": 86400,
        "unknown": 86400,
    }[source_type]


_DOMAIN_KEYWORDS: tuple[tuple[OSINTDomain, tuple[str, ...]], ...] = (
    ("dark_web_index", ("dark web", "darkweb", ".onion", "tor hidden", "onion")),
    ("breach_leak_monitoring", ("breach", "leak", "pastebin", "pastes", "dump", "haveibeenpwned")),
    ("maritime_aviation", ("maritime", "marine", "ais", "vessel", "ship", "flight", "aviation", "ads-b", "adsb", "aircraft")),
    ("geospatial_intel", ("geo", "map", "satellite", "imagery", "gis", "arcgis", "sentinel", "landsat", "usgs", "nasa", "noaa", "openstreetmap")),
    ("cyber_threat_intel", ("cyber", "threat", "malware", "cve", "vulnerability", "mitre", "nist", "nvd", "shodan", "censys", "virustotal", "urlscan")),
    ("public_social", ("reddit", "mastodon", "telegram", "twitter", "x.com", "bluesky", "social", "forum", "discord")),
    ("news_events", ("news", "gdelt", "media", "newspaper", "rss", "event registry")),
    ("directory_meta", ("start.me", "osint framework", "osintframework", "awesome-", "github.com", "directory", "tool list")),
)


def _fingerprint_text(link: ExtractedSourceLink) -> str:
    return f"{link.title} {link.url} {link.category} {link.context}".lower()


def _canonical_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    clean = parsed._replace(fragment="")
    return urllib.parse.urlunparse(clean)


def _unwrap_directory_redirect(url: str, hub: DirectoryHub) -> str:
    parsed = urllib.parse.urlparse(url)
    hub_host = urllib.parse.urlparse(hub.url).netloc.lower()
    if hub.kind != "startme" or parsed.netloc.lower() != hub_host:
        return url
    query = urllib.parse.parse_qs(parsed.query)
    for key in ("url", "u", "target", "redirect", "link"):
        for value in query.get(key, []):
            candidate = urllib.parse.unquote(value)
            if urllib.parse.urlparse(candidate).scheme in {"http", "https"}:
                return candidate
    return url


def _unique_source_id(link: ExtractedSourceLink, used_ids: set[str]) -> str:
    parsed = urllib.parse.urlparse(link.url)
    basis = parsed.netloc.removeprefix("www.") or link.title
    first_path = next((part for part in parsed.path.split("/") if part), "")
    if first_path and basis in {"github.com", "gitlab.com"}:
        basis = f"{basis}-{first_path}"
    source_id = re.sub(r"[^a-z0-9]+", "_", basis.lower()).strip("_")[:48] or "source"
    original = source_id
    suffix = 2
    while source_id in used_ids:
        source_id = f"{original[:42]}_{suffix}"
        suffix += 1
    used_ids.add(source_id)
    return source_id


def _rate_limit_policy(method: IngestionMethod) -> str:
    if method == "api":
        return "Disabled proposal; before enablement use official API limits, retries with backoff, and quota logging."
    if method == "rss":
        return "Disabled proposal; before enablement use conditional requests, feed TTLs, and conservative polling."
    if method == "scrape":
        return "Disabled proposal; scraping requires explicit ToS/robots review and a bounded crawler design."
    return "Disabled proposal; operator must define method, quota, and retry policy before collection."


def _allowed_use(source_type: OSINTDomain) -> str:
    return {
        "news_events": "Potential public news/event discovery after source review.",
        "public_social": "Potential public social monitoring only through official or authorized access.",
        "cyber_threat_intel": "Potential public cyber threat intelligence after source and license review.",
        "geospatial_intel": "Potential public geospatial intelligence after source and license review.",
        "maritime_aviation": "Potential public maritime/aviation tracking after source and license review.",
        "breach_leak_monitoring": "High-risk proposal; only lawful public breach metadata may be considered after review.",
        "dark_web_index": "High-risk proposal; no collection without explicit legal authorization and scope control.",
        "directory_meta": "Directory enrichment only; do not ingest as events.",
        "unknown": "Unknown candidate; classify and review before any collection.",
    }[source_type]
