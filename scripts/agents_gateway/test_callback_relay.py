"""Exercise relay isolation and the real gateway challenge path without public egress."""
import io
import ipaddress
import json
import socket
import threading
import unittest
from contextlib import redirect_stdout, redirect_stderr
from unittest.mock import patch

import callback_relay
import server
import test_server


class CallbackRelayTests(unittest.TestCase):
    def setUp(self):
        test_server.GatewayTests.setUp(self)
        self.relay = server.QuietThreadingHTTPServer(('127.0.0.1', 0),
                       callback_relay.make_handler('127.0.0.1', self.transport))
        self.thread = threading.Thread(target=self.relay.serve_forever, daemon=True)
        self.thread.start()
        self.client = server.RelayWebhookClient('http://127.0.0.1:' + str(self.relay.server_address[1]))
        self.gateway.callback_transport = self.client

    def tearDown(self):
        self.relay.shutdown()
        self.relay.server_close()
        self.thread.join(timeout=2)
        test_server.GatewayTests.tearDown(self)

    def test_relay_activates_challenge_without_gateway_dns_and_preserves_bytes(self):
        output = io.StringIO()
        original = socket.getaddrinfo
        def internal_only(host, port, *args, **kwargs):
            try:
                ipaddress.ip_address(host)
            except ValueError:
                raise socket.gaierror(-3, 'private-dns-detail')
            return original(host, port, *args, **kwargs)
        with self.assertRaisesRegex(server.UnsafeCallback, 'dns_failure'):
            server.SafeWebhookClient(resolver=internal_only).validate_url('https://callback.example.test/events')
        with patch.object(server.socket, 'getaddrinfo', side_effect=internal_only), redirect_stdout(output), redirect_stderr(output):
            subscription = test_server.GatewayTests.subscribe_local(self)
        self.assertTrue(subscription['id'].startswith('sub_'))
        self.assertEqual(self.gateway.db.execute('SELECT count(*) FROM subscriptions').fetchone()[0], 1)
        url, headers, body = self.transport.requests[-1]
        self.assertEqual(url, 'https://callback.example.test/events?key=private-query')
        self.assertEqual(set(headers), callback_relay.ALLOWED_HEADERS)
        self.assertEqual(json.loads(body)['type'], 'verification')
        expected = server.signature(b's' * 32, headers['webhook-id'], headers['webhook-timestamp'], body)
        self.assertEqual(headers['webhook-signature'], expected)
        for private in ('private-query', 'whsec_', 'callback.example.test', 'private-dns-detail'):
            self.assertNotIn(private, output.getvalue())

    def test_relay_rejects_private_callbacks_and_maps_dns_failure(self):
        with self.assertRaisesRegex(server.UnsafeCallback, 'non_public_address'):
            self.client.validate_url('https://127.0.0.1/private')
        with patch.object(self.transport.validator, 'resolver', side_effect=socket.gaierror(-3, 'private-dns-detail')):
            with self.assertRaisesRegex(server.UnsafeCallback, 'dns_failure'):
                self.client.validate_url('https://callback.example.test/private')
        self.assertEqual(len(self.transport.requests), 0)

    def test_relay_refuses_non_peer_and_injected_headers(self):
        other = server.QuietThreadingHTTPServer(('127.0.0.1', 0),
                callback_relay.make_handler('172.30.12.3', self.transport))
        thread = threading.Thread(target=other.serve_forever, daemon=True)
        thread.start()
        try:
            client = server.RelayWebhookClient('http://127.0.0.1:' + str(other.server_address[1]))
            with self.assertRaises(server.UnsafeCallback):
                client.validate_url('https://callback.example.test/private')
        finally:
            other.shutdown(); other.server_close(); thread.join(timeout=2)
        with self.assertRaises(server.UnsafeCallback):
            self.client.post('https://callback.example.test/private', {'Authorization': 'Bearer synthetic-token'}, b'{}', 10)
        self.assertEqual(len(self.transport.requests), 0)


if __name__ == '__main__':
    unittest.main()
