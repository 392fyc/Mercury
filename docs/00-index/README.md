# Mercury docs

`docs/` is Mercury's knowledge base and an Obsidian vault: research, decisions and design notes that explain why Mercury is the way it is. Open the main checkout's `docs/` folder as a vault to browse it.

It replaced the private `Mercury_KB` vault in October 2026 (Mercury #718). Only an audited snapshot was published here; the old repository stays private and archived, with its full history and the material not published (session ledgers, orchestrator-era task bundles and templates).

## Layout

| Folder | Content |
|---|---|
| `00-index/` | This entry and the decision index |
| `01-registry/` | Standing policies |
| `03-decisions/` | Decision records (ADRs) |
| `03-research/` | Early (phase 3) research reports |
| `04-research/` | Research reports; `04-research/mercury/` holds the Mercury-specific series, with `articles/`, `design/` and `incidents/` |

The harness's own guides stay in `.mercury/docs/`; `.mercury/docs/guides/kb-structure.md` describes how `docs/` is maintained.

## Working with it

- Change `docs/` in the task worktree together with the code it explains, and deliver both in one pull request.
- Agents read and search the files directly. An Obsidian MCP server, when connected, points at the main checkout and only sees merged content; agents do not write through it.
- `docs/` is public: no credentials, machine paths, private project details or personal information.
- Some older notes mention `Mercury_KB/...` paths or folders that were not published; read them as history.
