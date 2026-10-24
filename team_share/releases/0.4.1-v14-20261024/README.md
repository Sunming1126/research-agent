# 当前源码镜像：0.4.1-v14-20261024

2026-10-24 按当前工作目录构建，包含未提交的业务源码；本次没有修改 Agent 业务逻辑。使用独立日期标签，保留此前镜像。仅本地导出，未上传 GitHub 或赛事平台。

- 镜像：`research-assistant:0.4.1-v14-20261024`
- 文件：`../../image/research-assistant-0.4.1-v14-20261024.tar`
- 大小：295,421,952 字节
- SHA-256：`eddf0981df9ccee3a6a5f67cc359d3f44517198a0131e55f00397f87521a08ed`
- 平台：Linux/AMD64；用户：`agent`；容器端口：8080。

在项目根目录执行：

```powershell
Get-FileHash .\team_share\image\research-assistant-0.4.1-v14-20261024.tar -Algorithm SHA256
docker load -i .\team_share\image\research-assistant-0.4.1-v14-20261024.tar
docker image inspect research-assistant:0.4.1-v14-20261024
```

tar 包含基础镜像层和运行依赖，无须另行下载基础镜像。源码、依赖和非 root 用户核验见 `image-audit.json`；源码逐文件 SHA-256 与当前工作目录一致。构建上下文排除了本地密钥、论文、历史答案和本地完整算法夹具。

1 CPU / 1 GiB、无外部网络条件下，健康检查、A 类 SDK 请求和 D 类材料缺失答卷校验通过。A 类使用本地模拟模型网关，不消耗真实模型额度；这些检查不代表正式评分或 D 类来源凭证验收。详情见 `container-smoke.json`。

## 已确认的接入限制

- 当前官方 SDK 入口仍要求 `task_id` / `input` 包装；直接发送八字段题目 JSON 会返回 HTTP 422。该现象已在此镜像内复现，不能宣称当前镜像已完成赛题一直接请求适配。
- 授权材料目录默认为空；正式运行必须按平台实际挂载位置配置 `RESEARCH_MATERIAL_ROOTS` 或 `RESEARCH_CONFIG` 中的 `material_roots`，并按需配置材料描述别名。没有配置时，带材料的 B 类题会在调用模型前失败。
- 正式模型网关、Key、模型名称和本队目录仍需运行时注入。

上述问题未在本次打包中修复。重新构建镜像不会自动解决接口或材料配置问题。
