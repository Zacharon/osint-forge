from __future__ import annotations

import asyncio
import unittest

from osint_forge.ingestion.live_adapters import (
    AdapterError,
    GDELTPollingAdapter,
    RateLimiter,
    RedditOfficialAPIAdapter,
    RetryPolicy,
    TelegramBotWebhookAdapter,
)


class LiveIngestionAdapterTests(unittest.TestCase):
    def test_gdelt_adapter_health_and_normalization(self) -> None:
        async def fetcher(query: str) -> list[dict[str, str]]:
            self.assertEqual(query, "port delays")
            return [
                {
                    "url": "https://example.org/gdelt/1",
                    "title": "Port delays reported",
                    "seendate": "20260701090000",
                    "sourceCountry": "PL",
                    "themes": "TRANSPORT;PORT",
                }
            ]

        adapter = GDELTPollingAdapter(
            query="port delays",
            fetcher=fetcher,
            rateLimiter=RateLimiter(0),
            retryPolicy=RetryPolicy(maxAttempts=1, backoffSeconds=0),
        )
        asyncio.run(adapter.start())
        events = asyncio.run(adapter.poll_once())

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].source, "gdelt")
        self.assertEqual(events[0].geo["country"], "PL")
        self.assertGreaterEqual(len(events[0].provenance), 2)
        health = adapter.getHealth()
        self.assertTrue(health.running)
        self.assertEqual(health.successCount, 1)
        self.assertEqual(health.errorCount, 0)

    def test_duplicate_suppression_keeps_one_normalized_event(self) -> None:
        adapter = TelegramBotWebhookAdapter(rateLimiter=RateLimiter(0), allowedChatIds={"-1001"})
        raw = {
            "channel_post": {
                "message_id": 7,
                "date": 1782900000,
                "text": "Public authorized channel update",
                "chat": {"id": "-1001", "title": "Authorized Channel"},
            }
        }

        first = adapter.ingest_raw(raw)
        second = adapter.ingest_raw(raw)

        self.assertIsNotNone(first)
        self.assertIsNone(second)
        self.assertEqual(adapter.getHealth().successCount, 1)

    def test_malformed_payload_updates_health_and_fails_closed(self) -> None:
        adapter = RedditOfficialAPIAdapter(rateLimiter=RateLimiter(0))

        with self.assertRaises(AdapterError):
            adapter.ingest_raw({"data": {"id": "abc"}})

        health = adapter.getHealth()
        self.assertEqual(health.errorCount, 1)
        self.assertIn("missing public title", health.lastError)

    def test_source_outage_records_health_after_retries(self) -> None:
        def fetcher(**_: object) -> list[dict[str, object]]:
            raise RuntimeError("upstream unavailable")

        adapter = RedditOfficialAPIAdapter(
            fetcher=fetcher,
            rateLimiter=RateLimiter(0),
            retryPolicy=RetryPolicy(maxAttempts=2, backoffSeconds=0),
        )

        with self.assertRaises(AdapterError):
            asyncio.run(adapter.poll_once(subreddit="news", query="ports"))

        health = adapter.getHealth()
        self.assertEqual(health.errorCount, 1)
        self.assertEqual(health.errorRate, 1.0)
        self.assertIn("upstream unavailable", health.lastError)


if __name__ == "__main__":
    unittest.main()
