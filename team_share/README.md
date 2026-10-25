> 最新交付 **0.4.10-cd-20261025**，保留0.4.9 B修复并增加C/D修复。C两题官方API实测通过输出/文件检查，D源码通过全部13个官方公开输入检查；正式D凭证与隐藏题仍待平台复评。tar与校验见 [新版发布说明](releases/0.4.10-cd-20261025/README.md)。E未重新验收。

> 2026-10-24 更正：正式输出目录必须用平台注入的 RESEARCH_WORKSPACE_TEAM；报名队名和 TEAM_ID 都不能替代。0.4.6-team-20261024 写死队名，不能视为正确接入版。当前修复源码为0.4.7，镜像及验证状态见最新发布记录。

# 科研助手 Agent 队友测试包

> **0.4.5已导出，但官方验收失败**：`eval-20261024-a93a0c67` 出现B/C输出异常、C-03调用失败和追踪缺失。此前153项本地回归与43次合成材料模型调用不能证明平台已合规；暂不反复上传这个tar。详见 [本次复核](../docs/2026-10-24-0.4.5官方失败与输出契约复核.md)。

> **最新修复镜像（2026-10-24）：`research-assistant:0.4.2-http-20261024`**，tar 为 `image/research-assistant-0.4.2-http-20261024.tar`。已修复直接八字段 HTTP 422，本地 OTLP 实际接收任务/工具/模型追踪，详见 [交付说明](releases/0.4.2-http-20261024/README.md)。授权材料目录仍须配置，官方采集链路与成绩待平台验证；旧 0.4.1 镜像不含此修复。

> **最新本地打包（2026-10-24）：`research-assistant:0.4.1-v14-20261024`**，tar 位于 `image/research-assistant-0.4.1-v14-20261024.tar`。这是当前源码的重新构建，未修改业务逻辑、未上传。已确认直接八字段请求仍为 HTTP 422，授权材料目录仍需运行时配置；请先读 [本次交付说明与限制](releases/0.4.1-v14-20261024/README.md)。下方为历史交付记录。

> **本次新增镜像：`research-assistant:0.4.1-v14-20261023`**，完整 tar 位于 `image/research-assistant-0.4.1-v14-20261023.tar`。导入、正式环境配置与新旧版本对比请先读 [新版说明](releases/0.4.1-v14-20261023/README.md)。旧版 0.3.0 及下方历史说明保留供对比。GitHub 另一个同名 0.4.1 发布属于不同源码，请以完整标签和哈希区分。

这个目录用于把已经构建好的科研助手镜像交给队友做本地测试。

## 历史交付版本（2026-10-21）

- 镜像：`image/research-assistant-0.3.0-source-v1.tar`
- 镜像标签：`research-assistant:0.3.0-source-v1`
- 协议：`layered-d-v3-source-v1`
- 11 题本地全流程仿真报告：`output/v3-simulation-20261021/README.md`
- 本轮本地代理分：`88.60/100`；该分数用于工程回归，不能替代赛事平台榜分。

旧的 `0.2.0` tar 和命令仍保留作回滚参考；新测试优先使用上面的 `0.3.0-source-v1`。

## 目录内容

- image/research-assistant-0.3.0-source-v1.tar：当前推荐的 Docker 镜像；旧版 0.2.0 tar 仅作回滚参考。
- config/.env.example：不含密钥的环境变量模板。
- config/config.container.example.json：普通 A/B/C/E 测试配置。
- config/config.container.local-d.json：D 类公开实验本地配置。
- papers/：B/C/E 测试用的 7 篇 PDF。
- experiments/research-experiments/：D-01 至 D-05 公开实验包。
- requests/：A-E 测试请求 JSON。
- output/：队友运行时创建，用于保存结果和图表。
- SHA256SUMS.txt：发送前后用于核对文件完整性的 SHA-256 清单。

当前推荐镜像是 research-assistant:0.3.0-source-v1。如果要重新导出同一标签，执行：

docker save research-assistant:0.3.0-source-v1 -o team_share/image/research-assistant-0.3.0-source-v1.tar

使用 docker save 导出的 tar 已经包含官方基础镜像层，不需要再发送或加载 agenthack-base-1.1.1.tar。

## API Key 安全

本包不包含 API Key，也不要把 .env.local 放进压缩包或公共网盘。测试和比赛统一使用组委会指定的官方 Key，通过运行时环境变量注入；不要使用其他模型供应商或把 Key 写进镜像。

在队友工作区执行：

cp config/.env.example .env.local

编辑 .env.local，填写：

PLATFORM_LLM_URL=https://ark.cn-beijing.volces.com/api/coding/v3
PLATFORM_API_KEY=在这里填写自己的API_KEY
AGENT_MODEL_NAME=deepseek-v4.1-flash

不要把填好 Key 的 .env.local 回传到代码仓库或群聊。

## 模型 API 兼容范围

当前 Agent 使用 OpenAI Chat Completions 兼容协议，实际请求为：

POST <PLATFORM_LLM_URL>/chat/completions
Authorization: Bearer <PLATFORM_API_KEY>

以下类型通常可以直接使用：

- OpenAI 兼容网关；
- 火山方舟兼容接口；
- DeepSeek 兼容接口；
- SiliconFlow、Moonshot、部分 Qwen 网关等声明支持 OpenAI SDK 的服务。

不能保证直接兼容的情况包括：

- 只有 Anthropic Messages API 的服务；
- 只有 Google Gemini 原生接口的服务；
- 需要特殊签名、特殊请求头或非 Bearer 认证的服务；
- 只支持 Responses API、但不提供 /chat/completions 的服务。

这些服务需要在 src/research_agent/integrations/llm.py 中增加适配器，不能只替换 URL。正式比赛只能使用组委会指定的模型网关和模型名。

如果兼容地址已经包含 /v1，就把完整兼容基地址填入 PLATFORM_LLM_URL，不要重复填写 /chat/completions。

## Windows PowerShell 测试

在 team_share 的上一级目录执行：

docker load -i .\team_share\image\research-assistant-0.3.0-source-v1.tar

docker load 会把已经打包好的镜像导入 Docker，不会重新构建代码。

创建输出目录：

New-Item -ItemType Directory -Force .\team_share\output | Out-Null

启动普通测试容器（下面是一行命令）：

docker run -d --name research-agent-peer --cpus 1 --memory 1024m --env-file .env.local -e RESEARCH_CONFIG=/app/settings.json -e AGENTHACK_TELEMETRY_REQUIRED=false -p 127.0.0.1:18081:8080 -v "${PWD}\team_share\config\config.container.example.json:/app/settings.json:ro" -v "${PWD}\team_share:/workspace:ro" -v "${PWD}\team_share\output:/output" research-assistant:0.3.0-source-v1

参数含义：-d 后台运行；--env-file 读取本地密钥；-p 把本机 18081 映射到容器 8080；两个 :ro 挂载只读论文和配置；最后的镜像名指定启动的镜像。

健康检查：

Invoke-RestMethod http://127.0.0.1:18081/health

查看日志：

docker logs -f research-agent-peer

测试结束后删除容器：

docker rm -f research-agent-peer

## WSL/Linux 测试

在 team_share 的上一级目录执行：

docker load -i team_share/image/research-assistant-0.3.0-source-v1.tar
mkdir -p team_share/output
docker run -d --name research-agent-peer --cpus 1 --memory 1024m --env-file .env.local -e RESEARCH_CONFIG=/app/settings.json -e AGENTHACK_TELEMETRY_REQUIRED=false -p 127.0.0.1:18081:8080 -v "$(pwd)/team_share/config/config.container.example.json:/app/settings.json:ro" -v "$(pwd)/team_share:/workspace:ro" -v "$(pwd)/team_share/output:/output" research-assistant:0.3.0-source-v1

健康检查：

curl http://127.0.0.1:18081/health

## A-E 请求测试

requests/ 下已经准备了五类请求。以 B 类为例：

curl -sS -X POST http://127.0.0.1:18081/solve -H 'Content-Type: application/json' --data-binary @team_share/requests/B-01.json

/solve 返回中的 output.answer 是题型 JSON 字符串。C/E 还会把 Markdown 写入 /output/data/expert/，D 会生成指标、CSV、PNG 和运行记录。

## D 类公开实验

普通配置使用 experiment_mode=platform，适合模拟正式平台；本地没有平台实验会话时，D 类可能返回 materials_missing，这是预期保护行为。

如果要在本地真实运行 D-01 至 D-05，需要使用 config/config.container.local-d.json，并额外挂载 experiments/research-experiments。该配置只用于本地标定，不能替代正式平台实验 runner。

PowerShell 中的 D 类本地测试命令如下（使用 18082，避免占用普通测试容器的 18081）：

docker run -d --name research-agent-d-peer --cpus 1 --memory 1024m --env-file .env.local -e RESEARCH_CONFIG=/app/settings-d.json -e AGENTHACK_TELEMETRY_REQUIRED=false -p 127.0.0.1:18082:8080 -v "${PWD}\team_share\config\config.container.local-d.json:/app/settings-d.json:ro" -v "${PWD}\team_share:/workspace:ro" -v "${PWD}\team_share\experiments\research-experiments:/experiments:ro" -v "${PWD}\team_share\output:/output" research-assistant:0.3.0-source-v1

然后发送 D-01：

Invoke-RestMethod -Method Post -Uri http://127.0.0.1:18082/solve -ContentType 'application/json' -Body (Get-Content .\team_share\requests\D-01.json -Raw)

D 类完成后可在 team_share/output/data/figure/workspace_team/ 查看 PNG，在 team_share/output/data/workspace_team/ 查看 JSON 和运行回执。删除 D 类容器：

docker rm -f research-agent-d-peer

## 发送前检查

- image/ 中有最终要发送的镜像 tar。
- 用 SHA256SUMS.txt 核对镜像和测试材料没有传输损坏。
- 没有 .env.local、API Key 或其他密钥文件。
- papers/ 中的 PDF 与请求的 materials 文件名一致。
- experiments/research-experiments/ 完整存在。
- 已发送 tar 的 SHA-256，并让队友导入后检查镜像标签。
- 正式比赛仍使用组委会指定网关，不能把本地测试厂商配置带入正式提交。
