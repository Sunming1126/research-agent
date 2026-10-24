# 0.4.2-hybrid-v6 最终本地验收

主轮122PASS/1官方BLOCKED，独立D恢复轮21PASS，源码与evaluation166PASS/1条件跳过，26个公开算法输入全部通过。固定历史缺陷集6/6拦截，实际触发的两次完整源码修复经独立公开检查2/2恢复；样本很少，不代表全部未知场景或官方分。

完整结论见“最终验收与量化报告.md”，逐条指标见quantitative-results.json；固定缺陷定义见known-defect-catalog.json。candidate-manifest.json与candidate-audit.json确认tar/SHA、源代码字节、重新导入同ID和新只读容器健康200。实际上传文件见“打榜提交说明.md”。

原始v4/v5失败和第一次v6独立轮两次magic连接失败的记录均保留。当前成功主轮目录沿用旧v5前缀-r2，以image-inspect.json的v6标签和不可变ID为准；独立恢复轮为v6-repeat2-d-repeat。execution-toolkit保留实际执行的原始协调器字节。

全部在既有Harness Agent挑战赛的同一“科研助手镜像验收总控”原生对话中调度，magic API每题日志单独保存。完整公开证据随v0.4.2-hybrid-v6-20261023预发布交接；不含供应商Key、个人连接配置或原生账号会话。

正式Gateway来源准入、可信实验、私有题与官方分待平台；本次尚未上传赛事或开始打榜。
