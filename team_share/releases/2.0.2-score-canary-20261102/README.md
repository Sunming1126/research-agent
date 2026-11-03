# 2.0.2 score canary 提交候选

选择 `research-assistant-2.0.2-score-canary-20261102.tar`，不要选源码 ZIP，也不要仅凭同名文件认定版本。

- 大小：295482880 字节（约 282 MiB），低于平台 2 GiB。
- SHA256：`97d9583e4c7cb6981a959e7e79a5b401c9e9606d105ade1dcbdcca1bbbefc95f`。
- 镜像标签：`research-assistant:2.0.2-score-canary-20261102`。
- 镜像 ID：`sha256:fdfb5fbb2dcbfe488ee94775c53486badf6a4975c8883b562aeb22003144b728`。
- Linux/amd64，官方 AgentRuntime 入口，8080 端口。
- 已重新 docker load，逐文件核对生产源码与 `51e9514` 相同；基础镜像自带模板也与 `agenthack/base:1.1.1` 原样相同。

本候选曾正常上传为 `sub-20261102-303234b6.tar`，平台结构和 `/health` 检查通过，但账号无评测提交权限，未产生本候选官方分数。当前平台镜像已是另一个上传 ID，未经版本核对不要直接点击使用它评测。

镜像包含 C/E 规则、分题限时、Windows 授权路径修复和单独验证的 D-02 关闭推理候选。D-02 公开真实模型同输入成功 0/2→2/2；不能当正式提分。D-05 线上零分尚未证实修复，B/E 内容策略未混入本次 D-02 实验。

完整校验值和源码指纹见 `manifest.json`；正式采用须由新评测 ID、逐题分、总分和实际 Token 决定。不要把历史 75.8 或公开样例成绩归属给本候选。平台自动注入运行变量和凭据，交付不含个人配置或 Key。
