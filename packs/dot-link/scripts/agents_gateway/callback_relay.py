"""Private callback relay: no credentials, queue mounts or public listening port."""
from __future__ import annotations

import argparse
import base64
import binascii
import json
import ssl
from http.server import BaseHTTPRequestHandler

import server

MAX_REQUEST = 2 * server.MAX_BODY_BYTES + 8192
ALLOWED_HEADERS = {"Content-Type", "webhook-id", "webhook-timestamp", "webhook-signature", "X-MCP-Subscription-Id"}
SAFE_ERRORS = {"invalid_url", "dns_failure", "non_public_address"}


def make_handler(peer: str, transport: server.SafeWebhookClient):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, _format, *_args):
            pass

        def send_json(self, status, value):
            raw = server.canonical_json(value).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            self.send_json(200 if self.path == "/healthz" else 404, {"status": "ok"} if self.path == "/healthz" else {"error": "not_found"})

        def do_POST(self):
            if self.client_address[0] != peer:
                self.send_json(403, {"error": "forbidden"})
                return
            try:
                if self.path not in {"/validate", "/deliver"} or self.headers.get("Transfer-Encoding"):
                    raise ValueError("invalid_request")
                if self.headers.get("Content-Type") != "application/json":
                    raise ValueError("invalid_request")
                length = int(self.headers.get("Content-Length", "-1"))
                if not 0 < length <= MAX_REQUEST:
                    raise ValueError("invalid_request")
                self.connection.settimeout(3)
                request = server.parse_json(self.rfile.read(length))
                fields = {"url"} if self.path == "/validate" else {"url", "headers", "body_b64", "timeout"}
                if not isinstance(request, dict) or set(request) != fields:
                    raise ValueError("invalid_request")
                url = request["url"]
                if not isinstance(url, str) or len(url) > 8192:
                    raise ValueError("invalid_request")
                transport.validate_url(url)
                if self.path == "/validate":
                    self.send_json(200, {"ok": True})
                    return
                headers = request["headers"]
                if not isinstance(headers, dict) or set(headers) != ALLOWED_HEADERS:
                    raise ValueError("invalid_request")
                if any(not isinstance(v, str) or len(v) > 1024 or "\r" in v or "\n" in v for v in headers.values()):
                    raise ValueError("invalid_request")
                if headers["Content-Type"] != "application/json":
                    raise ValueError("invalid_request")
                body = base64.b64decode(request["body_b64"], validate=True)
                timeout = request["timeout"]
                if len(body) > server.MAX_BODY_BYTES or type(timeout) is not int or not 1 <= timeout <= server.CALLBACK_TIMEOUT_SECONDS:
                    raise ValueError("invalid_request")
                status, reply = transport.post(url, headers, body, timeout)
                self.send_json(200, {"status": status, "body_b64": base64.b64encode(reply).decode()})
            except server.UnsafeCallback as error:
                self.send_json(400, {"error": str(error) if str(error) in SAFE_ERRORS else "connection_refused"})
            except TimeoutError:
                self.send_json(503, {"error": "timeout"})
            except ssl.SSLError:
                self.send_json(503, {"error": "tls_error"})
            except (ValueError, TypeError, binascii.Error, server.GatewayError):
                self.send_json(400, {"error": "invalid_request"})
            except Exception:
                self.send_json(503, {"error": "connection_refused"})
    return Handler


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Private fixed-peer callback relay")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--peer", required=True)
    args = parser.parse_args()
    httpd = server.QuietThreadingHTTPServer((args.host, args.port), make_handler(args.peer, server.SafeWebhookClient()))
    httpd.serve_forever()
