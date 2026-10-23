# 原生Harness验收入口与历史比较工具

当前候选0.4.2-hybrid-v6。数据测试固定在用户已有工作区的同一个原生主对话，由其pwsh工具执行native_magic_run.py。regression运行离线源码/evaluation回归，使用既有只读test-deps与公开材料；all依次运行主轮、独立D轮和tar校验；d-repeat仅用于明确的新独立恢复轮，不覆盖旧目录。

本机主轮122PASS/1官方BLOCKED，独立恢复轮21PASS，166项源码/evaluation通过、1条件跳过，固定缺陷6/6与真实修复2/2均是小样本。最初独立轮的两次模型连接失败和v4/v5算法失败保留。原始执行代码在证据包各run/execution-toolkit，SHA与actual-toolkit-sha256.json一致；当前工具修正了后续目录命名并补充脱敏传输错误类型日志。

本目录不是完整材料工作区。队友复测需准备与公开复跑包一致的目录和论文，导入指定tar，并绑定自己的既有Harness工作区/主对话；本机ID与Windows路径不能照搬，不能伪造归属记录。magic连接仅从本机现有配置只读解析，供应商Key内存认证；不复制个人配置，不安装插件或改profile。旧headless入口仅保留历史证据，后续不要沿用。

交付物、完整公开证据、失败与修复历史见v0.4.2-hybrid-v6-20261023预发布和docs/testing/2026-10-23-hybrid。正式Gateway/私有题和官方分仍待平台，上传未执行。
