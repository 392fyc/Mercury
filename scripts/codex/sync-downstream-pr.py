#!/usr/bin/env python3
"""Publish manifest-only downstream updates; merge only reviewed, green heads."""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile


class UpdateError(Exception):
    pass


REQUIRED_CHECK_NAMES = {"Godot headless 回归", "仓库契约与采集配置测试"}


def run(args: list[str], cwd: Path | None = None) -> str:
    try:
        result = subprocess.run(args, cwd=cwd, capture_output=True, text=True,
                                encoding="utf-8", timeout=120)
    except (OSError, subprocess.TimeoutExpired):
        raise UpdateError("cannot complete automation command") from None
    if result.returncode:
        raise UpdateError(f"automation command failed (exit {result.returncode})")
    return result.stdout.strip()


def git(target: Path, *args: str) -> str:
    return run(["git", "-C", str(target), "-c", "core.fsmonitor=false", *args])


def changed_paths(target: Path, *revisions: str) -> list[str]:
    return [p for p in git(target, "diff", "--name-only", "-z", *revisions).split("\0") if p]


def api(repo: str, endpoint: str) -> object:
    return json.loads(run(["gh", "api", f"repos/{repo}/{endpoint}"]))


def sync_module(source: Path):
    path = source / "scripts/codex/sync-project-template.py"
    spec = importlib.util.spec_from_file_location("mercury_sync", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def approval_ready(reviews: list[dict], head: str, reviewer: str) -> bool:
    # Keep the latest substantive opinion per reviewer; a stale approval never
    # authorizes a new head, and another reviewer's outstanding rejection blocks.
    latest: dict[str, dict] = {}
    for review in reviews:
        if review["state"] in {"APPROVED", "CHANGES_REQUESTED", "DISMISSED"}:
            latest[review["user"]["login"]] = review
    if any(r["state"] == "CHANGES_REQUESTED" for r in latest.values()):
        return False
    review = latest.get(reviewer)
    return bool(review and review["state"] == "APPROVED"
                and review["commit_id"] == head)


def checks_ready(checks: list[dict]) -> bool:
    if not REQUIRED_CHECK_NAMES <= {c.get("name", c.get("context")) for c in checks}:
        return False
    # Neither absent checks nor skipped checks count as successful verification.
    for check in checks:
        if check.get("__typename") == "StatusContext":
            if check.get("state") != "SUCCESS":
                return False
        elif check.get("status") != "COMPLETED" or check.get("conclusion") != "SUCCESS":
            return False
    return True


def current_checks(repo: str, head: str) -> list[dict]:
    owner, name = repo.split("/")
    query = """query($owner:String!,$name:String!,$oid:GitObjectID!){
      repository(owner:$owner,name:$name){object(oid:$oid){... on Commit{
        statusCheckRollup{contexts(first:100){pageInfo{hasNextPage} nodes{
          __typename ... on CheckRun{name status conclusion}
          ... on StatusContext{context state}
        }}}
      }}}
    }"""
    result = json.loads(run(["gh", "api", "graphql", "-f", f"query={query}",
                             "-f", f"owner={owner}", "-f", f"name={name}",
                             "-f", f"oid={head}"]))
    rollup = result["data"]["repository"]["object"]["statusCheckRollup"]
    if rollup is None:
        return []
    contexts = rollup["contexts"]
    if contexts["pageInfo"]["hasNextPage"]:
        raise UpdateError("check history exceeds the verified page; refusing automatic merge")
    return contexts["nodes"]


def canonical_lock(module, source: Path, content: bytes):
    lock = module._parse_existing_lock(content)
    if lock.source_repo != "392fyc/Mercury":
        raise UpdateError("lock does not identify the Mercury upstream")
    commit = module._validate_source_commit(source, lock.source_commit)
    git(source, "merge-base", "--is-ancestor", commit, "origin/develop")
    template = module._load_template(source, commit)
    module._authenticate_existing_lock(source, template, lock)
    return lock, template


def validate_pr_tree(source: Path, target: Path, pr: dict, module) -> None:
    head = pr["headRefOid"]
    if re.fullmatch(r"[0-9a-f]{40}", head) is None:
        raise UpdateError("invalid PR head")
    git(target, "fetch", "origin", f"refs/pull/{pr['number']}/head")
    if git(target, "rev-parse", "FETCH_HEAD") != head:
        raise UpdateError("PR head changed during verification")
    # Read Git blobs without checking out or executing the proposed downstream
    # tree. Byte comparison also catches deleted files, symlinks and extras.
    base = git(target, "merge-base", f"origin/{pr['baseRefName']}", head)
    lock_path = ".codex/mercury-template.lock"
    raw_lock = subprocess.run(["git", "-C", str(target), "show", f"{head}:{lock_path}"],
                              capture_output=True, timeout=30)
    if raw_lock.returncode:
        raise UpdateError("PR is missing its generated lock")
    lock, template = canonical_lock(module, source, raw_lock.stdout)
    if pr["headRefName"] != f"codex/mercury-sync-{lock.source_commit}" or pr.get("isCrossRepository", False):
        raise UpdateError("PR is not a same-repository Mercury update branch")
    old_raw = subprocess.run(["git", "-C", str(target), "show", f"{base}:{lock_path}"],
                            capture_output=True, timeout=30)
    if old_raw.returncode:
        raise UpdateError("base is missing its generated lock")
    old_lock, _ = canonical_lock(module, source, old_raw.stdout)
    git(source, "merge-base", "--is-ancestor", old_lock.source_commit, lock.source_commit)
    allowed = {lock_path} | {f".codex/{p}" for p in lock.files} | {
        f".codex/{p}" for p in old_lock.files}
    changed = set(changed_paths(target, base, head))
    if not changed or not changed <= allowed:
        raise UpdateError("PR changes paths outside the template manifest")
    for item in template.files:
        path = f".codex/{item.destination.as_posix()}"
        entry = git(target, "ls-tree", head, "--", path).split()
        if not entry or entry[0] != "100644":
            raise UpdateError("generated file is missing or has an unsafe mode")
        raw = subprocess.run(["git", "-C", str(target), "show", f"{head}:{path}"],
                             capture_output=True, timeout=30)
        if raw.returncode or raw.stdout != item.content:
            raise UpdateError("PR generated content differs from Mercury")
    for removed in old_lock.files.keys() - lock.files.keys():
        if git(target, "ls-tree", head, "--", f".codex/{removed}"):
            raise UpdateError("PR retains a retired generated file")
    entry = git(target, "ls-tree", head, "--", lock_path).split()
    if not entry or entry[0] != "100644":
        raise UpdateError("lock has an unsafe mode")


def consider_merge(source: Path, target: Path, repo: str, pr: dict,
                   reviewer: str, module) -> None:
    validate_pr_tree(source, target, pr, module)
    reviews = api(repo, f"pulls/{pr['number']}/reviews?per_page=100")
    if len(reviews) == 100:
        raise UpdateError("review history needs pagination; refusing automatic merge")
    if not approval_ready(reviews, pr["headRefOid"], reviewer):
        print(f"PR #{pr['number']} is waiting for current-head independent approval")
        return
    if not checks_ready(current_checks(repo, pr["headRefOid"])):
        print(f"PR #{pr['number']} is waiting for successful checks")
        return
    if pr["mergeable"] != "MERGEABLE" or pr["isDraft"]:
        print(f"PR #{pr['number']} is not ready to merge")
        return
    # The server compares the head once more, including when branch protection
    # is unavailable for a private repository. No admin or bypass flag is used.
    run(["gh", "pr", "merge", str(pr["number"]), "--repo", repo, "--squash",
         "--match-head-commit", pr["headRefOid"]])
    print(f"merged verified template PR #{pr['number']}")


def update(source: Path, target: Path, repo: str, base: str,
           issue: int, reviewer: str) -> None:
    module = sync_module(source)
    if git(target, "status", "--porcelain"):
        raise UpdateError("target must be an exclusively owned clean checkout")
    if git(target, "rev-parse", "HEAD") != git(target, "rev-parse", f"origin/{base}"):
        raise UpdateError("target must start at the current downstream base")
    source_commit = git(source, "rev-parse", "HEAD")
    git(source, "merge-base", "--is-ancestor", source_commit, "origin/develop")
    fields = "number,headRefName,headRefOid,baseRefName,mergeable,isDraft,isCrossRepository"
    prs = json.loads(run(["gh", "pr", "list", "--repo", repo, "--base", base,
                          "--state", "open", "--limit", "100", "--json", fields]))
    existing = [pr for pr in prs if pr["headRefName"].startswith("codex/mercury-sync-")]
    if len(existing) > 1 or len(prs) == 100:
        raise UpdateError("ambiguous update queue; refusing to publish or merge")
    if existing:
        pr = existing[0]
        validate_pr_tree(source, target, pr, module)
        runs = json.loads(run(["gh", "run", "list", "--repo", repo,
                               "--workflow", "ci.yml", "--branch", pr["headRefName"],
                               "--limit", "100", "--json", "headSha"]))
        if not any(r["headSha"] == pr["headRefOid"] for r in runs):
            run(["gh", "workflow", "run", "ci.yml", "--repo", repo,
                 "--ref", pr["headRefName"]])
            print("recovered missing CI dispatch; leaving PR for a later check")
            return
        consider_merge(source, target, repo, pr, reviewer, module)
        return
    template = module._load_template(source, source_commit)
    lock_path = target / ".codex/mercury-template.lock"
    old_lock, _ = canonical_lock(module, source, lock_path.read_bytes())
    if (old_lock.manifest_sha256 == template.manifest_sha256
            and old_lock.files == {item.destination.as_posix(): module._sha256(item.content)
                                   for item in template.files}):
        # Confirm the installed bytes too; do not hide local drift as a no-op.
        verify = source / "scripts/codex/verify-project-template.py"
        run([sys.executable, "-B", str(verify), "--target", str(target)])
        print("template payload is unchanged; no update PR needed")
        return
    branch = f"codex/mercury-sync-{source_commit}"
    git(target, "switch", "--no-track", "-c", branch)
    module._apply(source, target, template, module._lock_bytes(template, source_commit))
    run([sys.executable, "-B", str(source / "scripts/codex/verify-project-template.py"),
         "--target", str(target)])
    paths = [".codex/mercury-template.lock"] + [f".codex/{p}" for p in
             sorted(set(old_lock.files) | {i.destination.as_posix() for i in template.files})]
    changed = changed_paths(target)
    if not changed or not set(changed) <= set(paths):
        raise UpdateError("synchronization changed unexpected files")
    git(target, "add", "--", *changed)
    git(target, "-c", "user.name=Mercury Sync", "-c",
        "user.email=41898282+github-actions[bot]@users.noreply.github.com", "commit",
        "-m", f"chore(harness): consume Mercury {source_commit[:12]}")
    run(["pwsh", "-NoProfile", "-File", str(target / "scripts/codex/sot-publish.ps1")],
        cwd=target)
    body = (f"Consume the manifest-generated Harness from "
            f"https://github.com/392fyc/Mercury/commit/{source_commit}.\n\n"
            f"Only declared Mercury files and the authenticated lock changed. "
            f"Downstream overlays remain unchanged.\n\nRefs #{issue}\n")
    with tempfile.TemporaryDirectory() as directory:
        body_file = Path(directory) / "body.md"
        body_file.write_text(body, encoding="utf-8")
        print(run(["gh", "pr", "create", "--repo", repo, "--base", base,
                   "--head", branch, "--title", f"chore(harness): Mercury {source_commit[:12]}",
                   "--body-file", str(body_file)]))
    # Explicit dispatch is supported for GITHUB_TOKEN-triggered workflows.
    # A GitHub App token also starts the normal PR CI without approval prompts.
    runs = json.loads(run(["gh", "run", "list", "--repo", repo, "--workflow", "ci.yml",
                           "--branch", branch, "--limit", "100", "--json", "headSha"]))
    head = git(target, "rev-parse", "HEAD")
    if not any(r["headSha"] == head for r in runs):
        run(["gh", "workflow", "run", "ci.yml", "--repo", repo, "--ref", branch])
    print("update published; a later scheduled run will recheck review and CI")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", required=True, type=Path)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--base", default="develop")
    parser.add_argument("--issue", required=True, type=int)
    parser.add_argument("--reviewer", default="argus-review[bot]")
    args = parser.parse_args()
    if (re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", args.repo) is None
            or args.base != "develop" or args.issue < 1
            or re.fullmatch(r"[A-Za-z0-9_-]+(?:\[bot\])?", args.reviewer) is None):
        parser.error("invalid repository, integration branch, issue or reviewer")
    try:
        update(Path(__file__).resolve().parents[2], args.target.resolve(),
               args.repo, args.base, args.issue, args.reviewer)
    except Exception as exc:
        # Never echo subprocess stderr, arguments, URLs with credentials or env.
        detail = str(exc) if isinstance(exc, UpdateError) else type(exc).__name__
        print(f"sync stopped: {detail}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
