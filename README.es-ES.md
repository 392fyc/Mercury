# Mercury

Mercury es un **framework de arnés para Claude Code** diseñado para mantener a los agentes de codificación de IA trabajando de forma continua, autónoma y con alta calidad. No es una aplicación que se instale y se ejecute, sino un repositorio que clonas y al cual diriges a Claude Code.

Mercury resuelve los problemas que Claude Code por sí solo no resuelve:

- Continuidad de la sesión cuando se llena el contexto (traspaso automático a una sesión fresca).
- Memoria a largo plazo entre sesiones y entre proyectos.
- Puertas de calidad (quality gates) para trabajo prolongado y desatendido.

Consulta [`.mercury/docs/DIRECTION.md`](.mercury/docs/DIRECTION.md) para ver el acta completa del proyecto y [`.mercury/docs/EXECUTION-PLAN.md`](.mercury/docs/EXECUTION-PLAN.md) para ver la hoja de ruta.

## Lo que Mercury NO es

El README anterior describía una aplicación de escritorio Tauri/Vue con un sidecar orquestador en Node.js. Esa arquitectura fue archivada en abril de 2026 como parte del pivote de dirección (ver [DIRECTION.md §五](.mercury/docs/DIRECTION.md)).

- **No** es un wrapper de CLI: Mercury no envuelve los binarios de `claude` / `codex` / `opencode`; los configura.
- **No** es un orquestador personalizado: los sub-agentes nativos de Claude Code cubren la función de despacho; el antiguo `packages/orchestrator/` reside en `archive/`.
- **No** es un sistema cerrado: cada habilidad, hook, agente y adaptador está diseñado para ser extraído y utilizado en otro repositorio.
- **No** es software para "modelos débiles": las funcionalidades están diseñadas para ser compatibles hacia arriba con modelos más fuertes, nunca basadas en las limitaciones actuales.

La única excepción a "no es una aplicación" es un MVP temprano de **GUI de escritorio** (`mercury-gui/`), un shell de Tauri 2 que observa el estado de ejecución de Mercury. Es una herramienta interna de Mercury explorada antes del activador de la Fase 6 bajo demanda, no el producto resucitado anterior al pivote.

## Estado actual (instantánea — 2026-06)

Mercury se construye fase por fase basándose en [`EXECUTION-PLAN.md`](.mercury/docs/EXECUTION-PLAN.md). Esta instantánea refleja el progreso acumulado; el plan es la fuente autoritativa.

| Fase | Alcance | Estado |
|---|---|---|
| **Fase 0** | Limpieza + andamiaje — archivar stack pre-pivote, migrar roles a `.claude/agents/*.md` | ✅ Completado |
| **Fase 1** | Pipeline de desarrollo — Cadena de revisión a ciegas Main → Dev → Acceptance + `pr-flow` | ✅ Completado |
| **Fase 2** | Puertas de calidad — aplicación mecánica de Stop-hooks (`adapters/mercury-test-gate/`, `mercury-loop-detector/`) | ✅ Completado |
| **Fase 3** | Capa de memoria — mem0 + Qdrant para memoria entre sesiones/proyectos (nivel usuario) | ✅ Completado |
| **Fase 4** | Continuidad de sesión — cadena de sesiones `claude-handoff`, worktree por tarea, prevención de compactación, detección de estancamiento | ✅ Completado |
| ~~**Fase 5**~~ | ~~Centro de notificaciones — canal de Telegram~~ — **abandonado y eliminado** ([#512](https://github.com/392fyc/Mercury/issues/512)): el enfoque de Telegram/Channels está limitado por el flag de despliegue `tengu_harbor` del lado del servidor de Anthropic (no disponible en cuentas personales), por lo que el subsistema fue eliminado | ❌ Eliminado |
| **Fase 6** | GUI de escritorio — evaluada bajo demanda después de que las Fases 1-4 sean estables | ⚪ Bajo demanda |

Según [`EXECUTION-PLAN.md`](.mercury/docs/EXECUTION-PLAN.md), la Fase 6 es explícitamente **bajo demanda y no forma parte de la hoja de ruta comprometida**. No obstante, existe un MVP temprano de `mercury-gui/` (Tauri 2 + React) en el árbol como trabajo exploratorio previo a cualquier activador formal.

Adiciones recientes sobre las fases principales:

- **Desarrollo multicanal (Multi-lane)** — múltiples carriles de trabajo aislados (propio worktree + rama + traspaso) ejecutándose en paralelo bajo un límite estricto de 5 carriles (ver [Desarrollo multicanal](#multi-lane-development)).
- **Agente de voz (experimental)** — bucle de conversación STT/TTS local en `scripts/voice/` (daemon de escucha, cola de transcripciones, reproducción interrumpible) expuesto a Claude Code mediante un servidor MCP.
- **Codex hooks GA** — los hooks de ciclo de vida ahora aplican la política de rama/alcance para sesiones de Codex, compartiendo los mismos scripts que Claude Code (ver [Runtimes multi-agente](#multi-agent-runtimes)).

## Arquitectura de un vistazo

```
Mercury (núcleo ligero — solo construye lo que ningún proyecto externo proporciona)
├── .claude/
│   ├── agents/        definiciones de roles de sub-agentes (main, dev, acceptance, critic, design, research, game-*)
│   ├── skills/        habilidades de flujo de trabajo reutilizables (pr-flow, autoresearch, dev-pipeline, dual-verify, ...)
│   └── hooks/         scripts de hooks de ciclo de vida (PreToolUse/PostToolUse/UserPromptSubmit/Stop/SubagentStop), conectados vía settings.json — compartidos con Codex
├── .codex/            configuración de CLI de Codex + hooks.json (lifecycle hooks GA) + rules/ defensa en profundidad
├── .mercury/
│   ├── docs/          DIRECTION.md + EXECUTION-PLAN.md + guides/ + research/
│   ├── templates/     plantillas de prompt de despacho
│   └── gates/         configuraciones de puertas de calidad
├── adapters/          adaptadores de hook/puerta/integración propiedad de Mercury (≤200 LOC cada uno para montajes externos)
├── scripts/           scripts de mantenimiento (lane-*, worktree-reaper, mem0 hooks, codex guardrails, voice/, ...)
├── mercury-gui/       MVP temprano de GUI de escritorio — Tauri 2 + React (Fase 6 es bajo demanda)
└── modules/           reservado para proyectos externos montados (actualmente vacío — ver Montajes de proyectos externos)
```

`adapters/` contiene actualmente cuatro adaptadores: `mercury-loop-detector` y `mercury-test-gate` (puertas mecánicas de Stop-hook), `gpt-image-2` (generación de activos de píxeles) y `playwright-mcp` (montaje de automatización de navegador).

La configuración reside en la raíz del repositorio:

- `CLAUDE.md` — instrucciones para sesiones de Claude Code (políticas DEBE/NO HACER)
- `AGENTS.md` — instrucciones para sesiones de Codex
- `GEMINI.md`, `OPENCODE.md` — archivos de instrucciones por agente

## Primeros pasos

### Prerrequisitos

- [Claude Code CLI](https://claude.com/claude-code) (runtime principal)
- [`gh`](https://cli.github.com/) — GitHub CLI para el flujo de PR
- `git` (se recomienda soporte para worktree)
- Opcional, por agente:
  - [Codex CLI](https://developers.openai.com/codex/) — para sesiones impulsadas por `AGENTS.md` (hooks de ciclo de vida soportados, ver abajo)
  - [Gemini CLI](https://www.npmjs.com/package/@google/gemini-cli) — para sesiones impulsadas por `GEMINI.md`

### Clonar y entrar

```bash
git clone https://github.com/392fyc/Mercury.git
cd Mercury
claude   # lanza una sesión de Claude Code en la raíz del repositorio
```

Al iniciar la sesión, Claude Code descubre automáticamente cada agente en `.claude/agents/` y cada habilidad en `.claude/skills/`. Los hooks no se descubren por directorio; están conectados a eventos de ciclo de vida en `.claude/settings.json` (y `.codex/hooks.json` para Codex), con los scripts residiendo en `.claude/hooks/`. No se requiere paso de compilación.

### Lista de verificación para la primera sesión típica

1. Leer `CLAUDE.md` (mostrado automáticamente por Claude Code) — impone flujo de trabajo basado en issues, verificación dual antes del commit, regla de PR hacia `develop`.
2. Leer `.mercury/docs/DIRECTION.md` — acta del proyecto y definiciones de módulos.
3. Revisar `.claude/skills/` — flujos de trabajo disponibles (`pr-flow`, `autoresearch`, `dev-pipeline`, `dual-verify`, `caveman-toggle`, ...).
4. Ejecutar tu primera tarea a través de la habilidad `dev-pipeline`: despacha un sub-agente `dev`, luego un sub-agente `acceptance`, y devuelve un veredicto de revisión a ciegas.

## Habilidades y sub-agentes

Las habilidades en `.claude/skills/` y los sub-agentes en `.claude/agents/` son **desacoplables**: cada directorio es autónomo y puede copiarse en otro proyecto de Claude Code. El frontmatter de la habilidad enumera las frases activadoras en inglés y chino. Considera el contenido del directorio como la lista autoritativa; la instantánea a continuación es actual al momento de escribir y no pretende ser un conteo fijo.

Habilidades (11 al momento de escribir):

| Habilidad | Propósito |
|-------|---------|
| `dev-pipeline` | Main → sub-agente Dev → sub-agente Acceptance con revisión a ciegas |
| `pr-flow` | Ciclo de vida de PR de extremo a extremo: crear → consultar Argus → corregir → fusionar |
| `dual-verify` | Revisión profunda de Claude Code en paralelo + auditoría de código de Codex (obligatorio pre-commit según CLAUDE.md) |
| `autoresearch` | Investigación web de múltiples rondas con una puerta de calidad mecánica |
| `web-research` | Protocolo de verificación web obligatorio para cualquier afirmación de SDK/API/CLI |
| `handoff` | Documento de traspaso de sesión a sesión + prompt de inicio listo para pegar |
| `systematic-debugging` | Flujo de trabajo de depuración basado primero en la causa raíz |
| `subagent-driven-development` | Ejecutar un plan mediante un sub-agente fresco por tarea, revisión en dos etapas |
| `verification-before-completion` | Punto de control de evidencia dura antes de afirmaciones antes del "hecho" |
| `animate-frames` | Pipeline de animación de cuadros de píxeles (secuencias de sprites) vía el adaptador `gpt-image-2` |
| `caveman-toggle` | Modo de salida concisa persistente |

Sub-agentes (9): `main`, `dev`, `acceptance`, `critic`, `design`, `research`, más tres agentes de diseño de juegos (`game-researcher`, `game-analyst`, `game-critic`) seleccionados de `msitarzewski/agency-agents`.

## Hooks

`.claude/settings.json` conecta los scripts de hook (en `.claude/hooks/`) con eventos de ciclo de vida:

- `session-init.sh` — inyección de contexto en `UserPromptSubmit` (fecha, índice de KB, instantáneas de memoria).
- `pre-commit-guard.sh`, `pr-create-guard.sh`, `pr-merge-guard.sh`, `push-guard.sh` — políticas de rama `PreToolUse` (Bash) + puerta de verificación dual.
- `scope-guard.sh` (`PreToolUse` en Edit/Write), `post-commit-reset.sh`, `post-review-flag.sh`, `post-web-research-flag.sh` — cumplimiento de alcance y ciclo de vida de flags de estado (`PostToolUse`).
- `stop-guard.sh`, `auto-handoff-stop.sh` — `Stop`; además de `research-stop-nudge.sh` en `SubagentStop`.

(Los hooks de memoria entre sesiones y compactación — `pre-compact.py`, `session-end.py` — se ejecutan al **nivel de usuario** en `~/.claude/hooks/`, no en este repositorio; ver [Ecosistema](#ecosystem). La integración de voz experimental incluye scripts en `scripts/voice/` + `.claude/hooks/voice-*.sh` que no están conectados al `settings.json` comprometido por defecto).

`adapters/mercury-loop-detector/` y `adapters/mercury-test-gate/` implementan la aplicación mecánica de Stop-hooks mediante códigos de salida (registrados en `PostToolUse` y `SubagentStop` respectivamente). Según DIRECTION.md §八-1, esta es la única implementación de Stop-hook mecánica basada en códigos de salida conocida en el ecosistema de Claude Code, una brecha identificada durante la evaluación de la Fase 2-1.

## Desarrollo multicanal (Multi-lane)

Mercury ejecuta múltiples **carriles (lanes)** en paralelo: flujos de trabajo independientes que no interfieren entre sí. Cada carril posee un worktree de git, un espacio de nombres de rama y un documento de traspaso, para que las sesiones concurrentes (por ejemplo, un carril de arquitectura y un carril de corrección de errores) permanezcan aisladas.

- **Prefijo de rama**: `lane/<corto>/<N>-<slug>` (≤40 caracteres; aún se acepta la forma heredada `feature/lane-<lane>/...`).
- **Límite estricto**: 5 carriles activos, basado en investigaciones de memoria de trabajo y sobrecarga de coordinación (ver [`lane-naming.md`](.mercury/docs/guides/lane-naming.md)).
- **Herramientas**: `scripts/lane-*.sh` (spawn / claim / close / sweep) + `lane-assertion.sh`, `lane-cap-check.sh` aplican el protocolo mecánicamente.
- **Guías de carril**: [`lane-spawn.md`](.mercury/docs/guides/lane-spawn.md), [`lane-claim.md`](.mercury/docs/guides/lane-claim.md), [`lane-close.md`](.mercury/docs/guides/lane-close.md), [`lane-sweep.md`](.mercury/docs/guides/lane-sweep.md), [`lane-emergency-escalation.md`](.mercury/docs/guides/lane-emergency-escalation.md).

## Runtimes multi-agente

Mercury es principalmente un arnés para Claude Code, pero las mismas políticas se reflejan para otras CLI de agentes para que una tarea pueda pasarse entre runtimes sin perder sus protecciones.

- **Claude Code** — runtime principal; lee `CLAUDE.md`, descubre automáticamente `.claude/{agents,skills}` y conecta hooks vía `.claude/settings.json`.
- **Codex CLI** — lee `AGENTS.md`; los hooks de ciclo de vida están en **GA** (Codex CLI ≥ v0.124, estable v0.128+) y se habilitan mediante `[features] hooks = true` en `.codex/config.toml`. Los scripts de hook residen en `.claude/hooks/` (única fuente de verdad, compartida con Claude Code); `.codex/rules/` + `scripts/codex/*.ps1` permanecen como defensa en profundidad, y `.codex/rules/` también impone lo que los hooks no pueden (por ejemplo, la puerta de investigación web).
- **Gemini / OpenCode** — `GEMINI.md` / `OPENCODE.md` llevan el conjunto de instrucciones equivalente.

## Ecosistema

Algunas capacidades de Mercury se ejecutan como capas independientes y desplegables en lugar de código en el repositorio:

| Capa | Ubicación | Rol |
|---|---|---|
| **claude-handoff** | Plugin local ([392fyc/claude-handoff](https://github.com/392fyc/claude-handoff)) | Traspaso / continuación de sesión + `session_chain` SQLite — respalda la Fase 4 |
| **Capa de Memoria** | `~/.claude/hooks/` + `~/.claude/scripts/` a nivel de usuario | adaptador mem0 + Qdrant, hooks de inicio/fin/pre-compactación de sesión, rastreador de costos — respalda la Fase 3 |
| **Argus** | Bot de revisión de PR auto-alojado | Revisión automatizada de PR en GitHub; se empareja con `dual-verify` y la habilidad `pr-flow` |
| **oh-my-claudecode (OMC)** | Plugin de Claude Code — habilitado en `.claude/settings.json` comprometido (`enabledPlugins`) | Compañero de orquestación multi-agente: ciclos de UltraQA, equipos de agentes, investigación profunda, ciclo de vida de habilidades. Adoptado como plugin (DEC-4 "Path β"); su puerta `SubagentStop` a nivel de LLM complementa el adaptador mecánico `mercury-test-gate` de Mercury. **Opcional y reversible** — tras clonar, ejecuta `/plugin marketplace add https://github.com/Yeachan-Heo/oh-my-claudecode`, luego `/plugin install oh-my-claudecode@omc`, y finalmente `/reload-plugins` (marketplace-add solo registra el catálogo; el paso de instalación explícito es lo que descarga el plugin). Para desactivarlo, configúralo como `false` en tu `.claude/settings.local.json` ignorado por git (la configuración local tiene prioridad sobre la del proyecto), de modo que Mercury se ejecute sin él y la configuración compartida nunca se toque. |

Los cambios a nivel de usuario (cualquier cosa bajo `~/.claude/`) se gobiernan separadamente de los PR del proyecto — consulta la sección "用户级变更治理" de [`CLAUDE.md`](CLAUDE.md) para la disciplina de seguimiento de issues y reversión.

> **OMC es un plugin, no un montaje en `modules/`.** La Fase 2-1 evaluó OMC (junto con GSD / Superpowers / OpenSpace) frente a un criterio mecánico de Stop-hook y lo aplazó (PR #195) por dos razones: la puerta de OMC es a nivel de LLM en lugar de una verificación mecánica de código de salida, y OMC se distribuye **solo como plugin sin ruta de git-submodule**, lo cual no encajaba con la lectura estricta de entonces del principio de "montar como submódulo bajo `modules/`". Por eso `modules/` permanece vacío. OMC fue adoptado posteriormente en su eje *soportado*: un **plugin** de Claude Code (DEC-4 "Path β"), registrado en `.claude/settings.json`. Así que Mercury *sí* utiliza OMC como compañero de plugin; simplemente no es (ni puede ser) suministrado como un submódulo. El plugin sigue siendo **opcional** — anúlalo a `false` en tu `.claude/settings.local.json` ignorado por git y Mercury funcionará sin cambios: un compañero de conveniencia, no una dependencia dura. Consulta [Montajes de proyectos externos](#external-project-mounts) para la filosofía de submódulos.

## Montajes de proyectos externos

La filosofía de montaje de Mercury (DIRECTION.md §四): construir el mínimo internamente; montar proyectos externos vía git submodule bajo `modules/` con una capa de traducción delgada en `adapters/<nombre>/` (≤200 LOC). La Fase 2-1 evaluó cuatro candidatos (GSD, Superpowers, OMC, OpenSpace) frente a un criterio estrecho de aceptación de Stop-hook; los cuatro fueron RECHAZADOS o APLAZADOS basándose en ese criterio, por lo que `modules/` está actualmente vacío. Otros valores de esos proyectos han sido seleccionados individualmente (ver `.mercury/state/upstream-manifest.json` y `scripts/upstream-drift-check.sh`).

Cuando se seleccionan archivos de un proyecto externo hacia Mercury, el protocolo de selección en [`CLAUDE.md`](CLAUDE.md) es la fuente canónica para la atribución / manifiesto / disciplina de deriva requerida. Dos casos adyacentes (andamiaje de CLI de un solo uso e importaciones por elemento basadas en registro) tienen una excepción más estrecha — CLAUDE.md mantiene un resumen; las reglas completas autoritativas residen en [`.mercury/docs/guides/cherry-pick-carve-out.md`](.mercury/docs/guides/cherry-pick-carve-out.md). Este README no repite las reglas; consulta esos documentos para detalles actuales.

## Archivos de ejemplo

Se incluyen dos archivos `.example` en la raíz del repositorio. Sirven para dos modelos de seguimiento diferentes:

| Plantilla | Objetivo | Modelo de seguimiento |
|---|---|---|
| `CLAUDE.local.md.example` | `CLAUDE.local.md` | El objetivo está **ignorando por git** — instrucciones personales por desarrollador |
| `.pr_agent.toml.example` | `.pr_agent.toml` | El objetivo está **comprometido** (configuración de alcance del proyecto para Argus); el `.example` es material de referencia al configurar el archivo en otro lugar |

### Modo Caveman (local, ignorado por git)

Estilo de salida concisa persistente basado en [JuliusBrussee/caveman](https://github.com/JuliusBrussee/caveman) (MIT). Actívalo mediante la habilidad `caveman-toggle`:

```
/caveman-on          # activar modo lite (predeterminado)
/caveman-on full     # activar modo full
/caveman-off         # desactivar
```

O manualmente: `cp CLAUDE.local.md.example CLAUDE.local.md`.

### Bot de revisión de PR (comprometido)

`.pr_agent.toml` ya está comprometido; edítalo directamente en lugar de copiarlo desde el `.example`. El `.example` existe para iniciar el archivo en otro repositorio o regenerarlo desde cero.

## Índice de documentación

| Tema | Ruta |
|---|---|
| Acta del proyecto y definiciones de módulos | [`.mercury/docs/DIRECTION.md`](.mercury/docs/DIRECTION.md) |
| Hoja de ruta de ejecución (Fase 0 → Fase 6) | [`.mercury/docs/EXECUTION-PLAN.md`](.mercury/docs/EXECUTION-PLAN.md) |
| Instrucciones de sesión de Claude Code | [`CLAUDE.md`](CLAUDE.md) |
| Instrucciones de sesión de Codex | [`AGENTS.md`](AGENTS.md) |
| Convenciones de Git-flow | [`.mercury/docs/guides/git-flow.md`](.mercury/docs/guides/git-flow.md) |
| Flujo de trabajo basado en issues | [`.mercury/docs/guides/issue-workflow.md`](.mercury/docs/guides/issue-workflow.md) |
| Nomenclatura de carriles + límite de concurrencia | [`.mercury/docs/guides/lane-naming.md`](.mercury/docs/guides/lane-naming.md) |
| Evaluación de arquitectura (PR #162) | [`.mercury/docs/research/issue-158-architecture-evaluation.md`](.mercury/docs/research/issue-158-architecture-evaluation.md) |

## Componentes heredados / archivados

Los siguientes directorios preservan la arquitectura de orquestador/GUI anterior al pivote y no forman parte del runtime activo. Se mantienen en el árbol para referencia histórica y posible selección; no los edites en PR activos.

- `archive/packages/{gui,orchestrator,sdk-adapters,poc}/` — antiguo stack de Tauri/Vue/Node.js
- `archive/roles/*.yaml` — antiguas definiciones de roles (migradas a `.claude/agents/*.md`)
- `archive/agents/`, `archive/skills/`, `archive/docs/` — contenido pre-pivote

`packages/core/` aún existe en la raíz del repositorio para cualquier tipo compartido que aún pueda ser consumido. `mercury.config.json` / `mercury.config.example.json` permanecen como configuración heredada — solo `obsidian.vaultName` / `obsidian.vaultPath` todavía se leen (por `session-init.sh`), y su eliminación está pendiente de la limpieza de migración a mem0.

(Nota: el MVP de GUI `mercury-gui/` temprano en la raíz del repositorio es distinto del `archive/packages/gui/` pre-pivote archivado).

## Licencia

MIT — ver [LICENSE](LICENSE).
