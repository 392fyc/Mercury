## Knowledge base

- The project's knowledge base follows `.mercury/packs/kb/docs/kb-conventions.md`: one entry page leading to the current rules, the progress page and the decision register; a README index with a status for every document in every directory; history in the archive.
- Before editing, read the KB documents that govern the task, starting from the entry page. When a change alters a rule, a decision or progress, update the current document and mark what it replaces (status `superseded` with its replacement) in the same pull request.
- Every pull request names its Issue and has a line starting with `KB:` that lists the KB documents it updated, or says `KB: no impact` and why. `guard.ps1 pre-merge` refuses a pull request without both. Check the KB with `python -B .mercury/packs/kb/scripts/kb_check.py <kb-root>`.
