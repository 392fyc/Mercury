#!/usr/bin/env python3
"""Small authenticated queue client; never executes event contents."""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


MAX_BYTES = 262144
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,95}\Z")
POLICY_FILE = Path.home() / '.codex' / 'dot-link' / 'event-policy' / 'targets.json'


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def request(base: str, token_file: Path, operation: str, payload: dict) -> dict:
    parsed = urllib.parse.urlsplit(base)
    if parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ('', '/'):
        raise ValueError('The base URL must be an origin without credentials, query or path')
    if parsed.scheme != 'https' and base != 'http://127.0.0.1:8765':
        raise ValueError('HTTPS is required except for the fixed in-container loopback origin')
    token = token_file.read_text(encoding='utf-8').strip()
    if not re.fullmatch(r'[A-Za-z0-9_-]{32,256}', token):
        raise ValueError('Invalid credential file')
    if operation == 'list':
        after = payload.get('after', 0)
        if isinstance(after, bool) or not isinstance(after, int) or after < 0:
            raise ValueError('after must be a nonnegative integer')
        path, data, method = '/api/events?after=' + str(after), None, 'GET'
    elif operation == 'publish':
        path, data, method = '/api/events', json.dumps(payload, separators=(',', ':')).encode(), 'POST'
    elif operation == 'consume':
        path, data, method = '/api/consumed', json.dumps(payload, separators=(',', ':')).encode(), 'POST'
    else:
        raise ValueError('Unknown operation')
    if data is not None and len(data) > MAX_BYTES:
        raise ValueError('Request too large')
    req = urllib.request.Request(base.rstrip('/') + path, data=data, method=method,
                                 headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
    # Never forward a credential to a redirect target or print a response's headers.
    opener = urllib.request.build_opener(NoRedirect)
    try:
        with opener.open(req, timeout=20) as response:
            raw = response.read(MAX_BYTES + 1)
    except urllib.error.HTTPError as exc:
        raise ValueError('Queue request rejected with HTTP ' + str(exc.code)) from None
    if len(raw) > MAX_BYTES:
        raise ValueError('Response too large')
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError('Queue response must be an object')
    return value


def through_ssh(config_file: Path, operation: str, payload: dict) -> dict:
    # Installation-owned policy is not an event or CLI input. The installer must
    # restrict Windows ACLs to the owner, SYSTEM and Administrators.
    policy_file = POLICY_FILE
    if any(path.is_symlink() for path in (policy_file, policy_file.parent, policy_file.parent.parent)):
        raise ValueError('Installation policy must not use symbolic links')
    if policy_file.samefile(config_file):
        raise ValueError('Configuration cannot be the installation policy')
    if os.name != 'nt':
        info = policy_file.stat()
        if info.st_uid != os.getuid() or info.st_mode & 0o022:
            raise ValueError('Installation policy must be owner-controlled')
    cfg = json.loads(config_file.read_text(encoding='utf-8'))
    policy = json.loads(policy_file.read_text(encoding='utf-8'))
    fields = {'ssh_host', 'container', 'principal', 'docker', 'docker_config'}
    if not isinstance(cfg, dict) or not isinstance(policy, dict) or set(cfg) != fields or set(policy) != fields:
        raise ValueError('SSH configuration and installation policy must contain exactly the approved fields')
    if cfg != policy:
        raise ValueError('SSH target differs from the approved installation policy')
    for name in ('ssh_host', 'container', 'principal'):
        if not isinstance(cfg.get(name), str) or not NAME.fullmatch(cfg[name]):
            raise ValueError('Invalid SSH configuration field: ' + name)
    for name in ('docker', 'docker_config'):
        if not isinstance(cfg.get(name), str) or not cfg[name].startswith('/') or any(c in cfg[name] for c in '\r\n\x00'):
            raise ValueError('Invalid absolute path: ' + name)
    remote = [cfg['docker'], '--config', cfg['docker_config'], '-H', 'unix:///var/run/system-docker.sock',
              'exec', '-i', cfg['container'], 'python', '/app/client.py',
              '--url', 'http://127.0.0.1:8765', '--token-file', '/credentials/' + cfg['principal'] + '.token', operation]
    command = ' '.join(shlex.quote(item) for item in remote)
    result = subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=15', cfg['ssh_host'], command],
                            input=json.dumps(payload).encode(), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            timeout=45, check=False)
    if result.returncode:
        # Remote stderr may contain operational details; do not propagate it into notifications.
        raise ValueError('SSH queue command failed (exit ' + str(result.returncode) + ')')
    if len(result.stdout) > MAX_BYTES:
        raise ValueError('SSH response too large')
    value = json.loads(result.stdout)
    if not isinstance(value, dict):
        raise ValueError('SSH response must be an object')
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--ssh-config', type=Path)
    mode.add_argument('--url')
    parser.add_argument('--token-file', type=Path)
    parser.add_argument('--payload-file', type=Path)
    parser.add_argument('operation', choices=['list', 'publish', 'consume'])
    args = parser.parse_args()
    try:
        raw = args.payload_file.read_bytes() if args.payload_file else sys.stdin.buffer.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ValueError('Input too large')
        payload = json.loads(raw or b'{}')
        if not isinstance(payload, dict):
            raise ValueError('Input must be an object')
        if args.ssh_config:
            value = through_ssh(args.ssh_config, args.operation, payload)
        else:
            if not args.token_file:
                raise ValueError('--token-file is required with --url')
            value = request(args.url, args.token_file, args.operation, payload)
        print(json.dumps(value, ensure_ascii=False, separators=(',', ':')))
        return 0
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        print(json.dumps({'error': type(exc).__name__, 'detail': str(exc)}), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
