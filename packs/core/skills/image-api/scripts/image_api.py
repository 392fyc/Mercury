"""Bounded, opt-in OpenAI Images calls; standard library only. Mercury #729."""
import argparse
import base64
import hashlib
import json
import os
import sqlite3
import struct
import subprocess
import urllib.request
import uuid
from contextlib import closing
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

MODELS = {f"gpt-image-2.5-{name}{date}" for name in ("flare", "sunburst")
          for date in ("", "-2026-09-08")}
PNG = b"\x89PNG\r\n\x1a\n"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def micro(value):
    number = Decimal(str(value)) * 1_000_000
    require(number.is_finite() and number > 0 and number == int(number), "invalid USD limit")
    return int(number)


def private(root, path):
    path = path.resolve()
    require(path.is_relative_to(root), "private path must be inside the primary project")
    result = subprocess.run(["git", "-C", str(root), "check-ignore", "-q", "--", str(path)],
                            capture_output=True)
    require(result.returncode == 0, "private path must be Git-ignored and untracked")
    return path


def load_job(config_path, card_path):
    cfg = json.loads(config_path.read_text(encoding="utf-8"))
    require(set(cfg) <= {"project_root", "state_dir", "key_file", "key_env", "budget_usd",
                         "reserve_usd", "max_calls"}, "unknown config fields")
    root = Path(cfg["project_root"]).resolve()
    main = subprocess.run(["git", "-C", str(root), "worktree", "list", "--porcelain"],
                          capture_output=True, text=True, check=True).stdout.splitlines()[0]
    require(main == "worktree " + root.as_posix(), "project_root must be the primary checkout")
    private(root, config_path)
    state = private(root, root / cfg["state_dir"])
    require(bool(cfg.get("key_file")) != bool(cfg.get("key_env")), "choose one credential reference")
    budget, reserve = micro(cfg["budget_usd"]), micro(cfg["reserve_usd"])
    require(reserve <= budget and type(cfg["max_calls"]) is int and cfg["max_calls"] > 0,
            "invalid reservation or call limit")
    card_path = private(root, card_path)
    card = json.loads(card_path.read_text(encoding="utf-8"))
    require(set(card) <= {"mode", "prompt", "references", "model", "size", "quality", "background"},
            "unknown card fields")
    mode = card["mode"]
    require(mode in ("generate", "edit"), "mode must be generate or edit")
    prompt = card["prompt"]
    require(isinstance(prompt, str) and 0 < len(prompt) <= 32000, "invalid prompt length")
    body = {"model": card.get("model", "gpt-image-2.5-flare-2026-09-08"), "prompt": prompt,
            "size": card.get("size", "1024x1024"), "quality": card.get("quality", "low"),
            "background": card.get("background", "opaque"), "output_format": "png", "n": 1}
    require(body["model"] in MODELS, "unsupported model; recheck model and prices before adding one")
    require(body["size"] in ("1024x1024", "1536x1024", "1024x1536"), "unsupported size")
    require(body["quality"] in ("low", "medium", "high"), "unsupported quality")
    require(body["background"] in ("opaque", "transparent", "auto"), "invalid background")
    refs = card.get("references", [])
    require(isinstance(refs, list) and (1 <= len(refs) <= 16 if mode == "edit" else not refs),
            "edit needs 1-16 local references; generate takes none")
    record = {k: v for k, v in body.items() if k != "prompt"}
    record.update(mode=mode, prompt_sha256=hashlib.sha256(prompt.encode()).hexdigest(), references=[])
    for reference in refs:
        path = root / reference
        require(path.stat().st_size <= 10 * 1024 * 1024, "reference exceeds 10 MiB")
        data = path.read_bytes()
        mime = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                ".webp": "image/webp"}.get(path.suffix.lower())
        require(mime and data, "unsupported or empty reference")
        body.setdefault("images", []).append({"image_url": "data:" + mime + ";base64," +
                                              base64.b64encode(data).decode("ascii")})
        record["references"].append({"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)})
    return cfg, state, body, record, budget, reserve


def reserve_call(db_path, limit, maximum, reserved, run_id):
    with closing(sqlite3.connect(db_path, timeout=10)) as db, db:
        db.execute("CREATE TABLE IF NOT EXISTS calls (id TEXT PRIMARY KEY, micro INTEGER, status TEXT)")
        db.execute("BEGIN IMMEDIATE")
        require(not db.execute("SELECT 1 FROM calls WHERE status='budget-exceeded'").fetchone(),
                "previous call exceeded its reservation; review spending before continuing")
        used, count = db.execute("SELECT COALESCE(SUM(micro),0), COUNT(*) FROM calls").fetchone()
        require(count < maximum and used + reserved <= limit, "call count or budget exhausted")
        db.execute("INSERT INTO calls VALUES (?, ?, 'pending')", (run_id, reserved))


def usage_cost(usage):
    try:
        details = usage["input_tokens_details"]
        values = [details["text_tokens"], details["image_tokens"], usage["output_tokens"]]
        require(all(type(x) is int and x >= 0 for x in values), "invalid usage")
        require(sum(values[:2]) == usage["input_tokens"], "incomplete input usage")
        return sum(n * rate for n, rate in zip(values, (5, 8, 30)))
    except (KeyError, TypeError, ValueError):
        return None


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def request(body, mode, key):
    endpoint = "edits" if mode == "edit" else "generations"
    req = urllib.request.Request("https://api.openai.com/v1/images/" + endpoint,
                                 data=json.dumps(body).encode(), method="POST",
                                 headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
    with urllib.request.build_opener(NoRedirect()).open(req, timeout=180) as response:
        return json.load(response)


def run(config_path, card_path, execute=False):
    cfg, state, body, record, budget, reserved = load_job(config_path, card_path)
    record.update(schema="mercury-image-api/1", status="dry-run", budget_usd=budget / 1e6,
                  reserve_usd=reserved / 1e6, max_calls=cfg["max_calls"])
    if not execute:
        return record
    if cfg.get("key_file"):
        lines = Path(cfg["key_file"]).read_text(encoding="utf-8-sig").splitlines()
        keys = [line.split("=", 1)[1].strip().strip("\"'") for line in lines
                if line.startswith("OPENAI_API_KEY=")]
        require(len(keys) == 1, "credential reference must contain one OPENAI_API_KEY")
        key = keys[0]
    else:
        key = os.environ.get(cfg["key_env"], "")
    require(key and not any(c.isspace() for c in key), "credential unavailable or invalid")
    state.mkdir(parents=True, exist_ok=True)
    run_id = uuid.uuid4().hex
    db_path = private(Path(cfg["project_root"]).resolve(), state / "usage.sqlite3")
    reserve_call(db_path, budget, cfg["max_calls"], reserved, run_id)
    out = state / run_id
    charge = reserved
    record.update(run_id=run_id, status="pending", at=datetime.now(timezone.utc).isoformat())
    try:
        out.mkdir()
        response = request(body, record["mode"], key)
        usage = response.get("usage")
        charge_actual = usage_cost(usage)
        if charge_actual is not None:
            charge = charge_actual
        record.update(usage={"input_tokens": usage["input_tokens"], "output_tokens": usage["output_tokens"],
                             "input_tokens_details": {k: usage["input_tokens_details"][k]
                                                      for k in ("text_tokens", "image_tokens")}}
                      if charge_actual is not None else None,
                      actual_usd=charge_actual / 1e6 if charge_actual is not None else None)
        data = base64.b64decode(response["data"][0]["b64_json"], validate=True)
        require(data.startswith(PNG) and len(data) >= 24, "response is not a PNG")
        with (out / "image.png").open("xb") as handle:
            handle.write(data)
        record.update(status="ok", image_sha256=hashlib.sha256(data).hexdigest(), bytes=len(data),
                      dimensions=list(struct.unpack(">II", data[16:24])),
                      reservation_exceeded=charge > reserved)
    except Exception as error:
        record.update(status="unknown-charge" if record.get("actual_usd") is None else "failed",
                      error_type=type(error).__name__)
    finally:
        if charge > reserved:
            record["status"] = "budget-exceeded"
        record["accounted_usd"] = charge / 1e6
        with closing(sqlite3.connect(db_path, timeout=10)) as db, db:
            db.execute("UPDATE calls SET micro=?, status=? WHERE id=?", (charge, record["status"], run_id))
        out.mkdir(exist_ok=True)
        (out / "receipt.json").write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--card", required=True, type=Path)
    parser.add_argument("--execute", action="store_true", help="send one paid request; default is offline preflight")
    args = parser.parse_args()
    try:
        result = run(args.config, args.card, args.execute)
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["status"] in ("ok", "dry-run") else 1
    except Exception as error:
        print(json.dumps({"status": "refused", "error_type": type(error).__name__}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
