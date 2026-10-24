# 科研助手完整镜像：0.4.1-v14-20261023

镜像标签：`research-assistant:0.4.1-v14-20261023`。这是当前本地 0.4.1 / 20261023-v14 版本，独立于已有的 `0.4.1-source-v1` 发布。请按完整标签、镜像 ID 和 SHA-256 区分。

## 导入

下载本发布的 tar、delivery-manifest.json 和 SHA256SUMS.txt。

```powershell
Get-FileHash .\research-assistant-0.4.1-v14-20261023.tar -Algorithm SHA256
docker load -i .\research-assistant-0.4.1-v14-20261023.tar
docker image inspect research-assistant:0.4.1-v14-20261023 --format '{{.Id}} {{.Os}}/{{.Architecture}} {{.Config.User}}'
```

tar 包含官方基础镜像层、SDK、固定版本依赖及 Agent；不需要另外导入基础镜像或在线安装依赖。平台为 linux/amd64，用户 agent，服务监听容器 8080，提供 /health 和 /solve。

## 官方测试配置

正式模型网关、模型名、Key、可信会话及授权材料在运行时注入。不要把本地开发 Key 提交给平台。配置环境变量如下：

- PLATFORM_LLM_URL、PLATFORM_API_KEY、AGENT_MODEL_NAME：按平台要求注入。
- RESEARCH_WORKSPACE_ROOT：派题工作区，镜像默认 /data。
- RESEARCH_RESULT_ROOT：可写输出根目录，镜像默认 /output。
- RESEARCH_WORKSPACE_TEAM：本队目录名，需与派题路径匹配。
- RESEARCH_CONFIG：可选，指向运行时挂载的 JSON 配置；通过 material_roots 配置独立授权论文目录。
- 正式 D 保持 experiment_mode=platform，使用官方可信会话和 runner；不得改为旧公开实验 local 模式。

启动命令已经内置。默认单题预算为 180 秒、30 次调用、40,000 Token。正式环境不要设置本地禁用遥测选项。

镜像不包含本地 Key、论文库、历史答案和本地实验算法夹具。依赖、源码一致性、非 root 运行及 1 CPU / 1 GiB 容器的健康和接口冒烟检查已通过；正式 D 来源凭证、隐藏输入与榜单分数仍须官方测试。

## 新旧版本对比

将本镜像单独登记，保留旧版本。每次记录完整镜像标签、ID、tar 哈希和平台 submission_id，再记录总分、分项、失败题、耗时和 Token。在官方允许的同题、同资源、同模型条件下比较；不同派题的总分只作参考。新版不保证比旧版得分高。

这是只交付镜像的 Release。GitHub 自动生成的 Source code 是仓库基线，不是本镜像构建快照；请使用本页 tar，镜像身份以 delivery-manifest.json 为准。公开附件不包含源码归档、题目、论文和详细测试记录。
