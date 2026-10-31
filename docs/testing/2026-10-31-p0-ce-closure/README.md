# P0 C/E源码候选：已完成的四项离线闭环

- [修复、量化结果与决策轨迹](P0四项修复闭环.md)
- [机器可读指标与未完成门禁](metrics.json)
- [全部执行及失败索引](execution-index.json)
- [最终受测应用源码指纹](candidate-source-files.json)
- `evidence/`：四个单因素旧新对照、完整回归、JUnit、网络护栏、合成schema diff与诊断。
- `e2e/`：两组分别保留的三轮官方SDK本地端到端试验；采用有完整环境指纹的最后一组。
- `toolkit/`：协调器、外连禁用护栏及旧版协调器。原授权见 `offline-authorization.json`。

复放要求：在用户已授权的Agent挑战赛工作目录，将toolkit协调器放入其`镜像测试`目录；使用同一Git基线及本机Python3.12/隔离依赖布局，不读取凭据。协调器调用示例：

```powershell
python 镜像测试/p0_ce_contract_20261031.py --stage baseline --suite recovery
python 镜像测试/p0_ce_contract_20261031.py --stage candidate --suite recovery
python 镜像测试/p0_ce_contract_20261031.py --stage baseline --suite wire --baseline-rev eb42a3934972548af3e784e8d2a362c5e6654cf2
python 镜像测试/p0_ce_contract_20261031.py --stage candidate --suite wire --baseline-rev eb42a3934972548af3e784e8d2a362c5e6654cf2
python 镜像测试/p0_ce_contract_20261031.py --stage regression --suite retry --baseline-rev c9c079d3c0c0371d0b369400ed9892096b05939d
python 镜像测试/p0_ce_e2e_20261031.py
```

脚本拒绝没有本轮明确授权记录的直接执行；Windows受限沙箱可能阻塞asyncio/SDK接口，须通过原生正常权限流程处理，不关闭护栏。基线首次预期失败，原始失败保留。该入口只用于离线合成夹具，不调用真实模型。

最终：439通过、6环境跳过、1已知xfail；固定C/E夹具交付旧3/12、新12/12。**这不是研究正确率、真实模型稳定性或官方分数。P0整体仍在推进，未发布包含修复的新镜像。**
