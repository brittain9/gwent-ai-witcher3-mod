"""Serve one isolated player seat and send player commands over localhost HTTP."""

from __future__ import annotations

import argparse
import hmac
import http.client
import json
import math
import os
import secrets
import signal
import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

MAX_BODY = 65536


class ConnectorServer(HTTPServer):
    """Serialize authenticated player requests for one host and one seat."""

    def __init__(
        self,
        host,
        *,
        port=0,
        reports_path: Path | None = None,
        journal_path: Path | None = None,
    ):
        if (
            reports_path is not None
            and journal_path is not None
            and reports_path.resolve() == journal_path.resolve()
        ):
            raise ValueError("reports must not share the journal path")
        self.host = host
        self.session_id = host.session_id
        self.seen_decisions = set()
        self.completed = False
        self.token = "w3gwent_" + secrets.token_urlsafe(32)
        self.reports_path = reports_path
        super().__init__(("127.0.0.1", port), _PlayerHandler)
        self.url = f"http://127.0.0.1:{self.server_port}"

    def run_for(self, seconds: float) -> bool:
        """Serve until the total runtime limit, then report observed completion."""
        if not math.isfinite(seconds) or seconds <= 0:
            raise ValueError("runtime limit must be positive and finite")
        deadline = time.monotonic() + seconds
        while (remaining := deadline - time.monotonic()) > 0:
            self.timeout = min(0.5, remaining)
            self.handle_request()
        return self.completed

    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(10)
        return connection, address

    def record_report(self, record: dict) -> None:
        if self.reports_path is None:
            raise ValueError("reports are disabled")
        descriptor = os.open(
            self.reports_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600
        )
        with os.fdopen(descriptor, "a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=True) + "\n")


class _PlayerHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # HTTP logs can contain player text. The trusted host owns the journal.
        pass

    def reply(self, status: int, packet: dict) -> None:
        body = json.dumps(packet, ensure_ascii=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)
        if status == 200 and self.path in ("/state", "/move"):
            decision = packet.get("decision_id")
            if type(decision) is int:
                self.server.seen_decisions.add(decision)
            self.server.completed = packet.get("completed") is True

    def authorized(self) -> bool:
        actual = self.headers.get("Authorization", "")
        expected = "Bearer " + self.server.token
        if not hmac.compare_digest(actual.encode(), expected.encode()):
            self.reply(401, {"error": "unauthorized"})
            return False
        return True

    def do_GET(self):
        if not self.authorized():
            return
        if self.path != "/state":
            self.reply(404, {"error": "unknown endpoint"})
            return
        try:
            self.reply(200, self.server.host.state())
        except Exception:
            self.reply(500, {"error": "state unavailable"})

    def read_body(self) -> dict | None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self.reply(400, {"error": "invalid request"})
            return None
        if length > MAX_BODY:
            self.reply(413, {"error": "request too large"})
            return None
        if length < 1 or self.headers.get("Transfer-Encoding"):
            self.reply(400, {"error": "invalid request"})
            return None
        try:
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise ValueError("expected object")
        except (ValueError, OSError):
            self.reply(400, {"error": "invalid request"})
            return None
        return data

    def do_POST(self):
        if not self.authorized():
            return
        if self.path not in ("/move", "/report"):
            self.reply(404, {"error": "unknown endpoint"})
            return
        data = self.read_body()
        if data is None:
            return
        if self.path == "/report":
            self.report(data)
            return
        if (
            set(data) - {"action_id", "decision_id", "token", "rationale"}
            or not isinstance(data.get("action_id"), str)
            or type(data.get("decision_id")) is not int
            or not isinstance(data.get("token"), str)
            or not isinstance(data.get("rationale", ""), str)
        ):
            self.reply(400, {"error": "invalid request"})
            return
        try:
            packet = self.server.host.act(
                data["action_id"],
                decision_id=data["decision_id"],
                token=data["token"],
                note=data.get("rationale", ""),
            )
        except ValueError:
            self.reply(409, {"error": "move rejected"})
        except Exception:
            self.reply(500, {"error": "move unavailable"})
        else:
            self.reply(200, packet)

    def report(self, data: dict) -> None:
        if self.server.reports_path is None:
            self.reply(404, {"error": "reports disabled"})
            return
        if (
            set(data) != {"decision_id", "label", "report"}
            or type(data.get("decision_id")) is not int
            or not isinstance(data.get("label"), str)
            or not isinstance(data.get("report"), str)
        ):
            self.reply(400, {"error": "invalid request"})
            return
        if data["decision_id"] not in self.server.seen_decisions:
            self.reply(409, {"error": "unknown decision"})
            return
        try:
            self.server.record_report(
                {
                    "schema": "w3gwent.llm_report.v1",
                    "validation": "unvalidated",
                    "session_id": self.server.session_id,
                    **data,
                }
            )
        except OSError:
            self.reply(500, {"error": "report unavailable"})
        else:
            self.reply(200, {"recorded": True, "validation": "unvalidated"})


def player_request(url: str, token: str, endpoint: str, data=None) -> tuple[int, dict]:
    """Send one request without following redirects or using HTTP proxies."""
    try:
        address = urlsplit(url)
        if (
            address.scheme != "http"
            or address.hostname != "127.0.0.1"
            or address.username is not None
            or address.password is not None
            or address.path not in ("", "/")
            or address.query
            or address.fragment
            or address.port is None
        ):
            raise ValueError("invalid URL")
        port = address.port
    except ValueError:
        return 1, {"error": "invalid local server URL"}
    try:
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=35)
        try:
            body = json.dumps(data) if data is not None else None
            connection.request(
                "POST" if data is not None else "GET",
                endpoint,
                body=body,
                headers={
                    "Authorization": "Bearer " + token,
                    "Content-Type": "application/json",
                },
            )
            response = connection.getresponse()
            raw = response.read(16 * 1024 * 1024 + 1)
            if len(raw) > 16 * 1024 * 1024:
                raise ValueError("response too large")
            packet = json.loads(raw)
            if not isinstance(packet, dict):
                raise ValueError("invalid response")
            return (0 if response.status == 200 else 1), packet
        finally:
            connection.close()
    except (OSError, ValueError, http.client.HTTPException):
        return 1, {"error": "connector unavailable"}


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve", help="trusted orchestrator: start one match")
    serve.add_argument("--engine", type=Path, required=True)
    serve.add_argument("--match-config", type=Path, required=True)
    serve.add_argument("--opponent-config", type=Path, required=True)
    serve.add_argument("--journal", type=Path, required=True)
    serve.add_argument("--reports", type=Path)
    serve.add_argument("--seat", type=int, choices=(0, 1), default=0)
    serve.add_argument("--port", type=int, default=0)
    serve.add_argument("--model", default="unspecified")
    serve.add_argument("--prompt-version", default="unspecified")
    serve.add_argument("--timeout", type=float, default=30)
    serve.add_argument("--max-actions", type=int, default=512)
    serve.add_argument("--max-seconds", type=float, default=3600)
    for command in ("state", "move", "report"):
        player = commands.add_parser(command, help=f"player: {command}")
        player.add_argument("--url", required=True)
        player.add_argument("--token", required=True)
        if command in ("move", "report"):
            player.add_argument("--decision-id", required=True, type=int)
        if command == "move":
            player.add_argument("--decision-token", required=True)
            player.add_argument("--action", required=True)
            player.add_argument("--rationale", default="")
        if command == "report":
            player.add_argument("--label", required=True)
            player.add_argument("--report", required=True)
    replay = commands.add_parser(
        "replay", help="trusted orchestrator: verify a journal"
    )
    replay.add_argument("--engine", type=Path, required=True)
    replay.add_argument("--journal", type=Path, required=True)
    return result


def _interrupt(signum, frame):
    raise KeyboardInterrupt


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    if args.command in ("serve", "replay"):
        from w3gwent.llm_host import MatchHost, replay_journal

        if args.command == "replay":
            try:
                packet = replay_journal([str(args.engine)], args.journal)
            except Exception:
                print(json.dumps({"error": "replay failed"}), file=sys.stderr)
                return 1
            print(json.dumps(packet))
            return 0
        try:
            if not math.isfinite(args.max_seconds) or args.max_seconds <= 0:
                raise ValueError("invalid runtime limit")
            if args.reports and args.reports.resolve() == args.journal.resolve():
                raise ValueError("reports must not share the journal path")
            match = json.loads(args.match_config.read_text())
            opponent = json.loads(args.opponent_config.read_text())
            if not isinstance(match, dict) or not isinstance(opponent, dict):
                raise ValueError("expected config objects")
            signal.signal(signal.SIGTERM, _interrupt)
            with MatchHost(
                [str(args.engine)],
                match,
                opponent,
                seat=args.seat,
                journal_path=args.journal,
                timeout=args.timeout,
                max_actions=args.max_actions,
                metadata={"model": args.model, "prompt_version": args.prompt_version},
            ) as host:
                with ConnectorServer(
                    host,
                    port=args.port,
                    reports_path=args.reports,
                    journal_path=args.journal,
                ) as server:
                    print(
                        json.dumps({"url": server.url, "token": server.token}),
                        flush=True,
                    )
                    if not server.run_for(args.max_seconds):
                        print(
                            json.dumps({"error": "session runtime limit reached"}),
                            file=sys.stderr,
                        )
                        return 1
        except KeyboardInterrupt:
            return 0
        except Exception:
            print(json.dumps({"error": "could not start connector"}), file=sys.stderr)
            return 1
        return 0
    data = None
    if args.command == "move":
        data = {
            "action_id": args.action,
            "decision_id": args.decision_id,
            "token": args.decision_token,
            "rationale": args.rationale,
        }
    if args.command == "report":
        data = {
            "decision_id": args.decision_id,
            "label": args.label,
            "report": args.report,
        }
    status, packet = player_request(args.url, args.token, "/" + args.command, data)
    print(json.dumps(packet), file=sys.stderr if status else sys.stdout)
    return status


if __name__ == "__main__":
    raise SystemExit(main())
