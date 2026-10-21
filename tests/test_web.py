"""Smoke checks for the dependency-free browser client assets."""

import unittest

from fastapi.testclient import TestClient

from smartplanner.api import create_app
from smartplanner.settings import Settings
from smartplanner.web import WEB_DIR


class BrowserAssetsTest(unittest.TestCase):
    def test_browser_client_references_its_assets(self):
        page = (WEB_DIR / "index.html").read_text(encoding="utf-8")
        self.assertIn('/assets/app.css', page)
        self.assertIn('/assets/app.js', page)
        self.assertIn('id="task-form"', page)
        self.assertIn('id="event-form"', page)
        self.assertIn('id="schedule-form"', page)
        self.assertIn('id="save-schedule-form"', page)
        self.assertIn('id="block-form"', page)
        self.assertIn('id="preferences-form"', page)
        self.assertIn('id="recurrence-form"', page)
        self.assertIn('id="schedule-days"', page)

    def test_assets_exist(self):
        self.assertTrue((WEB_DIR / "app.css").is_file())
        self.assertTrue((WEB_DIR / "app.js").is_file())
        self.assertTrue((WEB_DIR / "planner_time.mjs").is_file())

    def test_api_serves_browser_client_without_database_access(self):
        app = create_app(Settings("postgresql://unused:unused@localhost/unused"))
        with TestClient(app, base_url="http://localhost:8000") as client:
            page = client.get("/")
            script = client.get("/assets/app.js")
            time_script = client.get("/assets/planner_time.mjs")
        self.assertEqual(page.status_code, 200)
        self.assertIn("SmartPlanner", page.text)
        self.assertEqual(script.status_code, 200)
        self.assertEqual(time_script.status_code, 200)


if __name__ == "__main__":
    unittest.main()
