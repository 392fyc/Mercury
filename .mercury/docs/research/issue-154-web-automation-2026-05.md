---
issue: 154
title: "Web 自动化能力引入 — 无头浏览器 + Cookie/Session 认证复用 (Phase 1 设计 ADR)"
date: 2026-05-24
session: design lane
status: phase-1-design-deliverable
verdict: "推荐挂载微软官方 @playwright/mcp 作为 MCP server(Apache-2.0 permissive + 官方现成 + 全 Chromium 渲染 + Windows VERIFIED);adapter 在 adapters/playwright-mcp/ 仅做配置封装 wrapper(≤200 LOC)。拒绝 lightpanda(AGPL-3.0 强 copyleft 不过 license gate;其 Windows 原生 + 自带 MCP 现已支持,但 license 是干净 disqualifier)/ puppeteer(无官方 MCP,需自研)/ browser-use(agent-within-agent 重叠,留作未来 re-eval)。Cookie/Session 复用强制用独立 agent 专用 profile + isolated context storageState(scope 靠 context 隔离非内置域过滤),明确反对直读用户真实 Chrome/Edge profile 与 attach-到正在运行浏览器 的模式。"
relation: "设计层吸收 #62(Agent 高权限 test);#62 Close 时机绑定 Phase 2/3 可验证交付,design 阶段保持 OPEN;Phase 2 PoC 另开 follow-up Issue 实施,本 session 不实装"
research_protocol: "所有外部 SDK/库/服务能力对照官方文档 + registry 核实,核实日期 2026-05-24;Codex dual-verify 抓出前轮 research 在 lightpanda(版本/MCP/Windows)、--caps=storage、storageState 域范围、sessionStorage、puppeteer-core 版本上的错误,已逐条 web-re-verify 纠正;未核实项标 UNVERIFIED + 来源 URL"
---

# Issue #154 — Web 自动化能力引入: Phase 1 设计 ADR

> **本 doc 是设计交付物 / proposal,不实装、不改代码、不 dispatch 其它 agent。** Issue #154 保持 **OPEN**(设计阶段)。所有"如何实现"的描述均为 Phase 2 PoC 的推荐方向,**进入实装前需先 PoC 验证**(尤其 §6 列出的待 PoC 复核项:`--caps=storage` 实际启用、storageState 内容范围、Windows persistent profile 并发限制、CDP+storage-state open issue)。

## TL;DR / 结论

推荐 **挂载微软官方 `@playwright/mcp` 作为 Claude Code MCP server**,通过 `adapters/playwright-mcp/` 下一个**仅做配置封装**的 wrapper(≤200 LOC 硬约束可满足,见 §5)接入 Mercury。理由综合:

1. **License Apache-2.0(permissive,过 license gate)** —— 这是与 lightpanda 拉开差距的**决定性**一条。Mercury 只挂载 permissive license(MIT / Apache-2.0);lightpanda 是 AGPL-3.0 强 copyleft(已 `gh api` 确认),不过 gate。
2. **官方现成 MCP server**,维护方 Microsoft,当前版本 0.0.75(2026-05-07);无需自研主体逻辑 —— 符合 CLAUDE.md "No self-research / 能挂载就挂载"。浏览器实例的有状态长连接需求(跨多次 tool call 保持登录态)恰好匹配 MCP server 模型(研究 Q6 已结论)。
3. **Windows 11 支持 VERIFIED**,且基于真实 Chromium,**JS 渲染完整**(lightpanda 作为轻量浏览器 JS 渲染覆盖不完整)。
4. **Cookie/Session 复用机制可用(但有前提)** —— persistent profile + isolated `--storage-state` 注入 + `browser_storage_state` 导出/恢复 + `context.addCookies()` 逐条注入。**前提**:storage 相关工具是 **opt-in,需显式 `--caps=storage`**(VERIFIED);且 storageState 导出的是**当前 context 全量** cookies+localStorage(**不含 sessionStorage**,**无内置域过滤**)—— 单域 scope 靠 context 隔离实现(见 §4)。

**安全边界(一等约束)**:Cookie/Session 复用强制采用 **独立 agent 专用 profile + isolated context storageState** 的最小权限默认,**明确反对两类高危操作**:(a) 直读用户真实 Chrome/Edge profile(DPAPI 加密 + 完整用户 Web 身份);(b) 经 `--extension` / CDP attach 连接用户**正在运行**的浏览器(等同复用用户全部登录态/扩展,见 §4.2 红线)。学术研究已记录 browser agent 的会话权限过广类漏洞(§4)。

**Phase 2 PoC**(另开 follow-up Issue,本 session 不做):单站点、isolated mode + `--caps=storage` + storageState、人工提供认证态、不碰用户真实 profile、不用 attach 模式,验证 Windows 下 MCP server 起停 + storageState 注入是否 work,并实测 CDP+storage-state 的 open issue(#983)是否影响所选连接方式。

**拒绝/推迟**:lightpanda(**AGPL-3.0 不过 license gate** + JS 渲染覆盖不完整;注:其 Windows 原生 + 自带 MCP 现已支持,Windows/MCP 不再是拒绝理由)、puppeteer(无官方 MCP,挂载需自研 MCP 包装层,违反 No self-research)、browser-use(LLM 驱动的高层 agent,与 Mercury agent 层 agent-within-agent 重叠;留作未来"需 LLM 自主导航"时 re-eval `mcp_server_browser_use`)。

---

## 1. Context / 问题陈述

### 1.1 #154 scope

来源: <https://github.com/392fyc/Mercury/issues/154>

Mercury 需要 Web 自动化能力:无头浏览器驱动 + **Cookie/Session 认证态复用**(让 agent 能以已登录身份访问需要认证的站点,而非每次重新登录)。暴露形态为 Claude Code 可调用的 MCP tool 或 skill。

### 1.2 #154 吸收 #62 —— "Agent 高权限 test"

来源: <https://github.com/392fyc/Mercury/issues/62>

#62 设想给 agent 更高权限以执行需要真实浏览器 / 真实会话的测试类任务。#154 的"无头浏览器 + 认证复用"是 #62 诉求的**更具体、更可控的落地形态** —— #62 的"高权限"模糊诉求在 #154 被收敛为"受控的、最小权限的浏览器自动化能力"。

**关系结论**:#154 在设计层 **吸收并取代** #62。**#62 的 Close 时机绑定 Phase 2/3 可验证交付,不在本 design-only ADR 落地时关闭**(Argus iter-1 finding):本 ADR 仅给出设计,#62 的实际诉求(可执行的高权限测试能力)尚无实现与验收;过早关闭会丢失需求 tracking。在 Phase 2/3 实际 web 自动化能力上线 + 验收通过前,**#62 保持 OPEN**,作为"已被 #154 设计吸收、待实现验收"的 tracking。本 ADR 的安全边界设计(§4)正是对 #62 "高权限"诉求的安全化回应 —— 不是给 agent 完整用户身份,而是给一个受控的、专用 profile + 域级精控认证态。

### 1.3 暴露形态的前置结论(研究 Q6)

研究已结论:**无头浏览器控制必须用 MCP server,不能用 skill**。理由:浏览器实例需跨多次 tool call 存活(保持登录态 / 页面状态),这是有状态长连接场景,与 MCP 的 stdio/SSE 传输模型匹配;skill 是无状态 markdown 指令模板,每次调用经 prompt 传参,无法持有跨调用的浏览器实例。`@playwright/mcp` 已是现成官方 MCP server。

来源: <https://code.claude.com/docs/en/mcp> / <https://playwright.dev/docs/getting-started-mcp>

---

## 2. 候选方案对比

核实日期 2026-05-24。各项能力对照官方文档 + registry(来源见 §9)。**本表已纠正前轮 research 在 lightpanda 版本/MCP/Windows、puppeteer-core 版本上的错误(Codex dual-verify 抓出 + web-re-verify)。**

| 维度 | **@playwright/mcp (推荐)** | lightpanda | puppeteer | browser-use |
|---|---|---|---|---|
| **维护方** | Microsoft | lightpanda.io | Google | browser-use(社区) |
| **License** | **Apache-2.0**(permissive,**过 gate**) | **AGPL-3.0**(强 copyleft,**gate 不过**;`gh api` 确认 spdx_id=AGPL-3.0) | Apache-2.0(permissive) | MIT(permissive) |
| **Windows 11 支持** | **VERIFIED**(官方文档列三平台) | **原生支持**(官方 system req 列 Windows 10+ / Server 2016+,WSL 为可选项;**前轮"仅 WSL2"有误**) | **VERIFIED**(官方 system req 列 Windows x64,需 Node ≥22.12.0) | **UNVERIFIED**(PyPI 标 OS Independent,无明确 Windows CI) |
| **当前版本** | `@playwright/mcp` 0.0.75(2026-05-07)| **0.3.0(semver,2025-05-13;前轮"无 semver/nightly-only"有误)** | `puppeteer` 25.0.4 / `puppeteer-core` 25.0.4(registry latest)| `browser-use` 0.12.8(2026-05-23)|
| **现成 MCP server** | **是**(官方 npm `@playwright/mcp`)| **有原生 MCP server(自 v0.2.5 起,built into binary;前轮"无 MCP"有误)** | **无官方 MCP**;社区 `chrome-devtools-mcp`(成熟度 UNVERIFIED) | 有 `mcp_server_browser_use` PyPI 包(社区) |
| **Cookie/Session 复用** | 可用**但有前提**:storage 工具 **opt-in 需 `--caps=storage`**(VERIFIED);persistent profile / isolated `--storage-state` 注入 / `browser_storage_state` 导出(当前 context **全量** cookies+localStorage,**不含 sessionStorage**,**无内置域过滤**)/ `context.addCookies()` 逐条 | 经 CDP 理论可行(UNVERIFIED) | userDataDir 持久化 / `page.setCookie()` / `context.addCookies()` / CDP `Network.setCookies()`;限制:HTTP-only cookie 标准 API 读不到、userDataDir 单进程占用(puppeteer#6666) | UNVERIFIED(文档简略) |
| **JS 渲染覆盖** | 完整(基于 Playwright,真实 Chromium/Firefox/WebKit)| 轻量浏览器,**渲染覆盖不完整**(数百 Web API 未实现;确切覆盖度 UNVERIFIED,随版本提升)| 完整(真实 Chromium)| 完整(底层 Playwright)|
| **与 Mercury agent 层兼容性** | 好 —— Claude Code 直接驱动 MCP tool,Mercury agent 是唯一决策层 | 中 —— 有自带 MCP,但 AGPL 阻断挂载 | 中 —— 需自研 MCP 包装层 | **差 —— agent-within-agent**:browser-use 自带 LLM agent loop,与 Mercury agent 双层 LLM(增成本 + 不可预期)。挂 `mcp_server_browser_use` 可绕开,但仍多一层 |
| **安全隔离能力** | 强 —— isolated mode + per-agent profile + `--allowed-origins`/`--blocked-origins`(注:官方称非安全边界,见 §4) | 弱(早期项目) | 中(userDataDir 可隔离,但需自己管) | 弱 / UNVERIFIED |
| **成熟度** | 官方维护但 **pre-1.0(0.0.x),API 可能变** | 仍快速迭代(0.3.x)| 成熟(Google 长期维护) | 0.x,迭代快 |

---

## 3. 决策

### 推荐:挂载 `@playwright/mcp` 作为 MCP server

依据(逐条对照对比表):

1. **License 是决定性闸**:`@playwright/mcp`(Apache-2.0)与 puppeteer / browser-use(均 permissive)过 gate;**lightpanda(AGPL-3.0)不过**。Mercury 只挂载 permissive license。
2. **`@playwright/mcp` vs puppeteer 的决定性差异在"现成 MCP server"**:`@playwright/mcp` 是微软官方现成 MCP server,挂上即用;puppeteer 无官方 MCP,要把它做成 Mercury 可用的 MCP tool 必须自研一层 MCP 包装(或依赖成熟度 UNVERIFIED 的社区 `chrome-devtools-mcp`),违反 CLAUDE.md "No self-research / 能挂载就挂载"。
3. **Windows VERIFIED + 全 Chromium 渲染**:Windows 三平台支持,JS 渲染完整(覆盖"访问真实站点"诉求),lightpanda 作为轻量浏览器 JS 覆盖不完整。
4. **Cookie/Session 复用机制可用(需 `--caps=storage`)**:覆盖 #154 的认证复用需求,且 isolated mode + storageState 直接支撑 §4 的最小权限安全设计。**注意**:storage 工具是 opt-in 能力,需显式 `--caps=storage` 启用(见 §4.4 / §5.1)。

### 拒绝 / 推迟

- **lightpanda — 拒绝(license gate 不过)**:**核心理由是 AGPL-3.0 强 copyleft**(已 `gh api repos/lightpanda-io/browser` 确认 spdx_id=AGPL-3.0),Mercury 仅挂载 permissive(MIT/Apache-2.0)。**更正前轮 research**:lightpanda 现已有 **原生 Windows 支持**(官方 system req 列 Win10+,WSL 为可选)与 **自带 MCP server**(v0.2.5+,0.3.0 semver),故 Windows/MCP **不再**是拒绝理由 —— 但 AGPL 是干净 disqualifier,与其技术能力无关。次要顾虑:轻量浏览器 JS 渲染覆盖不完整(对"访问任意真实站点"是隐患,UNVERIFIED 确切覆盖度)。
- **puppeteer — 推迟(本案不选)**:Windows(Node ≥22.12.0)与 license(Apache-2.0)都过,但无官方 MCP 意味着挂载需自研包装层,违反 No self-research。若未来 `@playwright/mcp` 出现不可接受的阻塞(如 §6 的 CDP+storage-state issue 实测无法绕过),puppeteer + 成熟化后的 `chrome-devtools-mcp` 可作 fallback re-eval,**但需先核实社区 MCP 包的成熟度(当前 UNVERIFIED)**。
- **browser-use — 推迟(架构重叠)**:它是 **LLM 驱动的高层 agent 框架**(自带 agent loop),与 Mercury 的 agent 层 **agent-within-agent 双层 LLM**,增成本且行为不可预期;Mercury 的设计是"Claude Code agent 是唯一决策层,工具只做确定性动作"。**仅当**未来出现"需要浏览器内自主多步导航 / 自主决策"且 Mercury 主 agent 不便直接编排时,再 re-eval 挂载 `mcp_server_browser_use`(届时需核实其 Windows 支持 + cookie 复用,当前均 UNVERIFIED)。

---

## 4. Cookie/Session 复用设计 + 安全边界

> 本节是 #154 安全性的核心,也是对 #62 "高权限"诉求的**安全化收敛**。设计原则:**最小权限默认 + 默认拒绝直读用户真实 profile + 默认拒绝 attach 到用户正在运行的浏览器**。

### 4.1 复用机制谱系(从最安全到最危险)

> **重要纠正(Codex dual-verify)**:`browser_storage_state` 导出的是**当前 browser context 的全量 cookies + localStorage**(**不含 sessionStorage**),Playwright **没有内置"按域过滤"** 选项。因此"仅含目标域"不是 storageState 自带的特性,而是**通过 context 隔离实现**——在一个只访问/只认证目标站点的 isolated context 中导出,该 context 自然只沉淀目标站点的态。`browser_cookie_list` 支持域过滤,但 storageState 文件本身不支持。

| 机制 | 粒度 | 风险 | 推荐? |
|---|---|---|---|
| **isolated context 内 storageState 导出/导入** | 经 context 隔离限定(仅访问目标站点的 context → 仅含该站 cookie/localStorage;非内置域过滤) | 低(前提:context 确实只认证了目标站) | **推荐(默认)** |
| **逐条 cookie 注入**(`context.addCookies()`) | 精确,逐条可控 | 低 | 推荐(细粒度场景) |
| **persistent userDataDir(agent 专用 profile)** | 整个 profile(但是 agent 专用、空白起步,不含用户个人会话) | 中(profile 内沉淀的态会累积) | 有条件允许(见 4.3) |
| **attach 到用户正在运行的浏览器**(`--extension` / CDP attach) | 用户当前全部登录态 + 扩展 | **最高** | **明确反对(默认拒绝,见 4.2)** |
| **直读用户真实 Chrome/Edge profile** | 用户完整 Web 身份 | **最高** | **明确反对(默认拒绝,见 4.2)** |

### 4.2 安全红线:两类默认拒绝的高危操作

> **Codex dual-verify 抓出的 Critical**:仅禁"直读 on-disk profile"不够 —— Playwright 还提供 **attach 到用户正在运行浏览器** 的模式(`--extension` 浏览器扩展接管当前标签页 / CDP endpoint 连接已运行的 Chrome),这同样复用用户全部登录态、cookie、扩展,是 agent-only 边界的直接绕过。两者必须一并默认禁止。

**(a) 直读用户真实 Chrome/Edge profile —— 默认拒绝**

直读真实 profile(Windows 路径示意 `%LOCALAPPDATA%\Google\Chrome\User Data\Default`、Edge `%LOCALAPPDATA%\Microsoft\Edge\User Data\Default`;`%LOCALAPPDATA%` ≡ `%USERPROFILE%\AppData\Local`)技术上可行,但 **Mercury 明确禁止默认这样做**,理由:

1. **等于把完整用户 Web 身份交给 agent** —— agent 获得用户在所有站点的登录态(银行、邮箱、社交)。学术研究(arxiv 2512.07725 + Help Net Security 2025-12-22)记录 8 个 browser agent 共 30 个漏洞,关键风险正是 **会话权限过广**、跨站追踪暴露、profile 数据泄露、cookie banner 自动接受。
2. **运行时 profile 被锁** —— Chrome/Edge 运行时锁定 profile,无法并发读(技术上也不可靠)。
3. **DPAPI 加密** —— cookie SQLite 值经 Windows DPAPI 加密,需同用户上下文解密,绕过它本身就是把凭据暴露面扩大。

**(b) attach / 连接到任何预先存在或外部启动的浏览器/automation endpoint —— 默认拒绝(整类禁止)**

`@playwright/mcp` 暴露多种"接管已存在浏览器"的连接模式,**它们都会复用目标浏览器的全部登录态、cookie、扩展**,与 (a) 风险等价(甚至更高,因为是活会话)。**Mercury 默认禁止整类此种模式**,不逐一枚举地放行。已知属于此类的模式(非穷举):
- `--extension`(browser-extension 模式,接管用户当前标签页)
- CDP-attach(`--cdp-endpoint` 连接已运行的 Chrome)
- `--endpoint` / `remoteEndpoint`(连接已运行的 Playwright server / automation endpoint)

**策略表述(默认拒绝整类)**:除"MCP server 自起的独立浏览器(`--isolated` 或 agent 专用 `--user-data-dir`)"外,**任何连接到预先存在 / 外部启动的浏览器或 automation endpoint 的 flag 一律默认拒绝**;adapter 配置层(§5.2)对此做整类拦截,新出现的同类 flag 自动落入默认拒绝,无需逐个补丁。PoC / 实装只走 MCP server 自起独立浏览器路径。

来源: <https://arxiv.org/pdf/2512.07725> / <https://www.helpnetsecurity.com/2025/12/22/browser-agents-privacy-risks-study/> / <https://github.com/microsoft/playwright-mcp>

### 4.3 最小权限默认配置

推荐默认(Phase 2 PoC 即采用):

1. **独立隔离 profile —— 绝不共享真实用户 profile**。**纠正(Codex iter-2)**:`@playwright/mcp` 的 persistent profile 默认是 **workspace/project-scoped**(官方 Windows base 用 `%LOCALAPPDATA%`,确切子路径含 workspace hash,UNVERIFIED 待 PoC 复核),**不是 per-agent 专用**,也**不是**用户的 Chrome/Edge 真实 profile。要达到真正的 per-agent/per-session 隔离,Mercury **必须**显式 (i) 用 `--isolated`(每次全新、无持久态),或 (ii) 为每 agent/session 强制唯一的 `--user-data-dir`。默认 workspace-scoped profile 会被同 workspace 的多次调用共享,不能假设它天然 per-agent 隔离。同一 profile 同时只能被一个浏览器实例使用(并发约束,见 §6 V4)。
   - 来源: <https://playwright.dev/mcp/configuration/user-profile>
2. **优先 isolated mode + storageState(scope 靠 context 隔离)**:用 `--isolated` + `--storage-state=<repo 外私有路径>` 注入认证态;认证态在一个**只访问/只认证目标站点的 isolated context** 中导出(`browser_storage_state`),从而该 storageState 只含目标站点的态 —— **scope 来自 context 隔离,不是 storageState 的内置域过滤**(见 §4.1 纠正)。需要更硬的细粒度时,用 `context.addCookies()` 只注入目标域所需 cookie 子集。
3. **启用最小 `--caps` 集 + origin 限制**:storage 工具需 `--caps=storage`(opt-in,见 §4.4);**只开必需的 caps**(认证复用场景一般 `--caps=storage` 足够,不要开 `--caps=vision,pdf,...` 等无关能力)。配合 `--allowed-origins`(限制 agent 可访问的站点白名单)。**注意**:官方文档注明 `--allowed-origins`/`--blocked-origins` **不作为安全边界**(仅是导航约束),真正的隔离靠 isolated profile + 不注入无关域 cookie,origin 选项是 defense-in-depth 而非主防线。
4. **认证态由人工 / 受控流程提供,不让 agent 自行采集用户全量凭据**:PoC 阶段认证态文件由人工准备(例如手工登录目标站点后在 isolated context 导出 storageState),agent 只消费,不生产。
5. **认证态文件存放在 repo 外的 per-user 私有路径 + 轮转**:storageState 含活会话 cookie+localStorage,**绝不放进 repo 工作树**(误提交 / diff 泄露 / 跨 lane 复用风险)。默认放 per-user 私有目录(如 `%LOCALAPPDATA%\mercury\playwright-mcp\auth-state.json`,repo 外),并定义清理 / 轮转规则;adapter 配置层应**硬拒绝**指向 repo 内路径或用户真实浏览器 profile 路径的 storage-state 参数。
6. **sessionStorage 的局限**:storageState **不含 sessionStorage**(session 级,tab 关闭即清);依赖 sessionStorage 存 token 的认证方案无法用 storageState 持久化,PoC 须识别目标站属于哪类认证方案。
7. **cookie banner / 自动接受类行为需谨慎**:研究记录的风险之一是 agent 自动接受 cookie banner —— PoC 应观察并在 prompt/配置层约束此类自动同意行为。

### 4.4 已知限制(影响连接方式 / 能力开启)

- **storage 工具是 opt-in**(VERIFIED,microsoft/playwright-mcp README):`--caps` 取值含 `vision`/`pdf`/`devtools`/`config`/`network`/`storage`/`testing`;**cookie/localStorage/storageState 工具需显式 `--caps=storage` 才启用**,默认只有核心自动化工具(导航/点击/输入/快照)。**ADR 的认证复用流程依赖 `--caps=storage`,缺它则流程不存在。**
- 经 **CDP endpoint** 连接时,storage-state 配置支持是 **open issue**(microsoft/playwright-mcp#983)。若 Mercury 选择经 CDP 连接(而非让 MCP server 自起浏览器),storageState 注入可能不 work。**Phase 2 PoC 必须实测**:默认用 MCP server 自起浏览器(persistent / isolated)路径,**避免** CDP-connect 路径,直到 #983 确认解决。(注:CDP-attach 路径同时被 §4.2(b) 安全红线禁止。)

---

## 5. Mercury 集成方案

### 5.1 挂载方式 —— 运行时 npm 依赖,非 cherry-pick 单文件

`@playwright/mcp` 是作为 **MCP server 进程**被 Claude Code 拉起的运行时依赖(类比现有 `.mcp.json` 里的 `codex` 条目 `command: "codex", args: ["mcp-server"]`),**不是把上游源码 vendored 进 repo**。因此:

- 不触发"cherry-pick 单文件"协议(CLAUDE.md §Cherry-pick protocol 针对的是从上游某 commit lift 具体文件)。
- 它也不是 CLI scaffolding(Category A)或 shadcn registry add(Category B);它是 **运行时外部包挂载**,精神上与 `adapters/gpt-image-2/` 的 uvx-pinned-SHA 模式相近(运行时引用上游,不 vendoring 源码)。
- ⚠️ **但这是一种新的挂载模式,现有 adapter 挂载策略尚未授权**(Copilot finding):`adapters/README.md` §约束 + `.mercury/docs/DIRECTION.md` §四 当前只规定两种挂载方式 —— (1) 默认 git submodule 到 `modules/`;(2) runtime-only 经 `uvx --from git+<repo>@<SHA>` 的**有限例外**(首例 gpt-image-2,git+SHA pin)。**`npx @playwright/mcp@<version>` 是按 npm registry 版本解析 tarball,既非 submodule 也非 git+SHA,属第三种挂载模式**。因此 **Phase 2 实装前必须先**:更新 `adapters/README.md` §约束 + `DIRECTION.md` §四,把"npm-version-pinned MCP server"登记为一类受治理的 runtime-only 例外(明确版本 pin 规则、drift 监控、license gate),**或**为本次挂载记录一条经批准的 sanctioned exception。否则实装路径与现有挂载规则冲突。此为 Phase 2 的前置 gate,见 §6。
- **provenance 记录方式**:在 `.mercury/state/upstream-manifest.json` 登记一条(见 5.4),并在 `adapters/playwright-mcp/UPSTREAM.md` 记录版本/license/已知不兼容项,与现有 `adapters/gpt-image-2/UPSTREAM.md` 同结构。

`.mcp.json`(repo 根)新增条目示意(Phase 2 验证实际命令形式后定稿):

```jsonc
{
  "mcpServers": {
    "playwright": {
      "type": "stdio",
      "command": "npx",
      // --caps=storage 必需(cookie/localStorage/storageState 工具 opt-in);
      // storage-state 指向 repo 外 per-user 私有路径(示意,非 repo 内);
      // 默认不加 --extension / --cdp-endpoint(§4.2 安全红线)
      "args": ["@playwright/mcp@0.0.75", "--isolated", "--caps=storage", "--storage-state=%LOCALAPPDATA%/mercury/playwright-mcp/auth-state.json"]
    }
  }
}
```

> ⚠️ **上面的 `args` 是示意,不可直接复制到生产 `.mcp.json`。** Phase 2 PoC 须 web-verify `@playwright/mcp` 当时最新版本的实际 CLI flag 形式(`--isolated` / `--caps` / `--storage-state` 的确切拼写、`--storage-state` 是否支持 `%LOCALAPPDATA%` 这类环境变量展开、默认值)。版本号也以 PoC 时的最新核实为准。**禁止凭训练数据写死 flag。** `--caps=storage` 是已 VERIFIED 的必需项,但其它 flag 形式仍须复核。
>
> **落地前校验清单**(Phase 2 实装前逐项确认,任一不过不得写入生产配置):
> 1. ☐ 版本号 = 当时 `registry.npmjs.org/@playwright/mcp/latest` 的 `dist-tags.latest`(非 GitHub release 页、非 alpha prerelease)
> 2. ☐ 每个 flag 拼写对照官方 README / `--help` 实测(`--isolated` / `--caps=storage` / `--storage-state`)
> 3. ☐ `--storage-state` 路径在 repo 外 per-user 私有目录,且环境变量展开实测可用(否则用绝对展开后路径)
> 4. ☐ 无**任何** attach/连接预存或外部 endpoint 的 flag —— `--extension` / `--cdp-endpoint` / `--endpoint` / `remoteEndpoint` 及 §4.2(b) 整类红线下的同类(按整类拒绝,非仅枚举这几个)
> 5. ☐ `--caps` 仅含必需项(认证复用一般 `storage` 足够),不开无关能力
> 6. ☐ §6 验收门槛 V1–V7 全部实测通过
> 7. ☐ adapter 配置层**路径解析(env-var→绝对,不展开则启动期硬失败)+ 拒绝规则校验**(拒绝 repo 内 / 真实 profile 路径 / attach-类 flag)已生效(§5.2)
> 8. ☐ 用**非交互 npx**(`npx --yes` 或预装/pin),干净环境 smoke 不 hang(§6 V8)
> 9. ☐ 挂载策略已授权:`adapters/README.md` + `DIRECTION.md` 已登记 npm-version-pinned MCP 例外 / sanctioned exception(§6 step 0 / V9)

### 5.2 `adapters/playwright-mcp/` 职责边界 —— 仅配置封装,主体逻辑用上游

adapter 目录**只做配置封装**,**不实现任何浏览器控制逻辑**(浏览器控制全部由上游 `@playwright/mcp` 提供):

```
adapters/playwright-mcp/
  README.md      # 挂载说明: 挂了 @playwright/mcp、为什么、配了什么 flag(含 --caps=storage)
  UPSTREAM.md    # 上游版本/license/已知不兼容项(含 #983 CDP+storage-state、storage opt-in)
  config.*       # 安全默认 + 启动期硬 gate(强制交付,见下): 强制 --isolated + --caps 最小集 + storage-state 路径解析(env-var→绝对)+ 拒绝规则校验(拒绝 repo 内 / 真实 profile 路径,启动期硬失败)
```

- adapter 不持有浏览器实例、不写浏览器控制代码 —— 那些是上游 MCP server 的事。
- adapter 至多封装"安全默认配置"(强制 isolated 或 per-agent 唯一 `--user-data-dir`、限定 storageState 为 repo 外私有路径、**整类拦截**连接预存/外部 endpoint 的 flag(`--extension` / `--cdp-endpoint` / `--endpoint`/remoteEndpoint 及同类,§4.2(b))、拒绝指向真实用户 profile 的路径)。
- **adapter 强制路径解析 + 拒绝规则校验(硬 config gate,启动 MCP server 前执行)**(Argus iter-2 finding):`.mcp.json` 示例里的 `%LOCALAPPDATA%/...` 是文档可读写法,**不能假设上游或运行环境会展开它**——若 `@playwright/mcp` 不展开环境变量,`--storage-state` 会静默指向字面 `%LOCALAPPDATA%` 路径,导致认证态复用失败或隔离策略不生效。因此 adapter 在拉起 MCP server 前**必须**:(i) 把 storage-state / user-data-dir 路径**解析为绝对路径**(展开环境变量,失败则拒绝启动而非静默降级);(ii) 对解析后的绝对路径跑**拒绝规则校验**——拒绝 repo 工作树内路径(`git rev-parse --show-toplevel` 前缀)、拒绝真实浏览器 profile 路径(`...\Google\Chrome\User Data` / `...\Microsoft\Edge\User Data`)、拒绝 attach-类 flag(§4.2(b));任一校验不过则**拒绝启动并报错**,不把未校验配置透传给上游。这把"环境变量不展开 / 路径误用"从运行时静默失败前移为启动期硬失败。
  - **此 gate 是 Phase 2 强制交付,不是可选项**(Copilot finding):上面目录树里 `config.*` 标"可选"指的是**文件落点形式**可灵活(可以是独立 config 文件、也可以内联在 adapter 启动脚本里),但**路径解析 + 拒绝规则校验这一 gate 本身必须实现**。若 Phase 2 不以 `config.*` 文件承载,则必须显式指明由哪个组件(如 adapter 启动 wrapper)强制执行这些校验——安全 gate 不得被跳过。

### 5.3 adapter ≤200 LOC 硬约束 —— 满足

CLAUDE.md / DIRECTION.md §适配层规范(line 241 + line 386 硬约束条目)规定 **外部项目 adapter 在 `adapters/<vendor>/` 下 ≤200 行**。本方案满足:

- adapter 只是**配置封装**,主体浏览器逻辑在上游 npm 包里(不计入 Mercury LOC)。
- 若 `config.*` 接近 200 行,说明把本该交给上游 flag / `.mcp.json` 的东西塞进了 adapter —— **拆分预案**:把静态配置下推到 `.mcp.json` args 或上游配置文件,adapter 只保留"安全默认 + 路径校验"这类不可省的薄逻辑;校验逻辑若仍超限,提取为独立的可测函数并复用现有 Mercury 工具,而非在 adapter 内堆叠。
- **不存在"会超 200 行"的合理路径**:浏览器控制不在 adapter 里实现,adapter 没有理由变胖。这是选 `@playwright/mcp`(现成 MCP server)而非 puppeteer(需自研包装层,极可能超 200 行)的又一理由。

### 5.4 license / provenance 记录

`@playwright/mcp` 是 Apache-2.0(permissive),过 license gate。记录方式(类比 gpt-image-2 manifest 条目结构):

- **执行契约 = `.mcp.json` 里 pin 的版本**(`@playwright/mcp@<version>`)。这是 npm-by-version 依赖与 gpt-image-2(uvx git+SHA pin)的关键差异:**npm 包从 registry 按版本解析 tarball,不是 git checkout**,所以"运行时取哪份代码"由 pinned npm 版本号决定,而非 git SHA。
- **`.mercury/state/upstream-manifest.json`** 新增一条(字段类比 gpt-image-2):`path: "adapters/playwright-mcp/UPSTREAM.md"`、`scope: "project"`、`upstream_repo: "microsoft/playwright-mcp"`、`upstream_path: "package.json"`(跟踪版本契约,供 `scripts/upstream-drift-check.sh` 检测版本 bump)、`upstream_license: "Apache-2.0"`、`upstream_sha_at_import`(Phase 2 实装时填**该 npm 发布版本对应的 git tag commit**,经 `gh api repos/microsoft/playwright-mcp/commits/<sha>` 验证,**不得凭记忆填**)。**注意语义**:此处 `upstream_sha_at_import` 是 **审计元数据(把 pinned npm 版本映射回上游 git tag,供 drift / 供应链审计)**,**不是执行契约**(执行契约是上一条的 pinned npm 版本)。manifest 条目须在 `import_rationale` 里写清这一 npm-version↔git-tag 映射关系。
- **`adapters/playwright-mcp/UPSTREAM.md`** 记录 npm 版本(如 0.0.75)、对应 git tag/SHA、license(Apache-2.0)、已知不兼容项(#983 CDP+storage-state、storage opt-in、pre-1.0 API 可能变)、drift 策略。

---

## 6. 分阶段实施建议

### Phase 1 — 设计(本 doc)✅
本 ADR。Issue #154 保持 OPEN。

### Phase 2 — 最小 PoC(另开 follow-up Issue,本 session 不做)

**范围:单站点 + isolated mode + `--caps=storage` + 人工提供认证态 + 不碰用户真实 profile + 不用 attach 模式。**

具体建议:
0. **前置 gate(挂载策略授权,Copilot finding)**:在写任何 `.mcp.json` 前,先更新 `adapters/README.md` §约束 + `DIRECTION.md` §四,把"npm-version-pinned MCP server"登记为受治理的第三类 runtime-only 挂载例外(或记录 sanctioned exception),见 §5.1。否则实装与现有挂载策略冲突。
1. **挂载**:`.mcp.json` 加 `playwright` 条目(5.1 示意),Phase 2 先 web-verify `@playwright/mcp` 当时最新版本号 + CLI flag 实际形式(`--caps=storage` 已 VERIFIED 必需,其余 flag 复核)。**用非交互 npx**(`npx --yes @playwright/mcp@<pinned>`,或预装 / pin 到 lockfile)——裸 `npx` 在包未缓存时可能弹安装确认,会 hang 住 stdio MCP server 启动(Copilot finding);并从干净环境 smoke-test 启动。
2. **认证复用路径**:用 **isolated mode + `--caps=storage` + `--storage-state`**,认证态文件由人工准备(在只访问目标站的 isolated context 手工登录 → `browser_storage_state` 导出 → 确认仅含目标域、识别是否依赖 sessionStorage)。**不走 CDP-connect / attach 路径**(规避 #983 + §4.2 红线)。
3. **安全默认**:强制 isolated、`--caps` 最小集、storageState 放 repo 外 per-user 私有目录,**配置层硬拒绝**指向 repo 内路径、真实 profile 路径(`...\Google\Chrome\User Data` / `...\Microsoft\Edge\User Data`),以及 `--extension`/`--cdp-endpoint` attach flag。
4. **adapter**:建 `adapters/playwright-mcp/`(README + UPSTREAM.md + 可选薄 config),登记 manifest(执行契约 = pinned npm 版本;`upstream_sha_at_import` = 对应 git tag,审计元数据)+ 验证 upstream SHA。
5. **验收门槛(Phase 2 必须逐项验证,见下)**。

PoC 规避的风险:单站点 + isolated + 人工认证态 + 不碰真实 profile + 不用 attach,把会话权限过广风险降到最低;不走 CDP 路径规避 #983。

### Phase 2 验收门槛(UNVERIFIED / 待复核项固化,进入 Phase 3 前必须逐项 web-verify / 实测)

- **V1**:`@playwright/mcp` 在 Windows 11 下作为 MCP server 经 Claude Code 起停成功,能 navigate + screenshot(基础能力 smoke)。
- **V2**:`--caps=storage` + isolated mode + `--storage-state` 注入认证态在 Windows 下成功复用登录态(认证复用核心验证)。
- **V3**:实测 #983(CDP+storage-state)是否影响所选路径 —— PoC 默认避开 CDP,但需确认"MCP server 自起浏览器"路径下 storageState 完全 work。
- **V4**:persistent profile 的**单实例占用**约束(同一 profile 同时只能一个浏览器实例)在 Mercury 多 lane / 多 session 并发场景下的影响 —— 若多 session 需并发用浏览器,须用 isolated mode(每 session 独立态)而非共享 persistent profile。
- **V5**:核实当时 `@playwright/mcp` 最新版本号 + CLI flag 形式(pre-1.0,API 可能已变);确认 persistent profile 的 Windows 确切子路径(本 ADR 仅 VERIFIED base 为 `%LOCALAPPDATA%`,子路径 UNVERIFIED)。
- **V6**:确认在 isolated context 导出的 storageState 确实只含目标域(安全断言)、并识别目标站是否依赖 sessionStorage(storageState 不覆盖)。
- **V7**:确认 `--allowed-origins`/`--blocked-origins` 实际行为(官方称非安全边界,验证其约束范围,确保不被误当主防线)。
- **V8**:从**干净环境**(包未预缓存)非交互启动 smoke —— 确认 `npx --yes`(或预装/pin)不弹交互安装确认、不 hang stdio MCP server 启动(Copilot finding)。
- **V9**:确认 §6 step 0 的挂载策略授权已落地(`adapters/README.md` + `DIRECTION.md` 已登记 npm-version-pinned 例外 / sanctioned exception),否则不得进入 Phase 3。

### Phase 3 — 扩展(PoC 验证后)

- 多站点 / 多认证态管理(每站点独立 storageState 文件,均存 repo 外)。
- 评估 `browser_run_code_unsafe`(任意 Playwright 脚本,RCE-等价)是否开放 —— **默认关闭**,仅在受控、人工审过的脚本场景按需开,因其等于任意代码执行面。
- 若 `@playwright/mcp` 升到 1.0 / API 稳定,移除 pre-1.0 风险标注。
- 评估 tracing / 网络 mock 等高级能力按 Mercury 实际需求逐步开放(对应 `--caps` 按需增量,默认不开)。

---

## 7. #62 关系(设计层吸收,关闭时机绑定 Phase 2/3 验收)

- #62("Agent 高权限 test")的诉求 = 让 agent 能执行需要真实浏览器 / 真实会话的任务。
- #154 以**更安全、更具体**的形态吸收它:不是"给 agent 高权限",而是"给 agent 一个受控的、最小权限的浏览器自动化能力(独立专用 profile + 域级精控认证态 + 默认拒绝 attach/直读真实 profile)"。
- **#62 的 Close 时机绑定 Phase 2/3 可验证交付,不在本 design-only ADR 落地时关闭**(Argus iter-1 finding):design 阶段 #62 保持 OPEN(作为"已被 #154 设计吸收、待实现验收"tracking),待 Phase 2/3 实际 web 自动化能力上线 + 验收通过后再 close,理由注明"诉求已被 #154 实现并验收"。本 ADR §4 的安全边界正是对 #62 "高权限"模糊诉求的安全化回应。
- 来源: <https://github.com/392fyc/Mercury/issues/62>

---

## 8. 模块化 / DIRECTION.md 对齐

| DIRECTION.md / CLAUDE.md 约束 | #154 设计如何满足 |
|---|---|
| **独立可拆卸(modular design)** | 整个 #154 = `.mcp.json` 一个 `playwright` 条目 + `adapters/playwright-mcp/`(README + UPSTREAM + 可选薄 config)。删条目即停;删 adapter 无残留。浏览器主体逻辑全在上游 npm 包,Mercury 不持有。 |
| **adapter ≤200 LOC(硬约束)** | adapter 只做配置封装,主体逻辑用上游 → 天然远低于 200 行(见 §5.3,含拆分预案)。选 `@playwright/mcp`(现成 MCP server)而非 puppeteer(需自研包装,极可能超限)正是为满足此约束。 |
| **No self-research / 优先挂载** | `@playwright/mcp` 是微软官方现成 MCP server,直接挂载,不自研浏览器控制逻辑。puppeteer 被否的核心理由就是它需要自研 MCP 包装层。 |
| **暴露为 MCP tool / skill** | 研究 Q6 已结论用 MCP server(有状态长连接);`@playwright/mcp` 即现成 MCP server。 |
| **不自建 orchestrator** | Mercury 用 Claude Code 原生驱动 MCP tool,Mercury agent 是唯一决策层。browser-use 被否的核心理由就是它自带 agent loop(agent-within-agent)。 |
| **MANDATORY RESEARCH PROTOCOL** | 本 ADR 外部能力(版本号 / license / 平台支持 / cookie 机制 / `--caps`)均对照官方文档 + registry 核实并附 URL;经 Codex dual-verify 抓出前轮错误后已 web-re-verify 纠正;未核实项标 UNVERIFIED(见 §6 验收门槛 + §9)。Phase 2 实装前任何 CLI flag / API 形式需再次 web-verify。 |
| **upstream-manifest / cherry-pick 协议** | `@playwright/mcp` 是运行时 npm 依赖(非 vendored 单文件),按 §5.4 登记 manifest + UPSTREAM.md;执行契约 = pinned npm 版本,`upstream_sha_at_import` = 对应 git tag(审计元数据),SHA 经 `gh api` 验证,不凭记忆填。 |
| **Issue-first / PR to develop** | #154 是 issue-first;Phase 2 实装代码经正常 PR 入 develop。 |

---

## 9. 风险 / UNVERIFIED 项

1. **storage 工具 opt-in(`--caps=storage`)** —— 认证复用核心流程依赖它,缺则不可用。**缓解**:`.mcp.json` 必须含 `--caps=storage`(§5.1),Phase 2 V2 验证(已 VERIFIED 必需性,验证实际生效)。
2. **storageState 范围 = 全量 context cookies+localStorage,无内置域过滤,不含 sessionStorage** —— 单域 scope 靠 context 隔离实现;sessionStorage-依赖的认证方案 storageState 不覆盖。**缓解**:isolated context 只认证目标站(§4.3),Phase 2 V6 验证 + 识别认证方案类型。
3. **CDP + storage-state open issue(microsoft/playwright-mcp#983)** —— 经 CDP endpoint 连接时 storageState 配置支持未定。**缓解**:PoC 默认走 MCP server 自起浏览器路径,避开 CDP-connect(§4.4 / §6 V3);CDP-attach 同被 §4.2 安全红线禁止。
4. **`@playwright/mcp` pre-1.0(0.0.75)** —— 版本号偏早,CLI flag / API 可能在小版本间变。**缓解**:Phase 2 实装前 web-verify 当时最新版本 + flag 形式;manifest pin 版本 + 映射 git tag;drift-check 监控(§6 V5)。
5. **persistent profile 单实例占用 + Windows 确切子路径 UNVERIFIED** —— 同一 profile 同时只能一个浏览器实例;Mercury 多 lane / 多 session 并发下需用 isolated mode(每 session 独立),否则冲突(§6 V4)。persistent profile 的 Windows base 为 `%LOCALAPPDATA%`(VERIFIED 为 base),确切子路径官方文档本次未能定位 → UNVERIFIED,Phase 2 V5 复核。
6. **`--allowed-origins`/`--blocked-origins` 非安全边界** —— 官方文档明确这两个 origin 选项不作安全边界(仅导航约束)。**缓解**:真正隔离靠 isolated profile + 不注入无关域 cookie;origin 选项作 defense-in-depth,不当主防线(§4.3,§6 V7)。
7. **browser-use Windows 支持 + cookie 复用 = UNVERIFIED** —— 若未来 re-eval `mcp_server_browser_use`,这两项须先核实(§3 推迟理由)。
8. **puppeteer 社区 MCP(`chrome-devtools-mcp`)成熟度 = UNVERIFIED** —— 若 puppeteer 转为 fallback,须先核实其 MCP 包成熟度(§3 推迟理由)。
9. **lightpanda JS 渲染完整度 = UNVERIFIED** —— 已知数百 Web API 未实现(随版本提升),确切覆盖度未核实(对已拒绝方案,非阻塞)。lightpanda license = AGPL-3.0(VERIFIED via `gh api`)是拒绝主因。
10. **Anthropic API 文档 / Claude Code SDK 无关** —— 本 ADR 不引入 Anthropic SDK 调用;浏览器决策由 Claude Code 主 agent 经 MCP tool 完成,无额外 LLM 层。

> **核实状态说明(纠正前轮"all verified"过强表述)**:版本号 / license / 平台支持 / `--caps` / storageState 范围 / sessionStorage / 工具名(`browser_run_code_unsafe`)均已对照官方文档 + registry 核实(2026-05-24)。**仍标 UNVERIFIED 待 Phase 2 PoC 复核的**:persistent profile Windows 确切子路径、`--storage-state` 是否支持环境变量展开、pre-1.0 CLI flag 实际形式、`--allowed-origins` 实际约束范围。任一阻塞性 UNVERIFIED 项未核实不得进入 Phase 3 全量。

---

## 10. 给主 agent 的交接要点

- **#154 保持 OPEN**(设计交付物,非实现)。Phase 2 PoC **另开 follow-up Issue**,本 session 不实装。
- **#62 不在本 ADR 落地时关闭**(Argus iter-1 finding):closure 绑定 Phase 2/3 可验证交付;design 阶段 #62 保持 OPEN,待实现 + 验收后再 close(§7)。
- **推荐方案**:挂载 `@playwright/mcp`(Apache-2.0)作 MCP server,adapter `adapters/playwright-mcp/` 仅做配置封装(≤200 LOC)。
- **安全红线(两条)**:默认拒绝 (a) 直读用户真实 Chrome/Edge profile;(b) **整类** attach/连接到任何预先存在或外部启动的浏览器/automation endpoint —— 含 `--extension`、CDP-attach(`--cdp-endpoint`)、`--endpoint`/`remoteEndpoint` 及同类(完整定义见 §4.2(b),勿在 handoff/Phase 2 issue 里窄化为仅 `--extension`/CDP)。强制独立 agent 专用 profile + isolated context storageState(§4)。
- **关键前提**:storage 工具需 `--caps=storage`(opt-in);storageState = 全量 context 态(无内置域过滤,不含 sessionStorage),单域 scope 靠 context 隔离;认证态文件放 repo 外 per-user 私有路径。
- **Phase 2 前置**:web-verify 当时版本号 + CLI flag;实测 #983 是否影响所选路径;实测 Windows 下 `--caps=storage` + isolated + storageState 复用;确认多 session 并发用 isolated 而非共享 persistent profile(§6 验收门槛)。
- 路径写法在本 repo 文档中统一用环境变量形式;Windows persistent profile base 的 canonical 形式是 `%LOCALAPPDATA%`(≡ `%USERPROFILE%\AppData\Local`),不硬编码本机绝对路径。

---

## 参考来源

核实日期均为 2026-05-24。

**候选方案**:
- [Q1] lightpanda(Zig 无头浏览器): **0.3.0 semver(2025-05-13)** / **AGPL-3.0(`gh api repos/lightpanda-io/browser` spdx_id 确认)** / **原生 MCP server(自 v0.2.5)** / **Windows 原生支持(system req 列 Win10+/Server2016+,WSL 可选)** / JS 渲染覆盖不完整: <https://github.com/lightpanda-io/browser> / <https://github.com/lightpanda-io/browser/releases> / <https://lightpanda.io/docs/open-source/systems-requirements> / <https://lightpanda.io/docs/open-source/guides/mcp-server>
- [Q2] `@playwright/mcp`(Microsoft,Apache-2.0,三平台含 Windows): npm registry `dist-tags.latest = 0.0.75`(发布 2026-05-07,经 `registry.npmjs.org/@playwright/mcp/latest` 确认)。**注:npm registry 的 `latest` tag 是执行 source of truth(经 npx 从 npm 安装),GitHub release 页 tag 可能滞后于 npm publish**(Codex iter-2 读到的 GitHub release v0.0.74 即此滞后;registry latest 实为 0.0.75)。registry 另有 `0.0.75-alpha-*` prerelease(`next` tag),非 stable latest: <https://www.npmjs.com/package/@playwright/mcp> / <https://registry.npmjs.org/@playwright/mcp/latest> / <https://github.com/microsoft/playwright-mcp>
- [Q2-caps] `--caps`(含 storage opt-in)/ `--allowed-origins` / `--blocked-origins`(非安全边界)/ 工具名 `browser_run_code_unsafe`: <https://github.com/microsoft/playwright-mcp> / <https://playwright.dev/mcp/configuration/options>
- [Q2-storage] storageState 工具(`browser_storage_state` = 当前 context 全量 cookies+localStorage,不含 sessionStorage,无内置域过滤;`browser_cookie_list` 支持域过滤): <https://playwright.dev/mcp/tools/storage> / <https://playwright.dev/docs/api/class-browsercontext#browser-context-storage-state>
- [Q2-983] CDP endpoint + storage-state open issue: <https://github.com/microsoft/playwright-mcp/issues/983>
- [Q3] puppeteer(`puppeteer` 25.0.4 / `puppeteer-core` 25.0.4 / registry latest,Google,Apache-2.0,Windows x64 需 Node ≥22.12.0,无官方 MCP): <https://www.npmjs.com/package/puppeteer> / <https://registry.npmjs.org/puppeteer/latest> / <https://pptr.dev/guides/system-requirements>
- [Q4] browser-use(MIT,Python ≥3.11,LLM 驱动高层 agent,底层 Playwright): PyPI `info.version = 0.12.8`(经 `pypi.org/pypi/browser-use/json` 确认;Codex iter-2 读到的 0.12.7 已被 PyPI JSON API publish 字段推翻): <https://pypi.org/pypi/browser-use/json> / <https://pypi.org/project/browser-use/> / <https://github.com/browser-use/browser-use> / <https://pypi.org/project/mcp_server_browser_use/>

**安全边界(Q5)**:
- [Q5-a] browser agent 隐私风险研究(8 agent / 30 漏洞,会话权限过广 / 跨站追踪 / profile 泄露 / cookie banner 自动接受): <https://arxiv.org/pdf/2512.07725>
- [Q5-b] Help Net Security 报道(2025-12-22): <https://www.helpnetsecurity.com/2025/12/22/browser-agents-privacy-risks-study/>

**MCP vs skill(Q6)**:
- [Q6-a] Claude Code MCP docs: <https://code.claude.com/docs/en/mcp>
- [Q6-b] Playwright MCP getting started: <https://playwright.dev/docs/getting-started-mcp>

**Mercury-内部引用**(非外部 vendor 源):
- Issue #154 body: <https://github.com/392fyc/Mercury/issues/154>
- Issue #62 body(待 Close): <https://github.com/392fyc/Mercury/issues/62>
- DIRECTION.md 适配层规范(adapter ≤200 LOC,line 241 + 硬约束 line 386): `.mercury/docs/DIRECTION.md` §"适配层规范"
- 现有运行时挂载先例(uvx-pinned-SHA,manifest 条目结构参考): `adapters/gpt-image-2/UPSTREAM.md` + `.mercury/state/upstream-manifest.json`(`path: adapters/gpt-image-2/invoke.py`)
- CLAUDE.md: MANDATORY RESEARCH PROTOCOL / modular design / Cherry-pick protocol / adapter ≤200 LOC 硬约束
- #157 ADR(本 doc 沿用其格式): `.mercury/docs/research/issue-157-external-info-agent-2026-05.md`

---

## 附录:dual-verify 审计纠正记录(S134)

本 ADR 经 Codex dual-verify(Critical:1 High:4 Medium:4 Low:1 → NEEDS-CHANGES)后 web-re-verify 纠正。逐条:

| Codex 发现 | 仲裁(web-re-verify 2026-05-24) | 处置 |
|---|---|---|
| Critical: 红线未禁 attach 模式 | 成立 —— `--extension`/CDP-attach 复用用户活会话 | §4.2(b) 新增禁止 attach 模式红线 |
| High: lightpanda row stale(版本/MCP/Windows)| 成立 —— 0.3.0 semver / 原生 MCP(v0.2.5+)/ Windows 原生 | §2 表 + §3 拒绝理由重写;拒绝主因转为 AGPL |
| High: 缺 `--caps=storage` | 成立 —— storage 工具 opt-in | §4.4 / §5.1 / §9-1 补 |
| High: "域级 storageState" 夸大 | 成立 —— 全量 context 态,无内置域过滤 | §4.1 纠正 + §4.3-2 重述(scope 靠 context 隔离)|
| High: 包版本(browser-use/puppeteer/Node)| **部分不成立** —— browser-use 0.12.8、puppeteer 25.0.4、Node≥22.12.0 经 registry 确认前轮正确;**但 puppeteer-core 实为 25.0.4(前轮误写 24.4.1)** | §2 表 puppeteer-core 改 25.0.4 |
| Medium: "复用机制完整 VERIFIED" 夸大(sessionStorage)| 成立 | §4.3-6 + §9-2 补 sessionStorage 局限 |
| Medium: provenance npm-version vs git-SHA | 成立 | §5.4 区分执行契约(pinned npm 版本)vs 审计元数据(git tag)|
| Medium: auth-state.json 在 repo 内 | 成立(Claude 侧同发现)| §4.3-5 移至 repo 外 per-user 私有路径 + 轮转 |
| Medium: 缺最小 `--caps` + origin 限制 | 成立 | §4.3-3 补(注:origin 非安全边界)|
| Medium: "all verified" 过强 + 工具名 | 成立 —— 工具名 `browser_run_code_unsafe`(ADR 已用对)| §9 末"核实状态说明"软化 |
| Low: `%LOCALAPPDATA%` canonical | 成立 | §4.2/§4.3/§10 用 `%LOCALAPPDATA%` 为 canonical |

### iter-2 纠正(Codex re-audit Critical:0 High:1 Medium:2)

| Codex iter-2 发现 | 仲裁(web-re-verify 2026-05-24) | 处置 |
|---|---|---|
| High §4.3-1: persistent profile 默认 workspace-scoped 非 per-agent | 成立 —— 默认 profile 含 workspace hash,workspace-scoped | §4.3-1 重述:per-agent 隔离须强制 `--isolated` 或唯一 `--user-data-dir` |
| Medium §4.2(b): attach 红线漏 `--endpoint`/remoteEndpoint | 成立 | §4.2(b) 泛化为**整类拦截**(任何连接预存/外部 endpoint 的 flag 默认拒绝),不逐一枚举 |
| Medium: 版本事实(0.0.75 / 0.12.8)| **不成立** —— 经**权威 registry endpoint** 确认:npm `dist-tags.latest=0.0.75`(非 GitHub release 页的 v0.0.74,后者滞后)、PyPI `info.version=0.12.8`(非 0.12.7)。Codex 读的是滞后的 GitHub release / 过时 PyPI 页 | §9 [Q2]/[Q4] 改引 registry endpoint + 注明 registry latest 为 source of truth、GitHub release tag 滞后;date 纠正 05-09→05-07 |

> iter-2 后剩余分歧仅版本号,已用 `registry.npmjs.org/@playwright/mcp/latest`(=0.0.75)+ `pypi.org/pypi/browser-use/json`(=0.12.8)权威 publish 字段定案 —— registry publish 字段优先于 GitHub release 页(后者可滞后于 npm/PyPI publish)。
