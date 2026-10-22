# 科研助手本地诊断评测

本目录按赛事材料 `research-assistant-objective-v2` 的七项客观评分公式进行**本地近似评测**。`benchmark.py` 固定三套不同题目；每套含 A/B/C/D/E 各一题，使用公开真实论文和组委会公开 D 类小实验。每轮满分映射为 100，但只有每类一题，**不是正式 25 题自动榜分**，也不包含未知的专家与 Token 复核分。

## 论文与授权

论文从各自 arXiv 原始页面下载到 `local-workspace/papers/`，该目录只用于本地测试，已从 Git 与 Docker 上下文排除。题目只通过 `materials` 传入其授权 PDF；D 类只授权对应的公开 `publicdata/D-0N.json`。评测时不将其他材料作为 Agent 证据。

## 计分

| 类别 | 本地规则 | 满分 |
| --- | --- | ---: |
| A | 合法非空回答 3；合法非空澄清问题 2 | 5 |
| B | `clamp(15 × H/N − 0.75 × U, 0, 15)`，完整文件名大小写不敏感去重 | 15 |
| C 事实 | `clamp(15 × C/K − 3 × F, 0, 15)`，人工对照冻结事实和论文证据 | 15 |
| C 引文 | 五篇指定 PDF 同时有正文编号与参考文献条目，每篇 2 分 | 10 |
| D 流水线 | 本机八项可见运行检查，每项 2.5 分 | 20 |
| D 数据图表 | 公开参考值容差公式三项各 4 分；图表六项检查按比例分配 8 分 | 20 |
| E | 动机、路线、实验、数据集、量化指标、可行性各 1.5；瓶颈反思、文献互补各 3 | 15 |

`scoring.aggregate(..., mode="official_shape")` 强制每类五个结果；单题失败或超时为 0，未终止题阻止聚合。诊断模式只允许每类一题。未得到专家复核时 `composite_score = null`。

C/E 的判断写在 `evaluation/judgments/round-N.json`，每个要点保留可复核的文本位置；C 的严重事实错误需列原句和依据。C 引文覆盖和 D 文件证据由程序核对。D 的指标参考值来自公开 `research-experiments/references.json`，计时类指标受本机系统与依赖版本影响。平台私有基准、可信 syscall 记录及专家判断不可见，因此本机 D 满分不表示正式评测一定满分。

## 复跑

需要在本机安装项目依赖与 PDF 解析依赖，并在未提交的 `.env.local` 中配置模型网关。运行时不打印密钥：

```powershell
$env:PYTHONPATH="local-workspace/python-deps;src"
python -m evaluation.run_rounds --round 1
python -m evaluation.score_runs --round 1
python -m unittest evaluation.test_scoring evaluation.test_extraction
```

原始 JSON、模型调用记录、证据台账、实验产物和 `local-score.json` 保存在 `output/evaluation/round-N/`。评分脚本只在五题均已终止且 C/E 人工判断齐全时生成分数。

## 百篇论文库两轮测试

`library_benchmark.py` 固定第 4、5 轮题目。B 类授权 `paper_library/manifest.json` 中恰好 100 篇 PDF；C/E 仍按题目只授权指定五篇。第一轮测试检索增强问答，第二轮测试生物系统建模；D 分别执行公开 D-04 与 D-05。论文来源与哈希使用 `python scripts/verify_paper_library.py` 检查。

```powershell
python -m evaluation.run_library_rounds --round 4 --prepare-index
python -m evaluation.run_library_rounds --round 4
python -m evaluation.score_library_rounds --round 4
python -m evaluation.run_library_rounds --round 5 --prepare-index
python -m evaluation.run_library_rounds --round 5
python -m evaluation.score_library_rounds --round 5
```

历史原始诊断分为 89.96 和 45.00。第 5 轮已在独立目录 `round-5-retest/` 完整复测，得 94.00/100（A 5、B 15、C 25、D 40、E 9）；原始结果保持不变。E 类后续单题优化分别保存在 `round-5-followup*/`，不合并冒充完整轮次成绩。详见 `docs/2026-10-17-百篇论文库复测与优化复盘.md`。

全量百篇冷启动为 358.90 秒，超过默认单题预算；B 类现只为候选筛选索引每篇前八页，C/E 仍用授权全文。限页冷启动为 119.37 秒，B-04 与 B-05 定向实测均为 15/15。E-05 第三次定向实测按八项本地要点计 15/15；这些是单题测试，不组成新的完整轮次成绩。资源测量与质量局限详见上述复盘文档。
