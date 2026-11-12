# DEC-2026-11-12: downstream agents report harness problems to Mercury themselves

Status: current. Decided by the user on 2026-11-12 (#783).

## Decision

1. An agent working in a project that installs Mercury, when something Mercury installed blocks legitimate work,
   lacks an entry the work needs or says something wrong, opens an Issue in Mercury's repository itself (or comments
   on a matching open one) and gives the user the link. The user does not relay the problem by word of mouth.
2. This is a standing authorization for exactly two actions in Mercury's repository: opening such an Issue and
   commenting on one. Pull requests, labels on other Issues, closing Issues and any write to Mercury's branches stay
   outside it.
3. Mercury's repository is public. A report leaves out credentials, machine paths and the project's private content.
4. Mercury's main agent starts harness work from these Issues and checks each report against the code and the
   evidence before acting on it.

## Why

The problems that led to #783 (no guarded way to bring a task branch up to its own remote branch; briefs measured in
bytes against a budget in characters) were met by sessions in a downstream project and reached Mercury only because
the user repeated them in another session. That makes the user the transport, loses the exact output, and depends on
the user remembering.

## Where it lives

The rule for every project is in the managed section (`packs/core/agents.md`, "Reporting a harness problem to
Mercury"); the procedure and the content of a report are in `packs/core/docs/mercury-harness-ownership.md`
("Reporting a problem to Mercury"). Mercury's side is in `AGENTS.md` under "Ownership and memory".
