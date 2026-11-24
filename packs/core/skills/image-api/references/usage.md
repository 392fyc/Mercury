# 配置、费用和命令

脚本使用 Python 3.10+ 标准库和 Git，无须安装 OpenAI SDK。适用于普通非 bare Git 仓库；`project_root` 必须是主 checkout，配置、任务卡、状态目录与账本必须位于该根内并被 Git 忽略且未跟踪。任务卡和参考图使用 UTF-8 路径；卡内相对路径按 `project_root` 解析。输出始终留在私有状态目录，不覆盖正式素材。

## 私有配置

以下路径均为占位符。实际配置写在项目忽略目录，不能原样运行，也不能提交带本机路径的配置。

```json
{
  "project_root": "/absolute/project-root",
  "state_dir": ".tmp/image-api/task-001",
  "key_file": "/absolute/approved-credentials.env",
  "budget_usd": "2.00",
  "reserve_usd": "1.00",
  "max_calls": 2
}
```

`key_file` 读取其中唯一的 `OPENAI_API_KEY=` 行（可带引号）；也可删去该字段，改为 `"key_env": "APPROVED_IMAGE_API_KEY"`，只读取明确命名的进程环境变量。两者只能选一个，不接受配置内的明文 key。凭据只在执行请求时读取；离线检查不验证凭据是否存在、账号余额或模型访问权。

`budget_usd` 是该账本的累计预算，`reserve_usd` 是每次请求的保守预留，`max_calls` 是累计尝试次数。预留须大于零且不高于预算；失败或未知收费的尝试也占用次数。所有 worktree 应引用这同一份配置，不能复制账本后分别花费。一个额度池若还供其他项目或工具使用，必须另外分配总预算，不能把局部账本当作账号余额。

SQLite 的 `BEGIN IMMEDIATE` 将检查与预留放进同一事务，避免并行调用重复花掉同一余额。请求前落下 `pending`；异常、进程中断和不完整用量保持预留。只有完整、非负整数的文本输入、图像输入和输出 token 用量才用于结算。费用超过预留时返回 `budget-exceeded`，保留图片与实际费用并拒绝后续请求；不能仅删除该记录恢复调用。

预留不能约束服务端最终账单。先选有限的图片尺寸、质量、次数和保守预留；无完整用量时核对平台账单后再决定是否调整后续预算。本工具没有自动清账、自动重试或自动充值功能。

## 任务卡

文生图卡：

```json
{
  "mode": "generate",
  "model": "gpt-image-2.6-flare",
  "prompt": "Create one original dark-fantasy silver leaf emblem. Centered isolated object, muted antique metal, transparent background. No letters, numbers, border or interface.",
  "size": "1024x1024",
  "quality": "low",
  "background": "transparent"
}
```

参考编辑卡：

```json
{
  "mode": "edit",
  "model": "gpt-image-2.6-sunburst",
  "prompt": "Image 1 is the edit target. Preserve its silhouette, proportions and material detail. Change only the small central gemstone from red to blue. Keep the original background transparency. No text or extra objects.",
  "references": ["assets/approved-emblem.png"],
  "size": "1024x1024",
  "quality": "low",
  "background": "transparent"
}
```

支持 `gpt-image-2.6-flare`、`gpt-image-2.6-sunburst`（官方暂无日期快照），默认使用 `gpt-image-2.6-flare`；为复现旧素材，仍接受 2.5 的 `flare`、`sunburst` 及其 `-2026-09-08` 快照。只接受 `1024x1024`、`1536x1024`、`1024x1536` 与 `low`、`medium`、`high`，每次 `n=1`，PNG 输出。编辑接受 1—16 张本地 PNG/JPEG/WebP，每张不超过 10 MiB。第一张为编辑目标时，在提示词中明确说明；其余图片逐一说明是身份、材质还是构图参考。此小工具不提供蒙版、远程图片 URL 或批量接口。

输出回执记录请求参数、提示词 SHA-256、实际发送的各参考图字节数与 SHA-256、生成图字节数/尺寸/SHA-256，以及用量和费用。它不含 key、提示词正文和本机路径；原任务卡仍须保留，以便复现输入。`actual_usd=null` 表示不能确认费用，`accounted_usd` 是账本当前占用，不得写作实际账单。错误只报告类型，不输出服务器正文或 traceback。

## 当前接口与价格依据

2026-10-30 核对的 [生成接口](https://developers.openai.com/api/reference/resources/images/methods/generate) 和 [编辑接口](https://developers.openai.com/api/reference/resources/images/methods/edit) 支持 JSON 请求；编辑的 `images` 使用本地原字节编码的 data URL。请求固定发送到 OpenAI 官方 Images 端点，拒绝重定向，不将 Authorization 转发给新地址。

2026-11-05 核对的 2.6 [Flare 模型页](https://developers.openai.com/api/docs/models/gpt-image-2.6-flare) 与 [Sunburst 模型页](https://developers.openai.com/api/docs/models/gpt-image-2.6-sunburst) 费率相同，也与 2.5 相同：每百万文本输入 token 5 美元、图像输入 token 8 美元、输出图像 token 30 美元。脚本按这三个费率计算完整用量，不采用缓存折扣。模型、端点或计费方式变化后先核对文档再改费率，不能任意传模型名继续沿用旧价格。

费率相同不等于单张同价：2026-11-05 实测，`gpt-image-2.6-flare` 的 `low` 档 1024x1024 生成一张用了 824 个输出 token，约 0.025 美元；旧任务卡若未写 `model`，现在也会改用 2.6，`reserve_usd` 请按实测设置。工具不发送 `complexity`，模型按官方默认 `high` 处理；token 增加是否由它引起尚未核实。

官方提示，人物跨图一致性、精确构图与文字仍可能出错；蒙版也是指导而非逐像素保证。详见[图像生成指南](https://developers.openai.com/api/docs/guides/image-generation)。这些限制应由项目实际验收覆盖，不以模型名称或高质量档代替检查。

## 视觉验收

`scripts/image_check.py` 同样只用标准库，`project_root` 同样必须是主 checkout。它使用一份单独的私有配置，不与生图配置混用（生图配置拒绝未知字段）：

```json
{
  "project_root": "/absolute/project-root",
  "state_dir": ".tmp/image-api/task-001",
  "anthropic_key_file": "/absolute/approved-credentials.env",
  "model": "claude-opus-5-5",
  "budget_usd": "1.00",
  "reserve_usd": "0.30",
  "max_calls": 5
}
```

`anthropic_key_file` 读取其中唯一的 `ANTHROPIC_API_KEY=` 行，或改用 `"anthropic_key_env"` 指名环境变量，二者选一。账本是状态目录里的 `check-usage.sqlite3`，与生图账本分开，规则相同：请求前原子预留，用量完整才结算，失败保留预留，不自动重试。这是逐次的本地消费控制；账号的月度额度由负责人在服务端管理，脚本只报告每次用量。`model` 默认 `claude-opus-5-5`，也接受 `claude-sonnet-5-5`；单次最多输出 8000 token，`reserve_usd` 应覆盖这一上限（Opus 约 0.16 美元加输入）。

任务卡新增 `acceptance`：1—30 条可以逐条判断的短句，例如“画面里没有文字、数字或边框”。没有这个字段时拒绝送检；提示词不发送给检查者。检查顺序：

1. 机械检查：只接受非隔行 PNG，须有 `IEND`、逐块 CRC 正确、压缩流完整且每种格式的扫描行总长都对（解压以 64 MiB 为限）；真实尺寸须等于卡片 `size`；卡片 `background` 为 `transparent` 时，须是 8 位 RGBA 且四角完全透明，其他格式无法核实，按不通过处理。图片读不懂也算机械检查不通过（`mechanical.png: false`）。任一项不通过即 `verdict: fail`，不调用模型；带 `--execute` 时照样写回执。
2. 模型判定：图片以 base64 PNG 发送（单张不超过 7 MiB），条目按编号发送，结构化输出限定每条回答带编号 `id`，判定为 `pass`、`fail` 或 `unsure` 并附理由；脚本按编号对应条目，编号缺失、重复或未知时判为无法判定。任一条 `fail` 即不合格；无 `fail` 而有 `unsure` 即无法判定；全部 `pass` 才合格。回答被截断、拒答、条目数不符或出现未知判定值时判为无法判定，费用照计。

`check.json` 写在图片旁，必须被 Git 忽略且不是链接（付费请求之前就核对；在支持 `O_NOFOLLOW` 的平台上写入时也不跟随链接，Windows 只依赖请求前的检查）。它记录机械检查、每条判定与理由、模型、用量、费用和图片 SHA-256，不含 key、提示词和本机路径。

## 视觉验收的接口与价格依据

2026-11-24 核对：[价格页](https://platform.claude.com/docs/en/about-claude/pricing)列 `claude-opus-5-5` 每百万输入 token 4 美元、输出 20 美元，Sonnet 5.5 为 2 美元和 10 美元；[思考文档](https://platform.claude.com/docs/en/build-with-claude/thinking-steering-and-cost)说明思考 token 计入 `output_tokens` 按输出计费；[视觉文档](https://platform.claude.com/docs/en/build-with-claude/vision)说明一张图约 ⌈宽/28⌉×⌈高/28⌉ 个视觉 token（1024x1024 约 1369 个），其“Request limits”一节写明 Claude API 单张上限为 base64 后 10 MB（脚本限 7 MiB 原文件，编码后约 9.8 MB）；[结构化输出文档](https://platform.claude.com/docs/en/build-with-claude/structured-outputs)以 `output_config.format`（`type: "json_schema"`）发送约束，支持列表含 `claude-opus-5-5`；[版本文档](https://platform.claude.com/docs/en/api/versioning)示例的 `anthropic-version` 为 `2026-10-21`。2026-11-24 实测一次 1024x1024 送检（3 条条目）为 1858 输入、166 输出 token，约 0.011 美元；条目多或思考多时更高。模型或价格变化后先核对文档再改脚本里的费率。
