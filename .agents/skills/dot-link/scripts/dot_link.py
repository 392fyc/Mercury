#!/usr/bin/env python3
"""Small signed-message helper for dot-local/0.2."""

from __future__ import annotations

import argparse
import base64
import binascii
import datetime as dt
import hashlib
import json
import os
import re
import secrets
import sqlite3
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any


PROTOCOL = "dot-local/0.2"
MESSAGE_TYPES = {"HELLO", "ACK", "TASK", "STATUS", "RESULT", "RECEIPT", "CANCEL"}
BODY_FIELDS = {
    "protocol", "pairing_id", "client_id", "key_id", "type",
    "recipient_thread_id", "source_thread_id", "reply_thread_id", "task_id",
    "request_id", "attempt", "seq", "in_reply_to", "nonce", "expires_at",
    "scope", "payload", "artifacts", "delivery_status", "domain_verdict",
}
PROFILE_FIELDS = {"client_id", "key_id", "public_key_pem"}
PIN_FIELDS = {
    "public_key_pem", "key_id", "client_id", "pairing_id",
    "recipient_thread_id", "authorization_status",
}
CONFIG_FIELDS = {"openssl", "private_key", "public_profile", "pairing_id", "recipient_thread_id"}
OPTIONAL_CONFIG_FIELDS = {"protocol", "recipient_host_id", "python", "protocol_page_id", "receiver_status"}
ED25519_SPKI_PREFIX = bytes.fromhex("302a300506032b6570032100")
UTC_DEADLINE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$")


class DotLinkError(Exception):
    """Expected, safe-to-report protocol or command error."""


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DotLinkError(f"JSON 中有重复字段：{key}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise DotLinkError(f"JSON 不允许常量：{value}")


def _parse_json(raw: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(
            raw.decode("utf-8", errors="strict"),
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DotLinkError(f"{label} 不是严格 UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise DotLinkError(f"{label} 顶层必须是 JSON 对象")
    return value


def _read_json(path: str | Path, label: str) -> dict[str, Any]:
    try:
        raw = Path(path).read_bytes()
    except OSError as exc:
        raise DotLinkError(f"无法读取{label}：{path}") from exc
    return _parse_json(raw, label)


def _require_exact_fields(value: dict[str, Any], expected: set[str], label: str) -> None:
    missing = expected - value.keys()
    unknown = value.keys() - expected
    if missing:
        raise DotLinkError(f"{label} 缺少字段：{', '.join(sorted(missing))}")
    if unknown:
        raise DotLinkError(f"{label} 含未知字段：{', '.join(sorted(unknown))}")


def _require_string(value: Any, field: str, *, nullable: bool = False) -> None:
    if nullable and value is None:
        return
    if not isinstance(value, str) or not value.strip():
        raise DotLinkError(f"{field} 必须是非空字符串" + ("或 null" if nullable else ""))


def _openssl(openssl: str | Path, args: list[str], *, input_bytes: bytes | None = None) -> bytes:
    try:
        result = subprocess.run(
            [str(openssl), *args],
            input=input_bytes,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except OSError as exc:
        raise DotLinkError("无法启动配置中的 OpenSSL") from exc
    if result.returncode != 0:
        raise DotLinkError(f"OpenSSL 操作失败（退出码 {result.returncode}）")
    return result.stdout


def _public_der_from_pem(openssl: str | Path, public_pem: bytes) -> bytes:
    return _openssl(openssl, ["pkey", "-pubin", "-inform", "PEM", "-outform", "DER"], input_bytes=public_pem)


def _public_der_from_private(openssl: str | Path, private_key: str | Path) -> bytes:
    return _openssl(openssl, ["pkey", "-in", str(private_key), "-pubout", "-outform", "DER"])


def _validate_ed25519_der(public_der: bytes) -> None:
    if len(public_der) != len(ED25519_SPKI_PREFIX) + 32 or not public_der.startswith(ED25519_SPKI_PREFIX):
        raise DotLinkError("公钥不是有效的 Ed25519 SubjectPublicKeyInfo")


def _key_id(public_der: bytes) -> str:
    _validate_ed25519_der(public_der)
    return "sha256:" + hashlib.sha256(public_der).hexdigest()


def _load_profile(path: str | Path, openssl: str | Path) -> dict[str, Any]:
    profile = _read_json(path, "public profile")
    _require_exact_fields(profile, PROFILE_FIELDS, "public profile")
    _require_string(profile["client_id"], "client_id")
    _require_string(profile["key_id"], "key_id")
    _require_string(profile["public_key_pem"], "public_key_pem")
    try:
        public_pem = profile["public_key_pem"].encode("ascii", errors="strict")
    except UnicodeEncodeError as exc:
        raise DotLinkError("public profile 公钥 PEM 必须是 ASCII") from exc
    public_der = _public_der_from_pem(openssl, public_pem)
    if profile["key_id"] != _key_id(public_der):
        raise DotLinkError("public profile 的 key_id 与公钥 DER 摘要不匹配")
    return profile


def _load_config(path: str | Path) -> dict[str, Any]:
    config = _read_json(path, "sign config")
    missing = CONFIG_FIELDS - config.keys()
    unknown = config.keys() - CONFIG_FIELDS - OPTIONAL_CONFIG_FIELDS
    if missing:
        raise DotLinkError(f"sign config 缺少字段：{', '.join(sorted(missing))}")
    if unknown:
        raise DotLinkError(f"sign config 含未知字段：{', '.join(sorted(unknown))}")
    for field in CONFIG_FIELDS:
        _require_string(config[field], field)
    for field in OPTIONAL_CONFIG_FIELDS & config.keys():
        _require_string(config[field], field)
    if "protocol" in config and config["protocol"] != PROTOCOL:
        raise DotLinkError("sign config.protocol 与 dot-local/0.2 不匹配")
    return config


def _validate_body(body: dict[str, Any], *, now: dt.datetime | None = None) -> None:
    _require_exact_fields(body, BODY_FIELDS, "signed body")
    if body["protocol"] != PROTOCOL:
        raise DotLinkError(f"不支持的 protocol：{body['protocol']!r}")
    for field in (
        "pairing_id", "client_id", "key_id", "recipient_thread_id",
        "source_thread_id", "request_id", "nonce", "delivery_status",
    ):
        _require_string(body[field], field)
    if not isinstance(body["type"], str) or body["type"] not in MESSAGE_TYPES:
        raise DotLinkError("type 不属于 dot-local/0.2 允许的消息类型")
    for field in ("reply_thread_id", "task_id", "in_reply_to"):
        _require_string(body[field], field, nullable=True)
    for field in ("attempt", "seq"):
        number = body[field]
        if number is not None and (isinstance(number, bool) or not isinstance(number, int) or number < 0):
            raise DotLinkError(f"{field} 必须是非负整数或 null")
    if not isinstance(body["scope"], dict):
        raise DotLinkError("scope 必须是 JSON 对象")
    if not isinstance(body["payload"], dict):
        raise DotLinkError("payload 必须是 JSON 对象")
    if not isinstance(body["artifacts"], list):
        raise DotLinkError("artifacts 必须是 JSON 数组")
    if body["domain_verdict"] is not None and not isinstance(body["domain_verdict"], dict):
        raise DotLinkError("domain_verdict 必须是 JSON 对象或 null")
    expires_at = body["expires_at"]
    if not isinstance(expires_at, str) or not UTC_DEADLINE.fullmatch(expires_at):
        raise DotLinkError("expires_at 必须是带 Z 后缀的 UTC 时间")
    try:
        deadline = dt.datetime.fromisoformat(expires_at[:-1] + "+00:00")
    except ValueError as exc:
        raise DotLinkError("expires_at 不是有效 UTC 时间") from exc
    current = now or dt.datetime.now(dt.timezone.utc)
    if deadline <= current:
        raise DotLinkError("签名消息已过期")


def _canonical_json(value: dict[str, Any]) -> bytes:
    try:
        return json.dumps(
            value, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise DotLinkError("JSON 含不受支持的值") from exc


def _sign_bytes(openssl: str | Path, private_key: str | Path, body_bytes: bytes) -> bytes:
    with tempfile.TemporaryDirectory(prefix="dot-link-") as temp_dir:
        body_path = Path(temp_dir) / "signed-body.json"
        body_path.write_bytes(body_bytes)
        return _openssl(
            openssl,
            ["pkeyutl", "-sign", "-inkey", str(private_key), "-rawin", "-in", str(body_path)],
        )


def _verify_bytes(
    openssl: str | Path, public_pem: bytes, body_bytes: bytes, signature: bytes
) -> None:
    with tempfile.TemporaryDirectory(prefix="dot-link-") as temp_dir:
        temp = Path(temp_dir)
        public_path = temp / "public.pem"
        body_path = temp / "signed-body.json"
        signature_path = temp / "signature.bin"
        public_path.write_bytes(public_pem)
        body_path.write_bytes(body_bytes)
        signature_path.write_bytes(signature)
        try:
            result = subprocess.run(
                [
                    str(openssl), "pkeyutl", "-verify", "-pubin", "-inkey", str(public_path),
                    "-rawin", "-in", str(body_path), "-sigfile", str(signature_path),
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
        except OSError as exc:
            raise DotLinkError("无法启动配置中的 OpenSSL") from exc
        if result.returncode != 0:
            raise DotLinkError("Ed25519 签名验证失败")


def _create_identity(state_dir: str | Path, openssl: str | Path) -> dict[str, str]:
    state = Path(state_dir)
    if not state.is_dir():
        raise DotLinkError("state-dir 必须是已存在且已保护的目录")
    paths = {
        "private_key": state / "private.pem",
        "public_key": state / "public.pem",
        "public_profile": state / "public-profile.json",
    }
    existing = [path.name for path in paths.values() if path.exists() or path.is_symlink()]
    if existing:
        raise DotLinkError("身份文件已存在，拒绝覆盖：" + ", ".join(existing))

    private_pem = _openssl(openssl, ["genpkey", "-algorithm", "ED25519"])
    public_pem = _openssl(openssl, ["pkey", "-pubout"], input_bytes=private_pem)
    public_der = _public_der_from_pem(openssl, public_pem)
    client_id = str(uuid.uuid4())
    key_id = _key_id(public_der)
    profile = {"client_id": client_id, "key_id": key_id, "public_key_pem": public_pem.decode("ascii")}
    outputs = (
        (paths["private_key"], private_pem),
        (paths["public_key"], public_pem),
        (paths["public_profile"], _canonical_json(profile) + b"\n"),
    )
    for path, data in outputs:
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
        except FileExistsError as exc:
            raise DotLinkError(f"身份文件已存在，拒绝覆盖：{path.name}") from exc
        except OSError as exc:
            raise DotLinkError(f"无法创建身份文件：{path.name}") from exc
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
        except OSError as exc:
            raise DotLinkError(f"无法写入身份文件：{path.name}") from exc
    return {"client_id": client_id, "key_id": key_id, "public_profile": str(paths["public_profile"])}


def _sign_command(config_path: str, body_path: str, output_path: str) -> dict[str, Any]:
    config = _load_config(config_path)
    openssl = config["openssl"]
    profile = _load_profile(config["public_profile"], openssl)
    private_key = Path(config["private_key"])
    try:
        private_der = _public_der_from_private(openssl, private_key)
    except DotLinkError as exc:
        raise DotLinkError("无法读取配置中的 Ed25519 私钥") from exc
    profile_der = _public_der_from_pem(openssl, profile["public_key_pem"].encode("ascii"))
    if private_der != profile_der:
        raise DotLinkError("配置中的私钥与 public profile 不匹配")

    body = _read_json(body_path, "body")
    bindings = {
        "protocol": PROTOCOL,
        "pairing_id": config["pairing_id"],
        "client_id": profile["client_id"],
        "key_id": profile["key_id"],
        "recipient_thread_id": config["recipient_thread_id"],
        "nonce": secrets.token_urlsafe(32),
        "expires_at": (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=1))
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
    }
    for field, expected in bindings.items():
        if field in body and body[field] != expected and field not in {"nonce", "expires_at"}:
            raise DotLinkError(f"body 的 {field} 与本地签名配置不匹配")
        if field in {"nonce", "expires_at"}:
            if field not in body:
                body[field] = expected
        else:
            body[field] = expected
    _validate_body(body)
    body_bytes = _canonical_json(body)
    signature = _sign_bytes(openssl, private_key, body_bytes)
    envelope = {
        "encoding": "base64",
        "signed_body_b64": base64.b64encode(body_bytes).decode("ascii"),
        "signature_b64": base64.b64encode(signature).decode("ascii"),
    }
    try:
        Path(output_path).write_bytes(_canonical_json(envelope) + b"\n")
    except OSError as exc:
        raise DotLinkError(f"无法写入 envelope：{output_path}") from exc
    return {"status": "signed", "key_id": profile["key_id"], "out": output_path}


def _load_pin(path: str | Path, openssl: str | Path) -> dict[str, Any]:
    pin = _read_json(path, "pin")
    _require_exact_fields(pin, PIN_FIELDS, "pin")
    for field in ("public_key_pem", "key_id", "client_id", "pairing_id", "recipient_thread_id"):
        _require_string(pin[field], field)
    if not isinstance(pin["authorization_status"], str) or pin["authorization_status"] not in {
        "pending_human_binding", "active"
    }:
        raise DotLinkError("pin.authorization_status 值无效")
    try:
        public_pem = pin["public_key_pem"].encode("ascii", errors="strict")
    except UnicodeEncodeError as exc:
        raise DotLinkError("pin 公钥 PEM 必须是 ASCII") from exc
    public_der = _public_der_from_pem(openssl, public_pem)
    if pin["key_id"] != _key_id(public_der):
        raise DotLinkError("pin 的 key_id 与公钥 DER 摘要不匹配")
    return pin


def _decode_base64(value: Any, field: str) -> bytes:
    if not isinstance(value, str):
        raise DotLinkError(f"envelope.{field} 必须是 Base64 字符串")
    try:
        decoded = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise DotLinkError(f"envelope.{field} 不是严格 Base64") from exc
    if base64.b64encode(decoded).decode("ascii") != value:
        raise DotLinkError(f"envelope.{field} 不是规范 Base64")
    return decoded


def _accept_once(ledger_path: str | Path, body: dict[str, Any], signed_body: bytes, key_id: str) -> str:
    request_id = body["request_id"]
    nonce = body["nonce"]
    digest = hashlib.sha256(signed_body).hexdigest()
    try:
        connection = sqlite3.connect(str(ledger_path), timeout=30, isolation_level=None)
    except sqlite3.Error as exc:
        raise DotLinkError("无法打开 replay ledger") from exc
    try:
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute(
            "CREATE TABLE IF NOT EXISTS dot_link_acceptance ("
            "request_id TEXT PRIMARY KEY, nonce TEXT NOT NULL UNIQUE, "
            "signed_body_sha256 TEXT NOT NULL, key_id TEXT NOT NULL, accepted_at TEXT NOT NULL)"
        )
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT nonce, signed_body_sha256 FROM dot_link_acceptance WHERE request_id = ?",
            (request_id,),
        ).fetchone()
        if row is not None:
            if row[0] == nonce and row[1] == digest:
                connection.execute("COMMIT")
                return "duplicate"
            raise DotLinkError("request_id 已用于不同的签名正文")
        nonce_row = connection.execute(
            "SELECT request_id FROM dot_link_acceptance WHERE nonce = ?", (nonce,)
        ).fetchone()
        if nonce_row is not None:
            raise DotLinkError("nonce 已用于其他 request_id")
        accepted_at = dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")
        connection.execute(
            "INSERT INTO dot_link_acceptance "
            "(request_id, nonce, signed_body_sha256, key_id, accepted_at) VALUES (?, ?, ?, ?, ?)",
            (request_id, nonce, digest, key_id, accepted_at),
        )
        connection.execute("COMMIT")
        return "accepted"
    except DotLinkError:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise
    except sqlite3.Error as exc:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise DotLinkError("replay ledger 操作失败") from exc
    finally:
        connection.close()


def _verify_command(
    pin_path: str,
    envelope_path: str,
    openssl: str,
    transport_source: str | None,
    ledger_path: str | None,
    accept: bool,
) -> dict[str, Any]:
    if accept and ledger_path is None:
        raise DotLinkError("--accept 必须与 --ledger 一起使用")
    if ledger_path is not None and not accept:
        raise DotLinkError("--ledger 必须与 --accept 一起使用")
    pin = _load_pin(pin_path, openssl)
    envelope = _read_json(envelope_path, "envelope")
    _require_exact_fields(envelope, {"encoding", "signed_body_b64", "signature_b64"}, "outer envelope")
    if envelope["encoding"] != "base64":
        raise DotLinkError("envelope.encoding 必须是 base64")
    signed_body = _decode_base64(envelope["signed_body_b64"], "signed_body_b64")
    signature = _decode_base64(envelope["signature_b64"], "signature_b64")
    public_pem = pin["public_key_pem"].encode("ascii")
    _verify_bytes(openssl, public_pem, signed_body, signature)

    body = _parse_json(signed_body, "signed body")
    _validate_body(body)
    for body_field, pin_field in (
        ("key_id", "key_id"), ("client_id", "client_id"),
        ("pairing_id", "pairing_id"), ("recipient_thread_id", "recipient_thread_id"),
    ):
        if body[body_field] != pin[pin_field]:
            raise DotLinkError(f"signed body 的 {body_field} 与本地 pin 不匹配")
    if transport_source is not None and body["source_thread_id"] != transport_source:
        raise DotLinkError("signed body 的 source_thread_id 与传输来源不匹配")

    acceptance_status = "verified_unaccepted"
    if accept:
        acceptance_status = _accept_once(ledger_path, body, signed_body, pin["key_id"])
    authorized = pin["authorization_status"] == "active"
    execution_allowed = acceptance_status == "accepted" and authorized and body["type"] == "TASK"
    return {
        "status": acceptance_status,
        "verification": {
            "status": "verified",
            "key_id": pin["key_id"],
            "signed_body_sha256": hashlib.sha256(signed_body).hexdigest(),
        },
        "authorization": {
            "status": pin["authorization_status"],
            "authorized": authorized,
        },
        "acceptance": {
            "status": acceptance_status,
            "execution_allowed": execution_allowed,
            "payload_executed": False,
        },
        "verified_body": body,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="dot-local/0.2 的 Ed25519 签名与验签工具")
    commands = parser.add_subparsers(dest="command", required=True)
    init_parser = commands.add_parser("init", help="在已保护目录创建一次性 Ed25519 身份")
    init_parser.add_argument("--state-dir", required=True)
    init_parser.add_argument("--openssl", required=True)
    sign_parser = commands.add_parser("sign", help="签名 dot-local/0.2 正文")
    sign_parser.add_argument("--config", required=True)
    sign_parser.add_argument("--body", required=True)
    sign_parser.add_argument("--out", required=True)
    verify_parser = commands.add_parser("verify", help="验证签名、路由和本地 pin")
    verify_parser.add_argument("--pin", required=True)
    verify_parser.add_argument("--envelope", required=True)
    verify_parser.add_argument("--openssl", required=True)
    verify_parser.add_argument("--transport-source")
    verify_parser.add_argument("--ledger")
    verify_parser.add_argument("--accept", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
    args = _parser().parse_args(argv)
    try:
        if args.command == "init":
            result = _create_identity(args.state_dir, args.openssl)
        elif args.command == "sign":
            result = _sign_command(args.config, args.body, args.out)
        else:
            result = _verify_command(
                args.pin, args.envelope, args.openssl, args.transport_source, args.ledger, args.accept
            )
        sys.stdout.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
        return 0
    except DotLinkError as exc:
        sys.stderr.write(f"dot-link: {exc}\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
