"""Engine <-> design-library id map for Ship of Theseus (Mercury #556).

Two repositories describe the same game with two different id taxonomies:

  engine  `Ship_of_Theseus`   `data/**/*.json`   e.g. `swordsman_zhanji`
  design  `SoT-fyc-space`     `snapshots/*.json` e.g. `myrmidon_zhanji`

`id_map.json` is the single hand-maintained source of truth for that
correspondence; `checker.py` is the mechanical validator that reads both
repositories read-only and proves the map still describes reality.

Per the cross-team lane document (`docs/mercury-sot-lane-management.md`
section 2, row "engine <-> design id map"), Mercury owns the table and the
checker **only** — engine-side consumption of the map belongs to the SoT
lane. This package therefore deliberately ships no consumer logic: a second
consumer here would turn the map into a dual-write source of truth.

Invoke as a module: `python -m scripts.sot_id_map --help`.
"""
