#!/usr/bin/env python3
"""Local byte-preserving packets; transport and content review belong to the caller."""

import argparse
import base64
import binascii
import hashlib
import json
import os
import re
import sys
import uuid
from pathlib import Path

PREFIX = "MERCURY_TEST_FILE_JSON "
SCHEMA = "mercury-test-file/1"
MAX_CHUNK_BYTES = 65536


def sha(data):
    return hashlib.sha256(data).hexdigest()


def dump(value):
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"))


def write_json(path, value):
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(dump(value) + "\n")


def manifest_at(path):
    with path.open("rb") as stream:
        raw = stream.read(8193)
    if len(raw) > 8192:
        raise ValueError("manifest exceeds 8192 bytes")
    m = json.loads(raw.decode("utf-8"))
    if not isinstance(m, dict) or m.get("schema") != SCHEMA:
        raise ValueError("unsupported manifest")
    for key in ("total_bytes", "chunk_bytes", "parts"):
        if type(m.get(key)) is not int or m[key] < (0 if key == "total_bytes" else 1):
            raise ValueError("invalid manifest sizes")
    if m["chunk_bytes"] > MAX_CHUNK_BYTES:
        raise ValueError("chunk size exceeds 65536 bytes")
    if m["parts"] != max(1, (m["total_bytes"] + m["chunk_bytes"] - 1) // m["chunk_bytes"]):
        raise ValueError("invalid part count")
    for key, width in (("transfer_id", 32), ("sha256", 64)):
        if not isinstance(m.get(key), str) or not re.fullmatch("[0-9a-f]{%d}" % width, m[key]):
            raise ValueError("invalid manifest digest or transfer id")
    name = m.get("name")
    if not isinstance(name, str) or not name or name in (".", "..") or "/" in name or "\\" in name:
        raise ValueError("manifest name must be a label, not a path")
    return m


def prepare(source, output, chunk_bytes):
    if not 1 <= chunk_bytes <= MAX_CHUNK_BYTES:
        raise ValueError("chunk size must be between 1 and 65536 bytes")
    transfer_id, digest, total, part = uuid.uuid4().hex, hashlib.sha256(), 0, 0
    output.mkdir(parents=True, exist_ok=False)
    with source.open("rb") as raw, (output / "packets.jsonl").open("x", encoding="utf-8", newline="\n") as stream:
        while True:
            block = raw.read(chunk_bytes)
            if not block and part:
                break
            part += 1
            digest.update(block)
            packet = dict(transfer_id=transfer_id, part=part, offset=total,
                          bytes=len(block), sha256=sha(block),
                          data=base64.b64encode(block).decode("ascii"))
            stream.write(PREFIX + dump(packet) + "\n")
            total += len(block)
            if not block:
                break
    m = dict(schema=SCHEMA, transfer_id=transfer_id, name=source.name, total_bytes=total,
             sha256=digest.hexdigest(), chunk_bytes=chunk_bytes, parts=part)
    write_json(output / "manifest.json", m)
    return m


def decode(packet, m):
    if not isinstance(packet, dict) or packet.get("transfer_id") != m["transfer_id"]:
        raise ValueError("wrong transfer id")
    part = packet.get("part")
    if type(part) is not int or not 1 <= part <= m["parts"]:
        raise ValueError("invalid part number")
    offset = (part - 1) * m["chunk_bytes"]
    length = min(m["chunk_bytes"], m["total_bytes"] - offset)
    if type(packet.get("offset")) is not int or packet["offset"] != offset:
        raise ValueError("invalid offset")
    if type(packet.get("bytes")) is not int or packet["bytes"] != length:
        raise ValueError("invalid block length")
    encoded = packet.get("data")
    if not isinstance(encoded, str) or len(encoded) != 4 * ((length + 2) // 3):
        raise ValueError("invalid base64 length")
    block = base64.b64decode(encoded, validate=True)
    if len(block) != length or packet.get("sha256") != sha(block):
        raise ValueError("block digest or size mismatch")
    return part, block


def receive(manifest, input_path, output):
    m = manifest_at(manifest)
    output.mkdir(parents=True, exist_ok=True)
    saved_manifest = output / "manifest.json"
    if saved_manifest.exists():
        if manifest_at(saved_manifest) != m:
            raise ValueError("incoming directory belongs to another manifest")
    else:
        write_json(saved_manifest, m)
    accepted = 0
    with input_path.open(encoding="utf-8") as stream:
        while True:
            line = stream.readline(2 * MAX_CHUNK_BYTES)
            if not line:
                break
            if len(line) == 2 * MAX_CHUNK_BYTES and not line.endswith("\n"):
                raise ValueError("packet line too long")
            line = line.strip()
            if not line:
                continue
            packet = json.loads(line[len(PREFIX):] if line.startswith(PREFIX) else line)
            part, block = decode(packet, m)
            dest = output / ("part-%08d.bin" % part)
            if dest.exists():
                with dest.open("rb") as saved:
                    previous = saved.read(len(block) + 1)
                if previous != block:
                    raise ValueError("conflicting saved part %d" % part)
            else:
                with dest.open("xb") as target:
                    target.write(block)
            accepted += 1
    if not accepted:
        raise ValueError("no packets received")
    return {"accepted_packets": accepted}


def assemble(incoming, dest):
    m = manifest_at(incoming / "manifest.json")
    pending = dest.with_name(dest.name + ".incoming-" + uuid.uuid4().hex)
    digest, total = hashlib.sha256(), 0
    stream = pending.open("xb")
    try:
        with stream:
            for part in range(1, m["parts"] + 1):
                path = incoming / ("part-%08d.bin" % part)
                if not path.is_file():
                    raise ValueError("missing part: %d" % part)
                with path.open("rb") as raw:
                    block = raw.read(m["chunk_bytes"] + 1)
                expected = min(m["chunk_bytes"], m["total_bytes"] - total)
                if len(block) != expected:
                    raise ValueError("saved part size mismatch")
                digest.update(block)
                total += len(block)
                stream.write(block)
            if total != m["total_bytes"] or digest.hexdigest() != m["sha256"]:
                raise ValueError("full file digest or size mismatch")
            stream.flush()
        # Same-directory hard link publishes complete bytes atomically and never overwrites.
        os.link(pending, dest)
    finally:
        pending.unlink()
    return {"bytes": total, "sha256": digest.hexdigest(), "verified": True}


def emit(outgoing, start, end):
    m = manifest_at(outgoing / "manifest.json")
    end = start if end is None else end
    if not 1 <= start <= end <= m["parts"]:
        raise ValueError("invalid emit range")
    with (outgoing / "packets.jsonl").open(encoding="utf-8") as stream:
        for part, line in enumerate(stream, 1):
            if start <= part <= end:
                packet = json.loads(line[len(PREFIX):])
                actual, _ = decode(packet, m)
                if actual != part:
                    raise ValueError("packet order mismatch")
                print(line.rstrip("\n"))
            if part == end:
                return
    raise ValueError("outgoing packets truncated")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    prep = sub.add_parser("prepare")
    prep.add_argument("source", type=Path)
    prep.add_argument("--out", type=Path, required=True)
    prep.add_argument("--chunk-bytes", type=int, default=8192)
    send = sub.add_parser("emit")
    send.add_argument("outgoing", type=Path)
    send.add_argument("--start", type=int, required=True)
    send.add_argument("--end", type=int)
    recv = sub.add_parser("receive")
    recv.add_argument("--manifest", type=Path, required=True)
    recv.add_argument("--input", type=Path, required=True)
    recv.add_argument("--out", type=Path, required=True)
    join = sub.add_parser("assemble")
    join.add_argument("incoming", type=Path)
    join.add_argument("--dest", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.action == "prepare":
            result = prepare(args.source, args.out, args.chunk_bytes)
        elif args.action == "emit":
            emit(args.outgoing, args.start, args.end)
            return 0
        elif args.action == "receive":
            result = receive(args.manifest, args.input, args.out)
        else:
            result = assemble(args.incoming, args.dest)
        print(dump(result))
        return 0
    except (ValueError, OSError, binascii.Error, TypeError) as error:
        # Do not echo exception text: JSON errors and OS paths can expose input or local identities.
        reason = str(error) if type(error) is ValueError else type(error).__name__
        print("transfer failed: " + reason, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
