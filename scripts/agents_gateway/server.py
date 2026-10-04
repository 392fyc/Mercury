#!/usr/bin/env python3
"""Small, detachable event queue and MCP Events webhook gateway.

The service moves fixed, hashed task metadata between authenticated principals.
It has no model runtime, task executor, callback response interpreter, or command
runner. Runtime dependencies are limited to the Python standard library.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import hmac
import http.client
import ipaddress
import json
import os
import re
import socket
import sqlite3
import ssl
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable
from urllib.parse import parse_qs, urlsplit


MCP_VERSION = "2026-07-28"
SERVER_NAME = "agents-event-gateway"
SERVER_VERSION = "0.1.0"
EVENT_NAME = "agent.update"
EVENT_KINDS = frozenset(
    {"created", "started", "updated", "completed", "failed", "needs_attention", "cancelled"}
)
EVENT_REQUIRED = frozenset(
    {"event_id", "recipient", "task_id", "request_id", "kind", "body_sha256"}
)
EVENT_OPTIONAL = frozenset({"result_page_id"})
MAX_CONFIG_BYTES = 64 * 1024
MAX_BODY_BYTES = 32 * 1024
MAX_CALLBACK_RESPONSE_BYTES = 16 * 1024
MAX_QUEUE_PAGE = 100
DEFAULT_TTL_MS = 60 * 60 * 1000
MIN_TTL_MS = 60 * 1000
MAX_TTL_MS = 24 * 60 * 60 * 1000
MAX_DELIVERY_ATTEMPTS = 5
CALLBACK_TIMEOUT_SECONDS = 10
TOKEN_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,62}$")
OPAQUE_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._:/-]{0,255}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class GatewayError(Exception):
    """Expected request or state error with a safe external response."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


class ConfigError(Exception):
    pass


class UnsafeCallback(Exception):
    pass


class QuietThreadingHTTPServer(ThreadingHTTPServer):
    """Hide exception details raised while handling untrusted HTTP requests."""

    def handle_error(self, request: Any, client_address: Any) -> None:
        # The base implementation prints a traceback, which may include caller data.
        print("agents_gateway request_error", file=sys.stderr, flush=True)


class RpcFault(Exception):
    def __init__(self, code: int, message: str, data: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def parse_json(raw: bytes) -> Any:
    try:
        return json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=reject_duplicate_keys,
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError("invalid JSON number")),
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise GatewayError("invalid_json") from exc


def _safe_text(value: Any, field: str, maximum: int = 128) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise GatewayError(f"invalid_{field}")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise GatewayError(f"invalid_{field}")
    return value


def validate_event(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise GatewayError("event_must_be_object")
    keys = set(payload)
    if not EVENT_REQUIRED <= keys or keys - EVENT_REQUIRED - EVENT_OPTIONAL:
        raise GatewayError("invalid_event_fields")
    event = {
        "event_id": _safe_text(payload["event_id"], "event_id"),
        "recipient": _safe_text(payload["recipient"], "recipient", 63),
        "task_id": _safe_text(payload["task_id"], "task_id"),
        "request_id": _safe_text(payload["request_id"], "request_id"),
        "kind": _safe_text(payload["kind"], "kind", 32),
        "body_sha256": _safe_text(payload["body_sha256"], "body_sha256", 64),
    }
    for field in ("event_id", "task_id", "request_id"):
        if not OPAQUE_ID_RE.fullmatch(event[field]):
            raise GatewayError(f"invalid_{field}")
    if not TOKEN_ID_RE.fullmatch(event["recipient"]):
        raise GatewayError("invalid_recipient")
    if event["kind"] not in EVENT_KINDS:
        raise GatewayError("invalid_kind")
    if not SHA256_RE.fullmatch(event["body_sha256"]):
        raise GatewayError("invalid_body_sha256")
    if "result_page_id" in payload:
        event["result_page_id"] = _safe_text(payload["result_page_id"], "result_page_id", 256)
        if not OPAQUE_ID_RE.fullmatch(event["result_page_id"]):
            raise GatewayError("invalid_result_page_id")
    return event


def load_config(path: str) -> dict[str, dict[str, Any]]:
    try:
        with open(path, "rb") as handle:
            raw = handle.read(MAX_CONFIG_BYTES + 1)
    except OSError as exc:
        raise ConfigError("configuration_unavailable") from exc
    if len(raw) > MAX_CONFIG_BYTES:
        raise ConfigError("configuration_too_large")
    try:
        config = json.loads(raw.decode("utf-8"), object_pairs_hook=reject_duplicate_keys)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ConfigError("configuration_invalid") from exc
    if not isinstance(config, dict) or set(config) != {"principals"}:
        raise ConfigError("configuration_invalid")
    entries = config["principals"]
    if not isinstance(entries, list) or not entries or len(entries) > 64:
        raise ConfigError("configuration_invalid")
    principals: dict[str, dict[str, Any]] = {}
    hashes: set[str] = set()
    for item in entries:
        if not isinstance(item, dict) or set(item) != {"id", "token_sha256", "allowed_recipients"}:
            raise ConfigError("configuration_invalid")
        principal_id = item["id"]
        token_hash = item["token_sha256"]
        recipients = item["allowed_recipients"]
        if not isinstance(principal_id, str) or not TOKEN_ID_RE.fullmatch(principal_id):
            raise ConfigError("configuration_invalid")
        if not isinstance(token_hash, str) or not SHA256_RE.fullmatch(token_hash):
            raise ConfigError("configuration_invalid")
        if token_hash in hashes or principal_id in principals:
            raise ConfigError("configuration_invalid")
        if not isinstance(recipients, list) or len(recipients) > 64:
            raise ConfigError("configuration_invalid")
        if any(not isinstance(value, str) or not TOKEN_ID_RE.fullmatch(value) for value in recipients):
            raise ConfigError("configuration_invalid")
        if len(set(recipients)) != len(recipients):
            raise ConfigError("configuration_invalid")
        principals[principal_id] = {
            "id": principal_id,
            "token_sha256": token_hash,
            "allowed_recipients": frozenset(recipients),
        }
        hashes.add(token_hash)
    if any(recipient not in principals for p in principals.values() for recipient in p["allowed_recipients"]):
        raise ConfigError("configuration_invalid")
    return principals


def authenticate(config_path: str, authorization: str | None) -> dict[str, Any] | None:
    if not isinstance(authorization, str) or not authorization.startswith("Bearer "):
        return None
    token = authorization[7:]
    if not token or len(token) > 512 or any(ord(char) < 33 or ord(char) > 126 for char in token):
        return None
    digest = hashlib.sha256(token.encode("ascii")).hexdigest()
    for principal in load_config(config_path).values():
        if hmac.compare_digest(digest, principal["token_sha256"]):
            return principal
    return None


def is_public_ip(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return bool(
        address.is_global
        and not address.is_private
        and not address.is_loopback
        and not address.is_link_local
        and not address.is_multicast
        and not address.is_reserved
        and not address.is_unspecified
    )


def callback_host_and_port(url: str) -> tuple[Any, str, int, str]:
    if not isinstance(url, str) or len(url) > 2048:
        raise UnsafeCallback("invalid_url")
    if "\\" in url or any(ord(char) <= 32 or ord(char) == 127 for char in url):
        raise UnsafeCallback("invalid_url")
    try:
        parsed = urlsplit(url)
        port = parsed.port if parsed.port is not None else 443
    except ValueError as exc:
        raise UnsafeCallback("invalid_url") from exc
    if (
        parsed.scheme.lower() != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or not (1 <= port <= 65535)
    ):
        raise UnsafeCallback("invalid_url")
    hostname = parsed.hostname.rstrip(".")
    if not hostname or len(hostname) > 253:
        raise UnsafeCallback("invalid_url")
    path = parsed.path or "/"
    if parsed.query:
        path += "?" + parsed.query
    return parsed, hostname, port, path


class SafeWebhookClient:
    """HTTPS client that validates and pins public DNS results for each request."""

    def __init__(self, resolver: Callable[..., list[tuple[Any, ...]]] = socket.getaddrinfo):
        self.resolver = resolver
        self.context = ssl.create_default_context()

    def resolve(self, hostname: str, port: int) -> list[tuple[Any, ...]]:
        try:
            literal = ipaddress.ip_address(hostname)
            if not is_public_ip(str(literal)):
                raise UnsafeCallback("non_public_address")
            family = socket.AF_INET6 if literal.version == 6 else socket.AF_INET
            sockaddr: tuple[Any, ...] = (str(literal), port, 0, 0) if family == socket.AF_INET6 else (str(literal), port)
            return [(family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", sockaddr)]
        except ValueError:
            pass
        try:
            records = self.resolver(hostname, port, type=socket.SOCK_STREAM)
        except (OSError, UnicodeError) as exc:
            raise UnsafeCallback("dns_failure") from exc
        if not records:
            raise UnsafeCallback("dns_failure")
        safe: list[tuple[Any, ...]] = []
        for record in records:
            family, socktype, proto, canonname, sockaddr = record
            if family not in (socket.AF_INET, socket.AF_INET6) or not is_public_ip(sockaddr[0]):
                raise UnsafeCallback("non_public_address")
            safe.append((family, socktype, proto, canonname, sockaddr))
        return safe

    def validate_url(self, url: str) -> None:
        _parsed, hostname, port, _path = callback_host_and_port(url)
        self.resolve(hostname, port)

    def post(self, url: str, headers: dict[str, str], body: bytes, timeout: int) -> tuple[int, bytes]:
        _parsed, hostname, port, path = callback_host_and_port(url)
        addresses = self.resolve(hostname, port)
        failures: list[Exception] = []
        for family, socktype, proto, _canonname, sockaddr in addresses:
            connection = PinnedHTTPSConnection(
                hostname, port, sockaddr, family, socktype, proto, self.context, timeout
            )
            try:
                connection.request("POST", path, body=body, headers=headers)
                response = connection.getresponse()
                response_body = response.read(MAX_CALLBACK_RESPONSE_BYTES + 1)
                if len(response_body) > MAX_CALLBACK_RESPONSE_BYTES:
                    response_body = b""
                return response.status, response_body
            except (OSError, ssl.SSLError, http.client.HTTPException) as exc:
                failures.append(exc)
            finally:
                connection.close()
        if failures:
            raise failures[-1]
        raise OSError("callback_connection_failed")


class PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(
        self,
        host: str,
        port: int,
        sockaddr: tuple[Any, ...],
        family: int,
        socktype: int,
        proto: int,
        context: ssl.SSLContext,
        timeout: int,
    ):
        super().__init__(host=host, port=port, timeout=timeout, context=context)
        self._sockaddr = sockaddr
        self._family = family
        self._socktype = socktype
        self._proto = proto

    def connect(self) -> None:
        sock = socket.socket(self._family, self._socktype, self._proto)
        sock.settimeout(self.timeout)
        try:
            sock.connect(self._sockaddr)
            self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
        except Exception:
            sock.close()
            raise


def signature(secret: bytes, webhook_id: str, timestamp: str, body: bytes) -> str:
    signed = webhook_id.encode("utf-8") + b"." + timestamp.encode("ascii") + b"." + body
    digest = hmac.new(secret, signed, hashlib.sha256).digest()
    return "v1," + base64.b64encode(digest).decode("ascii")


def format_time(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


class Gateway:
    def __init__(
        self,
        config_path: str,
        database_path: str,
        callback_transport: Any | None = None,
        clock: Callable[[], float] = time.time,
    ):
        self.config_path = os.path.abspath(config_path)
        self.database_path = os.path.abspath(database_path)
        self.clock = clock
        self.callback_transport = callback_transport or SafeWebhookClient()
        os.makedirs(os.path.dirname(self.database_path) or ".", mode=0o700, exist_ok=True)
        self.db_lock = threading.RLock()
        self.db = sqlite3.connect(self.database_path, check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.execute("PRAGMA busy_timeout = 5000")
        self.db.execute("PRAGMA journal_mode = WAL")
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS events (
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT NOT NULL UNIQUE,
                publisher_id TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                payload_sha256 TEXT NOT NULL,
                created_at REAL NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('pending','sent','consumed'))
            );
            CREATE INDEX IF NOT EXISTS events_recipient_seq ON events(json_extract(payload_json, '$.recipient'), seq);
            CREATE TABLE IF NOT EXISTS subscriptions (
                id TEXT PRIMARY KEY,
                principal_id TEXT NOT NULL,
                principal_fingerprint TEXT NOT NULL,
                url TEXT NOT NULL,
                name TEXT NOT NULL,
                arguments_json TEXT NOT NULL,
                secret_b64 TEXT NOT NULL,
                expires_at REAL NOT NULL,
                active INTEGER NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS subscriptions_active_expiry ON subscriptions(active, expires_at);
            CREATE TABLE IF NOT EXISTS outbox (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT NOT NULL REFERENCES events(event_id),
                subscription_id TEXT NOT NULL REFERENCES subscriptions(id),
                state TEXT NOT NULL CHECK(state IN ('pending','sending','sent','failed','cancelled')),
                attempts INTEGER NOT NULL DEFAULT 0,
                next_attempt REAL NOT NULL,
                lease_until REAL,
                last_status INTEGER,
                last_error TEXT,
                UNIQUE(event_id, subscription_id)
            );
            CREATE INDEX IF NOT EXISTS outbox_due ON outbox(state, next_attempt, lease_until);
            """
        )
        try:
            os.chmod(self.database_path, 0o600)
        except OSError:
            pass
        self._verification_cache: dict[tuple[str, str], float] = {}
        self._cache_lock = threading.Lock()
        self.worker_stop = threading.Event()

    def principals(self) -> dict[str, dict[str, Any]]:
        return load_config(self.config_path)

    def principal_for_token(self, authorization: str | None) -> dict[str, Any] | None:
        return authenticate(self.config_path, authorization)

    def publish(self, publisher: dict[str, Any], raw_event: Any) -> tuple[dict[str, Any], bool]:
        event = validate_event(raw_event)
        recipient = event["recipient"]
        if recipient not in publisher["allowed_recipients"]:
            raise GatewayError("recipient_forbidden", 403)
        if recipient not in self.principals():
            raise GatewayError("recipient_unknown", 400)
        payload_json = canonical_json(event)
        payload_digest = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
        now = self.clock()
        principals = self.principals()
        with self.db_lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                prior = self.db.execute(
                    "SELECT * FROM events WHERE event_id = ?", (event["event_id"],)
                ).fetchone()
                if prior is not None:
                    if prior["publisher_id"] != publisher["id"] or prior["payload_json"] != payload_json:
                        raise GatewayError("event_id_conflict", 409)
                    self.db.execute("COMMIT")
                    return self._event_row(prior), True
                self.db.execute(
                    "INSERT INTO events(event_id,publisher_id,payload_json,payload_sha256,created_at,status) "
                    "VALUES(?,?,?,?,?,'pending')",
                    (event["event_id"], publisher["id"], payload_json, payload_digest, now),
                )
                subscriptions = self.db.execute(
                    "SELECT * FROM subscriptions WHERE active=1 AND expires_at>? AND name=?",
                    (now, EVENT_NAME),
                ).fetchall()
                event_args = canonical_json({"recipient": recipient})
                for subscription in subscriptions:
                    owner = principals.get(subscription["principal_id"])
                    if (
                        owner is not None
                        and owner["token_sha256"] == subscription["principal_fingerprint"]
                        and subscription["arguments_json"] == event_args
                    ):
                        self.db.execute(
                            "INSERT OR IGNORE INTO outbox(event_id,subscription_id,state,next_attempt) "
                            "VALUES(?,?,'pending',?)",
                            (event["event_id"], subscription["id"], now),
                        )
                row = self.db.execute("SELECT * FROM events WHERE event_id=?", (event["event_id"],)).fetchone()
                self.db.execute("COMMIT")
                return self._event_row(row), False
            except Exception:
                if self.db.in_transaction:
                    self.db.execute("ROLLBACK")
                raise

    def _event_row(self, row: sqlite3.Row) -> dict[str, Any]:
        event = json.loads(row["payload_json"])
        return {
            "seq": row["seq"],
            **event,
            "status": row["status"],
            "created_at": format_time(row["created_at"]),
        }

    def list_events(self, principal_id: str, after: int = 0, limit: int = MAX_QUEUE_PAGE) -> dict[str, Any]:
        if after < 0 or limit < 1 or limit > MAX_QUEUE_PAGE:
            raise GatewayError("invalid_page")
        with self.db_lock:
            rows = self.db.execute(
                "SELECT * FROM events WHERE json_extract(payload_json,'$.recipient')=? AND seq>? "
                "ORDER BY seq LIMIT ?",
                (principal_id, after, limit),
            ).fetchall()
            events = [self._event_row(row) for row in rows]
        next_after = events[-1]["seq"] if events else after
        return {"events": events, "next_after": next_after}

    def mark_consumed(self, principal_id: str, event_id: str) -> dict[str, Any]:
        event_id = _safe_text(event_id, "event_id")
        with self.db_lock:
            cursor = self.db.execute(
                "UPDATE events SET status='consumed' WHERE event_id=? "
                "AND json_extract(payload_json,'$.recipient')=?",
                (event_id, principal_id),
            )
            if cursor.rowcount != 1:
                raise GatewayError("event_not_found", 404)
            row = self.db.execute("SELECT * FROM events WHERE event_id=?", (event_id,)).fetchone()
            return self._event_row(row)

    def _subscription_id(
        self, principal_id: str, url: str, name: str, arguments: dict[str, Any]
    ) -> str:
        identity = canonical_json([principal_id, url, name, arguments]).encode("utf-8")
        return "sub_" + hashlib.sha256(identity).hexdigest()[:32]

    @staticmethod
    def _check_whsec(value: Any) -> tuple[str, bytes]:
        if not isinstance(value, str) or not value.startswith("whsec_"):
            raise GatewayError("invalid_delivery_secret")
        encoded = value[6:]
        try:
            secret = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise GatewayError("invalid_delivery_secret") from exc
        if not 24 <= len(secret) <= 64 or base64.b64encode(secret).decode("ascii") != encoded:
            raise GatewayError("invalid_delivery_secret")
        return encoded, secret

    @staticmethod
    def _check_subscription_arguments(principal: dict[str, Any], arguments: Any) -> dict[str, str]:
        if not isinstance(arguments, dict) or set(arguments) != {"recipient"}:
            raise GatewayError("invalid_subscription_arguments")
        recipient = _safe_text(arguments["recipient"], "recipient", 63)
        if recipient != principal["id"]:
            raise GatewayError("subscription_must_target_self", 403)
        return {"recipient": recipient}

    def _verify_callback(
        self,
        principal: dict[str, Any],
        subscription_id: str,
        url: str,
        secret: bytes,
    ) -> None:
        cache_key = (principal["id"], url)
        now = self.clock()
        with self._cache_lock:
            cached_until = self._verification_cache.get(cache_key, 0)
        if cached_until > now:
            return
        challenge = base64.urlsafe_b64encode(os.urandom(24)).decode("ascii").rstrip("=")
        webhook_id = "msg_verification_" + uuid.uuid4().hex
        body = canonical_json({"type": "verification", "challenge": challenge}).encode("utf-8")
        timestamp = str(int(now))
        headers = {
            "Content-Type": "application/json",
            "webhook-id": webhook_id,
            "webhook-timestamp": timestamp,
            "webhook-signature": signature(secret, webhook_id, timestamp, body),
            "X-MCP-Subscription-Id": subscription_id,
        }
        try:
            status, response_body = self.callback_transport.post(
                url, headers, body, CALLBACK_TIMEOUT_SECONDS
            )
        except TimeoutError as exc:
            raise RpcFault(-32015, "CallbackEndpointError", {"reason": "timeout"}) from exc
        except ssl.SSLError as exc:
            raise RpcFault(-32015, "CallbackEndpointError", {"reason": "tls_error"}) from exc
        except UnsafeCallback as exc:
            raise RpcFault(-32015, "CallbackEndpointError", {"reason": "connection_refused"}) from exc
        except Exception as exc:
            raise RpcFault(-32015, "CallbackEndpointError", {"reason": "connection_refused"}) from exc
        if status < 200 or status >= 300:
            reason = "http_5xx" if status >= 500 else "http_4xx" if status >= 400 else "challenge_failed"
            raise RpcFault(-32015, "CallbackEndpointError", {"reason": reason})
        if len(response_body) > MAX_CALLBACK_RESPONSE_BYTES:
            raise RpcFault(-32015, "CallbackEndpointError", {"reason": "challenge_failed"})
        try:
            response = parse_json(response_body)
        except GatewayError as exc:
            raise RpcFault(-32015, "CallbackEndpointError", {"reason": "challenge_failed"}) from exc
        returned = response.get("challenge") if isinstance(response, dict) else None
        if not isinstance(returned, str) or not hmac.compare_digest(returned, challenge):
            raise RpcFault(-32015, "CallbackEndpointError", {"reason": "challenge_failed"})
        with self._cache_lock:
            self._verification_cache[cache_key] = now + 300

    def subscribe(self, principal: dict[str, Any], params: Any) -> dict[str, Any]:
        if not isinstance(params, dict) or set(params) - {
            "name", "arguments", "delivery", "cursor", "ttlMs"
        }:
            raise GatewayError("invalid_subscribe_params")
        name = params.get("name")
        if name != EVENT_NAME:
            raise GatewayError("unknown_event")
        arguments = self._check_subscription_arguments(principal, params.get("arguments"))
        delivery = params.get("delivery")
        if not isinstance(delivery, dict) or set(delivery) != {"mode", "url", "secret"}:
            raise GatewayError("invalid_delivery")
        if delivery["mode"] != "webhook":
            raise GatewayError("unsupported_delivery")
        url = delivery["url"]
        if not isinstance(url, str):
            raise GatewayError("invalid_callback_url")
        try:
            self.callback_transport.validate_url(url)
        except UnsafeCallback as exc:
            raise GatewayError("invalid_callback_url") from exc
        secret_b64, secret = self._check_whsec(delivery["secret"])
        cursor = params.get("cursor")
        if cursor is not None:
            raise GatewayError("cursor_not_supported")
        ttl = params.get("ttlMs", DEFAULT_TTL_MS)
        if ttl is None:
            ttl = DEFAULT_TTL_MS
        if isinstance(ttl, bool) or not isinstance(ttl, int) or ttl < 0:
            raise GatewayError("invalid_ttl")
        granted_ms = max(MIN_TTL_MS, min(ttl, MAX_TTL_MS))
        subscription_id = self._subscription_id(principal["id"], url, name, arguments)
        self._verify_callback(principal, subscription_id, url, secret)
        now = self.clock()
        expires_at = now + granted_ms / 1000
        arguments_json = canonical_json(arguments)
        with self.db_lock:
            self.db.execute(
                "INSERT INTO subscriptions(id,principal_id,principal_fingerprint,url,name,arguments_json,secret_b64,expires_at,active,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,1,?,?) "
                "ON CONFLICT(id) DO UPDATE SET principal_fingerprint=excluded.principal_fingerprint, "
                "secret_b64=excluded.secret_b64,expires_at=excluded.expires_at,active=1,updated_at=excluded.updated_at",
                (
                    subscription_id,
                    principal["id"],
                    principal["token_sha256"],
                    url,
                    name,
                    arguments_json,
                    secret_b64,
                    expires_at,
                    now,
                    now,
                ),
            )
        return {
            "id": subscription_id,
            "refreshBefore": format_time(expires_at),
            "cursor": None,
            "truncated": False,
        }

    def unsubscribe(self, principal: dict[str, Any], params: Any) -> dict[str, Any]:
        if not isinstance(params, dict) or set(params) != {"name", "arguments", "delivery"}:
            raise GatewayError("invalid_unsubscribe_params")
        if params["name"] != EVENT_NAME:
            raise GatewayError("unknown_event")
        arguments = self._check_subscription_arguments(principal, params["arguments"])
        delivery = params["delivery"]
        if not isinstance(delivery, dict) or set(delivery) - {"mode", "url"}:
            raise GatewayError("invalid_delivery")
        if delivery.get("mode", "webhook") != "webhook" or not isinstance(delivery.get("url"), str):
            raise GatewayError("invalid_delivery")
        url = delivery["url"]
        try:
            callback_host_and_port(url)
        except UnsafeCallback as exc:
            raise GatewayError("invalid_callback_url") from exc
        subscription_id = self._subscription_id(principal["id"], url, EVENT_NAME, arguments)
        now = self.clock()
        with self.db_lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                self.db.execute(
                    "UPDATE subscriptions SET active=0,updated_at=? WHERE id=? AND principal_id=?",
                    (now, subscription_id, principal["id"]),
                )
                self.db.execute(
                    "UPDATE outbox SET state='cancelled',last_error='unsubscribed' "
                    "WHERE subscription_id=? AND state IN ('pending','sending')",
                    (subscription_id,),
                )
                self.db.execute("COMMIT")
            except Exception:
                if self.db.in_transaction:
                    self.db.execute("ROLLBACK")
                raise
        return {}

    def process_outbox_once(self) -> bool:
        now = self.clock()
        try:
            principals = self.principals()
        except ConfigError:
            return False
        with self.db_lock:
            self.db.execute("BEGIN IMMEDIATE")
            row = self.db.execute(
                "SELECT o.id AS outbox_id,o.event_id,o.subscription_id,o.attempts,e.payload_json,e.created_at,e.status AS event_status, "
                "s.principal_id,s.principal_fingerprint,s.url,s.secret_b64,s.expires_at,s.active "
                "FROM outbox o JOIN events e ON e.event_id=o.event_id "
                "JOIN subscriptions s ON s.id=o.subscription_id "
                "WHERE ((o.state='pending' AND o.next_attempt<=?) OR (o.state='sending' AND o.lease_until<=?)) "
                "ORDER BY o.id LIMIT 1",
                (now, now),
            ).fetchone()
            if row is None:
                self.db.execute("COMMIT")
                return False
            owner = principals.get(row["principal_id"])
            if (
                not row["active"]
                or row["expires_at"] <= now
                or owner is None
                or owner["token_sha256"] != row["principal_fingerprint"]
            ):
                self.db.execute(
                    "UPDATE outbox SET state='cancelled',last_error='subscription_inactive',lease_until=NULL WHERE id=?",
                    (row["outbox_id"],),
                )
                if owner is None or (owner and owner["token_sha256"] != row["principal_fingerprint"]):
                    self.db.execute(
                        "UPDATE subscriptions SET active=0,updated_at=? WHERE id=?",
                        (now, row["subscription_id"]),
                    )
                self.db.execute("COMMIT")
                return True
            attempt = row["attempts"] + 1
            self.db.execute(
                "UPDATE outbox SET state='sending',attempts=?,lease_until=? WHERE id=?",
                (attempt, now + CALLBACK_TIMEOUT_SECONDS + 5, row["outbox_id"]),
            )
            self.db.execute("COMMIT")

        event = json.loads(row["payload_json"])
        envelope = {
            "eventId": row["event_id"],
            "name": EVENT_NAME,
            "timestamp": format_time(row["created_at"]),
            "data": event,
            "cursor": None,
        }
        body = canonical_json(envelope).encode("utf-8")
        timestamp = str(int(now))
        webhook_headers = {
            "Content-Type": "application/json",
            "webhook-id": row["event_id"],
            "webhook-timestamp": timestamp,
            "webhook-signature": signature(
                base64.b64decode(row["secret_b64"], validate=True), row["event_id"], timestamp, body
            ),
            "X-MCP-Subscription-Id": row["subscription_id"],
        }
        status: int | None = None
        failure: str | None = None
        transient = False
        try:
            status, _response = self.callback_transport.post(
                row["url"], webhook_headers, body, CALLBACK_TIMEOUT_SECONDS
            )
            if 200 <= status < 300:
                failure = None
            elif status in (408, 429) or status >= 500:
                failure = "http_5xx" if status >= 500 else "http_4xx"
                transient = True
            elif status in (410, 413):
                failure = "http_4xx"
            elif status >= 400:
                failure = "http_4xx"
            else:
                failure = "http_4xx"
        except TimeoutError:
            failure, transient = "timeout", True
        except ssl.SSLError:
            failure, transient = "tls_error", False
        except UnsafeCallback:
            failure, transient = "unsafe_callback", False
        except OSError:
            failure, transient = "connection_refused", True
        except Exception:
            failure, transient = "connection_refused", True

        finished = self.clock()
        with self.db_lock:
            if failure is None:
                self.db.execute(
                    "UPDATE outbox SET state='sent',last_status=?,last_error=NULL,lease_until=NULL WHERE id=?",
                    (status, row["outbox_id"]),
                )
                self.db.execute(
                    "UPDATE events SET status='sent' WHERE event_id=? AND status='pending' "
                    "AND EXISTS(SELECT 1 FROM outbox WHERE event_id=? AND state='sent') "
                    "AND NOT EXISTS(SELECT 1 FROM outbox WHERE event_id=? AND state IN ('pending','sending'))",
                    (row["event_id"], row["event_id"], row["event_id"]),
                )
            elif transient and attempt < MAX_DELIVERY_ATTEMPTS:
                delay = min(2 ** (attempt - 1), 30)
                self.db.execute(
                    "UPDATE outbox SET state='pending',next_attempt=?,last_status=?,last_error=?,lease_until=NULL WHERE id=?",
                    (finished + delay, status, failure, row["outbox_id"]),
                )
            else:
                self.db.execute(
                    "UPDATE outbox SET state='failed',last_status=?,last_error=?,lease_until=NULL WHERE id=?",
                    (status, failure, row["outbox_id"]),
                )
        return True

    def worker_loop(self, poll_seconds: float = 0.25) -> None:
        while not self.worker_stop.is_set():
            try:
                processed = self.process_outbox_once()
            except Exception as exc:
                # Error class only: exception text may contain a callback URL.
                print(f"agents_gateway worker_error={type(exc).__name__}", file=sys.stderr, flush=True)
                processed = False
            if not processed:
                self.worker_stop.wait(poll_seconds)

    def close(self) -> None:
        self.worker_stop.set()
        with self.db_lock:
            self.db.close()

    def _tool_definitions(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "read_queue",
                "description": "Read the authenticated principal's event queue. This never switches recipients.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "after": {"type": "integer", "minimum": 0},
                        "limit": {"type": "integer", "minimum": 1, "maximum": MAX_QUEUE_PAGE},
                    },
                    "additionalProperties": False,
                },
            },
            {
                "name": "mark_consumed",
                "description": "Mark one event in the authenticated principal's queue as consumed.",
                "inputSchema": {
                    "type": "object",
                    "properties": {"event_id": {"type": "string", "maxLength": 128}},
                    "required": ["event_id"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "publish_update",
                "description": "Publish fixed task metadata to an explicitly authorized recipient queue.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "event_id": {"type": "string", "maxLength": 128, "pattern": "^[A-Za-z0-9][A-Za-z0-9._:/-]*$"},
                        "recipient": {"type": "string", "maxLength": 63},
                        "task_id": {"type": "string", "maxLength": 128, "pattern": "^[A-Za-z0-9][A-Za-z0-9._:/-]*$"},
                        "request_id": {"type": "string", "maxLength": 128, "pattern": "^[A-Za-z0-9][A-Za-z0-9._:/-]*$"},
                        "kind": {"type": "string", "enum": sorted(EVENT_KINDS)},
                        "result_page_id": {"type": "string", "maxLength": 256, "pattern": "^[A-Za-z0-9][A-Za-z0-9._:/-]*$"},
                        "body_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                    },
                    "required": sorted(EVENT_REQUIRED),
                    "additionalProperties": False,
                },
            },
        ]

    def _event_definition(self) -> dict[str, Any]:
        payload_properties: dict[str, Any] = {
            "event_id": {"type": "string"},
            "recipient": {"type": "string"},
            "task_id": {"type": "string"},
            "request_id": {"type": "string"},
            "kind": {"type": "string", "enum": sorted(EVENT_KINDS)},
            "body_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
            "result_page_id": {"type": "string"},
        }
        return {
            "name": EVENT_NAME,
            "description": "A fixed metadata update for the authenticated principal's agent queue.",
            "delivery": ["webhook"],
            "inputSchema": {
                "type": "object",
                "properties": {
                    "recipient": {
                        "type": "string",
                        "description": "Must equal the authenticated principal's configured ID.",
                    }
                },
                "required": ["recipient"],
                "additionalProperties": False,
            },
            "payloadSchema": {
                "type": "object",
                "properties": payload_properties,
                "required": sorted(EVENT_REQUIRED),
                "additionalProperties": False,
            },
        }

    def rpc(self, principal: dict[str, Any], request: Any) -> dict[str, Any] | None:
        if not isinstance(request, dict) or request.get("jsonrpc") != "2.0" or not isinstance(request.get("method"), str):
            raise RpcFault(-32600, "Invalid Request")
        method = request["method"]
        request_id = request.get("id")
        has_id = "id" in request
        params = request.get("params", {})
        if params is None:
            params = {}
        if not isinstance(params, dict):
            raise RpcFault(-32602, "Invalid params")
        try:
            if method == "server/discover":
                result = {
                    "resultType": "complete",
                    "supportedVersions": [MCP_VERSION],
                    "capabilities": {"tools": {}, "events": {}},
                }
            elif method == "initialize":
                result = {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                }
            elif method == "notifications/initialized":
                if has_id:
                    raise RpcFault(-32600, "Invalid Request")
                return None
            elif method == "tools/list":
                result = {"tools": self._tool_definitions()}
            elif method == "tools/call":
                tool_name = params.get("name")
                arguments = params.get("arguments", {})
                if not isinstance(arguments, dict):
                    raise RpcFault(-32602, "Invalid params")
                tool_error = False
                try:
                    if tool_name == "read_queue":
                        if set(arguments) - {"after", "limit"}:
                            raise GatewayError("invalid_arguments")
                        after = arguments.get("after", 0)
                        limit = arguments.get("limit", MAX_QUEUE_PAGE)
                        if isinstance(after, bool) or not isinstance(after, int):
                            raise GatewayError("invalid_after")
                        if isinstance(limit, bool) or not isinstance(limit, int):
                            raise GatewayError("invalid_limit")
                        result = self.list_events(principal["id"], after, limit)
                    elif tool_name == "mark_consumed":
                        if set(arguments) != {"event_id"}:
                            raise GatewayError("invalid_arguments")
                        result = self.mark_consumed(principal["id"], arguments["event_id"])
                    elif tool_name == "publish_update":
                        event, duplicate = self.publish(principal, arguments)
                        result = {"event": event, "duplicate": duplicate}
                    else:
                        raise RpcFault(-32602, "Unknown tool")
                except GatewayError as exc:
                    result = {"error": exc.message}
                    tool_error = True
                result = {
                    "content": [{"type": "text", "text": canonical_json(result)}],
                    "structuredContent": result,
                    "isError": tool_error,
                }
            elif method == "events/list":
                if set(params) - {"cursor"} or params.get("cursor") not in (None, ""):
                    raise RpcFault(-32602, "Invalid params")
                result = {"events": [self._event_definition()]}
            elif method == "events/subscribe":
                try:
                    result = self.subscribe(principal, params)
                except GatewayError as exc:
                    code = -32012 if exc.status == 403 else -32602
                    raise RpcFault(code, "Forbidden" if code == -32012 else "Invalid params") from exc
            elif method == "events/unsubscribe":
                try:
                    result = self.unsubscribe(principal, params)
                except GatewayError as exc:
                    code = -32012 if exc.status == 403 else -32602
                    raise RpcFault(code, "Forbidden" if code == -32012 else "Invalid params") from exc
            else:
                raise RpcFault(-32601, "Method not found")
        except RpcFault:
            raise
        except GatewayError as exc:
            code = -32012 if exc.status == 403 else -32602
            raise RpcFault(code, "Forbidden" if code == -32012 else "Invalid params") from exc
        if not has_id:
            return None
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    def make_handler(self) -> type[BaseHTTPRequestHandler]:
        gateway = self

        class Handler(BaseHTTPRequestHandler):
            server_version = ""
            sys_version = ""

            def log_message(self, _format: str, *_args: Any) -> None:
                # The default access log includes query strings and can expose callback URLs.
                return

            def send_json(self, status: int, payload: Any) -> None:
                body = canonical_json(payload).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                self.wfile.write(body)

            def read_json_body(self) -> Any:
                length_header = self.headers.get("Content-Length")
                if length_header is None or not length_header.isdigit():
                    raise GatewayError("invalid_content_length", 411)
                length = int(length_header)
                if length > MAX_BODY_BYTES:
                    raise GatewayError("body_too_large", 413)
                content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
                if content_type != "application/json":
                    raise GatewayError("application_json_required", 415)
                return parse_json(self.rfile.read(length))

            def principal(self) -> dict[str, Any] | None:
                return gateway.principal_for_token(self.headers.get("Authorization"))

            def do_GET(self) -> None:
                parsed = urlsplit(self.path)
                if parsed.path == "/healthz":
                    if parsed.query:
                        self.send_json(400, {"error": "query_not_allowed"})
                    else:
                        self.send_json(200, {"status": "ok"})
                    return
                if parsed.path != "/api/events":
                    self.send_json(404, {"error": "not_found"})
                    return
                try:
                    principal = self.principal()
                except ConfigError:
                    self.send_json(503, {"error": "service_unavailable"})
                    return
                if principal is None:
                    self.send_json(401, {"error": "unauthorized"})
                    return
                try:
                    query = parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True)
                    if set(query) - {"after"} or any(len(values) != 1 for values in query.values()):
                        raise GatewayError("invalid_query")
                    after_text = query.get("after", ["0"])[0]
                    if not after_text.isdigit():
                        raise GatewayError("invalid_after")
                    self.send_json(200, gateway.list_events(principal["id"], int(after_text)))
                except (GatewayError, ValueError):
                    self.send_json(400, {"error": "invalid_query"})

            def do_POST(self) -> None:
                parsed = urlsplit(self.path)
                if parsed.query:
                    self.send_json(400, {"error": "query_not_allowed"})
                    return
                if parsed.path not in ("/mcp", "/api/events", "/api/consumed"):
                    self.send_json(404, {"error": "not_found"})
                    return
                try:
                    principal = self.principal()
                except ConfigError:
                    self.send_json(503, {"error": "service_unavailable"})
                    return
                if principal is None:
                    self.send_json(401, {"error": "unauthorized"})
                    return
                try:
                    request = self.read_json_body()
                    if parsed.path == "/api/events":
                        row, duplicate = gateway.publish(principal, request)
                        self.send_json(200 if duplicate else 202, {"event": row, "duplicate": duplicate})
                        return
                    if parsed.path == "/api/consumed":
                        if not isinstance(request, dict) or set(request) != {"event_id"}:
                            raise GatewayError("invalid_consumed_request")
                        row = gateway.mark_consumed(principal["id"], request["event_id"])
                        self.send_json(200, {"event_id": row["event_id"], "status": "consumed"})
                        return
                    try:
                        response = gateway.rpc(principal, request)
                    except RpcFault as exc:
                        error: dict[str, Any] = {"code": exc.code, "message": exc.message}
                        if exc.data is not None:
                            error["data"] = exc.data
                        request_id = request.get("id") if isinstance(request, dict) else None
                        self.send_json(200, {"jsonrpc": "2.0", "id": request_id, "error": error})
                        return
                    if response is None:
                        self.send_response(202)
                        self.send_header("Content-Length", "0")
                        self.end_headers()
                    else:
                        self.send_json(200, response)
                except GatewayError as exc:
                    self.send_json(exc.status, {"error": exc.message})

        return Handler


def serve(args: argparse.Namespace) -> None:
    gateway = Gateway(args.config, args.database)
    try:
        gateway.principals()
    except ConfigError:
        print("agents_gateway configuration_error", file=sys.stderr, flush=True)
        gateway.close()
        raise SystemExit(2)
    worker = threading.Thread(target=gateway.worker_loop, name="agents-gateway-outbox", daemon=True)
    worker.start()
    server = QuietThreadingHTTPServer((args.host, args.port), gateway.make_handler())
    server.daemon_threads = True
    print(f"agents_gateway listening host={args.host} port={args.port}", flush=True)
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        gateway.worker_stop.set()
        worker.join(timeout=CALLBACK_TIMEOUT_SECONDS + 2)
        server.server_close()
        gateway.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Detachable authenticated agent event queue")
    parser.add_argument("--config", required=True, help="read-only JSON principal configuration")
    parser.add_argument("--database", default="./agents_gateway.sqlite3", help="SQLite queue/outbox path")
    parser.add_argument("--host", default="127.0.0.1", help="bind address; default loopback")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    if not (1 <= args.port <= 65535):
        parser.error("--port must be between 1 and 65535")
    serve(args)


if __name__ == "__main__":
    main()
