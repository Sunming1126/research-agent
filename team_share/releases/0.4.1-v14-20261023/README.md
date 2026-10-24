# 当前本地版本：0.4.1-v14-20261023

这是 2026-10-23 两轮完整仿真所用的 **0.4.1 / 20261023-v14** 业务源码镜像。镜像发布名增加 v14 和日期，避免与仓库另一个 `v0.4.1-source-v1` 发布混淆。两个版本源码不同，不能仅凭 0.4.1 判断一致。

## 下载与校验

GitHub Release：<https://github.com/Sunming1126/research-agent/releases/tag/v0.4.1-v14-20261023>。公开附件只有镜像、脱敏的 `USAGE.md`、`delivery-manifest.json` 和 `SHA256SUMS.txt`，它们对应本地 `public/` 目录；完整测试证据保留在本地。

下载 `research-assistant-0.4.1-v14-20261023.tar` 和 `SHA256SUMS.txt`。tar 包含官方基础镜像层、Python、SDK、固定依赖和 Agent 代码，可直接导入，无须重新构建或另下基础镜像。GitHub 自动生成的 Source code 为仓库基线，不是本镜像构建快照，不能据此重建本次镜像。

```powershell
Get-FileHash .\research-assistant-0.4.1-v14-20261023.tar -Algorithm SHA256
docker load -i .\research-assistant-0.4.1-v14-20261023.tar
docker image inspect research-assistant:0.4.1-v14-20261023 --format '{{.Id}} {{.Os}}/{{.Architecture}} {{.Config.User}}'
```

镜像平台应为 `linux/amd64`，用户为 `agent`。精确标识、文件哈希见 `public/delivery-manifest.json`，导入后按其中的 ID 核对。

## 官方测试

- 将本次 tar 作为独立版本登记，记录 `submission_id`、镜像标签、镜像 ID 与 tar SHA-256。
- 容器通过官方 AgentHack SDK 在 **8080** 提供 `/health`、`/solve`。启动命令已内置，不需要覆盖。
- 运行时由平台注入正式模型网关、模型、Key 和可信会话信息，挂载授权题目、材料及可写结果目录；不要使用本地厂商 Key 作为正式配置。
- 配置本队目录：`RESEARCH_WORKSPACE_ROOT` 是派题工作区，`RESEARCH_RESULT_ROOT` 是可写输出根目录，`RESEARCH_WORKSPACE_TEAM` 是本队目录名。必要时由 `RESEARCH_CONFIG` 指向挂载的配置 JSON；授权论文根目录必须单独配置，不能设成整个工作区。
- 默认单题预算 180 秒、30 次模型调用、40,000 Token。正式 D 使用 `experiment_mode=platform` 和运行时允许的协议，不要切成旧公开包的 `local` 模式。
- 正式评测保持官方遥测要求；不要带入本地 `AGENTHACK_TELEMETRY_REQUIRED=false` 开发配置。
- 镜像未附带论文、历史答案、本地实验算法夹具或模型 Key。

## 本地快速检查

现有 `team_share` 论文、请求和配置仍可用。先复制 `.env.example` 到私有 `.env.local`，填写自己的临时 Key；该文件不能上传或发进群。

使用项目目录中的 `team_share` 材料，在项目根目录运行：

```powershell
docker run -d --name research-agent-v14 --cpus 1 --memory 1024m --env-file .env.local -e RESEARCH_WORKSPACE_ROOT=/workspace -e RESEARCH_CONFIG=/app/settings.json -e AGENTHACK_TELEMETRY_REQUIRED=false -p 127.0.0.1:18084:8080 -v "${PWD}\team_share\releases\0.4.1-v14-20261023\config.container.json:/app/settings.json:ro" -v "${PWD}\team_share:/workspace:ro" -v "${PWD}\team_share\output:/output" research-assistant:0.4.1-v14-20261023
Invoke-RestMethod http://127.0.0.1:18084/health
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:18084/solve -ContentType 'application/json; charset=utf-8' -Body ([System.Text.Encoding]::UTF8.GetBytes((Get-Content .\team_share\requests\A-01.json -Raw -Encoding UTF8)))
docker rm -f research-agent-v14
```

先确保 `team_share/output` 存在。`/solve` 响应中的 `output.answer` 是合法题型 JSON 字符串。

## 与旧版如何比较

1. 现有旧镜像 `research-assistant:0.3.0-source-v1` 及其 tar 原样保留；GitHub 已有另一个 `0.4.1-source-v1`，如也参与对比需记录对应 ID，分别登记。
2. 新版使用 `research-assistant:0.4.1-v14-20261023`，不要把它重新标成旧版名称。
3. 若官方允许同一套题重复评测，在同一评测轮次、材料、模型、预算与资源下比较；若派题不同，总分只能作参考。
4. 保存官方总分、A/B/C/D/E 分项、失败题、耗时、Token、平台错误及版本编号。重点查看 C/E 内容覆盖和新版 D 的正式执行结果。
5. 本地两轮代理分为 **92.8000 / 90.7167**，不是官方成绩，不能证明新版一定胜出。C-01 一轮触发保底，机制覆盖、医学基准与资源匹配仍有不足。

本地容器验收见本目录 `image-audit.json`、`container-smoke.json`，两轮评分材料留在项目 `output/`，未公开上传。此次容器冒烟沿用历史共享配置的 300 秒超时，A/B/C 实际均在 180 秒内返回；镜像默认及本目录新版示例配置均为 180 秒。正式来源凭证、可信执行和隐藏输入评分仍需官方测试；接口通过不能代替这些验收。
