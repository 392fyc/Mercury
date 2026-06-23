# NAS Service Registry — auto-update + reconciliation drift detection

> Issue #163. Mercury-internal tooling under `scripts/` (no LOC cap). The NAS is
> a QNAP TS-464 + Container Station running services behind a token-based
> cloudflared tunnel. A hand-maintained `services.yaml` describes the fleet and
> drifts over time; this suite keeps it honest from two directions.

## Components

| File | Role | Writes services.yaml? |
|------|------|----------------------|
| `scripts/lib/registry-sh.sh` | Shared BusyBox-sh helpers (read fields, comment-preserving upsert, port reserve, mkdir lock) | via upsert/reserve only |
| `scripts/validate-registry.sh` | PULL: scan live docker, FLAG drift | **never** (read-only) |
| `scripts/register-service.sh` | PUSH: deploy-time idempotent upsert + port reserve | yes (atomic mktemp+mv, timestamped `.bak`) |
| `scripts/test-validate-registry.sh` / `scripts/test-register-service.sh` | Fixture-driven tests (no real NAS / no real docker) | no |

## Why two scripts (AC closure rationale)

- **register-service.sh satisfies the deploy-time AC**: when a service lands, the
  deploy step pushes an accurate entry (port, derived container set) so the
  registry is correct at write time. It cannot, however, see anything it was
  never told about.
- **validate-registry.sh satisfies the drift-detection AC**: it enumerates what
  docker is *actually* running and reconciles against the registry, catching the
  three failure modes a push structurally misses — a project deployed without
  ever calling register (`sot-codex`), a hand-typed `containers[]` that went
  stale (`argus` missing `argus-selfcheck-scheduler`), and an occupied host port
  nobody reserved (`8400`).

Together: push keeps the common path correct; pull is the safety net for the
out-of-band path.

## Field derivability matrix

Which `services.<name>` fields can self-heal from docker vs. require a human:

| Field | Source | Self-healing? |
|-------|--------|---------------|
| `port` | docker published host port | yes (validate flags mismatch; register writes it) |
| `containers[]` | docker running container set per compose project | yes (register auto-derives; validate flags drift) |
| `compose` | compose `config_files` label (comma-split for multi-file) | partially — discoverable, but a path the operator usually passes explicitly |
| `subdomain` | — | **human-only** (no on-NAS source) |
| `url` | — | **human-only** |
| `purpose` | — | **human-only** |
| `repo` | — | **human-only** |
| `cicd` | — | **human-only** |

`subdomain` / `url` route through a token-based cloudflared tunnel with no
on-NAS ingress config to diff, so validate emits them as
`[OUT-OF-SCOPE-UNVERIFIABLE]` and never reconciles them.

## validate finding categories

```
[UNREGISTERED-PROJECT]      running compose project absent from registry
[CONTAINER-SET-DRIFT]       registry containers[] != actual running set
[UNRESERVED-PORT]           running host port not in reserved_ports
[PORT-CONFLICT]             one host port claimed by >1 compose project
[NOT-RUNNING]               registry service has zero live containers (advisory)
[OUT-OF-SCOPE-UNVERIFIABLE] subdomain/url — tunnel has no diffable ingress
```

`[NOT-RUNNING]` deliberately means "registered but currently down" — it is NOT
treated as "removed", so a container bouncing during a restart is not misread as
a deletion.

## BusyBox / NAS constraints (why the code looks the way it does)

- `/bin/sh` is BusyBox; `/bin/bash` is a symlink to it. All scripts are pure
  POSIX/BusyBox sh — no `[[ ]]`, arrays, `${var,,}`, process substitution, or
  `function` keyword.
- `python` / `python3` / `yq` / `jq` / `flock` / Entware are absent. Therefore:
  locking is `mkdir`-based; docker output is consumed via Go-template `--format`
  (never `--format json`, no jq); YAML is read/written with line-anchored
  sed/awk (never a full YAML re-serialization).
- The docker command is parameterized via `REGISTRY_DOCKER` (default is the NAS
  `sudo env DOCKER_HOST=... <path>/docker` invocation). Tests inject a
  fixture-printing stub, so the suite runs on any box with `sh`+`awk`+`sed`.
- `REGISTRY_FILE` and `REGISTRY_LOCKDIR` are likewise env-parameterized.

## Out of scope for this PR (deferred to user-level governance)

NAS deployment, the periodic cron schedule that runs `validate-registry.sh`, and
any historical backfill of the existing registry are **user-level governance**
and are NOT part of this PR (which produces only the repo-side scripts). Per
`CLAUDE.md` user-level change governance, those steps should be tracked under a
follow-up Issue with a command log + verification checklist when performed on the
NAS.

## Running the tests

```sh
sh scripts/test-validate-registry.sh
sh scripts/test-register-service.sh
```

Both are fixture-driven and require no NAS, no docker, and no network.
