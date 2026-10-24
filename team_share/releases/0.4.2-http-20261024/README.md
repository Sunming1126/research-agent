# 平台直接题目接入修复：0.4.2-http-20261024

> 后续核对：用户提供了强制原生 `AgentRuntime(MyAgent()).run()` 的新要求。本镜像继承 SDK 并替换过路由；本地 OTLP 导出成功不等于已满足该入口要求，适配方式尚待官方确认。当前官网下载的原生 SDK 仍拒绝八字段直传，需核对官方标准适配/更新说明。详情见项目 `docs/2026-10-24-新运行时要求与SDK协议核对.md`。本文件追加说明未改变已有 tar。

对应失败评测 `eval-20261024-d031f009`。旧 0.4.1 镜像在 FastAPI 请求校验阶段拒绝八字段 JSON，业务和 SDK 任务追踪均未启动。本版本在保留官方 SDK 原始源码的前提下适配输入输出，直接题目经官方 SDK 原有任务路由执行并导出 OTLP。

## 镜像文件

- 标签：`research-assistant:0.4.2-http-20261024`
- tar：`../../image/research-assistant-0.4.2-http-20261024.tar`
- 大小：295,423,488 字节
- SHA-256：`9ff9e15a4cf60884639e1ca1289ed1ed005b5d625c2e3c109ff7dc8613144bc1`
- Linux/AMD64，非 root 用户 `agent`，容器端口 8080。

从项目根目录导入：

```powershell
Get-FileHash .\team_share\image\research-assistant-0.4.2-http-20261024.tar -Algorithm SHA256
docker load -i .\team_share\image\research-assistant-0.4.2-http-20261024.tar
```

tar 已重新导入，镜像 ID 与交付清单一致。旧 tar 原样保留；此修复不在旧 0.4.1 镜像内。当前仅本地交付，尚未上传平台或 GitHub。

## 已验证

- 7 项真实 SDK HTTP 回归、38 项核心/架构/网关回归通过；3 项因未挂载公开实验材料跳过。
- 1 CPU / 1 GiB、无外网，运行实际 `agent.py` 主进程，启用 `AGENTHACK_TELEMETRY_REQUIRED=true`。
- A-05、B-01 直接题目、D-01 材料缺失及 A-06 旧包装请求均返回 200，落盘内容与响应一致。
- OTLP/HTTP 本地接收器实际收到 51 条追踪：有 SDK `agent.session` / `agent.task`、业务 `agent.tool`，A/B 有真实 HTTP 请求产生的 `agent.llm` / `agent.http`。父追踪、任务关联与模拟响应的用量计数已核验。
- 官方 SDK 所有 Python 源码哈希与基础镜像一致；运行依赖检查和本地源码核对通过。

模型响应、论文和 OTLP 接收器均为本地测试夹具；没有使用真实模型 Key，也不构成正式成绩或 D 类来源凭证验收。原始接收记录见 `otlp-received-spans.json`，摘要见 `container-smoke.json`。

## 平台运行配置

平台需继续注入正式模型 URL、Key、模型名、队伍目录和 OTLP 收集地址/认证。不要覆盖这些值为本地测试端口，不要在正式环境关闭遥测。

`GET /health` 的 `telemetry_configured=true` 表示配置存在，不能代替收集端实际收到追踪。本地导出成功也不能证明远端平台链路正常；如新评测仍报追踪缺失，应携新评测编号核查平台注入配置、容器日志和收集链路。

授权论文目录仍须用 `RESEARCH_MATERIAL_ROOTS` 或配置文件的 `material_roots` 指定平台实际挂载位置；材料描述别名按需配置。不能把整个工作区作为论文库，也不能用空论文列表冒充检索成功。
