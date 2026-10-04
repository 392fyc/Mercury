from __future__ import annotations

import base64
import hashlib
import hmac
import http.client
import io
import json
import os
import socket
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import server


class MutableClock:
    def __init__(self) -> None:
        self.value = 1_900_000_000.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class FakeWebhookClient:
    """In-memory transport with the same public-address checks as production."""

    def __init__(self, statuses: list[int] | None = None, echo_challenge: bool = True):
        self.statuses = list(statuses or [])
        self.echo_challenge = echo_challenge
        self.requests: list[tuple[str, dict[str, str], bytes]] = []

        def public_resolver(host: str, port: int, **_kwargs: object) -> list[tuple[object, ...]]:
            return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, host, ("8.8.8.8", port))]

        self.validator = server.SafeWebhookClient(resolver=public_resolver)

    def validate_url(self, url: str) -> None:
        self.validator.validate_url(url)

    def post(self, url: str, headers: dict[str, str], body: bytes, timeout: int) -> tuple[int, bytes]:
        self.validate_url(url)
        self.requests.append((url, dict(headers), body))
        envelope = json.loads(body)
        if envelope.get("type") == "verification":
            if self.echo_challenge:
                return 200, json.dumps({"challenge": envelope["challenge"]}).encode()
            return 200, b'{"challenge":"wrong"}'
        status = self.statuses.pop(0) if self.statuses else 204
        return status, b""


def make_config(path: Path, local_token: str = "local-token", dot_token: str = "dot-token") -> None:
    config = {
        "principals": [
            {
                "id": "sender",
                "token_sha256": hashlib.sha256(b"sender-token").hexdigest(),
                "allowed_recipients": ["local", "dot"],
            },
            {
                "id": "local",
                "token_sha256": hashlib.sha256(local_token.encode()).hexdigest(),
                "allowed_recipients": [],
            },
            {
                "id": "dot",
                "token_sha256": hashlib.sha256(dot_token.encode()).hexdigest(),
                "allowed_recipients": [],
            },
        ]
    }
    path.write_text(json.dumps(config), encoding="utf-8")


def sample_event(event_id: str = "evt-001", recipient: str = "local", **changes: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "event_id": event_id,
        "recipient": recipient,
        "task_id": "task-17",
        "request_id": "request-23",
        "kind": "updated",
        "body_sha256": "a" * 64,
    }
    payload.update(changes)
    return payload


class GatewayTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.config_path = self.root / "principals.json"
        self.db_path = self.root / "queue.sqlite3"
        make_config(self.config_path)
        self.clock = MutableClock()
        self.transport = FakeWebhookClient()
        self.gateway = server.Gateway(
            str(self.config_path), str(self.db_path), self.transport, self.clock
        )
        self.principals = self.gateway.principals()
        self.sender = self.principals["sender"]
        self.local = self.principals["local"]
        self.dot = self.principals["dot"]

    def tearDown(self) -> None:
        self.gateway.close()
        self.temp.cleanup()

    def subscribe_local(
        self,
        ttl_ms: int | None = 60_000,
        url: str = "https://callback.example.test/events?key=private-query",
    ) -> dict[str, object]:
        args: dict[str, object] = {
            "name": server.EVENT_NAME,
            "arguments": {"recipient": "local"},
            "delivery": {
                "mode": "webhook",
                "url": url,
                "secret": "whsec_" + base64.b64encode(b"s" * 32).decode("ascii"),
            },
            "cursor": None,
        }
        if ttl_ms is not None:
            args["ttlMs"] = ttl_ms
        return self.gateway.subscribe(self.local, args)

    def test_rest_auth_receiver_isolation_idempotency_and_consumption(self) -> None:
        httpd, thread, base = self.start_http()
        try:
            status, health = self.request(base + "/healthz")
            self.assertEqual(status, 200)
            self.assertEqual(health, {"status": "ok"})

            status, denied = self.request(base + "/api/events", method="POST", body=sample_event())
            self.assertEqual(status, 401)
            self.assertEqual(denied, {"error": "unauthorized"})

            event = sample_event()
            status, published = self.request(
                base + "/api/events", "sender-token", "POST", event
            )
            self.assertEqual(status, 202)
            self.assertFalse(published["duplicate"])
            second_status, _ = self.request(
                base + "/api/events",
                "sender-token",
                "POST",
                sample_event(event_id="evt-002", recipient="dot"),
            )
            self.assertEqual(second_status, 202)

            status, sender_queue = self.request(base + "/api/events?after=0", "sender-token")
            self.assertEqual(status, 200)
            self.assertEqual(sender_queue["events"], [])

            status, local_queue = self.request(base + "/api/events?after=0", "local-token")
            self.assertEqual(status, 200)
            self.assertEqual(local_queue["events"][0]["event_id"], "evt-001")
            self.assertEqual(len(local_queue["events"]), 1)
            self.assertEqual(local_queue["events"][0]["status"], "pending")
            self.assertNotIn("body", local_queue["events"][0])

            status, _ = self.request(
                base + "/api/events?after=0&recipient=dot", "local-token"
            )
            self.assertEqual(status, 400)

            status, cross_consume = self.request(
                base + "/api/consumed", "sender-token", "POST", {"event_id": "evt-001"}
            )
            self.assertEqual(status, 404)
            self.assertEqual(cross_consume["error"], "event_not_found")

            status, consumed = self.request(
                base + "/api/consumed", "local-token", "POST", {"event_id": "evt-001"}
            )
            self.assertEqual(status, 200)
            self.assertEqual(consumed["status"], "consumed")
            self.assertEqual(self.gateway.list_events("local")["events"][0]["status"], "consumed")
            self.assertEqual(len(self.gateway.list_events("local")["events"]), 1)

            status, duplicate = self.request(
                base + "/api/events", "sender-token", "POST", event
            )
            self.assertEqual(status, 200)
            self.assertTrue(duplicate["duplicate"])
            self.assertEqual(duplicate["event"]["status"], "consumed")

            conflicting = sample_event(body_sha256="b" * 64)
            status, body = self.request(
                base + "/api/events", "sender-token", "POST", conflicting
            )
            self.assertEqual(status, 409)
            self.assertEqual(body["error"], "event_id_conflict")
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=2)

    def test_event_schema_and_recipient_allowlist(self) -> None:
        with self.assertRaises(server.GatewayError) as denied:
            self.gateway.publish(self.local, sample_event(recipient="dot"))
        self.assertEqual(denied.exception.status, 403)
        with self.assertRaises(server.GatewayError):
            self.gateway.publish(self.sender, sample_event(extra_instruction="run this"))
        with self.assertRaises(server.GatewayError):
            self.gateway.publish(self.sender, sample_event(kind="unknown"))
        with self.assertRaises(server.GatewayError):
            self.gateway.publish(self.sender, sample_event(body_sha256="not-a-hash"))
        with self.assertRaises(server.GatewayError):
            self.gateway.publish(self.sender, sample_event(recipient="missing"))

    def test_sqlite_restart_preserves_pending_event(self) -> None:
        self.gateway.publish(self.sender, sample_event())
        self.gateway.close()
        self.gateway = server.Gateway(
            str(self.config_path), str(self.db_path), self.transport, self.clock
        )
        events = self.gateway.list_events("local")["events"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["status"], "pending")
        self.assertEqual(events[0]["event_id"], "evt-001")

    def test_sqlite_restart_preserves_subscription_and_outbox(self) -> None:
        self.subscribe_local()
        self.gateway.publish(self.sender, sample_event())
        self.gateway.close()
        self.gateway = server.Gateway(
            str(self.config_path), str(self.db_path), self.transport, self.clock
        )
        self.assertTrue(self.gateway.process_outbox_once())
        self.assertEqual(self.gateway.list_events("local")["events"][0]["status"], "sent")
        self.assertTrue(any(json.loads(item[2]).get("eventId") == "evt-001" for item in self.transport.requests))

    def test_ssrf_rejection_and_public_dns_pinning_policy(self) -> None:
        def resolver_for(address: str):
            def resolve(_host: str, port: int, **_kwargs: object) -> list[tuple[object, ...]]:
                family = socket.AF_INET6 if ":" in address else socket.AF_INET
                sockaddr = (address, port, 0, 0) if family == socket.AF_INET6 else (address, port)
                return [(family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", sockaddr)]

            return resolve

        for address in ("127.0.0.1", "10.1.2.3", "169.254.1.1", "192.0.2.7", "::1", "fc00::1", "fe80::1"):
            client = server.SafeWebhookClient(resolver=resolver_for(address))
            with self.subTest(address=address), self.assertRaises(server.UnsafeCallback):
                client.validate_url("https://receiver.example.test/hook")
        mixed = server.SafeWebhookClient(
            resolver=lambda host, port, **_kwargs: [
                (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("8.8.8.8", port)),
                (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("127.0.0.1", port)),
            ]
        )
        with self.assertRaises(server.UnsafeCallback):
            mixed.validate_url("https://receiver.example.test/hook")
        public = server.SafeWebhookClient(resolver=resolver_for("8.8.8.8"))
        public.validate_url("https://receiver.example.test/hook")
        with self.assertRaises(server.UnsafeCallback):
            public.validate_url("http://receiver.example.test/hook")
        with self.assertRaises(server.UnsafeCallback):
            public.validate_url("https://user:pass@receiver.example.test/hook")

    def test_subscribe_verification_exact_signature_expiry_and_delivery_retries(self) -> None:
        self.transport.statuses = [503, 204]
        result = self.subscribe_local(ttl_ms=0)
        self.assertTrue(result["id"].startswith("sub_"))
        self.assertEqual(result["cursor"], None)
        verification_url, verify_headers, verify_body = self.transport.requests[0]
        verification = json.loads(verify_body)
        raw_secret = b"s" * 32
        expected = server.signature(
            raw_secret, verify_headers["webhook-id"], verify_headers["webhook-timestamp"], verify_body
        )
        self.assertEqual(verify_headers["webhook-signature"], expected)
        self.assertEqual(verify_headers["X-MCP-Subscription-Id"], result["id"])
        self.assertEqual(verification["type"], "verification")
        self.assertEqual(len(verification["challenge"]), 32)
        self.assertTrue(verification_url.startswith("https://"))
        self.assertEqual(result["refreshBefore"], server.format_time(self.clock() + 60))

        refreshed = self.subscribe_local(ttl_ms=120_000)
        self.assertEqual(refreshed["id"], result["id"])
        # A bounded successful verification cache avoids a second challenge for the same owner and URL.
        self.assertEqual(len(self.transport.requests), 1)

        self.gateway.publish(self.sender, sample_event())
        self.assertTrue(self.gateway.process_outbox_once())
        self.assertEqual(self.gateway.list_events("local")["events"][0]["status"], "pending")
        self.clock.advance(1)
        self.assertTrue(self.gateway.process_outbox_once())
        event_delivery = [
            item for item in self.transport.requests if json.loads(item[2]).get("eventId") == "evt-001"
        ]
        self.assertEqual(len(event_delivery), 2)
        self.assertEqual(event_delivery[0][2], event_delivery[1][2])
        self.assertNotEqual(event_delivery[0][1]["webhook-timestamp"], event_delivery[1][1]["webhook-timestamp"])
        for _url, headers, body in event_delivery:
            timestamp = headers["webhook-timestamp"]
            signed = headers["webhook-id"].encode() + b"." + timestamp.encode() + b"." + body
            expected_signature = "v1," + base64.b64encode(
                hmac.new(raw_secret, signed, hashlib.sha256).digest()
            ).decode()
            self.assertEqual(headers["webhook-signature"], expected_signature)
            self.assertEqual(headers["X-MCP-Subscription-Id"], result["id"])
        delivered = self.gateway.list_events("local")["events"][0]
        self.assertEqual(delivered["status"], "sent")
        self.gateway.mark_consumed("local", "evt-001")
        self.assertEqual(self.gateway.list_events("local")["events"][0]["status"], "consumed")

        # Delivery acknowledgement advances transport state only; the envelope has no execution result.
        delivery_data = json.loads(event_delivery[-1][2])["data"]
        self.assertEqual(set(delivery_data), set(server.EVENT_REQUIRED))
        self.assertNotIn("execution_result", delivery_data)

    def test_expired_subscription_does_not_enqueue_or_deliver(self) -> None:
        self.subscribe_local(ttl_ms=60_000)
        self.clock.advance(60)
        self.gateway.publish(self.sender, sample_event())
        self.assertFalse(self.gateway.process_outbox_once())
        self.assertEqual(len(self.transport.requests), 1)  # verification only
        self.assertEqual(self.gateway.list_events("local")["events"][0]["status"], "pending")

    def test_revoked_principal_cancels_outbox(self) -> None:
        self.subscribe_local()
        self.gateway.publish(self.sender, sample_event())
        make_config(self.config_path, local_token="rotated-local-token")
        self.assertTrue(self.gateway.process_outbox_once())
        with self.gateway.db_lock:
            row = self.gateway.db.execute("SELECT state FROM outbox").fetchone()
        self.assertEqual(row["state"], "cancelled")
        self.assertEqual(len(self.transport.requests), 1)

    def test_unsubscribe_idempotency_and_principal_ownership(self) -> None:
        result = self.subscribe_local()
        delivery = {
            "mode": "webhook",
            "url": "https://callback.example.test/events?key=private-query",
        }
        request = {
            "name": server.EVENT_NAME,
            "arguments": {"recipient": "local"},
            "delivery": delivery,
        }
        with self.assertRaises(server.GatewayError) as not_self:
            self.gateway.unsubscribe(self.dot, request)
        self.assertEqual(not_self.exception.status, 403)
        self.assertEqual(self.gateway.unsubscribe(self.local, request), {})
        self.assertEqual(self.gateway.unsubscribe(self.local, request), {})
        self.gateway.publish(self.sender, sample_event())
        self.assertFalse(self.gateway.process_outbox_once())
        with self.gateway.db_lock:
            subscription = self.gateway.db.execute(
                "SELECT active FROM subscriptions WHERE id=?", (result["id"],)
            ).fetchone()
            outbox_count = self.gateway.db.execute("SELECT COUNT(*) AS n FROM outbox").fetchone()["n"]
        self.assertEqual(subscription["active"], 0)
        self.assertEqual(outbox_count, 0)

    def test_callback_challenge_mismatch_and_non_retryable_status(self) -> None:
        self.transport.echo_challenge = False
        with self.assertRaises(server.RpcFault) as challenge_error:
            self.subscribe_local()
        self.assertEqual(challenge_error.exception.code, -32015)
        self.assertEqual(challenge_error.exception.data, {"reason": "challenge_failed"})

        self.transport.echo_challenge = True
        self.transport.statuses = [410]
        self.subscribe_local()
        self.gateway.publish(self.sender, sample_event())
        self.assertTrue(self.gateway.process_outbox_once())
        self.clock.advance(100)
        self.assertFalse(self.gateway.process_outbox_once())
        with self.gateway.db_lock:
            row = self.gateway.db.execute("SELECT state,attempts FROM outbox").fetchone()
        self.assertEqual(row["state"], "failed")
        self.assertEqual(row["attempts"], 1)

    def test_transient_delivery_retries_are_bounded(self) -> None:
        self.transport.statuses = [503] * server.MAX_DELIVERY_ATTEMPTS
        self.subscribe_local()
        self.gateway.publish(self.sender, sample_event())
        for attempt in range(server.MAX_DELIVERY_ATTEMPTS):
            self.assertTrue(self.gateway.process_outbox_once())
            if attempt < server.MAX_DELIVERY_ATTEMPTS - 1:
                self.clock.advance(2**attempt)
        self.assertFalse(self.gateway.process_outbox_once())
        with self.gateway.db_lock:
            row = self.gateway.db.execute("SELECT state,attempts FROM outbox").fetchone()
        self.assertEqual(row["state"], "failed")
        self.assertEqual(row["attempts"], server.MAX_DELIVERY_ATTEMPTS)

    def test_event_is_sent_only_after_all_matching_callbacks_finish(self) -> None:
        self.subscribe_local(url="https://callback-a.example.test/hook")
        self.subscribe_local(url="https://callback-b.example.test/hook")
        self.transport.statuses = [503]
        self.gateway.publish(self.sender, sample_event())
        self.assertTrue(self.gateway.process_outbox_once())
        self.assertEqual(self.gateway.list_events("local")["events"][0]["status"], "pending")
        self.clock.advance(1)
        self.assertTrue(self.gateway.process_outbox_once())
        self.assertEqual(self.gateway.list_events("local")["events"][0]["status"], "pending")
        self.assertTrue(self.gateway.process_outbox_once())
        self.assertEqual(self.gateway.list_events("local")["events"][0]["status"], "sent")

    def test_mcp_discovery_tools_events_and_self_only_subscription(self) -> None:
        discover = self.gateway.rpc(self.local, {"jsonrpc": "2.0", "id": 1, "method": "server/discover"})
        self.assertEqual(discover["result"]["resultType"], "complete")
        self.assertEqual(discover["result"]["supportedVersions"], [server.MCP_VERSION])
        self.assertEqual(set(discover["result"]["capabilities"]), {"tools", "events"})
        tools = self.gateway.rpc(self.local, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        self.assertEqual(
            {tool["name"] for tool in tools["result"]["tools"]},
            {"read_queue", "mark_consumed", "publish_update"},
        )
        events = self.gateway.rpc(self.local, {"jsonrpc": "2.0", "id": 3, "method": "events/list"})
        self.assertEqual(events["result"]["events"][0]["name"], server.EVENT_NAME)
        self.assertEqual(events["result"]["events"][0]["delivery"], ["webhook"])
        with self.assertRaises(server.RpcFault) as forbidden:
            self.gateway.rpc(
                self.local,
                {
                    "jsonrpc": "2.0",
                    "id": 4,
                    "method": "events/subscribe",
                    "params": {
                        "name": server.EVENT_NAME,
                        "arguments": {"recipient": "dot"},
                        "delivery": {
                            "mode": "webhook",
                            "url": "https://callback.example.test/hook",
                            "secret": "whsec_" + base64.b64encode(b"s" * 32).decode(),
                        },
                    },
                },
            )
        self.assertEqual(forbidden.exception.code, -32012)

    def test_http_handler_exception_is_redacted_from_stderr(self) -> None:
        httpd, thread, _base = self.start_http()
        output = io.StringIO()
        try:
            # A real request reaches the queue read, where the deliberately closed
            # database connection raises inside the HTTP handler thread.
            self.gateway.close()
            connection = http.client.HTTPConnection("127.0.0.1", httpd.server_port, timeout=3)
            with redirect_stderr(output):
                try:
                    connection.request(
                        "GET",
                        "/api/events?after=0",
                        headers={
                            "Authorization": "Bearer local-token",
                            "X-Caller-Data": "sensitive-caller-marker",
                        },
                    )
                    response = connection.getresponse()
                    response.read()
                except (OSError, http.client.HTTPException):
                    pass
                finally:
                    connection.close()
            log = output.getvalue()
            self.assertEqual(log, "agents_gateway request_error\n")
            self.assertNotIn("Traceback", log)
            self.assertNotIn("local-token", log)
            self.assertNotIn("sensitive-caller-marker", log)
            self.assertNotIn("/api/events", log)
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=2)

    def start_http(self):
        httpd = server.QuietThreadingHTTPServer(("127.0.0.1", 0), self.gateway.make_handler())
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        return httpd, thread, f"http://127.0.0.1:{httpd.server_port}"

    @staticmethod
    def request(
        url: str,
        token: str | None = None,
        method: str = "GET",
        body: object | None = None,
    ) -> tuple[int, object]:
        headers: dict[str, str] = {}
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        request = Request(url, data=data, headers=headers, method=method)
        try:
            response = urlopen(request, timeout=3)
        except HTTPError as error_response:
            response = error_response
        with response:
            raw = response.read()
            return response.status, json.loads(raw) if raw else None


if __name__ == "__main__":
    unittest.main()
