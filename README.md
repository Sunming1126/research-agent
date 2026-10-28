# 当前自我优化候选 0.4.15

基于0.4.14/f42a9f7，增加B条件覆盖证据选择、命中位置连续截取、按输入预算分批及全局数量语义保护。真实测试发现并修复“同一篇”误作1篇上限的问题。原Harness中，8道自拟题同库同模型对照集合完全匹配由7/8到8/8，调用24到16，Token 47115到44055；另有首轮失败保留。最终Windows源码回归329通过、0失败、6跳过。**这不是官方金标或镜像验收，没有新tar，不提交打榜。** 结果与限制见[本轮验收与交接](docs/testing/2026-10-29-b-evidence-budget/最终验收与队友交接.md)。

## 上一候选 0.4.14

独立分支 `improve/stability-b-retrieval-20261027`：修复取消、部分发布和收据不一致，改进B文档级排序融合、子题候选保留与后续失败的部分结果处理。原Harness中的Windows源码回归为 **282通过、0失败、6跳过**，同一组六条故障反例旧版全失败、候选全通过。**Linux新镜像验收和B真实模型提分对照尚未完成，没有新的已验收tar。** 当前只自我优化，不提交打榜。证据、跳过范围和交接步骤见[验收结果与量化](docs/testing/2026-10-27-stability-and-retrieval/验收结果与量化.md)。

# 平台工作目录配置

`<team>` 是平台分配的工作目录标识，正式运行必须使用注入的 `RESEARCH_WORKSPACE_TEAM`，不能写报名队名“最初一舞”，也不能使用遥测身份 `TEAM_ID`。本地可用 `TEAM_NAME` 或配置的 `team`；无配置时使用测试兜底 `workspace_team`。镜像不预设工作目录标识。

答案写入 `/output/data/<workspace_team>/<case_id>.json`，C/E 报告命名和 D 图片目录使用同一个标识。论文默认挂载 `/data/papers`，可显式设置 `PAPERS_PATH` 或 `RESEARCH_MATERIAL_ROOTS`。平台派题和当前 assigned 位于 `/output/data/<workspace_team>/`，正式 Single 请求会检查本题哈希与请求一致性，不读取其他 assigned。

0.4.6-team-20261024 是写死真实队名的旧版本，已确认不能据此视为正式接入正确。

# 科研助手 Agent

0.4.11 保留B/C/D修复，补齐E的阶段/传输时间预留与完整文本免网格解析；统一组合引用检查，精确定位错误并合并修订；修订保留题目观察、约束及邻段引用证据。审查即使返回ok=true，只要仍列出问题也会修订。E完整模型稿通过格式、引用编号等硬检查后，语义自审未通过会如实记录model_report_needs_review并交付原模型稿给平台评分，不再误报为接口失败；未生成完整稿或硬检查失败仍报错，禁止模板补写。自审与内容分不同，真实实测与限制见发布记录，不保证隐藏题得分。

0.4.10 保留0.4.9的B类修复；C类读取完整文本但不重建表格网格，各模型阶段为写作、审查、保存预留时间，未知引用编号仅在本篇连续原文唯一匹配时恢复。D类识别指标的多种明确写法，与当前挂载protocol交叉核对，实验目录不进入论文解析；有当前运行证据的失败部分结果予以保留，客户端原始结果另存供核查。正式平台是否恢复须以新评测及具体异常信息为准。

0.4.9 专项修复 B 类候选库的故障隔离、明确 PDF 范围解析、缓存重建与失败答卷保存。B 的失败原因保留在本队工作目录、响应头和真实 SDK 失败追踪中，JSON 仍只有 `case_id,papers`；空答卷不表示检索成功。材料重名冲突不猜测选择，不能保存或派题核验失败时仍返回错误。

0.4.8修复整体论文库材料同义描述，以及题面逻辑目录`data/论文库`与挂载`/data/papers`的有界映射。报告合并多篇授权证据提取，仍逐条核验原文、禁止跨论文绑定、进行事实与写作审查；无表格标题的页面直接保留文本块，减少解析耗时。内部时限保留落盘与SDK导出的余量。官方API实测和限制见本版release记录。

基于官方 `agenthack/base:1.1.1` 的科研助手。正式路径支持 v3/11 题派题，兼容历史 25 题测试；包含 A/B/C/D/E 处理器、授权材料定位、页码切分、混合检索、证据台账、报告审查、layered-d-v3 实验执行和提交预检查。

## 工程目录

```text
agent.py                    # 官方 SDK 的 HTTP 入口
src/research_agent/
  core/                     # 配置、派题、题目上下文、调度和安全文件操作
  rag/                      # 授权材料解析、页级切分、索引和检索
  evidence/                 # 事实支持核验、覆盖矩阵、引用渲染和报告约束
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

2026-10-24 源码为 `0.4.8`：通过原生官方 `AgentRuntime` 启动，八字段题目进入原生 SDK 执行，成功的 HTTP 响应直接返回题型 JSON。SDK 真实追踪元数据放入响应头；原生 SDK 代码、路由和 OTLP 导出保持原样。修复 0.4.3 的最外层包装体与赛题输出 Schema 不一致的问题，并增加新公布的 C/E 正文上限及材料路径适配。旧 0.4.3 镜像不作为格式合格版继续使用。

2026-10-23 的 `0.4.1` 修复了报告审查与修订预算分配，增加本篇方法定义优先召回、跨论文简称归因检查，以及拟议实验条件和数字的区分。修复与真实复测见 [0.4.1 修复记录](docs/2026-10-23-0.4.1失分修复与复测.md)。0.4.0 架构改动保留在 [升级与验证报告](docs/2026-10-22-0.4.0升级与验证报告.md)，原始 92.2044 分保留在 [完整仿真评分](docs/2026-10-23-0.4.0完整仿真评分.md)。历史 `0.3.0-source-v1` tar 不包含后续修复；旧 0.4.1 tar 不包含 0.4.2 的直接请求接入修复。

建议使用 Docker，保持官方 SDK 和 Linux 运行环境。先导入组委会基础镜像，再执行：

```powershell
docker build --platform linux/amd64 -t research-assistant:0.4.8-api-20261024 .
```

本地密钥位于 `.env.local`，通过 `--env-file` 注入。文件已从版本跟踪和 Docker 构建上下文排除。镜像中不包含密钥、论文、题目、测试答案或本地输出。服务器不会自动读取本机密钥文件。

将论文放到 `local-workspace/papers`，将 `config.container.example.json` 中材料描述别名改为实际题目采用的描述，然后：

```powershell
docker run --rm --name research-agent-local --cpus 1 --memory 1024m --env-file .env.local -e RESEARCH_WORKSPACE_ROOT=/workspace -e RESEARCH_CONFIG=/app/settings.json -e AGENTHACK_TELEMETRY_REQUIRED=false -p 127.0.0.1:18080:8080 -v "${PWD}/config.container.example.json:/app/settings.json:ro" -v "${PWD}/local-workspace:/workspace:ro" -v "${PWD}/output:/output" research-assistant:0.4.8-api-20261024
```

正式平台不设置关闭遥测的开发选项。按平台公告提供配置和模型环境变量。

正式 HTTP 接口直接接收八字段题目 JSON：

```json
{
  "case_id": "A-01",
  "function": "Chat",
  "title": "城市绿地科研对话",
  "input": "解释城市绿地与地表温度的可能关系，并提出一个关键澄清问题。",
  "materials": [],
  "constraints": [],
  "expected_output": "回答和澄清问题",
  "output_schema_ref": "Chat"
}
```

发送至 `POST /solve` 后，成功响应直接返回当前题型 JSON，例如 Chat 仅含 `case_id`、`answer`、`clarifying_question`，与保存的 JSON 一致。`X-AgentHack-Trace-Id`、`X-AgentHack-Session-Id` 和 `X-AgentHack-Metadata` 响应头复制 SDK 实际生成的追踪元数据，不在答卷顶层增加字段。业务失败使用 HTTP 500，超时使用 HTTP 504；SDK 真实 metadata 保留在响应头，不把 SDK 错误包装当作 HTTP 200 答卷。健康检查 `GET /health` 的 `telemetry_configured` 只表示已配置导出；正式环境保留平台注入的 OTLP 地址、认证及追踪头。

本地旧脚本的 `{"task_id":"my-test","input":<题目对象>}` 继续返回 SDK 包装格式。直接题目调用使用配置的单题时限，最多180秒。默认优先采用 `/data/papers`；未找到时兼容本地中文专用库，仍拒绝不明确的多库选择。授权根内的绝对材料路径可读，仍拒绝目录穿越、符号链接和其他队伍材料。指定子集的模糊文字不会自动扩展成整个论文库。

C 正文最多4500字符、40次引用，E 最多6000字符；计数包含空格、标点、Markdown，参考文献节不计正文。E 写作失败会保留诊断并报告失败，不再拼接硬编码保底正文。D 额外授权当前题的公开实验目录，正式成功仍需要当前会话的模型生成、自测和平台来源凭证。

整轮请求的 `input` 可使用 `{"dispatch": true}`。它读取配置工作区下 `data/<team>/dispatch.json`，按当前 schema 动态校验题目身份、类别、SHA-256 和本队 assigned 路径；v3 正式派题为 11 题，历史 v2/25 题仅用于兼容测试。整轮调用需要平台允许足够的请求超时；平台逐题调用时直接传题目对象。

## 配置与运行方式

`config.example.json` 用于本机 CLI；`config.container.example.json` 用于容器。路径以配置的工作区为基准；工作区必须是包含 `data/` 的目录，不要重复添加一层 `data`。

| 配置 | 含义 |
| --- | --- |
| workspace / RESEARCH_WORKSPACE_ROOT | 派题与题目工作区根目录 |
| output / RESEARCH_RESULT_ROOT | 产物根目录 |
| team / RESEARCH_WORKSPACE_TEAM | 平台注入优先，其次本地 TEAM_NAME/config.team；不使用真实队名或 TEAM_ID，不能含路径分隔符 |
| material_roots / RESEARCH_MATERIAL_ROOTS | 专用材料目录；RESEARCH_MATERIAL_ROOTS > PAPERS_PATH > 配置 > /data/papers；不能将整个工作区、整个 data 或输出目录作为论文库 |
| material_aliases | 题目材料描述到具体文件/目录的显式映射；无法定位时不会扩大检索范围 |
| experiment_mode | `platform` 为默认正式模式；`local` 仅用于公开实验开发 |
| local_experiment_root / local_python | 本地冻结实验包及可选解释器 |
| llm_calls / llm_tokens / case_timeout | 每题默认 30 次调用 / 40000 Token / 180 秒；D 正式模式最长 180 秒，包含生成、自测和正式实验；按平台配额收紧 |
| ocr | 默认关闭；启用需预装 Tesseract 和 eng/chi_sim 语言包，否则扫描页明确报错 |

模型配置优先读取 `CHALLENGE_LLM_BASE_URL`、`CHALLENGE_LLM_API_KEY`、`CHALLENGE_LLM_MODEL`，兼容 `PLATFORM_LLM_URL`、`PLATFORM_API_KEY`、`PLATFORM_LLM_MODEL`、`AGENT_MODEL_NAME`。使用 `CHALLENGE_SESSION_ID` 发送 `X-Session-ID`，不改写带会话路由的 Base URL。本地开发可选 `RESEARCH_LLM_THINKING=disabled`；它只在显式设置时发送供应商扩展参数，换赛事网关时应移除。参数依据：[火山方舟思考模式文档](https://docs.volcengine.com/docs/ark/deep-thinking?lang=zh)。

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

0.4.0 在正式锁源前增加公开样例子进程自测：检查预测与模型一致性、排名与分组选择、任务守恒和时间约束。失败时最多让模型重新生成一次完整源码，保存每次原始回复；正式请求发出后不再改源码。子进程不继承网关凭据；Linux 系统调用限制取决于 `libseccomp` 是否可用，隔离能力随自测记录保存。公开自测不能签发正式来源凭证或给出官方分数。

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

> 2026-10-27 自我优化候选：`0.4.14-stability-b-20261027`，基于 main c443883。改进取消/发布边界、保存回滚、B失败保留及文档级RRF。当前未生成新镜像、未上传平台，不能按版本号推定验收通过。方案与真实测试边界见 [本轮说明](docs/testing/2026-10-27-stability-and-retrieval/B检索优化方案.md)。旧0.4.13原始tar保留。

