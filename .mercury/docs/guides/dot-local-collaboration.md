# dot 与本地 Codex 协作

Mercury 管理 `dot-link` 原生技能和签名工具。用户级安装让本地新会话读取同一配置并沿用稳定公钥；会话 ID 只用于路由。技能只在用户任务需要 dot YC 时使用，不启动常驻协调服务。接收方的真实人类授权决定可执行范围，签名只证明消息来自已配对的安装。

## 安装与身份

使用 PowerShell 7 执行 `scripts/codex/install-dot-link.ps1`，提供已存在的 Python、OpenSSL、配对标识和 YC 线程。脚本安装到用户的 `.agents/skills/dot-link`，在 `.codex/AGENTS.md` 增加一个有边界的入口段落，并写入 `.codex/dot-link/config.json`。它备份所修改的原文件并输出备份目录。机器路径只保存在用户配置，源码不含凭据。

首次调用 `dot_link.py init --state-dir <identity目录> --openssl <可执行文件>` 前，应为 identity 目录设置访问控制。Windows 只允许当前用户和 SYSTEM 访问，禁用继承；Linux 使用仅本人可访问的目录。随后生成独立 Ed25519 身份，不复用 SSH/API 凭据。初始化拒绝覆盖现存身份。私钥仅由本地工具读取，不上传、不放进提示词或 Git。

首次配对把公钥及其 DER SHA256 指纹交给接收方，由接收方从可信人类来源确认该公钥和长期协作范围。收到的代理请求不能自己激活授权，也不能自动换钥。若接收方不能读取本地人类指令，可准备候选登记与验签结果；激活等待接收方可核实的人类依据。

## 任务传递

本地创建完整消息正文，用共享配置执行 `sign`，再通过已有 `send_message_to_thread` 发送原始签名信封。正文中的任务范围、类型、路由、期限、关联字段与证据摘要全部进入签名字节。接收方用既有 pin 执行 `verify`；`ready_for_authorization_review` 只证明可信身份和首次去重条件满足。工具始终返回 `task_authorization_checked: false`、`execution_allowed: false`；原生代理核实真实用户授权、任务范围和操作边界后，才处理请求。工具不会执行正文中的命令。

接收方用 SQLite 持久记录 request_id 和 nonce。相同请求重发返回已有状态；相同 request_id 内容不同或 nonce 复用拒绝。新会话登记新的 source/reply 地址并沿用相同 client_id 和公钥，不重新生成身份。传输元数据可用时必须核对其 sourceThreadId。

YC 使用自己的会话交付结果和私有 Page。反向消息不可用时，本地读取 YC 会话与 Page。原文件清单包含精确引用、字节数及 SHA256；传递核验和业务验收分别记录。证据读取成功不等于 Godot 图形验收通过。

## 授权与撤销

用户发起任务即授权其正常执行链路，包括必要的工具、代理交互和私有材料传递。已获授权的步骤不重复确认。高危操作和用户明确要求单独授权的行为保留确认；GitHub 评论或回复须有明确授权。协议不能替代工具权限、审批或受保护分支规则。

回滚安装时执行安装脚本的 `-Rollback -BackupPath <输出的备份目录>`。脚本先核对全部文件仍为本次安装的摘要，再恢复原文件或逐文件移除新增文件，保留签名身份。变更后的文件应人工合并，不能覆盖后来工作。结束配对须另行撤销接收端公钥；换电脑或换钥需要重新确认信任。

## 验证

`python scripts/codex/test-dot-link.py` 运行有效签名、行为字段篡改、过期、错误公钥、严格 JSON 解析、持久去重与跨会话身份测试。GitHub 的 Dot Link Check 重复执行便携测试。本地安装、真实 YC 验签与授权生效另存仓库外回执，不能从静态测试推断云端权限已开通。

命令行为依据 [OpenSSL 3.1 pkeyutl](https://docs.openssl.org/3.1/man1/openssl-pkeyutl/)；用户级技能目录依据 [Codex 技能文档](https://learn.chatgpt.com/docs/build-skills)。
