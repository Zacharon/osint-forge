from __future__ import annotations

import unittest
from pathlib import Path


DASHBOARD_DIR = Path(__file__).resolve().parents[1] / "osint_forge" / "dashboard"


class DashboardScaffoldTests(unittest.TestCase):
    def test_dashboard_contains_required_operational_views(self) -> None:
        html = (DASHBOARD_DIR / "index.html").read_text(encoding="utf-8")
        required = [
            "Global Event Map",
            "Mission Board",
            "Source Health",
            "Memory Graph Explorer",
            "Agent Capital Market",
            "Contradiction Queue",
            "Report Builder",
            "Timeline",
        ]
        for label in required:
            self.assertIn(label, html)

    def test_dashboard_renders_provenance_confidence_source_health_and_states(self) -> None:
        app_js = (DASHBOARD_DIR / "app.js").read_text(encoding="utf-8")
        styles = (DASHBOARD_DIR / "styles.css").read_text(encoding="utf-8")
        for token in ["Provenance", "Confidence", "supports", "contradicts", "quota", "last"]:
            self.assertIn(token, app_js)
        for state in ["loadingState", "emptyState", "degradedState", "errorState"]:
            self.assertIn(state, (DASHBOARD_DIR / "index.html").read_text(encoding="utf-8"))
        self.assertIn("--cyan", styles)
        self.assertIn("--red", styles)


if __name__ == "__main__":
    unittest.main()
