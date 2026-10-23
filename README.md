# 科研助手 Agent

当前融合候选 **0.4.2-hybrid-v2**：基于双镜像对照，保留 source-v1 的 D 可靠性并吸收队友 v14 的证据/报告改进，修复可选补检预留、标准库自测兼容与 D05 丢弃优先级探针。构建及真实模型验收单独记录，详见[融合依据](docs/2026-10-23-融合优化与项目论文依据.md)；下方0.4.1为已完成历史基线，不代表融合版成绩。

2026-10-23 可靠性更新至 **0.4.1**：扩展 D-05 公开边界自测，修复反馈携带失败源码和具体输入，保留每次模型生成版本；增加 D-01 重复性检查、正式客户端取消清理及平台错误分类。参考依据与实现范围见 [优化说明](docs/2026-10-23-开源与论文参考及D类可靠性优化.md)。实际验收与镜像交付结果以本次独立报告为准，历史分数不代表新版成绩。

本次[验收记录、已知失败和队友复测入口](docs/testing/2026-10-23/README.md)随代码同步。镜像tar与源码构建快照通过GitHub Release分发，校验值记录在验收目录。

[0.4.1镜像及校验文件下载](https://github.com/Sunming1126/research-agent/releases/tag/v0.4.1-source-v1) · [代码审查与讨论 PR #1](https://github.com/Sunming1126/research-agent/pull/1)。此版本为本地验收预发布，尚未合并主分支或进行官方比赛提交。

基于官方 `agenthack/base:1.1.1` 的科研助手。正式路径支持 v3/11 题派题，兼容历史 25 题测试；包含 A/B/C/D/E 处理器、授权材料定位、页码切分、混合检索、证据台账、报告审查、layered-d-v3 实验执行和提交预检查。

## 工程目录

```text
agent.py                    # 官方 SDK 的 HTTP 入口
src/research_agent/
  core/                     # 配置、派题、题目上下文、调度和安全文件操作
  rag/                      # 授权材料解析、页级切分、索引和检索
  workflows/                # A/B/C/E 任务处理与 D 类实验执行
  integrations/             # 模型网关和平台工具调用适配
  contracts/                # 结果 Schema 与产物校验
tests/                      # 离线回归与合成材料
scripts/                    # 联调、标定和回归入口
evaluation/                 # 公开论文诊断题、人工复核与本地评分
paper_library/              # 本地百篇真实 PDF 与来源清单，不进入镜像
科研助手赛题材料/             # 原始赛事材料与公开实验包
```

实现代码使用必要的中文注释说明授权范围、证据来源和提交约束；测试材料、运行产物及本地密钥不会进入正式镜像。

**本地开发已接入用户指定模型；正式评测需使用赛事发放的模型网关、材料目录及实验会话。** 不把本地模拟题成绩当作官方评分。

## 快速使用

当前源码为 `0.4.1`。2026-10-22 的三轮 11 题仿真属于历史 0.3.1，见 [三轮测试报告](docs/2026-10-22-三轮仿真与逐轮优化.md)，不能当作本次镜像成绩。历史 `team_share/image/` 中的 `0.3.0-source-v1` tar 不包含当前优化；本次镜像与验收清单另存于交付目录。

建议使用 Docker，保持官方 SDK 和 Linux 运行环境。先导入组委会基础镜像，再执行：

```powershell
docker build --platform linux/amd64 -t research-assistant:0.4.1-source-v1 .
```

本地密钥位于 `.env.local`，通过 `--env-file` 注入。文件已从版本跟踪和 Docker 构建上下文排除。镜像中不包含密钥、论文、题目、测试答案或本地输出。服务器不会自动读取本机密钥文件。

将论文放到 `local-workspace/papers`，将 `config.container.example.json` 中材料描述别名改为实际题目采用的描述，然后：

```powershell
docker run --rm --init --name research-agent-local --cpus 1 --memory 1024m --env-file .env.local -e RESEARCH_CONFIG=/app/settings.json -e AGENTHACK_TELEMETRY_REQUIRED=false -p 127.0.0.1:18080:8080 -v "${PWD}/config.container.example.json:/app/settings.json:ro" -v "${PWD}/local-workspace:/workspace:ro" -v "${PWD}/output:/output" research-assistant:0.4.1-source-v1
```

正式平台不设置关闭遥测的开发选项。按平台公告提供配置和模型环境变量。

HTTP 接口使用官方 SDK 信封：

```json
{
  "task_id": "my-test",
  "constraints": {"timeout_ms": 300000},
  "input": {
    "case_id": "A-01",
    "function": "Chat",
    "title": "城市绿地科研对话",
    "input": "解释城市绿地与地表温度的可能关系，并提出一个关键澄清问题。",
    "materials": [],
    "constraints": [],
    "expected_output": "回答和澄清问题",
    "output_schema_ref": "Chat"
  }
}
```

发送至 `POST /solve`。SDK 返回 `output.answer`，其值是可解析的完整题型 JSON 字符串；落盘的题型 JSON 没有 SDK 包装。这样避免 SDK 把普通字典隐式变成 Python repr。健康检查为 `GET /health`。

整轮请求的 `input` 可使用 `{"dispatch": true}`。它读取配置工作区下 `data/<team>/dispatch.json`，按当前 schema 动态校验题目身份、类别、SHA-256 和本队 assigned 路径；v3 正式派题为 11 题，历史 v2/25 题仅用于兼容测试。整轮调用需要平台允许足够的请求超时；平台逐题调用时直接传题目对象。

## 配置与运行方式

`config.example.json` 用于本机 CLI；`config.container.example.json` 用于容器。路径以配置的工作区为基准；工作区必须是包含 `data/` 的目录，不要重复添加一层 `data`。

| 配置 | 含义 |
| --- | --- |
| workspace / RESEARCH_WORKSPACE_ROOT | 派题与题目工作区根目录 |
| output / RESEARCH_RESULT_ROOT | 产物根目录 |
| team / RESEARCH_WORKSPACE_TEAM | 平台分配的本队目录名，不能含路径分隔符 |
| material_roots / RESEARCH_MATERIAL_ROOTS | 专用材料目录；不能将整个工作区、整个 data 或输出目录作为论文库 |
| material_aliases | 题目材料描述到具体文件/目录的显式映射；无法定位时不会扩大检索范围 |
| experiment_mode | `platform` 为默认正式模式；`local` 仅用于公开实验开发 |
| local_experiment_root / local_python | 本地冻结实验包及可选解释器 |
| llm_calls / llm_tokens / case_timeout | 每题模型调用、Token 和耗时预算，默认 30 / 40000 / 300 秒；按平台配额收紧 |
| ocr | 默认关闭；启用需预装 Tesseract 和 eng/chi_sim 语言包，否则扫描页明确报错 |

模型配置为 `PLATFORM_LLM_URL`、`PLATFORM_API_KEY`、`AGENT_MODEL_NAME`。本地开发可选 `RESEARCH_LLM_THINKING=disabled`；它只在显式设置时发送供应商扩展参数，换赛事网关时应移除。参数依据：[火山方舟思考模式文档](https://docs.volcengine.com/docs/ark/deep-thinking?lang=zh)。

本机 CLI 先安装依赖与本项目包（建议在虚拟环境中执行）：

```powershell
python -m pip install -r requirements.txt
python -m pip install -e .
```

然后运行：

```powershell
python -m research_agent case --config config.example.json --env-file .env.local --input path/to/case.json
python -m research_agent batch --config config.example.json --env-file .env.local
python -m research_agent precheck --config config.example.json
```

CLI 为显式本地开发模式，不生成平台可信遥测。正式 HTTP 使用 SDK 的 `@tool` 和模型调用采集。

## 文档、召回和证据

- 按明确文件名或显式授权目录定位；重名、大小写重复、缺文件、路径越界和符号链接均拒绝。没有“找不到文件就搜索全部论文”的兜底。
- PDF 验证文件签名，按物理页码提取正文块；中文片段约 850 字、英文约 1800 字符，重叠 110 字符。长段落实际拆分，表格/图注尽量保持连续，参考文献段不参与事实召回。
- 物理 PDF 页码与论文印刷页码可能不同；内部和输出统一以从 1 开始的 PDF 物理页码定位，不猜测印刷页码。
- 本地中文双字词、英文词法 BM25，中英文查询改写、标题/摘要/章节字段加权，再用模型根据正文证据进行论文级筛选。B 类至多返回 10 篇；错误候选不靠凑数保留。
- C/E 先逐篇选择摘要与跨页证据片段，再提取带原文短摘录的事实。摘录核验只容忍 PDF 的换行、连字符和连字字形差异，仍要求原文连续字符；正文生成后执行引用校验及独立模型审查。C 类将已核验事实逐篇列出，C/E 在模型修复失败时使用有证据的保底文本。
- B 类百篇候选库先索引每篇前八页，C/E 读取授权论文全文；缓存按文件内容哈希、文件名、切分版本、OCR 与页数上限隔离。每题重新过滤授权材料，缓存和证据只写本队 work 目录。
- 模型审查不能证明所有语义正确；高分质量仍需用真实公开论文评测。测试通过证明工程流程与已覆盖场景，不保证正式成绩。

## 实验模式与输出

正式 D 类 v3 调用 `/data/experiments/run_experiment.py --case <当前题> --implementation <本队源码>`，验证平台注入的当前 case。新版 `d-model-source-v1` 下，源码必须由当前题 Gateway 生成并原样保存；Agent 会保存完整模型响应，提取唯一完整的 `run_experiment(request)`，然后由当前平台客户端申请来源凭证。镜像内的 `d_algorithms/` 仅用于公开离线夹具，Docker 构建会排除它，不能冒充正式源码。缺少会话或模型来源时返回 `materials_missing/failed`，不会转而运行本地算法冒充正式结果。

本地 D 类须显式指定公开实验包，真实执行冻结 `experiment.py`，保存 stdout、stderr、metrics、CSV、PNG 和 command。对指标名称和单位逐项比对，不把别的指标数值换个名称提交。图表复制前后校验哈希，completed 必须有当前运行证据。

当前本地容器 Python 版本来自官方 Agent 镜像（3.11），公开材料声明的正式实验 runner 为 Python 3.9。已保存本地实际版本，计时指标不能替代正式环境测量。

产物布局：

```text
<output>/data/<team>/<case_id>.json
<output>/data/<team>/work/<case_id>/<run_id>/
<output>/data/<team>/work/document-cache/
<output>/data/expert/C-xx-综述-<team>.md
<output>/data/expert/E-xx-灵感Idea报告-<team>.md
<output>/data/figure/<team>/
```

JSON 和 Markdown 原子写入后回读。收据保存产物哈希、模型 Token 记录、实验来源和题目身份。预检查拒绝缺失/额外结果、正文不一致、文件被篡改和不合法 Schema；不会自行计算官方分数。

材料或模型失败会记录 work 诊断，且不提交凑字数报告。非 D 类失败由 SDK 返回运行错误；D 类按规范返回 failed/materials_missing 和三项 null 指标。批次会继续其他题，并明确汇总失败。

## 验证与人工事项

三轮公开真实论文的本地诊断评估、赛事公式评分器和逐轮复盘见 `docs/2026-10-17-科研助手三轮真实论文评估.md` 与 `evaluation/README.md`。每轮 A–E 各一题，仅用于开发诊断；旧报告中的 25 题是当时的压力形态。根据 v19，科研助手正式赛题一为 11 题（A/B/C/D/E=1/3/2/3/2），正式派题以运行时 `dispatch.json` 为准。

新增的本地百篇论文库在 `paper_library/`，涵盖十个 arXiv 学科方向，清单记录来源、哈希和页数。可运行 `python scripts/verify_paper_library.py` 回读核验。两轮原始结果见 `docs/2026-10-17-百篇论文库两轮评测.md`，独立复测与后续优化见 `docs/2026-10-17-百篇论文库复测与优化复盘.md`；本地 PDF 不随源码或镜像分发。

运行 `scripts/run_checks.ps1` 可在禁网、1 CPU、1GB 容器中重跑测试。25 题集成测试对 A/B/C/E 使用明确标注的假模型与合成 PDF，对 D-01 至 D-05 实际运行公开脚本。真实模型测试脚本 `scripts/live_smoke.py` 会消耗 API 配额，仅发送合成测试论文。

- 自动：实现、索引、召回、证据与报告生成、实验执行、文件校验、镜像构建、本地测试。
- 人工/平台资料：赛事报名与队伍名、正式模型凭证、授权材料路径、配额、提交截止、可信实验会话。
- 人工最终决策：选择并上传正式版本、确认第三方依赖分发要求、抽查真实论文事实、根据正式反馈选择优化方向。

原始赛事材料保持原样。SDK、冻结实验和各依赖沿用各自许可；随源码交付保留依赖版本清单，发布前结合比赛要求检查分发义务。

