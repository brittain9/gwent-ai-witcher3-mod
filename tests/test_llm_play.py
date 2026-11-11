from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

from scripts.llm_play import ConnectorServer

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "scripts/llm_play.py"


class FakeHost:
    def __init__(self) -> None:
        self.calls = []
        self.session_id = "fixed-host-session"
        self.packet = {
            "schema": "w3gwent.llm.v1",
            "decision_id": 1,
            "token": "decision-token",
            "seat": 0,
            "observation": {"hand": ["visible-card"]},
            "legal_actions": [{"id": "play-0", "kind": "play"}],
            "history": [],
            "completed": False,
        }

    def state(self) -> dict:
        self.calls.append("state")
        return self.packet

    def act(self, action_id, *, decision_id, token, note="") -> dict:
        self.calls.append((action_id, decision_id, token, note))
        if decision_id != 1 or token != "decision-token":
            raise ValueError("private engine detail: stale lease")
        if action_id != "play-0":
            raise ValueError("private engine detail: illegal action")
        return {**self.packet, "completed": True}


class ConnectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.reports = Path(self.temp.name) / "reports.jsonl"
        self.host = FakeHost()
        self.server = ConnectorServer(self.host, reports_path=self.reports)
        self.addCleanup(self.server.server_close)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)

    def stop_server(self) -> None:
        self.server.shutdown()
        self.thread.join(timeout=5)

    def cli(self, *args) -> subprocess.CompletedProcess:
        return subprocess.run(
            [
                sys.executable,
                str(CLI),
                *args,
                "--url",
                self.server.url,
                "--token",
                self.server.token,
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )

    def request(self, path, data=None, token=None):
        headers = {"Authorization": f"Bearer {token or self.server.token}"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            self.server.url + path,
            data=data,
            headers=headers,
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as error:
            with error:
                return error.code, json.load(error)

    def test_generated_capability_is_safe_as_a_cli_argument(self) -> None:
        with patch("scripts.llm_play.secrets.token_urlsafe", return_value="-edge-token"):
            server = ConnectorServer(self.host)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            result = subprocess.run(
                [sys.executable, str(CLI), "state", "--url", server.url,
                 "--token", server.token],
                capture_output=True, text=True, timeout=10,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout), self.host.packet)
        finally:
            server.shutdown()
            thread.join(timeout=5)
            server.server_close()

    def test_subprocess_state_and_move_use_only_player_packet(self) -> None:
        result = self.cli("state")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), self.host.packet)
        result = self.cli(
            "move",
            "--decision-id",
            "1",
            "--decision-token",
            "decision-token",
            "--action",
            "play-0",
            "--rationale",
            "Play safely.",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)["completed"])
        self.assertEqual(
            self.host.calls[-1],
            (
                "play-0",
                1,
                "decision-token",
                "Play safely.",
            ),
        )

    def test_rejected_auth_and_unknown_paths_never_call_host(self) -> None:
        for path in ("/state", "/journal", "/engine"):
            status, result = self.request(path, token="wrong-token")
            self.assertEqual(status, 401)
            self.assertEqual(result, {"error": "unauthorized"})
        status, _ = self.request("/journal")
        self.assertEqual(status, 404)
        self.assertEqual(self.host.calls, [])

    def test_stale_or_illegal_move_is_rejected_without_private_errors(self) -> None:
        for decision, action in (("0", "play-0"), ("1", "cheat")):
            result = self.cli(
                "move",
                "--decision-id",
                decision,
                "--decision-token",
                "decision-token",
                "--action",
                action,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(json.loads(result.stderr), {"error": "move rejected"})
            self.assertEqual(result.stdout, "")

    def test_malformed_or_excessive_requests_never_call_host(self) -> None:
        for payload in (b"not json", b"[]", b'{"command":"dump"}', b"x" * 65537):
            status, _ = self.request("/move", data=payload)
            self.assertIn(status, (400, 413))
        self.assertEqual(self.host.calls, [])

    def test_reports_are_separate_unvalidated_records(self) -> None:
        self.assertEqual(self.cli("state").returncode, 0)
        result = self.cli(
            "report",
            "--decision-id",
            "1",
            "--label",
            "possible-bug",
            "--report",
            "This move may ignore weather.",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        record = json.loads(self.reports.read_text())
        self.assertEqual(record.pop("session_id"), self.host.session_id)
        self.assertEqual(
            record,
            {
                "schema": "w3gwent.llm_report.v1",
                "validation": "unvalidated",
                "decision_id": 1,
                "label": "possible-bug",
                "report": "This move may ignore weather.",
            },
        )
        self.assertEqual(self.host.calls, ["state"])

    def test_reports_require_a_previously_delivered_decision(self) -> None:
        result = self.cli(
            "report",
            "--decision-id",
            "999",
            "--label",
            "possible-bug",
            "--report",
            "Unknown decision.",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stderr), {"error": "unknown decision"})
        self.assertFalse(self.reports.exists())
        self.assertEqual(self.host.calls, [])

    def test_reports_cannot_share_the_host_journal(self) -> None:
        with self.assertRaises(ValueError):
            ConnectorServer(
                self.host, reports_path=self.reports, journal_path=self.reports
            )
        self.assertFalse(self.reports.exists())

    def test_startup_error_does_not_expose_private_paths(self) -> None:
        private = Path(self.temp.name) / "PRIVATE-MATCH.json"
        result = subprocess.run(
            [
                sys.executable,
                str(CLI),
                "serve",
                "--engine",
                "/PRIVATE-ENGINE",
                "--match-config",
                str(private),
                "--opponent-config",
                str(private),
                "--journal",
                str(private),
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertEqual(
            json.loads(result.stderr), {"error": "could not start connector"}
        )

    def test_server_stops_at_total_runtime_limit(self) -> None:
        started = time.monotonic()
        with ConnectorServer(self.host) as server:
            self.assertFalse(server.run_for(0.05))
        self.assertLess(time.monotonic() - started, 1)
        self.assertEqual(self.host.calls, [])

    def test_serve_rejects_invalid_total_runtime_limit(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                str(CLI),
                "serve",
                "--engine",
                "/PRIVATE-ENGINE",
                "--match-config",
                "/PRIVATE-MATCH",
                "--opponent-config",
                "/PRIVATE-OPPONENT",
                "--journal",
                "/PRIVATE-JOURNAL",
                "--max-seconds",
                "0",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(
            json.loads(result.stderr), {"error": "could not start connector"}
        )

    def test_client_rejects_nonlocal_urls(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                str(CLI),
                "state",
                "--url",
                "https://example.com",
                "--token",
                "secret",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(
            json.loads(result.stderr), {"error": "invalid local server URL"}
        )


if __name__ == "__main__":
    unittest.main()
