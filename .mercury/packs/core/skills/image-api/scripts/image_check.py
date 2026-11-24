"""Check one generated PNG against its card's acceptance list with Claude; verdict only. Mercury #821."""
import argparse
import base64
import hashlib
import json
import os
import sqlite3
import struct
import sys
import urllib.request
import uuid
import zlib
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from image_api import NoRedirect, PNG, micro, primary_root, private, require, reserve_call  # noqa: E402

MODELS = {"claude-opus-5-5": (4, 20), "claude-sonnet-5-5": (2, 10)}  # USD per 1M input, output tokens
SYSTEM = ("Check one generated image against numbered criteria, judging only what is visible: pass, fail, or unsure "
          "when the image does not let you decide, each with a short reason. Do not judge transparency.")
ITEM = {"type": "object", "additionalProperties": False, "required": ["id", "result", "reason"],
        "properties": {"id": {"type": "integer"}, "reason": {"type": "string"},
                       "result": {"type": "string", "enum": ["pass", "fail", "unsure"]}}}
SCHEMA = {"type": "object", "additionalProperties": False, "required": ["items"],
          "properties": {"items": {"type": "array", "items": ITEM}}}


def png_facts(data):
    """Size and edge transparency (8-bit RGBA only, else None) of a non-interlaced PNG; standard library."""
    require(data.startswith(PNG), "not a PNG")
    pos, idat, ihdr, kind, palette = 8, [], None, b"", False
    while pos < len(data) and kind != b"IEND":
        length, kind = struct.unpack(">I4s", data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + length]
        require(len(body) == length and zlib.crc32(kind + body) ==
                struct.unpack(">I", data[pos + 8 + length:pos + 12 + length])[0], "corrupt PNG chunk")
        ihdr = body if kind == b"IHDR" else ihdr
        idat += [body] if kind == b"IDAT" else []  # joined once below, not copied per chunk
        pos, palette = pos + 12 + length, palette or kind == b"PLTE"
    require(kind == b"IEND" and ihdr is not None and idat, "PNG lacks IHDR, IDAT or IEND")
    width, height, depth, color, compression, method, interlace = struct.unpack(">IIBBBBB", ihdr)
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}.get(color)
    depths = (1, 2, 4, 8, 16) if color == 0 else (1, 2, 4, 8) if color == 3 else (8, 16)
    require(channels and depth in depths and not (compression or method or interlace) and width and height
            and (color != 3 or palette), "unsupported PNG format")
    inflater, rowbytes = zlib.decompressobj(), (width * channels * depth + 7) // 8
    raw = inflater.decompress(b"".join(idat), 64 * 1024 * 1024)  # bounded: a small file cannot inflate without limit
    require(inflater.eof and len(raw) == height * (1 + rowbytes), "PNG data is truncated or the wrong length")
    require(all(raw[y * (rowbytes + 1)] <= 4 for y in range(height)), "unknown PNG filter")
    edges = None
    if depth == 8 and color == 6:  # all edge pixels transparent: necessary, not proof of the background
        edges = all(max(row[3::4]) == 0 if y in (0, height - 1) else row[3] == row[-1] == 0
                    for y, row in enumerate(unfilter(raw, width * 4, height, 4)))
    return {"dimensions": [width, height], "edges_transparent": edges}


def unfilter(raw, stride, height, bpp):  # yields each row; keeps only the previous one
    prev = bytearray(stride)
    for y in range(height):
        kind, line = raw[y * (stride + 1)], bytearray(raw[y * (stride + 1) + 1:(y + 1) * (stride + 1)])
        if kind == 2:
            line = bytearray((x + b) & 0xFF for x, b in zip(line, prev))
        elif kind:
            for i in range(stride):
                a, b = line[i - bpp] if i >= bpp else 0, prev[i]
                c = prev[i - bpp] if i >= bpp else 0
                pa, pb, pc = abs(b - c), abs(a - c), abs(a + b - 2 * c)
                paeth = a if pa <= pb and pa <= pc else b if pb <= pc else c
                line[i] = (line[i] + (a if kind == 1 else (a + b) // 2 if kind == 3 else paeth)) & 0xFF
        yield (prev := line)


def request(body, key):
    req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=json.dumps(body).encode(),
                                 method="POST", headers={"x-api-key": key, "anthropic-version": "2026-10-21",
                                                         "content-type": "application/json"})
    with urllib.request.build_opener(NoRedirect()).open(req, timeout=300) as response:
        return json.load(response)


def read_key(cfg):
    if cfg.get("anthropic_key_file"):
        lines = Path(cfg["anthropic_key_file"]).read_text(encoding="utf-8-sig").splitlines()
        keys = [line.split("=", 1)[1].strip().strip("\"'") for line in lines if line.startswith("ANTHROPIC_API_KEY=")]
        require(len(keys) == 1, "credential reference must contain one ANTHROPIC_API_KEY")
        return keys[0]
    return os.environ.get(cfg["anthropic_key_env"], "")


def run(config_path, card_path, image_path, execute=False):
    cfg = json.loads(config_path.read_text(encoding="utf-8"))
    require(set(cfg) <= {"project_root", "state_dir", "anthropic_key_file", "anthropic_key_env", "model",
                         "budget_usd", "reserve_usd", "max_calls"}, "unknown config fields")
    root = primary_root(cfg["project_root"])
    private(root, config_path)
    state = private(root, root / cfg["state_dir"])
    require(bool(cfg.get("anthropic_key_file")) != bool(cfg.get("anthropic_key_env")), "choose one key reference")
    require((model := cfg.get("model", "claude-opus-5-5")) in MODELS, "unsupported model; recheck prices first")
    budget, reserved = micro(cfg["budget_usd"]), micro(cfg["reserve_usd"])
    require(reserved <= budget and type(cfg["max_calls"]) is int and cfg["max_calls"] > 0,
            "invalid reservation or call limit")
    card = json.loads(private(root, card_path).read_text(encoding="utf-8"))
    criteria = card.get("acceptance")
    require(isinstance(criteria, list) and 0 < len(criteria) <= 30 and
            all(isinstance(c, str) and 0 < len(c) <= 500 for c in criteria), "card needs an acceptance list")
    image_path = private(root, image_path)
    with image_path.open("rb") as handle:  # read at most one byte past the limit
        data = handle.read(7 * 1024 * 1024 + 1)
    require(len(data) <= 7 * 1024 * 1024, "image too large to send")
    report = image_path.parent / "check.json"  # never a link and never one of the inputs it would overwrite
    inputs = (config_path.resolve(), card_path.resolve(), image_path,
              Path(cfg.get("anthropic_key_file") or image_path).resolve())  # the key file is an input too
    require(not report.is_symlink() and report.resolve() not in inputs, "check.json must not be a link or an input")
    report = private(root, report)
    try:
        facts = png_facts(data)
    except (ValueError, struct.error, zlib.error):
        facts = {"dimensions": None, "edges_transparent": None}
    mechanical = {"png": facts["dimensions"] is not None,
                  "size": facts["dimensions"] == [int(n) for n in card.get("size", "1024x1024").split("x")]}
    if card.get("background") == "transparent":  # unverifiable transparency does not pass
        mechanical["transparent_edges"] = facts["edges_transparent"] is True
    record = {"schema": "mercury-image-check/1", "model": model, "image_sha256": hashlib.sha256(data).hexdigest(),
              "dimensions": facts["dimensions"], "mechanical": mechanical, "status": "dry-run", "items": []}
    if not all(mechanical.values()):
        record.update(status="checked", verdict="fail")
    if not execute or record["status"] == "checked":
        return finish(record, report, execute)
    require((key := read_key(cfg)) and not any(c.isspace() for c in key), "credential unavailable or invalid")
    state.mkdir(parents=True, exist_ok=True)
    db_path, run_id, charge = private(root, state / "check-usage.sqlite3"), uuid.uuid4().hex, reserved
    reserve_call(db_path, budget, cfg["max_calls"], reserved, run_id)
    record.update(status="unknown-charge", at=datetime.now(timezone.utc).isoformat())
    listing = "\n".join(f"{i + 1}. {c}" for i, c in enumerate(criteria))
    body = {"model": model, "max_tokens": 8000, "system": SYSTEM, "thinking": {"type": "adaptive"},
            "output_config": {"format": {"type": "json_schema", "schema": SCHEMA}},
            "messages": [{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                             "data": base64.b64encode(data).decode("ascii")}},
                {"type": "text", "text": "Judge each criterion once; answer with its number as id:\n" + listing}]}]}
    try:
        response = request(body, key)
        tokens = [response["usage"]["input_tokens"], response["usage"]["output_tokens"]]
        require(all(type(n) is int and n >= 0 for n in tokens), "invalid usage")
        charge = sum(n * rate for n, rate in zip(tokens, MODELS[model]))
        record.update(usage=dict(zip(("input_tokens", "output_tokens"), tokens)), actual_usd=charge / 1e6)
        require(response.get("stop_reason") == "end_turn", "answer incomplete or refused")
        text = "".join(b["text"] for b in response["content"] if b.get("type") == "text")
        answer = {i["id"]: i for i in json.loads(text)["items"]}
        require(len(answer) == len(json.loads(text)["items"]) and set(answer) == set(range(1, len(criteria) + 1)),
                "answer must judge each criterion exactly once")
        record["items"] = [{"id": n, "criterion": c, "result": answer[n]["result"], "reason": answer[n]["reason"]}
                           for n, c in enumerate(criteria, 1)]
        results = {i["result"] for i in record["items"]}
        verdict = "fail" if "fail" in results else "pass" if results == {"pass"} else "unsure"
        record.update(status="checked", verdict=verdict)
    except Exception as error:
        record.update(status="unknown-charge" if record.get("actual_usd") is None else "failed",
                      verdict="unsure", error_type=type(error).__name__)
    finally:
        if charge > reserved:
            record["status"] = "budget-exceeded"
        record["accounted_usd"] = charge / 1e6
        with closing(sqlite3.connect(db_path, timeout=10)) as db, db:
            db.execute("UPDATE calls SET micro=?, status=? WHERE id=?", (charge, record["status"], run_id))
    return finish(record, report, execute)


def finish(record, report, execute):
    if execute:  # a new file renamed over check.json: never truncates a link, hard link or other alias
        temp = report.with_name(f".check-{uuid.uuid4().hex}.json")
        with open(os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, indent=2) + "\n")
        os.replace(temp, report)
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("--config", "--card", "--image"):
        parser.add_argument(name, required=True, type=Path)
    parser.add_argument("--execute", action="store_true", help="send one paid request; default is offline checks")
    try:
        args = parser.parse_args()  # a usage error refuses (3); it must not read as "unsure" (2)
        result = run(args.config, args.card, args.image, args.execute)
        print(json.dumps(result))  # ASCII escapes: a paid result must not fail on a narrow console code page
        return {"pass": 0, "fail": 1, "unsure": 2}.get(result.get("verdict"), 4)  # 4: preflight passed, not judged
    except (Exception, SystemExit) as error:
        if isinstance(error, SystemExit) and not error.code and "args" not in locals():  # only parse_args --help
            return 0
        print(json.dumps({"status": "refused", "error_type": type(error).__name__}))
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
