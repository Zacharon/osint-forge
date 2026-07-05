"""Production-safe live OSINT ingestion adapter contracts and implementations."""

from __future__ import annotations

import asyncio
import json
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Literal

from osint_forge.provenance import ProvenanceRef, stable_hash, stable_json, utc_now


AdapterMode = Literal["poll", "stream", "webhook"]
OSINTSource = Literal["gdelt", "reddit", "telegram", "x", "manual", "rss"]


class AdapterError(Exception):
    """Raised when a live ingestion adapter cannot safely normalize or fetch data."""


@dataclass
class AdapterHealth:
    sourceName: str
    mode: AdapterMode
    running: bool = False
    lastSuccessfulIngest: str | None = None
    lagSeconds: float | None = None
    errorRate: float = 0.0
    quotaState: str = "unknown"
    successCount: int = 0
    errorCount: int = 0
    lastError: str | None = None

    def record_success(self, published_at: str | None = None) -> None:
        self.successCount += 1
        self.lastSuccessfulIngest = utc_now()
        self.lastError = None
        self._recalculate_error_rate()
        if published_at:
            self.lagSeconds = _seconds_since(published_at)

    def record_error(self, error: Exception | str) -> None:
        self.errorCount += 1
        self.lastError = str(error)
        self._recalculate_error_rate()

    def _recalculate_error_rate(self) -> None:
        total = self.successCount + self.errorCount
        self.errorRate = round(self.errorCount / total, 3) if total else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "sourceName": self.sourceName,
            "mode": self.mode,
            "running": self.running,
            "lastSuccessfulIngest": self.lastSuccessfulIngest,
            "lagSeconds": self.lagSeconds,
            "errorRate": self.errorRate,
            "quotaState": self.quotaState,
            "successCount": self.successCount,
            "errorCount": self.errorCount,
            "lastError": self.lastError,
        }


@dataclass
class NormalizedOSINTEvent:
    id: str
    source: OSINTSource
    observedAt: str
    text: str
    provenance: list[ProvenanceRef]
    publishedAt: str | None = None
    url: str | None = None
    authorHandle: str | None = None
    language: str | None = None
    geo: dict[str, Any] | None = None
    entities: list[str] = field(default_factory=list)
    topics: list[str] = field(default_factory=list)
    confidence: float = 0.5

    def __post_init__(self) -> None:
        if not self.id:
            raise AdapterError("normalized event id is required")
        if self.source not in {"gdelt", "reddit", "telegram", "x", "manual", "rss"}:
            raise AdapterError(f"unsupported normalized source: {self.source}")
        if not self.text.strip():
            raise AdapterError("normalized event text is required")
        if not self.provenance:
            raise AdapterError("normalized event provenance is required")
        self.confidence = max(0.0, min(1.0, float(self.confidence)))
        self.provenance = [ProvenanceRef.from_value(item) for item in self.provenance]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "source": self.source,
            "observedAt": self.observedAt,
            "publishedAt": self.publishedAt,
            "url": self.url,
            "authorHandle": self.authorHandle,
            "text": self.text,
            "language": self.language,
            "geo": dict(self.geo or {}),
            "entities": list(self.entities),
            "topics": list(self.topics),
            "confidence": self.confidence,
            "provenance": [item.to_dict() for item in self.provenance],
        }


class RawPayloadStore:
    """Stores raw source payloads separately from normalized events."""

    def __init__(self, root: str | Path | None = None) -> None:
        self.root = Path(root) if root else None
        self._payloads: dict[str, dict[str, Any]] = {}
        if self.root:
            self.root.mkdir(parents=True, exist_ok=True)

    def save(self, source: str, payload: Any) -> str:
        payload_hash = stable_hash({"source": source, "payload": payload})
        record = {"source": source, "payload": payload, "storedAt": utc_now()}
        self._payloads[payload_hash] = record
        if self.root:
            source_dir = self.root / source
            source_dir.mkdir(parents=True, exist_ok=True)
            (source_dir / f"{payload_hash}.json").write_text(stable_json(record), encoding="utf-8")
        return payload_hash

    def get(self, payload_hash: str) -> dict[str, Any] | None:
        return self._payloads.get(payload_hash)


class Deduplicator:
    def __init__(self) -> None:
        self._seen: set[str] = set()

    def accept(self, event_id: str) -> bool:
        if event_id in self._seen:
            return False
        self._seen.add(event_id)
        return True


class RateLimiter:
    def __init__(self, min_interval_seconds: float) -> None:
        self.min_interval_seconds = max(0.0, float(min_interval_seconds))
        self._last_call = 0.0

    async def wait(self) -> None:
        if self.min_interval_seconds <= 0:
            return
        now = time.monotonic()
        delay = self.min_interval_seconds - (now - self._last_call)
        if delay > 0:
            await asyncio.sleep(delay)
        self._last_call = time.monotonic()


@dataclass
class RetryPolicy:
    maxAttempts: int = 3
    backoffSeconds: float = 0.1
    multiplier: float = 2.0


FetchCallable = Callable[..., Any] | Callable[..., Awaitable[Any]]


class IngestionAdapter(ABC):
    sourceName: str
    mode: AdapterMode

    @abstractmethod
    async def start(self) -> None:
        raise NotImplementedError

    @abstractmethod
    async def stop(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def normalize(self, raw: Any) -> NormalizedOSINTEvent:
        raise NotImplementedError

    @abstractmethod
    def getHealth(self) -> AdapterHealth:
        raise NotImplementedError


class BaseOSINTAdapter(IngestionAdapter):
    def __init__(
        self,
        *,
        sourceName: str,
        source: OSINTSource,
        mode: AdapterMode,
        rawStore: RawPayloadStore | None = None,
        rateLimiter: RateLimiter | None = None,
        retryPolicy: RetryPolicy | None = None,
        quotaState: str = "ok",
    ) -> None:
        self.sourceName = sourceName
        self.source = source
        self.mode = mode
        self.rawStore = rawStore or RawPayloadStore()
        self.rateLimiter = rateLimiter or RateLimiter(0)
        self.retryPolicy = retryPolicy or RetryPolicy()
        self.deduplicator = Deduplicator()
        self.health = AdapterHealth(sourceName=sourceName, mode=mode, quotaState=quotaState)

    async def start(self) -> None:
        self.health.running = True

    async def stop(self) -> None:
        self.health.running = False

    def getHealth(self) -> AdapterHealth:
        return self.health

    def ingest_raw(self, raw: Any) -> NormalizedOSINTEvent | None:
        raw_hash = self.rawStore.save(self.source, raw)
        try:
            event = self.normalize(raw)
            event.provenance.append(
                ProvenanceRef(
                    source=self.source,
                    locator=f"raw:{raw_hash}",
                    observedAt=event.observedAt,
                    contentHash=raw_hash,
                    extractor=f"{self.__class__.__name__}.normalize",
                    metadata={"raw_payload_ref": raw_hash},
                )
            )
            if not self.deduplicator.accept(event.id):
                return None
            self.health.record_success(event.publishedAt)
            return event
        except Exception as exc:
            self.health.record_error(exc)
            if isinstance(exc, AdapterError):
                raise
            raise AdapterError(str(exc)) from exc

    async def fetch_with_retry(self, fetcher: FetchCallable, *args: Any, **kwargs: Any) -> Any:
        attempts = max(1, self.retryPolicy.maxAttempts)
        delay = max(0.0, self.retryPolicy.backoffSeconds)
        last_error: Exception | None = None
        for attempt in range(1, attempts + 1):
            await self.rateLimiter.wait()
            try:
                result = fetcher(*args, **kwargs)
                if hasattr(result, "__await__"):
                    result = await result
                return result
            except Exception as exc:
                last_error = exc
                if attempt == attempts:
                    self.health.record_error(exc)
                    raise AdapterError(f"{self.sourceName} fetch failed after {attempts} attempts: {exc}") from exc
                if delay:
                    await asyncio.sleep(delay)
                    delay *= self.retryPolicy.multiplier
        raise AdapterError(f"{self.sourceName} fetch failed: {last_error}")


class GDELTPollingAdapter(BaseOSINTAdapter):
    def __init__(self, *, query: str, fetcher: FetchCallable | None = None, **kwargs: Any) -> None:
        super().__init__(
            sourceName="gdelt",
            source="gdelt",
            mode="poll",
            rateLimiter=kwargs.pop("rateLimiter", RateLimiter(0.2)),
            **kwargs,
        )
        self.query = query
        self.fetcher = fetcher

    async def poll_once(self) -> list[NormalizedOSINTEvent]:
        if self.fetcher is None:
            raise AdapterError("GDELT polling requires an injected DOC/API fetcher")
        raw_records = await self.fetch_with_retry(self.fetcher, self.query)
        if isinstance(raw_records, dict):
            raw_records = raw_records.get("articles") or raw_records.get("events") or []
        return _ingest_many(self, raw_records)

    def normalize(self, raw: Any) -> NormalizedOSINTEvent:
        record = _require_mapping(raw, "gdelt")
        url = _first_text(record, "url", "sourceurl")
        title = _first_text(record, "title", "seendate") or "GDELT event"
        text = _first_text(record, "text", "summary", "title")
        if not text:
            raise AdapterError("GDELT payload missing text/title")
        published_at = _parse_datetime(_first_text(record, "publishedAt", "seendate", "eventTimeDate"))
        basis = {"source": "gdelt", "url": url, "title": title, "publishedAt": published_at, "text": text}
        content_hash = stable_hash(basis)
        return NormalizedOSINTEvent(
            id=f"gdelt:{content_hash}",
            source="gdelt",
            observedAt=utc_now(),
            publishedAt=published_at,
            url=url,
            text=f"{title}. {text}" if title and title not in text else text,
            language=_first_text(record, "language"),
            geo=_geo_from_record(record),
            entities=_entity_list(record),
            topics=_topic_list(record),
            confidence=0.72,
            provenance=[
                ProvenanceRef(
                    source="gdelt",
                    locator=url or f"gdelt:{content_hash}",
                    observedAt=utc_now(),
                    contentHash=content_hash,
                    extractor="GDELTPollingAdapter.normalize",
                    metadata={"query": self.query},
                )
            ],
        )


class RedditOfficialAPIAdapter(BaseOSINTAdapter):
    def __init__(self, *, fetcher: FetchCallable | None = None, oauthToken: str | None = None, **kwargs: Any) -> None:
        super().__init__(
            sourceName="reddit",
            source="reddit",
            mode="poll",
            rateLimiter=kwargs.pop("rateLimiter", RateLimiter(1.0)),
            quotaState="api-governed",
            **kwargs,
        )
        self.fetcher = fetcher
        self.oauthToken = oauthToken

    async def poll_once(self, *, subreddit: str, query: str | None = None) -> list[NormalizedOSINTEvent]:
        if self.fetcher is None:
            raise AdapterError("Reddit adapter requires an injected official API client")
        raw_records = await self.fetch_with_retry(self.fetcher, subreddit=subreddit, query=query, oauthToken=self.oauthToken)
        if isinstance(raw_records, dict):
            raw_records = raw_records.get("children") or raw_records.get("data", {}).get("children", [])
        return _ingest_many(self, raw_records)

    def normalize(self, raw: Any) -> NormalizedOSINTEvent:
        record = _require_mapping(raw, "reddit")
        data = record.get("data") if isinstance(record.get("data"), dict) else record
        text = " ".join(
            item
            for item in [
                str(data.get("title", "")).strip(),
                str(data.get("selftext", "")).strip(),
            ]
            if item
        )
        if not text:
            raise AdapterError("Reddit payload missing public title/selftext")
        post_id = str(data.get("id") or stable_hash(data))
        permalink = str(data.get("permalink") or "")
        url = permalink if permalink.startswith("http") else f"https://www.reddit.com{permalink}" if permalink else None
        published_at = _unix_datetime(data.get("created_utc"))
        return NormalizedOSINTEvent(
            id=f"reddit:{post_id}",
            source="reddit",
            observedAt=utc_now(),
            publishedAt=published_at,
            url=url,
            authorHandle=_clean_public_handle(data.get("author")),
            text=text,
            language=None,
            geo=None,
            entities=[],
            topics=[str(data.get("subreddit"))] if data.get("subreddit") else [],
            confidence=0.48,
            provenance=[
                ProvenanceRef(
                    source="reddit",
                    locator=url or f"reddit:{post_id}",
                    observedAt=utc_now(),
                    contentHash=stable_hash(data),
                    extractor="RedditOfficialAPIAdapter.normalize",
                    metadata={"api": "official", "private_data_collected": False},
                )
            ],
        )


class TelegramBotWebhookAdapter(BaseOSINTAdapter):
    def __init__(
        self,
        *,
        botToken: str | None = None,
        webhookUrl: str | None = None,
        longPolling: bool = False,
        allowedChatIds: set[str] | None = None,
        **kwargs: Any,
    ) -> None:
        if webhookUrl and longPolling:
            raise AdapterError("Telegram bot webhooks and long polling are mutually exclusive")
        super().__init__(
            sourceName="telegram",
            source="telegram",
            mode="webhook" if webhookUrl else "poll",
            rateLimiter=kwargs.pop("rateLimiter", RateLimiter(0.1)),
            quotaState="bot-api-governed",
            **kwargs,
        )
        self.botToken = botToken
        self.webhookUrl = webhookUrl
        self.longPolling = longPolling
        self.allowedChatIds = {str(item) for item in (allowedChatIds or set())}

    def normalize(self, raw: Any) -> NormalizedOSINTEvent:
        update = _require_mapping(raw, "telegram")
        message = update.get("message") or update.get("channel_post") or update.get("edited_channel_post")
        if not isinstance(message, dict):
            raise AdapterError("Telegram update missing message/channel_post")
        chat = message.get("chat") if isinstance(message.get("chat"), dict) else {}
        chat_id = str(chat.get("id", ""))
        if self.allowedChatIds and chat_id not in self.allowedChatIds:
            raise AdapterError("Telegram update came from an unauthorized chat")
        text = str(message.get("text") or message.get("caption") or "").strip()
        if not text:
            raise AdapterError("Telegram update missing text/caption")
        msg_id = str(message.get("message_id") or stable_hash(message))
        published_at = _unix_datetime(message.get("date"))
        locator = f"telegram:{chat_id}:{msg_id}"
        return NormalizedOSINTEvent(
            id=locator,
            source="telegram",
            observedAt=utc_now(),
            publishedAt=published_at,
            url=None,
            authorHandle=_clean_public_handle(chat.get("username") or chat.get("title")),
            text=text,
            language=None,
            geo=None,
            entities=[],
            topics=[],
            confidence=0.52,
            provenance=[
                ProvenanceRef(
                    source="telegram",
                    locator=locator,
                    observedAt=utc_now(),
                    contentHash=stable_hash(message),
                    extractor="TelegramBotWebhookAdapter.normalize",
                    metadata={"authorized_chat": bool(self.allowedChatIds), "private_data_collected": False},
                )
            ],
        )


class XFilteredStreamAdapter(BaseOSINTAdapter):
    def __init__(self, *, rules: list[str], fetcher: FetchCallable | None = None, bearerToken: str | None = None, **kwargs: Any) -> None:
        super().__init__(
            sourceName="x",
            source="x",
            mode="stream",
            rateLimiter=kwargs.pop("rateLimiter", RateLimiter(0.0)),
            quotaState="filtered-stream-governed",
            **kwargs,
        )
        self.rules = list(rules)
        self.fetcher = fetcher
        self.bearerToken = bearerToken

    async def read_batch(self) -> list[NormalizedOSINTEvent]:
        if self.fetcher is None:
            raise AdapterError("X filtered stream adapter requires an injected stream client")
        raw_records = await self.fetch_with_retry(self.fetcher, rules=self.rules, bearerToken=self.bearerToken)
        if isinstance(raw_records, dict):
            raw_records = raw_records.get("data") or []
        return _ingest_many(self, raw_records)

    def normalize(self, raw: Any) -> NormalizedOSINTEvent:
        record = _require_mapping(raw, "x")
        data = record.get("data") if isinstance(record.get("data"), dict) else record
        text = str(data.get("text") or "").strip()
        if not text:
            raise AdapterError("X payload missing public text")
        tweet_id = str(data.get("id") or stable_hash(data))
        created_at = _parse_datetime(str(data.get("created_at") or "")) if data.get("created_at") else None
        return NormalizedOSINTEvent(
            id=f"x:{tweet_id}",
            source="x",
            observedAt=utc_now(),
            publishedAt=created_at,
            url=f"https://x.com/i/web/status/{tweet_id}",
            authorHandle=_clean_public_handle(data.get("author_id") or data.get("username")),
            text=text,
            language=data.get("lang"),
            geo=_geo_from_record(data),
            entities=_x_entities(data),
            topics=list(self.rules),
            confidence=0.45,
            provenance=[
                ProvenanceRef(
                    source="x",
                    locator=f"x:{tweet_id}",
                    observedAt=utc_now(),
                    contentHash=stable_hash(data),
                    extractor="XFilteredStreamAdapter.normalize",
                    metadata={"rules": list(self.rules), "api": "filtered_stream", "private_data_collected": False},
                )
            ],
        )


def _ingest_many(adapter: BaseOSINTAdapter, raw_records: Any) -> list[NormalizedOSINTEvent]:
    if not isinstance(raw_records, list):
        raise AdapterError(f"{adapter.sourceName} fetcher must return a list or source response object")
    events: list[NormalizedOSINTEvent] = []
    for raw in raw_records:
        event = adapter.ingest_raw(raw)
        if event is not None:
            events.append(event)
    return events


def _require_mapping(raw: Any, source: str) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise AdapterError(f"{source} payload must be a JSON object")
    return raw


def _first_text(record: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = record.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _parse_datetime(value: str | None) -> str | None:
    if not value:
        return None
    text = str(value).strip()
    for fmt in ("%Y%m%d%H%M%S", "%Y%m%dT%H%M%SZ", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            dt = datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
            return dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")
        except ValueError:
            pass
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    except ValueError:
        return None


def _unix_datetime(value: Any) -> str | None:
    if value in {None, ""}:
        return None
    try:
        return datetime.fromtimestamp(float(value), tz=timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    except (TypeError, ValueError, OSError):
        return None


def _seconds_since(value: str) -> float | None:
    parsed = _parse_datetime(value)
    if not parsed:
        return None
    dt = datetime.fromisoformat(parsed.replace("Z", "+00:00"))
    return max(0.0, (datetime.now(timezone.utc) - dt).total_seconds())


def _geo_from_record(record: dict[str, Any]) -> dict[str, Any] | None:
    geo: dict[str, Any] = {}
    for target, keys in {
        "lat": ("lat", "latitude"),
        "lon": ("lon", "lng", "longitude"),
        "country": ("country", "sourceCountry"),
        "placeName": ("placeName", "location", "place"),
    }.items():
        for key in keys:
            if key in record and record[key] not in {None, ""}:
                geo[target] = record[key]
                break
    return geo or None


def _entity_list(record: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for key in ("persons", "organizations", "locations", "entities"):
        value = record.get(key)
        if isinstance(value, list):
            values.extend(str(item) for item in value if str(item).strip())
        elif isinstance(value, str):
            values.extend(item.strip() for item in value.replace(";", ",").split(",") if item.strip())
    return sorted(set(values))


def _topic_list(record: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for key in ("themes", "topics", "sourceCollectionIdentifier"):
        value = record.get(key)
        if isinstance(value, list):
            values.extend(str(item) for item in value if str(item).strip())
        elif isinstance(value, str):
            values.extend(item.strip() for item in value.replace(";", ",").split(",") if item.strip())
    return sorted(set(values))


def _x_entities(data: dict[str, Any]) -> list[str]:
    entities = data.get("entities")
    if not isinstance(entities, dict):
        return []
    values: list[str] = []
    for key in ("hashtags", "mentions", "cashtags"):
        raw = entities.get(key)
        if isinstance(raw, list):
            for item in raw:
                if isinstance(item, dict):
                    value = item.get("tag") or item.get("username") or item.get("text")
                    if value:
                        values.append(str(value))
                elif item:
                    values.append(str(item))
    return sorted(set(values))


def _clean_public_handle(value: Any) -> str | None:
    if value in {None, "", "[deleted]"}:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text if text.startswith("@") else f"@{text}"


def normalized_event_json(event: NormalizedOSINTEvent) -> str:
    return json.dumps(event.to_dict(), sort_keys=True, ensure_ascii=False)
