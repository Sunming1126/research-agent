# 赛题一 D 类公开小实验材料

这是 `objective-v2-20261010` 的 D01--D05 公开实验包。它是组委会提供的
可离线运行的小规模标定实验，不是题目所列论文的原始 benchmark 复现，也不
包含官方私有答案、gold reference 或评分配置。论文只用于说明实验主题，实际
数据、seed、切分、算法参数和指标口径都以本目录为准。

## 运行协议

固定 seed 为 `42`，单进程、离线 CPU 执行。官方 runner 使用 Python 3.9；推荐
依赖版本见 `requirements.txt`。入口命令为：

```text
python experiment.py --case D-01 --output /output --data /data \
  --team workspace_team --run-id run-id-D01 \
  --task-id TASK_D_01_SINGLE_MACHINE
```

`D-01` 到 `D-05` 使用相同 CLI。`--data` 必须指向本包的 `publicdata/` 挂载目录，
不得从网络下载数据或模型。`--team` 只用于公开图表相对路径，脚本会拒绝路径
分隔符并将其他字符规范化。

脚本会真实写入以下产物：

* `/output/metrics.json`：排序后的紧凑 JSON，包含 `run_id`、`task_id` 和三个指标；
* `/output/submission.json`：只含 `case_id`、`status`、`metrics`、`figures`；
* `/output/data/source_data.csv`：本次读取数据及实际计算结果的公开源数据；
* `/output/data/figure/<team>/D-0N.png`：本次运行生成的 PNG；
* `/output/command.json`：实际命令、版本和实验口径的公开运行记录。

stdout 使用 JSON Lines。`data_preprocess` 和 `result_generated` 是实际执行阶段，
`loop_iteration` 在每个真实循环完成后发出，便于平台从 syscall、file_read 和
结构化运行事件确认过程。脚本不从 stdout 伪造 `file_read` 或 `file_write` 事件；
这些事件应由平台采集。

## 五题设计与指标口径

| 题号 | 公开小实验 | 三个保留指标 | 公开数据与可信产物 |
| --- | --- | --- | --- |
| D-01 | 24 条三类短文本，TF-IDF+Logistic Regression 对多数类基线 | `Macro-F1`（分数）、`Accuracy`（%）、`运行时间`（秒） | `D-01.json`、训练/测试行和两种模型的实际分数 |
| D-02 | 12 篇本地文档和 6 条查询的 BM25 排序 | `Recall@5`（%）、`MRR@10`（分数）、`查询吞吐`（queries/s） | `D-02.json`、每条查询真实排名和 qrels |
| D-03 | 固定 token 序列上的轻量 NumPy 序列状态推理，比较无缓存/前缀缓存 | `推理吞吐`（samples/s）、`P95 延迟`（ms）、`缓存命中率`（%） | `D-03.json`、两种执行模式每个样本的耗时 |
| D-04 | 36 条二维分类数据上的 3x2 网格搜索和约束筛选 | `验证集准确率`（%）、`约束通过率`（%）、`搜索耗时`（分钟） | `D-04.json`、每个网格候选的验证结果和约束状态 |
| D-05 | 固定到达/服务事件的 FIFO 与公平调度离散事件仿真 | `公平性 Jain 指数`（分数）、`归一化吞吐`（%）、`平均等待时间`（ms） | `D-05.json`、每个策略真实完成数和等待时间 |

指标从推荐策略（D01 的 TF-IDF 模型、D02 BM25、D03 前缀缓存、D04 约束搜索、
D05 公平调度）产生；图表同时展示基线或对照策略。所有 `reference` 和
`relative_error` 均为 `null`，因为本公开实验没有可宣称的论文对照值。

## 标定

在具有推荐依赖的环境中运行：

```text
python calibrate.py --data publicdata --output calibration
```

标定器会为 D01--D05 分别创建临时输出目录，调用同一个 `experiment.py`，校验
JSON schema、三个指标的名称/单位、PNG/CSV、迭代事件数量，并写出
`calibration.json`。它记录实际 Python/依赖版本、墙钟耗时和产物 SHA-256；它
不读取或生成隐藏答案，也不修改官方项目文件。

## 边界

本包不实现 HTTP、平台服务、Gateway、可信 syscall 采集或正式评分。Agent 仍
必须通过官方 `/solve` 流程调用公开 runner；runner 负责隔离容器、真实文件读写
观察和 run/task 身份绑定。D 类成功只能表明公开小实验在该环境完成，不表示
复现对应论文的原始数据集、模型或 benchmark。

## 云端执行与实测参考（2026-10-10）

平台标准目录是 `repo/experiment.py` 和 `data/`；根目录脚本/publicdata 为同内容的本地开发兼容入口。平台内使用 `python /data/experiments/run_experiment.py --case <当前D题>`，认证和Unix通道由平台注入，无公网调用。

本版参考在1CPU/1024MiB、Python3.9.25、冻结依赖和strace采集条件下，每题独立实测3次，取中位数；公开 `references.json` 包含参考、样本及容差。确定性指标1%，计时指标至少25%且覆盖测量离散度；这是工程小实验标定，不是原论文实测或正式高并发标定。实际判题只接受平台本轮采集的文件哈希与指标。
