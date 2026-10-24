# 原生官方运行时与标准论文库：0.4.3-runtime-20261024

> 后续诊断：本轮官方 11 题全 error。已在此镜像复现最终 HTTP 响应仍为 SDK 四字段包装，与公开题型顶层 Schema 不一致；此前内部答案/OTLP 检查未覆盖该边界。当前 tar 未修复此问题，暂不作为赛题格式合格版继续提交。材料路径和新正文上限另有兼容缺口；详见项目 `docs/2026-10-24-官方零分逐项诊断.md`。以下保留当时本地验证记录。

镜像：`research-assistant:0.4.3-runtime-20261024`。本版本使用现有官方 SDK，无须另找新 SDK。主入口直接构造官方 `AgentRuntime` 并执行 `run()`，原生 health/solve 路由和 SDK 源码保持；只在请求进入 SDK 前转换八字段输入。离线 HTTP 服务已移到测试目录，不进入镜像。

## 交付文件

- tar：`../../image/research-assistant-0.4.3-runtime-20261024.tar`
- 大小：295,424,000 字节
- SHA-256：`88c238063cb01d3c4ed4a82b7f03ff2818cbea4dcd29d13030c58f37a4f6abaa`
- Linux/AMD64，非 root 用户 agent，8080/tcp，内置启动命令。

```powershell
Get-FileHash .\team_share\image\research-assistant-0.4.3-runtime-20261024.tar -Algorithm SHA256
docker load -i .\team_share\image\research-assistant-0.4.3-runtime-20261024.tar
```

镜像已重新导入并核对 ID；未上传平台或 GitHub。旧镜像保留为历史版本。

## 本次修复

此前版本把 SDK 响应改成题型对象，去掉 metadata，又把 SDK 业务失败转换成 HTTP 500。本版本原样保留官方 `task_id/status/output/metadata` 响应，包括成功、error 和 timeout；`output.answer` 内为完整题型 JSON，磁盘结果仍保持原题型格式。metadata 的 trace_id/session_id 与实际 OTLP 关联已验证。

无自定义配置时支持标准 `/data/论文库`、规范中的 `本地论文文件夹内的 PDF` 描述、明确的 `data/论文库/<文件名>` 路径，队伍目录从非空 TEAM_ID 读取。自定义材料根、别名和队伍配置优先，显式空材料配置不被自动扩大。指定领域的模糊描述不自动授权整个论文库；其他布局仍需明确配置。

## 验证与证据

55 项测试通过，3 项缺实验挂载跳过。只读标准论文库、无自定义 RESEARCH 配置、1 CPU / 1 GiB、无外网条件下：

- A-05、B-01 检索描述、B-02 明确路径均成功，响应和落盘一致。
- 故意缺文件的 B-03 保留 SDK error 及真实失败追踪，不返回空检索假装成功。
- D-01 材料缺失保留标准答卷，旧 SDK 包装请求继续可用。
- 六次请求均有 metadata，trace_id 与接收器实际收到的 OTLP 一致；成功和失败共收到 74 条追踪。没有消耗真实模型额度，PDF 与模型回复均为合成夹具。
- SDK 源码与官方基础镜像一致，原生路由与实例类型、依赖、源码哈希和 tar 回读均通过。

`baseline-042.json` 和原始 spans 保留旧版 A 成功/B 失败的同条件对照；新版见 `container-smoke.json`、`otlp-received-spans.json`、`image-audit.json`。

关联评测 `eval-20261024-486e8a18` 没有开放详细运行日志。本地可复现的材料/响应缺口已修复，但不据此宣称官方追踪缺失的全部原因已确定。正式网关、OTLP 地址及认证仍由平台注入，不能用本地测试参数覆盖。官方接收、判题兼容与 D 来源凭证待真实平台验证。
