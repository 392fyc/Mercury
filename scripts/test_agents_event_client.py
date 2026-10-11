import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import agents_event_client as client


class FakeResponse:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self, _limit):
        return b'{"ok":true}'


class FakeOpener:
    def __init__(self):
        self.requests = []

    def open(self, request, timeout):
        self.requests.append((request, timeout))
        return FakeResponse()


class ClientBoundaryTests(unittest.TestCase):
    def policy_copy(self, config):
        policy = config.parent / 'policy.json'
        policy.write_bytes(config.read_bytes())
        policy.chmod(0o600)
        return policy

    def test_rejects_plain_http_and_credentials_before_reading_token(self):
        for url in ('http://example.com', 'http://127.0.0.1:9999',
                    'https://user:password@example.com', 'https://example.com?token=private'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                client.request(url, Path('missing-credential'), 'list', {})

    def test_never_follows_redirects_with_bearer(self):
        self.assertIsNone(client.NoRedirect().redirect_request(None, None, 302, '', {}, 'https://other.example'))

    def test_ssh_uses_batch_mode_and_remote_credential_file(self):
        with tempfile.TemporaryDirectory() as folder:
            config = Path(folder) / 'bridge.json'
            config.write_text(json.dumps({'ssh_host': 'nas.example', 'container': 'agents-events',
                                         'principal': 'local-agent', 'docker': '/opt/docker',
                                         'docker_config': '/private/path with spaces'}))
            policy = self.policy_copy(config)
            with patch.object(client, 'POLICY_FILE', policy), patch.object(client.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, b'{"events":[]}', b'')) as run:
                self.assertEqual(client.through_ssh(config, 'list', {'after': 0}), {'events': []})
                argv = run.call_args.args[0]
                self.assertIn('BatchMode=yes', argv)
                self.assertIn("'/private/path with spaces'", argv[-1])
                self.assertIn('/credentials/local-agent.token', argv[-1])
                self.assertEqual(run.call_args.kwargs['input'], b'{"after": 0}')

    def test_rejects_shell_or_ssh_option_injection(self):
        with tempfile.TemporaryDirectory() as folder:
            config = Path(folder) / 'bridge.json'
            for field, value in (('ssh_host', '-oProxyCommand=malicious'),
                                 ('container', 'good; touch /tmp/pwn'),
                                 ('principal', '../other'), ('docker', '/opt/docker\nmalicious')):
                cfg = {'ssh_host': 'nas.example', 'container': 'agents-events', 'principal': 'local',
                       'docker': '/opt/docker', 'docker_config': '/private/docker'}
                cfg[field] = value
                config.write_text(json.dumps(cfg))
                policy = self.policy_copy(config)
                with self.subTest(field=field), patch.object(client, 'POLICY_FILE', policy), patch.object(client.subprocess, 'run') as run:
                    with self.assertRaises(ValueError):
                        client.through_ssh(config, 'list', {})
                    run.assert_not_called()

    def test_does_not_expose_remote_error_details(self):
        with tempfile.TemporaryDirectory() as folder:
            config = Path(folder) / 'bridge.json'
            config.write_text(json.dumps({'ssh_host': 'nas.example', 'container': 'agents-events',
                                         'principal': 'local', 'docker': '/opt/docker', 'docker_config': '/private/docker'}))
            policy = self.policy_copy(config)
            with patch.object(client, 'POLICY_FILE', policy), patch.object(client.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1, b'', b'secret')):
                with self.assertRaises(ValueError) as error:
                    client.through_ssh(config, 'list', {})
                self.assertNotIn('secret', str(error.exception))

    def test_well_formed_but_unapproved_target_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            config, policy = Path(folder) / 'bridge.json', Path(folder) / 'policy.json'
            approved = {'ssh_host': 'nas.example', 'container': 'agents-events', 'principal': 'local',
                        'docker': '/opt/docker', 'docker_config': '/private/docker'}
            policy.write_text(json.dumps(approved))
            policy.chmod(0o600)
            for field, value in [('ssh_host', 'other.example'), ('container', 'other-container'), ('principal', 'other'),
                                 ('docker', '/other/docker'), ('docker_config', '/other/config')]:
                candidate = dict(approved, **{field: value})
                config.write_text(json.dumps(candidate))
                with self.subTest(field=field), patch.object(client, 'POLICY_FILE', policy), patch.object(client.subprocess, 'run') as run:
                    with self.assertRaises(ValueError):
                        client.through_ssh(config, 'list', {})
                    run.assert_not_called()

    def test_policy_cannot_be_same_file_as_config(self):
        with tempfile.TemporaryDirectory() as folder:
            config = Path(folder) / 'bridge.json'
            config.write_text('{}')
            with patch.object(client, 'POLICY_FILE', config), patch.object(client.subprocess, 'run') as run:
                with self.assertRaises(ValueError):
                    client.through_ssh(config, 'list', {})
                run.assert_not_called()

    def test_rest_operation_mapping_keeps_event_paths_and_adds_fixed_ledger_paths(self):
        with tempfile.TemporaryDirectory() as folder:
            token_file = Path(folder) / 'credential'
            token_file.write_text('a' * 32, encoding='utf-8')
            opener = FakeOpener()
            metadata = {
                'generation': '7d52c2a2-8c11-4de1-9407-45782dcd8f19', 'pairing_id': 'pair-1',
                'key_id': 'key-1', 'client_id': 'codex-main', 'recipient_thread_id': 'thread-1',
                'task_id': 'task-17', 'request_id': 'request-23', 'attempt': 1, 'nonce': 'nonce-1',
                'body_sha256': 'a' * 64, 'expires_at': '2030-03-17T17:46:40Z', 'protocol_sha256': 'b' * 64,
            }
            cases = [
                ('list', {'after': 2}, 'GET', '/api/events?after=2'),
                ('publish', {'event_id': 'evt', 'kind': 'updated'}, 'POST', '/api/events'),
                ('consume', {'event_id': 'evt'}, 'POST', '/api/consumed'),
                ('ledger-status', {'request_id': 'req/1'}, 'GET', '/api/tasks/status?request_id=req%2F1'),
                ('admit', metadata, 'POST', '/api/tasks/admit'),
                ('claim', {'request_id': 'request-23', 'body_sha256': 'a' * 64,
                           'generation': metadata['generation']}, 'POST', '/api/tasks/claim'),
                ('transition', {'request_id': 'request-23', 'body_sha256': 'a' * 64,
                                'generation': metadata['generation'], 'expected_state': 'claimed',
                                'state': 'started', 'evidence_sha256': 'b' * 64},
                 'POST', '/api/tasks/transition'),
            ]
            with patch.object(client.urllib.request, 'build_opener', return_value=opener):
                for operation, payload, method, expected_path in cases:
                    with self.subTest(operation=operation):
                        self.assertEqual(
                            client.request('https://gateway.example', token_file, operation, payload),
                            {'ok': True},
                        )
                        request, timeout = opener.requests[-1]
                        parsed = client.urllib.parse.urlsplit(request.full_url)
                        self.assertEqual(request.get_method(), method)
                        self.assertEqual(parsed.path + (('?' + parsed.query) if parsed.query else ''), expected_path)
                        self.assertLessEqual(timeout, 20)

    def test_ledger_status_rejects_unmapped_query_fields(self):
        with tempfile.TemporaryDirectory() as folder:
            token_file = Path(folder) / 'credential'
            token_file.write_text('a' * 32, encoding='utf-8')
            with patch.object(client.urllib.request, 'build_opener') as opener:
                with self.assertRaises(ValueError):
                    client.request('https://gateway.example', token_file, 'ledger-status', {'recipient': 'other'})
                opener.assert_not_called()

    def test_wait_maps_to_events_read_and_network_timeout_exceeds_server_wait(self):
        with tempfile.TemporaryDirectory() as folder:
            token_file = Path(folder) / 'credential'
            token_file.write_text('a' * 32, encoding='utf-8')
            opener = FakeOpener()
            with patch.object(client.urllib.request, 'build_opener', return_value=opener):
                result = client.request(
                    'https://gateway.example', token_file, 'wait', {'after': 17, 'timeout': 50}
                )
            self.assertEqual(result, {'ok': True})
            request, timeout = opener.requests[0]
            parsed = client.urllib.parse.urlsplit(request.full_url)
            self.assertEqual(request.get_method(), 'GET')
            self.assertEqual(parsed.path, '/api/events')
            self.assertEqual(parsed.query, 'after=17&timeout=50')
            self.assertEqual(timeout, 55)

    def test_wait_rejects_boolean_noninteger_out_of_range_and_unknown_fields(self):
        with tempfile.TemporaryDirectory() as folder:
            token_file = Path(folder) / 'credential'
            token_file.write_text('a' * 32, encoding='utf-8')
            opener = FakeOpener()
            invalid_payloads = (
                {'after': True, 'timeout': 1},
                {'after': 1.5, 'timeout': 1},
                {'after': 0, 'timeout': False},
                {'after': 0, 'timeout': 1.5},
                {'after': 0, 'timeout': 51},
                {'after': 0, 'timeout': 1, 'recipient': 'local'},
                {'after': 0},
            )
            with patch.object(client.urllib.request, 'build_opener', return_value=opener):
                for payload in invalid_payloads:
                    with self.subTest(payload=payload), self.assertRaises(ValueError):
                        client.request('https://gateway.example', token_file, 'wait', payload)
            self.assertEqual(opener.requests, [])

    def test_wait_authentication_error_fails_instead_of_returning_an_empty_queue(self):
        with tempfile.TemporaryDirectory() as folder:
            token_file = Path(folder) / 'credential'
            token_file.write_text('a' * 32, encoding='utf-8')
            opener = FakeOpener()
            denied = client.urllib.error.HTTPError(
                'https://gateway.example/api/events?after=0&timeout=1', 401, 'Unauthorized', None, None
            )
            with patch.object(client.urllib.request, 'build_opener', return_value=opener), patch.object(
                opener, 'open', side_effect=denied
            ):
                with self.assertRaisesRegex(ValueError, 'HTTP 401'):
                    client.request('https://gateway.example', token_file, 'wait', {'timeout': 1})

    def test_new_operations_reuse_the_existing_fixed_ssh_route(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            config = root / 'bridge.json'
            config.write_text(json.dumps({'ssh_host': 'nas.example', 'container': 'agents-events',
                                         'principal': 'local-agent', 'docker': '/opt/docker',
                                         'docker_config': '/private/docker'}))
            policy = self.policy_copy(config)
            payload = {'request_id': 'request-23'}
            with patch.object(client, 'POLICY_FILE', policy), patch.object(
                client.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, b'{"ok":true}', b'')
            ) as run:
                result = client.through_ssh(config, 'ledger-status', payload)
            self.assertEqual(result, {'ok': True})
            argv = run.call_args.args[0]
            self.assertEqual(argv[:5], ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=15'])
            self.assertEqual(argv[5], 'nas.example')
            self.assertIn('agents-events', argv[-1])
            self.assertIn('/credentials/local-agent.token', argv[-1])
            self.assertTrue(argv[-1].endswith('ledger-status'))
            self.assertEqual(run.call_args.kwargs['input'], b'{"request_id": "request-23"}')

    def test_ssh_wait_timeout_exceeds_the_bounded_server_wait(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            config = root / 'bridge.json'
            config.write_text(json.dumps({'ssh_host': 'nas.example', 'container': 'agents-events',
                                         'principal': 'local-agent', 'docker': '/opt/docker',
                                         'docker_config': '/private/docker'}))
            policy = self.policy_copy(config)
            payload = {'after': 9, 'timeout': 50}
            with patch.object(client, 'POLICY_FILE', policy), patch.object(
                client.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, b'{"events":[]}', b'')
            ) as run:
                result = client.through_ssh(config, 'wait', payload)
            self.assertEqual(result, {'events': []})
            self.assertEqual(run.call_args.kwargs['timeout'], 60)
            self.assertTrue(run.call_args.args[0][-1].endswith(' wait'))
            self.assertEqual(run.call_args.kwargs['input'], b'{"after": 9, "timeout": 50}')


if __name__ == '__main__':
    unittest.main()
