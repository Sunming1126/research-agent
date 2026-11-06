# D专项可重放证据

本轮回归门禁完成：636通过、0失败、0错误、0跳过。仅layered_d_runner.py两处取消检查为生产修改；不是D-02/D-05官方提分结论。

- diagnostic-matrix.json：修复前30项假设、旧失败及原JUnit SHA；最初“待验证”字段保留为预注册记录。
- closure.json：30项原ID→现ID及实际结果、单因素范围、三轮配对和结论边界。
- native-pair-*.xml：原Harness新旧各三轮5项故障注入；旧版每轮2失败，候选每轮全过。
- native-full.xml：原总控首次全量635通过/1失败；缺失历史提示词对照文件的失败完整保留。
- codex-offline-full.xml：补齐固定Git历史文件后，Codex离线Linux完整回归636通过，零失败/错误/跳过。该执行不冒充原总控。
- native-proof-summary.json：原会话、原工作区及新鲜工具调用的摘要证明；完整原生证明本地保留。
- codex-offline-source-manifest.json、codex-offline-environment.json：源码、材料与环境指纹。
- evidence-sha256.json：证据文件校验清单。

运行候选为2e6f51a643acf66920612f83555a1c3a59d9d8b8，比较基线为dc9ef0f2c9d257d14536389cbf72fa0b3e89b9bd。生产修改来自24a222b；测试维护和实际生产修复分别归因。

归档协调器见evaluation/d_contract_20261106.py，与原目录镜像测试下入口同内容。它依赖原Agent挑战赛目录内的冻结镜像/材料/证据，真实模型仍只在原总控/magic。Codex无模型执行校验用户授权收据并单独记录来源。程序用排他登记防止排队任务与直接执行重复启动。

补齐挂载后只复跑失败所需的完整回归；六次配对通过源码/测试/材料/环境清单与原JUnit一致性核验后引用，没有伪装成新增实验。所有官方分数及Delta仍为null。
