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
  "model": "gpt-image-2.5-flare-2026-09-08",
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
  "model": "gpt-image-2.5-sunburst-2026-09-08",
  "prompt": "Image 1 is the edit target. Preserve its silhouette, proportions and material detail. Change only the small central gemstone from red to blue. Keep the original background transparency. No text or extra objects.",
  "references": ["assets/approved-emblem.png"],
  "size": "1024x1024",
  "quality": "low",
  "background": "transparent"
}
```

支持 `flare`、`sunburst` 的上述快照及无日期别名；默认使用固定日期的 flare。只接受 `1024x1024`、`1536x1024`、`1024x1536` 与 `low`、`medium`、`high`，每次 `n=1`，PNG 输出。编辑接受 1—16 张本地 PNG/JPEG/WebP，每张不超过 10 MiB。第一张为编辑目标时，在提示词中明确说明；其余图片逐一说明是身份、材质还是构图参考。此小工具不提供蒙版、远程图片 URL 或批量接口。

输出回执记录请求参数、提示词 SHA-256、实际发送的各参考图字节数与 SHA-256、生成图字节数/尺寸/SHA-256，以及用量和费用。它不含 key、提示词正文和本机路径；原任务卡仍须保留，以便复现输入。`actual_usd=null` 表示不能确认费用，`accounted_usd` 是账本当前占用，不得写作实际账单。错误只报告类型，不输出服务器正文或 traceback。

## 当前接口与价格依据

2026-10-30 核对的 [生成接口](https://developers.openai.com/api/reference/resources/images/methods/generate) 和 [编辑接口](https://developers.openai.com/api/reference/resources/images/methods/edit) 支持 JSON 请求；编辑的 `images` 使用本地原字节编码的 data URL。请求固定发送到 OpenAI 官方 Images 端点，拒绝重定向，不将 Authorization 转发给新地址。

[Flare 模型页](https://developers.openai.com/api/docs/models/gpt-image-2.5-flare) 与 [Sunburst 模型页](https://developers.openai.com/api/docs/models/gpt-image-2.5-sunburst) 当前的 Standard 费率相同：每百万文本输入 token 5 美元、图像输入 token 8 美元、输出图像 token 30 美元。脚本按这三个费率计算完整用量，不采用缓存折扣。模型、端点或计费方式变化后先核对文档再改费率，不能任意传模型名继续沿用旧价格。

官方提示，人物跨图一致性、精确构图与文字仍可能出错；蒙版也是指导而非逐像素保证。详见[图像生成指南](https://developers.openai.com/api/docs/guides/image-generation)。这些限制应由项目实际验收覆盖，不以模型名称或高质量档代替检查。
