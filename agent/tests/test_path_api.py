import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from test_normalized_model import network
from aiops_agent.topology import build_snapshot


class PathApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with patch("aiops_agent.inventory.load_inventory", return_value=[]):
            from aiops_agent.main import app
        cls.client = TestClient(app)

    def test_page_and_api_same_result(self):
        with patch(
            "aiops_agent.main.load_snapshot", return_value=build_snapshot(network())
        ):
            for params in [
                dict(src="a:Lo0", dst="192.0.2.2"),
                dict(src="a:Et999", dst="b:Et1"),
                dict(src="a:Et1", dst="b:Et1", mode="physical"),
            ]:
                a = self.client.get("/api/path", params=params)
                b = self.client.get("/tools/path", params=params)
                self.assertEqual(a.status_code, 200)
                self.assertEqual(a.json(), b.json())

    def test_site_options_and_lab_policy_result_match_api(self):
        from test_service_path import lab, SRC, DST
        with patch("aiops_agent.main.load_snapshot", return_value=lab()):
            params = dict(src=SRC, dst=DST, site="lab")
            api = self.client.get("/api/path", params=params).json()
            self.assertEqual(api, self.client.get("/tools/path", params=params).json())
            self.assertEqual(api["status"], "blocked")
            self.assertEqual(api["site"], "lab")
            self.assertEqual(self.client.get("/tools/options?site=missing").json()["devices"], [])
            self.assertEqual(self.client.get("/api/routes?site=missing").json()["routes"], [])
            self.assertEqual(self.client.get("/tools/options?site=lab").json()["sites"], ["lab"])

    def test_mode_validation(self):
        self.assertEqual(
            self.client.get(
                "/api/path", params=dict(src="a:Et1", dst="b:Et1", mode="invalid")
            ).status_code,
            422,
        )

    def test_new_schema_preserves_old_fields(self):
        with patch(
            "aiops_agent.main.load_snapshot", return_value=build_snapshot(network())
        ):
            payload = self.client.get("/api/topology").json()
            self.assertTrue(
                {
                    "devices",
                    "interfaces",
                    "links",
                    "endpoints",
                    "layout",
                    "observed_at",
                    "schema_version",
                    "vrfs",
                    "routes",
                }
                <= payload.keys()
            )
            self.assertEqual(payload["schema_version"], "1.1")

    def test_page_renders_branches_and_topology_has_no_timer(self):
        page = self.client.get("/tools").text
        self.assertIn("payload.paths", page)
        self.assertIn("Source candidates", page)
        page = self.client.get("/topology").text
        self.assertNotIn("setInterval", page)
        self.assertIn("link.confirmation==='confirmed'", page)
