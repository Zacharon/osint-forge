from __future__ import annotations

import unittest

from osint_forge.ingestion.registry_inference import (
    DirectoryHub,
    ExtractedSourceLink,
    SourceDiscoveryEngine,
    infer_ingestion_method,
    infer_legal,
    infer_source_type,
)


class RegistryInferenceTests(unittest.TestCase):
    def test_startme_style_hub_generates_disabled_registry_proposals(self) -> None:
        html = """
        <html>
          <body>
            <h2>News OSINT</h2>
            <a href="https://api.gdeltproject.org/api/v2/doc/doc">GDELT DOC API</a>
            <h2>Maritime / aviation tracking</h2>
            <a href="https://www.marinetraffic.com/">MarineTraffic AIS</a>
            <h2>Dark web intelligence</h2>
            <a href="https://example.org/leak-index">Public leak index</a>
          </body>
        </html>
        """
        hub = DirectoryHub(hub_id="osint4all", url="https://start.me/p/L1rEYQ/osint4all", kind="startme")
        snapshot = SourceDiscoveryEngine().discover_from_html(hub, html, captured_at="2026-07-02")

        self.assertEqual(len(snapshot.links), 3)
        self.assertEqual(snapshot.links[0].category, "News OSINT")
        gdelt = next(item for item in snapshot.proposals if item.source_id == "api_gdeltproject_org")
        self.assertEqual(gdelt.source_type, "news_events")
        self.assertEqual(gdelt.method, "api")
        self.assertEqual(gdelt.legal_risk, "low")

        registry_entry = gdelt.to_disabled_source_registry_entry(verified_at="2026-07-02")
        self.assertFalse(registry_entry["enabled"])
        self.assertFalse(registry_entry["live_capable"])
        self.assertEqual(registry_entry["collection_mode"], "disabled")
        self.assertEqual(registry_entry["adapter"], "ManualRegistryReviewAdapter")

        leak = next(item for item in snapshot.proposals if item.source_id == "example_org")
        self.assertEqual(leak.source_type, "dark_web_index")
        self.assertEqual(leak.method, "manual")
        self.assertEqual(leak.legal_risk, "high")

    def test_osint_framework_tree_links_are_classified_without_same_host_links(self) -> None:
        html = """
        <h3>Social Networks</h3>
        <ul>
          <li><a href="/about">Internal OSINT Framework page</a></li>
          <li><a href="https://www.reddit.com/dev/api/">Reddit API</a></li>
          <li><a href="https://mastodon.social/">Mastodon public social search</a></li>
        </ul>
        """
        hub = DirectoryHub(hub_id="osint-framework", url="https://osintframework.com/", kind="osint_framework")
        snapshot = SourceDiscoveryEngine().discover_from_html(hub, html)

        self.assertEqual([link.domain for link in snapshot.links], ["mastodon.social", "reddit.com"])
        reddit = next(item for item in snapshot.proposals if item.source_id == "reddit_com")
        self.assertEqual(reddit.source_type, "public_social")
        self.assertEqual(reddit.method, "api")
        self.assertTrue(reddit.legal["requires_api_key"])
        self.assertFalse(reddit.legal["scraping_allowed"])

    def test_startme_wrapped_redirects_and_github_repo_links_are_preserved(self) -> None:
        startme = DirectoryHub(hub_id="osint4all", url="https://start.me/p/L1rEYQ/osint4all", kind="startme")
        startme_snapshot = SourceDiscoveryEngine().discover_from_html(
            startme,
            """
            <h2>Cyber threat intel</h2>
            <a href="/go?url=https%3A%2F%2Fnvd.nist.gov%2Fvuln">NVD CVE Search</a>
            """,
        )
        self.assertEqual(startme_snapshot.links[0].url, "https://nvd.nist.gov/vuln")
        self.assertEqual(startme_snapshot.proposals[0].source_type, "cyber_threat_intel")

        github = DirectoryHub(hub_id="awesome-osint", url="https://github.com/example/awesome-osint", kind="github_list")
        github_snapshot = SourceDiscoveryEngine().discover_from_html(
            github,
            """
            <h2>Tool list</h2>
            <a href="https://github.com/projectdiscovery/nuclei">Nuclei templates</a>
            """,
        )
        self.assertEqual(github_snapshot.links[0].domain, "github.com")
        self.assertEqual(github_snapshot.proposals[0].source_id, "github_com_projectdiscovery")

    def test_inference_marks_scrape_candidates_as_disabled_medium_risk(self) -> None:
        link = ExtractedSourceLink(
            title="Regional news monitor",
            url="https://regional-news.example/events",
            hub_id="hub",
            hub_url="https://start.me/p/example",
            category="News OSINT",
        )

        source_type, _ = infer_source_type(link)
        method, _ = infer_ingestion_method(link, source_type)
        legal_risk, legal = infer_legal(link, source_type, method)

        self.assertEqual(source_type, "news_events")
        self.assertEqual(method, "scrape")
        self.assertEqual(legal_risk, "medium")
        self.assertFalse(legal["scraping_allowed"])

    def test_snapshot_diff_detects_added_removed_and_recategorized_sources(self) -> None:
        engine = SourceDiscoveryEngine()
        hub = DirectoryHub(hub_id="osint4all", url="https://start.me/p/L1rEYQ/osint4all", kind="startme")
        previous = engine.discover_from_html(
            hub,
            """
            <h2>News OSINT</h2>
            <a href="https://news.example/feed">News feed</a>
            <h2>Cyber threat intel</h2>
            <a href="https://cve.example/api">CVE API</a>
            """,
        )
        current = engine.discover_from_html(
            hub,
            """
            <h2>News OSINT</h2>
            <a href="https://news.example/feed">News feed</a>
            <a href="https://api.gdeltproject.org/api/v2/doc/doc">GDELT DOC API</a>
            <h2>Geo / mapping tools</h2>
            <a href="https://cve.example/api">CVE API</a>
            """,
        )

        diff = engine.diff_snapshots(previous, current)

        self.assertEqual(diff.added_urls, ["https://api.gdeltproject.org/api/v2/doc/doc"])
        self.assertEqual(diff.removed_urls, [])
        self.assertEqual(
            diff.changed_categories,
            [
                {
                    "url": "https://cve.example/api",
                    "previous_category": "Cyber threat intel",
                    "current_category": "Geo / mapping tools",
                }
            ],
        )


if __name__ == "__main__":
    unittest.main()
