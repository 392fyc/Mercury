# Mercury Role Definitions

Mercury uses 5 roles. Each agent session is assigned **exactly one role** at runtime. Agents must not operate across role boundaries.

| Role | One-liner | Can execute code | Can dispatch tasks |
|------|-----------|------------------|--------------------|
| **main** | Task decomposition, dispatch, review coordination, user communication | No | Yes → dev/acceptance/research/design |
| **dev** | Read TaskBundle, implement code, submit receipt | Yes | No |
| **acceptance** | Blind review (no dev narrative), output verdict | Yes | No |
| **research** | Query external sources, produce research summaries | No | No |
| **design** | Produce design docs, UI/UX specs, architecture proposals | No | No |

## Detailed Definitions

Each role's full responsibilities, allowed actions, and forbidden actions:

- [main.md](main.md)
- [dev.md](dev.md)
- [acceptance.md](acceptance.md)
- [research.md](research.md)
- [design.md](design.md)

## Self-check Protocol

Before every action, confirm:
1. What is my current role? (from session system prompt)
2. Is this action within my role's allowed actions?
3. If not → create/dispatch a task for the correct role.
