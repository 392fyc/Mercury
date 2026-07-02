# SoT 设计库 Phase 3 — 交互审阅前端实现规格（2026-06-23）

> **性质**：SoT 设计库（`D:/ShipOfTheseus/SoT-fyc-space`，FastAPI+SQLModel+SQLite，部署 NAS sot.fyc-space.uk）Phase 3 实现规格。改 SoT-fyc-space 仓已授权。本文件 = 对抗式设计 Workflow（11 agent：4 研究+3 架构+1 评判+2 批判）产出 + main-loop web-verify 后的最终落地方案，dev agent 据此实现。配套权威待办在路线图 §10。
>
> **来源**：设计 Workflow `wf_cf1d0c3a-8a2`（冠军=草案3 UX优先，嫁接草案2稳健+草案1复用；2 批判者各判「需修订后实施」）。技术地基 main-loop web-verified（见 §6）。

---

## 0. 目标与约束

**目标**：网页内投入天赋 → 现场异步审核 1/多张 → 对话反馈 → agent 帮改天赋（effect/rules/tags/trigger）→ 读新内容重审。

**硬约束**：
1. review 真实 ~247s → 前端**绝不同步等**（异步化）。
2. 纯 OpenAI gpt-5.4（openai 2.43 async SDK），**不用 Claude/Anthropic**。env `OPENAI_API_KEY`。
3. 服务端渲染 Jinja2 + HTMX 2.0.4 + Alpine（CDN），**禁 node 构建**。
4. 单 worker uvicorn（Dockerfile CMD 无 `--workers`）—— 进程内 task + 模块级状态成立的前提。
5. **review 绝不静默改库 Talent**（延续 Comment 独立旁注纪律）；帮改作用于会话内工作草稿，落库须显式动作。
6. 本地无 `OPENAI_API_KEY` → 只能 mock 测编排；真实验证靠 NAS 部署 smoke。
7. 产代码改动照走 dual-verify（跨仓库手动）+ commit master + tar 同步 NAS。

---

## 1. 异步传输：DB 持久 job + asyncio.create_task + HTMX 短轮询（**非 SSE，非 BackgroundTasks**）

**决定性论据（web-verified §6）**：CF Tunnel 对 SSE 有 ~100s 超时 + ~100KB 缓冲 + GET 连接关闭前不流式 → SSE 对 247s 任务在 sot.fyc-space.uk **结构性失效**。FastAPI `BackgroundTasks` 是「响应后 fire-and-forget」无 job 句柄无法轮询。→ **DB job + `asyncio.create_task` + HTMX 轮询**。

**机制**：
- `POST .../sessions`（或 rerun）→ 同步内联跑 **L1**（`run_l1_deterministic`，<1s，立即填首屏）→ 建 `ReviewJob` 行（status=queued，l1_json 已写满）→ 经临界区 `asyncio.create_task(_run_l2_l3_job(...))` → 立即返回 job 卡片片段（含轮询触发器）。
- 后台协程 `_run_l2_l3_job` 用**独立 Session**（见 §6-C1 engine 注入），分阶段写 `progress_stage`，`await run_l2_l3`（async I/O，单事件循环挂起期间可服务轮询）。
- 轮询端点 `GET .../jobs/{job_id}/status`：queued/running→进度片段（带 `hx-trigger="every 2s"`）；done→结果片段（**不带 trigger=停轮询**）+ 亮对话面板；failed→错误卡 + 重审按钮（不带 trigger）。

**停轮询（web-verified §6）**：done/failed 片段不再含 `hx-trigger` 即停（htmx 官方法一），轮询端点终态可附 `HX-Trigger`/286 兜底。

---

## 2. DB schema：2 表（落 `app/models.py` 末尾，随 `create_db_and_tables()` 自动建表，无 alembic）

**`ReviewSession`（持久会话容器）**：
- `id: str` PK（`uuid4().hex`）
- `talent_id: Optional[str]` FK→talent.id，nullable index —— **软引用不级联**（删 Talent 不销毁审阅历史）
- `class_id: str` default `"ss"`
- `mode: str`（`"existing"`/`"draft"`）
- `working_draft: str`（JSON 工作草稿；帮改作用于此，**绝不写 Talent**）
- `messages: str`（JSON OpenAI 风格对话历史，默认 `"[]"`）
- `rejected_proposals: str`（JSON list，§6-M4 否决记忆；审阅者点「拒绝此提案」append 摘要）
- `latest_verdict: Optional[str]`
- `status: str`（`"open"`/`"applied"`/`"archived"`）
- `created_at`/`updated_at`（复用 models.py 既有 timezone-aware + `onupdate=_utcnow`）

**`ReviewJob`（一次异步运行）**：
- `id: str` PK（`uuid4().hex` = job_id）
- `session_id: str` FK→reviewsession.id index
- `candidate_snapshot: str`（JSON，派发时冻结候选副本）
- `corpus_snapshot: str`（JSON，**派发时冻结 peers+rules**，§6-M3 防语料漂移污染 verdict 对比；后台协程用快照非实时查库）
- `status: str` 状态机：`queued`→`running`→终态 `done`|`failed`
- `l3_effort: str`
- `l1_json: str`（同步内联产出，建 job 即写满）
- `progress_stage: str`（`l1_done`/`l2_running`/`l3_optimizer`/`l3_defender`/`synthesizing`）
- `progress_detail: str`（JSON `{l2_total,l2_done,l2_dropped,l3_phase}`）
- `result_json: Optional[str]`（终态 = `_deep_review` 返回形状 + `corpus_dropped`）
- `error: Optional[str]`
- `prompt_tokens`/`completion_tokens: int` default 0（从 OpenAI usage 累加，成本监控）
- `attempt: int` default 1
- `created_at`/`updated_at`

**隔离纪律**：working_draft/candidate_snapshot 是 Talent 字段的 JSON 副本，审阅全程读写副本，Talent 行零触碰；落库是唯一写 Talent 路径，复用现有 `talent_update`（pages.py:407）/`talent_create`（pages.py:215）。

---

## 3. 提速（纯 llm.py + env，与异步正交，先做）

> ⚠️ 澄清：现有 ss 职业仅 ~20 天赋，corpus 不是 247s 主因（主因 = L3 xhigh reasoning + L2 pair triage）。**主延迟处理 = 异步化**（用户不同步等）+ 已在生产 .env 应用的 effort 降档（xhigh→high）+ L2 pair cap=5。corpus cap 是**无上限增长护栏**（272K cliff 防护），非主提速杠杆。

- **(a) L3 corpus top-N 截断**（§6-C3 **关键修正**）：**不复用** `enumerate_shared_pairs`（它只返回共享 tag 的 peer，会结构性丢掉零 tag 交集 peer = 恰是跨机制 exploit 候选，破坏 L3）。新增独立 `_select_corpus(peers, candidate, cap)`：优先保留共享 tag peer，**cap 未用满时用零 tag 交集 peer 填充**（按稀有度/更新时间），保证跨机制候选不被剔除。cap 走 env `SOT_LLM_L3_CORPUS_CAP`（**默认 40，高于现有天赋数 → 现数据不触发截断**）。触发时 `corpus_dropped` surface「对抗仅覆盖 top-N，截断 M 张（其中 K 张零 tag 交集）」；若被丢的含零 tag peer，L3 标 `partial_coverage` 计入 stage_failures（fail-closed 抬到 revise）。
- **(b) effort 透传**：`run_l3(..., l3_effort=None)` + `run_l2_l3(..., l3_effort=None)`，None 回退模块级 `L3_EFFORT`（默认 xhigh 不变）。前端 effort 选择器暴露 high/xhigh 两档写进 `job.l3_effort`。
- **(c)** L2 PAIR_CAP 保持现状（生产=5），不动默认。
- **token 粗估两路全覆盖**（§6-M3）：L2 = Σ over capped pairs `len(_slim(cand)+_slim(peer)+rules)`；L3 = `len(cand)+len(capped_corpus)+rules`；取较大/求和与 250K（env `SOT_LLM_TOKEN_WARN`）比，超则 job failed「语料过大需收窄」。不引 tiktoken，字符数/3.5 粗估（worst-case 1.35x 留余量）。

---

## 4. 对话：Chat Completions 自存 messages（**非 Responses API**）

- **理由**：现有 `_call_json`（llm.py:101）全建在 `chat.completions.create` + 扁平 `reasoning_effort` + `response_format` json_schema，44 测 + NAS smoke 已过；切 Responses API blast radius 过大。Chat Completions 自存 messages = 真持久（单一事实源在 DB）。
- **存哪/怎么续**：帮改对话独立 messages 流（`ReviewSession.messages`）；用户发反馈→append `{role:user}`→构造 `messages=[system(帮改专用 prompt，注入当前 working_draft + 最近 job verdict + 关键 finding 压缩摘要，**不塞 L2/L3 raw JSON** 控 token) + 历史]`→`chat.completions.create(model, messages, reasoning_effort="medium", response_format=帮改提案 schema)`→append assistant 提案→写回。L2/L3 审阅调用**不进**对话历史（结构化一次性，存 ReviewJob.result_json）。
- **messages 滑窗 + 否决记忆**（§6-M4）：超 env `SOT_REVIEW_MAX_TURNS`（默认 20）丢最早 user/assistant 对，**保留 system + 首条结论摘要**；额外把 `rejected_proposals`（ReviewSession 字段）每轮注入 system prompt「以下提案已被审阅者否决，勿重提：<list>」—— 滑窗丢历史不丢否决记忆，token 恒定。

---

## 5. 帮改三段式（守 review 绝不静默改库）

- **(1) 提案作用于工作草稿**：帮改 LLM 输出结构化 diff，schema = `{rationale, changes:[{field: enum[effect/rules/tags/trigger]【白名单，additionalProperties:false，非白名单 id/class_id/status 提案直接拒绝 surface 警告】, before, after, reason}], tags_add:[str], tags_remove:[str]}`。后端校验 tags_add/remove key ∈ Tag 注册表（非法拒绝该项 surface 警告不静默吞）。
- **(2) 应用到工作草稿**（会话内不落库）：`POST .../draft/apply` 把选中 changes 合并进 `ReviewSession.working_draft`（仅副本，Talent 零触碰），支持逐字段 checkbox 部分应用，返回草稿片段（变更字段 `.field-changed` 高亮）。
- **(3) 重审**：`POST .../rerun` 以 working_draft 为 candidate_snapshot 建新 ReviewJob（同 session 累积保留历史可对比 verdict 演进）→ 同异步流程。
- **(4) 落库**（唯一写 Talent 路径，显式动作）：`POST .../commit` → **reject/confirmed_exploit/stage_failures 非空时二次确认门对 existing 和 draft 两条路径都生效**（§6-H3，按最近 done job 的 verdict 判，与 mode 无关，确认文案明示 exploit 摘要）→ 复用 `talent_update`（existing）/`talent_create`（draft）→ Talent 更新 + session.status=`applied` + 可选追加 Comment（author=`审阅器`，§6 草案1 嫁接：审阅结论沉淀进现有讨论流，零新表）。

---

## 6. 批判修正（must-fix，全部 baked-in；技术点 web-verified）

### web-verified 技术地基（main-loop，2026-06-23）
| 点 | 结论 | 来源 |
|---|---|---|
| HTMX 停轮询 | `every <t>` 轮询；停 = HTTP 286 或返回不含 hx-trigger 的片段 | <https://htmx.org/attributes/hx-trigger/> |
| FastAPI 长任务 | job-id+轮询（返 202）；create_task 起后台 + task 引用集合 + semaphore | <https://fastapi.tiangolo.com/tutorial/background-tasks/> |
| asyncio GC | 事件循环仅持弱引用，3.12 起 task 可执行中途被 GC；解法=set 持强引用+add_done_callback | <https://docs.python.org/3/library/asyncio-task.html>, cpython#91887 |
| CF Tunnel SSE | SSE 缓冲+~100s 超时+~100KB 阈值+GET 不流式 → SSE 对 247s 失效 | cloudflared #199 / #1449 |
| SQLite PRAGMA | WAL 持久（设一次）；busy_timeout per-connection 须 connect listener 每连接设 | <https://docs.sqlalchemy.org/en/20/dialects/sqlite.html> |

### C1 [critical] 后台协程 engine 注入
后台协程不能直接用全局磁盘 `app.db.engine`（测试注入内存 engine，会成两个库 → 异步路径单测结构性不可达 + 生产跨连接读旧态）。**修**：db.py 加 `get_engine()` 模块级函数（测试可 monkeypatch）；`_run_l2_l3_job(job_id, *, session_factory=None)` 接受 session 工厂，默认 `lambda: Session(get_engine())`。test fixture **同时** override `get_session` + monkeypatch `app.db` engine 指向同一内存 StaticPool engine。

### C2 [critical] TestClient 不驱动 create_task
同步 httpx TestClient 不持续调度 create_task 后台协程 → job 状态机测试假死 queued。**修**：`_run_l2_l3_job` 设计成可直接 `await` 的纯协程；测试用 `asyncio.run(_run_l2_l3_job(job_id, session_factory=...))` 单独测后台逻辑（状态机/result_json/异常→failed），与派发解耦；派发端点单独测（start 后断言 job=queued + l1_json 已填 + 片段含轮询触发器，不依赖后台跑完）。文档明确：真实 create_task 事件循环交互只靠 NAS smoke 验。

### C3 [critical] L3 corpus 截断破坏对抗 → 见 §3(a)（独立 `_select_corpus`，不复用 enumerate_shared_pairs，零 tag peer 填充 + partial_coverage fail-closed）。

### H1 [high] create_task 异常静默 + 弱引用 GC
**修**：① `_run_l2_l3_job` 顶层 `try/except/finally`，任何异常→短事务 `job.status=failed, error=str(e)`，finally 确保非终态兜成 failed；② 模块级 `_RUNNING: set[asyncio.Task] = set()`，`create_task` 后 `_RUNNING.add(t); t.add_done_callback(_RUNNING.discard)`（asyncio 官方写法）；③ running 超时墙：轮询端点若 `status∈(queued,running)` 且 `now-updated_at > SOT_REVIEW_JOB_STALE_SECONDS`（默认 600）则渲染 failed + 停轮询（防 GC/OS kill/卡死永久转圈）。

### H2 [high] 防抖 check-then-act 竞态
SELECT-then-INSERT 在并发下双双查到无 running job 双双 create_task。**修**：临界区用进程内 `asyncio.Lock`（单 worker 有效，锁内 SELECT+INSERT 无 await，L1 同步跑完、create_task 在锁外），或对 active job 加 DB 唯一约束（`active_marker` 列）。文档绑定单 worker 前提（与 §7 多 worker 失效声明对齐）。

### H3 [high] reject 二次确认门覆盖不全 → 见 §5(4)（门对 existing+draft 两路径都生效）。

### H4 [high] 鉴权默认 off 公网烧钱 → **决策点见下「待用户拍板」**。无论结果，token-独立成本闸**必做**：全局并发 job 上限（env `SOT_REVIEW_MAX_CONCURRENT` 默认 2，模块级 `asyncio.Semaphore`）+ 每 session 防抖（§H2）+ 全局每日 job 硬上限（env `SOT_REVIEW_DAILY_CAP`）+ per-job token ceiling（env `SOT_REVIEW_JOB_TOKEN_CEILING` 默认 300K，累计超限中止剩余调用置 failed）。

### M3 [medium] 语料快照 + token 两路 → 见 §2（corpus_snapshot 冻结）+ §3（token 两路粗估）。

### M4 [medium] 滑窗丢否决记忆 → 见 §4（rejected_proposals 钉住注入）。

### Medium SQLite WAL/busy_timeout
**修**：busy_timeout 用 `sqlalchemy.event.listens_for(engine,"connect")` 每连接 `PRAGMA busy_timeout=`（不是 _build_engine 设一次）；WAL 用 connect listener 或建库后一次性设；**仅磁盘 DB 启用**（DB_PATH 是 `:memory:`/`sqlite://` 时跳过，env `SOT_SQLITE_WAL` 默认 on 内存强制 off）。`database is locked` 不复现标为 **NAS smoke-only**（StaticPool 单连接测不出文件锁），单测改断言 connect listener 确执行 PRAGMA。

### 重启孤儿 job
`main.py` startup 扫 `status IN('queued','running')` → 一律置 failed（error=「服务重启中断，请重审」），**绝不自动 requeue**（防重启风暴重复烧钱），attempt 记录。

---

## 7. 路由（全部网页层，新建 `app/web/review_ui.py` 便于独立可拆，main.py include_router）

**注册顺序铁律**：`/talents/review/...` 静态段必须前置于 `/talents/{talent_id}` 参数路由（否则被 `talent_id="review"` 捕获，pages.py:252 同款陷阱）。

- 会话：`POST /talents/{talent_id}/review/sessions`（existing）；`POST /talents/review/sessions`（draft，收 TalentValidateIn 同款字段，**须前置**）；`GET /talents/review/sessions/{sid}`（整页工作台，**须前置**）
- 异步：`POST /talents/review/sessions/{sid}/rerun`（防抖）；`GET /talents/review/sessions/{sid}/jobs/{job_id}/status`（轮询，终态片段不带触发器）
- 帮改：`POST .../chat`（反馈→帮改 LLM→append messages→对话流片段）；`POST .../draft/apply`（应用提案→草稿片段）；`POST .../draft`（手动编辑工作草稿回存，不落库）；`POST .../proposals/reject`（记否决，§M4）
- 落库：`POST .../commit`（显式落库，二次确认门，existing→talent_update/draft→talent_create，session.status=applied，可选追加 Comment）
- **保留**：现有 `POST /api/talents/{id}/review`（require_token，同步 247s）不动，作 CLI/脚本入口；异步化只发生在网页层，API 契约不破，44 测零回归。

**多 worker 隐性耦合标注（必做）**：`review_ui.py` 顶部注释 + usage guide 显式标「依赖单 worker uvicorn」（进程内 task + 模块级 Semaphore/Lock/_RUNNING 在多 worker 下全失效，须换 ARQ/Redis）。

---

## 8. 模板（最大复用现有 HTMX 模式，暗色中文 UI，零 node）

- 整页 `templates/review_session.html`（extends base.html）：顶部 job 历史 timeline（verdict 徽章演进）+ 左主区工作草稿编辑器（复用 talent_detail.html 字段表单 + tag-chip 复选，hx-post 到 draft 路由不落库）+ 内嵌 L1 即时校验（复用 `#validation-panel` + `/talents/validate` 作用于草稿）+ 中区当前 job 卡 + 右区对话面板。
- 片段 `partials/`：
  - `review_job_card.html`（轮询核心：queued/running→进度条 5 段灯 + l2_dropped/corpus_dropped 提示 + `hx-get .../status hx-trigger="every 2s" hx-target="this" hx-swap="outerHTML"`；done→复用 `validation_result.html` 的 verdict-{{verdict}}+vbadge 着色 + L2 flagged 列表 + L3 abuse_line/defense 折叠**不带 trigger 停轮询**；failed→错误卡+重审按钮停轮询）
  - `review_chat_stream.html`（仿 comment_stream.html 逐条 role/content 气泡 + outerHTML swap 到 `#review-chat-stream`；assistant 含提案则嵌 diff 卡）
  - `review_diff_card.html`（逐 change field + before（删除线红）/after（高亮绿）纯 CSS + 每项 checkbox 部分应用 + tags_add/remove 徽章 + 应用选中按钮 hx-post draft/apply + rationale）
  - `review_draft_form.html`（草稿编辑表单，apply 后局部刷新变更字段加 `.field-changed`）
  - `review_l2l3_result.html`（L2 flagged 对列表 + L3 abuse_line 逐回合 + defense.mechanism + confirmed_exploit/undetermined + corpus_dropped advisory）
- `talent_detail.html` 增量一处：validation-panel 旁加「深度审阅」按钮 → `hx-post /talents/{id}/review/sessions`（复用现有「结构校验」按钮的 hx-post + hx-vals 注入 id 模式）。
- 复用 CSS class：verdict-pass/revise/reject、vbadge、badge rarity-、status-、tag-chip、comment-list/comment、btn-*；新增少量 progress-stage 灯、diff-before/diff-after、field-changed、timeline-verdict。Alpine 仅管轮询期禁用按钮/diff 全选/commit 确认弹层（CDN 已含）。

---

## 9. 测试（复用 conftest 临时 DB + test_llm_review.py `_FakeClient(fn)`，本地全 mock + NAS smoke）

**基建坑（§6-C2）**：现有 `_FakeCompletions.create` 强读 `kw['response_format']['json_schema']['name']` → 新增对话/帮改轮**必须带 response_format**（帮改轮用结构化提案 schema，正好满足）；effort 透传断言需扩展 fake 让 create 捕获 kw（加 `self.last_kw=kw`）；后台协程测试走 `asyncio.run` + session_factory 注入（不走 TestClient）。

**新建 `tests/test_review_session.py` + `test_review_jobs.py`**（route 测走 TestClient + dependency_overrides + monkeypatch engine 内存 DB）：
① 会话生命周期（existing/draft，working_draft 正确快照，首 job queued+l1_json 已填）；② job 状态机+防抖（已有 running job 时 rerun 拒建第二个，断言行数不增；done 后允许）；③ 异步编排（asyncio.run + mock run_l2_l3，queued→running→done，result_json 形状，stage_failures 透传 fail-closed）；④ corpus 截断（>cap peers→corpus_dropped surface，**零 tag peer 在 cap 内不被丢、cap 外触发 partial_coverage**）；⑤ effort 透传（high→断言 _call_json 收 reasoning_effort=high）；⑥ 帮改提案（chat append user+assistant，提案 JSON 解析渲染 diff，非法 tag 拒绝 surface，非白名单 field 提案被拒）；⑦ 应用提案（draft/apply 改 working_draft，`session.get(Talent,id)` 字段逐一断言不变）；⑧ messages 滑窗（超 MAX_TURNS 保留 system+首条摘要+rejected_proposals 注入）；⑨ **落库纪律【最关键】**（commit 前 Talent==原值，commit 后==working_draft；existing→update/draft→create；**existing+draft 两路径 reject/confirmed_exploit 直接 commit 都被二次确认门拦**）；⑩ 重启恢复（预置 running 孤儿 job→触发 startup→变 failed+不重跑，_FakeClient 调用=0）；⑪ 轮询片段（status queued/running 含 every2s；done/failed 不含 every2s 停轮询，grep 模板输出）；⑫ review 不写 Talent 静态断言（grep review_ui.py 除 commit 端点外无 Talent session.add+commit）；⑬ 现有 44 测 fail-closed 不回归。

**NAS smoke（真实 gpt-5.4）**：① 网页开 existing 会话见切 ss_jianqie→L1 秒回 revise→轮询到 done→L3 confirmed_exploit=true（对照 Phase 2 同步版验异步未改裁决）；② 单张 high+corpus40+pair5 实测时长 + 多次轮询无 CF 524；③ 轮询不阻塞（start 后 <1s 返回 + 开第二 tab 验事件循环未阻塞）；④ 帮改一轮→提案 diff→应用→重审→verdict 变化 + **确认库内 Talent 未变**；⑤ commit 落库→Talent 更新+session applied；⑥ 重启遗弃→孤儿 job failed 不永久转圈不重烧；⑦ SQLite 并发（review 期间并发轮询+网页表单写另一天赋→无 database is locked，验 WAL+busy_timeout+短事务）。

---

## 10. 待用户拍板（实现前 1 个决策点）

**鉴权/成本姿态（§6-H4）**：sot.fyc-space.uk 经 CF Tunnel 部署 —— LLM 触发端点（每次 ~$0.1-0.5 真金）是否默认要求 token？取决于**部署访问面**：① 已在 CF Access/私有边界（仅用户可达）→ 网页端点免 token + 成本闸即可；② 公网可达 → LLM 触发端点须默认 require token（与现有 `/api/review` 一致）防爬虫刷量烧钱。token-独立成本闸（并发≤2 + 每日上限 + 防抖 + per-job token ceiling）**无论如何都做**。

**用户决策（2026-06-23）**：Q1=**默认 require token**（推荐，secure-by-default）；Q2=**站点已在 CF Access/私有边界**。→ `SOT_REVIEW_REQUIRE_TOKEN` 默认 True（纵深防御，perimeter 已挡），网页一次性「🔑 解锁」框（localStorage→hx-headers，仅注入 /review 路径），成本闸全做。

---

## 11. 实施完成（2026-06-24）

**全部实现 + 71 pytest 全绿**（44 现有零回归 + 27 新）+ 本地 live uvicorn smoke 通过（真实 create_task 异步路径跑通）+ **NAS 部署 + 真实 gpt-5.4 端到端 smoke 全绿（2026-06-24，SoT master `619f167`）**：
- 见切 ss_jianqie 网页发起异步深审 → 轮询 ~144s → **DONE 裁决=驳回 + L3 confirmed_exploit**（真实 gpt-5.4 经新异步端点复现 Phase 2 同步版结论，异步化未改裁决）；done 片段 0 个 hx-trigger（停轮询验证）。
- 帮改对话一轮（真实 gpt-5.4 medium）→ 结构化提案含 effect/rules 修改；commit H3 二次确认门（latest verdict=reject+confirmed_exploit）正确拦住未写库。
- **数据纪律 NAS 验证**：DB 查 `ss_jianqie.updated_by=None`（review 流程从未碰 Talent）+ `review_session status=applied 数=0`（无静默落库）。
- L1 回归 = revise（R3.13c+R6.6，与 Phase 1/2 一致）；app 启动建表 + recover_orphan_jobs 无崩溃；token 门全程生效（复用现有 API_TOKEN）。
- 部署：tar `app` 同步 NAS sot-codex/（备份 `app.backup-pre-phase3` = Phase1/2）+ `docker compose build app && up -d`，两容器（app-1 + tunnel-1）重建启动。

**变更文件**（SoT 仓 `D:/ShipOfTheseus/SoT-fyc-space`，无 git remote，commit master + tar 同步 NAS）：
- 后端：`app/web/review_ui.py`（新，核心编排+路由）、`app/validation/llm.py`（_select_corpus/effort/token估算/run_chat_turn/Budget）、`app/models.py`（ReviewSession+ReviewJob）、`app/db.py`（get_engine+PRAGMA listener）、`app/config.py`、`app/deps.py`、`app/validation/context.py`、`app/main.py`
- 前端：`templates/review_session.html` + `partials/review_*.html`(7) + base.html/talent_detail.html/talent_new.html/app.css
- 测试：`tests/test_review_async.py`（27 测）

**dual-verify 闭环（跨仓库手动：code-reviewer + Codex MCP 并行）** —— 双路一致确认 §6 七项批判修正（C1/C2/C3/H1/H2/H3/H4）**均真实正确实现**。双路 findings 全部修复：
- **[HIGH-A] job 终态非单调**（Codex）：`_run_l2_l3_job` 改 catch `CancelledError`(重抛)+`Exception`+`finally` 兜底；终态写 `_finalize` **单调**（仅当 status 仍 active 才落终态，不覆盖 stale 墙置的 failed）；进度写 `_update` 也跳过已终态。
- **[HIGH-B] commit 枚举强转 500**（双路）：`_enum_or_400` 包装 cast → 400；草稿表单枚举改 `<select>`（防手输非法）。
- **[HIGH-C] per-job token ceiling 未实装**（双路）：`llm.Budget` 类穿 `_call_json_messages`→`_call_json`/`_triage_pair`→run_l2/run_l3/run_l2_l3；从 `resp.usage` 累加；调用前超 ceiling 抛错中止；超限 job 置 failed + 持久化 `prompt_tokens`/`completion_tokens`；新 env `SOT_REVIEW_JOB_TOKEN_CEILING`（默认 300K）。
- **[MED-D] draft 审阅前端没接通**（Codex）：`start_draft_session` 改收 Form 字段（非 JSON body）+ `talent_new.html` 加「深度审阅」按钮。
- **[MED-E] diff 卡 `value="{{...|tojson}}"` 双引号属性破坏**（Claude）：改单引号（tojson 不转义 `"`）。
- **[MED-F] 令牌注入限 /review 路径**（Claude）：`htmx:configRequest` 按 `path.indexOf('/review')` 过滤。
- **[LOW-G] reject_proposal 加 token**；**[LOW-H] 对话 history 持久化前截断**（`_persist_history`）；**[LOW-I] `_build_chat_messages` 直接收 history 不 mutate ORM**（删 `_with_messages`）。

**NAS 部署 runbook**（照 handoff §部署 Runbook）：tar `app` 同步 + `docker compose build app && up -d` + 真实 smoke（见切 ss_jianqie 网页审阅→轮询到 done→L3 confirmed_exploit；帮改一轮；commit 落库验 Talent 变更；并发轮询验无 database is locked）。回滚 `app.backup-pre-validate`。生产 `.env` 沿用 `SOT_LLM_L3_EFFORT=high`/`SOT_LLM_L2_PAIR_CAP=5` + 新增可选 `SOT_REVIEW_*`（默认值即安全，无需配也行）。

**遗留 follow-up（非阻断，NAS smoke 验）**：① 后台协程 commit 跨 session 可见性（WAL，Open Question，NAS 并发 smoke ⑦ 验）；② LLM 输出入 HTML 属性的 XSS 边界（autoescape 默认开，NAS smoke 用含 `<script>`/`'"` 的 reply 实测）；③ `progress_detail` 字段定义但未写 L2 细粒度进度（功能缺口非 bug）；④ 派发端点 `async def` 内同步 DB 查询轻微占事件循环（poll 是 `def`/线程池不受影响，当前低流量可接受）。
