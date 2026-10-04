import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import agents_event_client as client


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


if __name__ == '__main__':
    unittest.main()
