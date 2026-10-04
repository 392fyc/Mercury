# 私有任务更新与代理自动继续

任务站只传递任务编号、结果 Page 引用和已签名正文摘要。事件内容是协作资料；接收代理继续使用真实用户授权与 dot-link 验签规则。服务不会执行事件中的命令，也不把签名、通知或会话可读性当成授权。

## 两端入口

| 接收者 | 入口 | 生效条件 |
| --- | --- | --- |
| 本地 Codex 当前会话 | 原生 heartbeat 定时读自己的队列 | 主机及 Codex 正在运行；已有 SSH 通道可用 |
| dot / Cloud Work | MCP Events webhook | 私有 MCP 认证连接、订阅、回调挑战、实际会话运行均验证成功 |
| Claude Code | 可由用户的调度器调用相同队列客户端，再使用 `claude -p --resume` | 只续接 Claude 自己的会话；需该环境认证与运行验收 |

本地 heartbeat 是定时唤醒，不能称为即时 webhook。MCP Events 回调的 HTTP 2xx 只表示平台接收，不能作为代理执行完成的证据。当前桌面会话没有已确认的外部 webhook 地址，不猜测或接管内部 app-server。

## 队列与凭据

`scripts/agents_gateway/server.py` 使用 Python 标准库、SQLite 和固定字段事件。`scripts/agents_event_client.py` 提供 list、publish、consume；它不启动模型、不解释事件。队列按认证身份隔离，发布方只能向允许的接收方投递。

本地可通过现有 SSH 通道在服务容器内调用客户端。私有配置只保存 SSH 主机别名、Docker 路径、容器名和 principal；安装策略在用户目录 `.codex/dot-link/event-policy/targets.json` 单独固定这些获准值，不能通过命令参数换用其他策略。客户端拒绝符号链接或把同一文件同时作为配置和策略，逐项匹配后才连接。安装时 Windows 策略目录及文件权限必须限制为当前用户、SYSTEM 和 Administrators；Unix 策略必须由当前用户拥有且其他用户不可写。配置和策略属于安装材料，事件不能选择或修改它们。令牌只保存在服务器的只读凭据挂载中。不要将令牌、私钥、会话记录或真实任务资料加入版本库。客户端禁止将 Bearer 凭据跟随 HTTP 重定向。

以下是便携模板，主机、路径和身份由安装者填写；NAS 安装的身份名称应以其实际私有部署清单为准。将已批准的同一内容分别保存为配置和安装策略，不能直接照抄模板来访问现有服务器：

```json
{
  "ssh_host": "nas.example",
  "docker": "/opt/docker",
  "docker_config": "/private/docker-config",
  "container": "agents-events",
  "principal": "local-agent"
}
```

```text
python scripts/agents_event_client.py --ssh-config PRIVATE_CONFIG --payload-file PRIVATE_REQUEST list
```

客户端在容器内使用固定 loopback 地址 `http://127.0.0.1:8765`，凭据位置为 `/credentials/<principal>.token`。部署应使用固定镜像摘要，非 root 用户、只读程序和配置、独立可写数据库目录、无主机端口映射、无 Docker socket 挂载。首次只启用内部收件队列；公开 MCP、OAuth 与回调出站连接需分别验证。

## 接收与去重

1. heartbeat 或 webhook 使原生代理开始一轮工作，读取自己的新增事件。
2. 以 task_id / request_id 找到当前授权任务；取回指向的完整签名消息和原文件，核对签名、固定来源、期限、正文摘要与任务范围。未知关联只报告所缺依据，不能自动执行。
3. 使用现有原子账本去重。重复事件或已关闭任务只读取缓存结果，不再次执行任务。
4. 原文件读回与消费记录保存后标记事件 consumed；保存队列序号。收到通知、成功读取与完成处理分别记录。
5. consumed 不再产生反向事件，避免无限相互唤醒。无新事件时保持安静，只报告完成、失败或实际需要用户操作的变化。

创建原生 heartbeat 必须使用 Codex 的 automation_update 工具。更新现有匹配自动化时保持未请求修改的字段；不要手工写入自动化配置、另造 cron，或用 `codex exec` 冒充当前桌面会话续接。

## 云端启用前的验收

内部 Bearer 只解决队列认证，不代表 ChatGPT 私有 MCP 认证已兼容。使用已有 OAuth 提供方，并保持 Cloudflare 访问限制；不能为连接方便开放匿名工具、共享浏览器 cookie 或上传机器私钥。

验收必须保留：连接认证、events/list、events/subscribe、带签名挑战及回显、稳定 eventId 的实际事件投递、接收会话自动开始工作的证据、结果原文件消费和重复通知只命中缓存的证据。单元测试或虚假回调成功不能替代真实两端验收。

官方依据：[MCP Events](https://developers.openai.com/plugins/build/mcp-events)、[MCP 认证](https://developers.openai.com/plugins/build/auth)、[原生自动化](https://learn.chatgpt.com/docs/automations)、[Claude Code CLI](https://code.claude.com/docs/en/cli-usage)。
