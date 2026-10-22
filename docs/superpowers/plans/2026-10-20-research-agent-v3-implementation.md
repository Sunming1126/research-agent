# Research Agent v3 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 将科研助手 Agent 从旧版 v2/25 题和旧 D helper 适配到官方 `research-assistant-objective-v3`、11 题派题和 `layered-d-v3` D 类公开框架。

**Architecture:** 用 `DispatchPlan` 解耦派题协议与处理引擎，v3 为正式路径、v2 为历史兼容路径；用 `LayeredDRunner` 负责复制本队可写算法源码、调用新版平台 client、归一化状态并保留可信产物；D-01/D-02/D-05 算法实现分别独立于外层 Reproduction JSON。

**Tech Stack:** Python 3.9 兼容标准库算法、现有 Python 3.12 开发环境、pytest、JSON/Markdown/PDF 现有管线；新版公开 D 包的 `protocol.json`、`method.md`、`worker.py` 和 `run_experiment.py`。

**Spec:** `科研助手赛题材料/x新/research-agent-spec.md`、`科研助手赛题材料/x新/research-description.md`、`科研助手赛题材料/x新/research-scoring-rules.md`、`科研助手赛题材料/x新/research-experiments/D类实验要求.md`。

## Global Constraints

- 正式派题使用 `schema_version: 3`、`rules_version: research-assistant-objective-v3` 和 A/B/C/D/E=1/3/2/3/2 的 11 个 case。
- Agent 必须按运行时 dispatch 的 case 数量生成和预检查结果，不自行补题。
- D-01、D-02、D-05 使用 `layered-d-v3`、seed=42、Python 3.9、单 CPU、1024 MiB、每阶段 20 秒。
- D 类平台命令必须传 `--case <case_id> --implementation <源码路径>`；算法内部返回 baseline/candidate/details，不直接填写最终指标。
- 最终 Reproduction JSON 仍只含 `case_id`、`status`、`metrics`、`figures`，指标恰好三项；未知数值为 `null`。
- 旧 v2/25 题公开测试继续可运行，但不得影响 v3 正式路径。
- 所有新增源码注释使用中文；不得读取内部金标、私有实例或其他队伍文件。

## Review Focus

- v3 `cases_per_category` 是对象而不是整数：由 `test_load_dispatch_v3_accepts_11_cases` 固定。
- 题号可以是 `A-100` 等三位后缀：由 `test_load_dispatch_v3_accepts_three_digit_case_id` 固定。
- 整轮只有 11 题且失败题仍要进入结果集合：由 `test_run_dispatch_uses_runtime_case_count` 固定。
- 新 runner 缺少 `--implementation` 或返回非 complete：由 `test_layered_runner_requires_implementation_and_normalizes_complete` 固定。
- D-05 任务既不能遗漏也不能重复，边界 drop 规则要稳定：由 `test_d05_conserves_jobs_and_applies_deadlines` 固定。

---

### Task 1: v3 动态派题与结果数量

**Files:**
- Create: `src/research_agent/core/dispatch_plan.py`
- Modify: `src/research_agent/core/dispatch.py`
- Modify: `src/research_agent/core/models.py`
- Modify: `src/research_agent/core/engine.py`
- Test: `tests/test_dispatch_v3.py`
- Test: `tests/test_agent_core.py`

**Interfaces:**
- Produces `DispatchPlan.from_dict(dispatch: dict, settings) -> DispatchPlan` with `case_ids`, `category_counts`, `schema_version`, `rules_version`, `difficulty_by_case`, and `cases`.
- `load_dispatch(settings) -> list[dict]` returns validated case payloads for both v3 and legacy v2.

- [ ] 写 v3 dispatch、三位后缀和动态数量的失败测试；运行测试确认旧实现因 schema v2/25 限制失败。
- [ ] 实现 `DispatchPlan`，v3 校验 11 个题号、类别计数、`difficulty`、题目哈希和 `question_file` 授权边界；保留旧 v2 分支。
- [ ] 修改 `load_dispatch` 使用计划对象，取消 25 题硬编码；保持 `CaseContext` 的输入 Schema 校验。
- [ ] 修改 `run_dispatch` 和 `precheck` 从计划对象读取期望题数、结果文件集合和类别计数。
- [ ] 运行 `tests/test_dispatch_v3.py tests/test_agent_core.py -k dispatch`，确认 v3 新测试与既有兼容测试通过。

### Task 2: layered-d-v3 算法实现

**Files:**
- Create: `src/research_agent/workflows/d_algorithms/__init__.py`
- Create: `src/research_agent/workflows/d_algorithms/d01.py`
- Create: `src/research_agent/workflows/d_algorithms/d02.py`
- Create: `src/research_agent/workflows/d_algorithms/d05.py`
- Test: `tests/test_layered_d_algorithms.py`

**Interfaces:**
- `run_d01(request: dict) -> dict` returns `baseline`, `candidate`, `details`.
- `run_d02(request: dict) -> dict` supports `phase=run` and `phase=rank`.
- `run_d05(request: dict) -> dict` returns FIFO/fair traces and details.

- [ ] 写 D-01/D-02/D-05 最小公开样例、空输入、边界输入和 Schema 失败测试；运行确认空模板不能通过。
- [ ] 实现 D-01 的训练集词表/IDF、TF-IDF+bias、40/100 轮 protocol 梯度更新、字典序类别和并列规则。
- [ ] 实现 D-02 的独立 title/body BM25、27 组参数、分组 macro 选参、run 和 rank 阶段。
- [ ] 实现 D-05 的事件顺序、FIFO/加权公平、setup、deadline、horizon、任务守恒和 Jain/吞吐/等待所需轨迹。
- [ ] 运行专项测试，确认所有公开边界输入输出可 JSON 序列化且没有 NaN/Infinity。

### Task 3: 新版 D runner 与 Reproduction 证据

**Files:**
- Create: `src/research_agent/workflows/layered_d_runner.py`
- Modify: `src/research_agent/workflows/experiments.py`
- Modify: `src/research_agent/core/config.py`
- Modify: `src/research_agent/contracts/validation.py`
- Test: `tests/test_layered_d_runner.py`

**Interfaces:**
- `LayeredDRunner.prepare_implementation(case_id, work) -> Path` copies the correct algorithm source into the case work directory.
- `LayeredDRunner.run(case, specs) -> dict` returns the existing outer Reproduction Schema and writes current-run proof.

- [ ] 写命令参数、implementation 源码大小、RPC `complete`、失败 null 指标、图表路径和部分图保留测试；运行确认旧 runner 不满足新契约。
- [ ] 从显式配置的新版实验根目录读取 D-01/D-02/D-05 公共框架，复制本队源码并调用 `--implementation`；平台模式继续使用 `/data/experiments`。
- [ ] 归一化 RPC `complete` 为外层 `completed`，分离 `submission_status=valid/evidence_pending` 等判分状态，不把判分状态添加到最终 JSON。
- [ ] 保留 stdout/stderr、源码哈希、实验产物和图表哈希；任何未核验指标保持 null，禁止旧结果借用。
- [ ] 用新版公开 client/fixture 运行 runner 测试，确认命令和证据校验通过。

### Task 4: Agent 集成、镜像和本地 v3 联调

**Files:**
- Modify: `src/research_agent/core/engine.py`
- Modify: `README.md`
- Modify: `AGENT.md`
- Modify: `docs/依赖清单与Docker部署.md`
- Create: `scripts/build_v3_dispatch.py`
- Create: `scripts/run_v3_public_d_smoke.ps1`
- Test: `tests/test_v3_integration.py`

**Interfaces:**
- `/solve` accepts one v3 case or `{ "dispatch": true }` and returns only current case result/summary.
- Local smoke script consumes `x新/research-experiments` without network access.

- [ ] 写 11 题合成 dispatch、HTTP `/solve`、整轮结果数和 D 公开样例集成失败测试。
- [ ] 接入 `DispatchPlan` 和 `LayeredDRunner`，更新配置项、工作目录和 Docker 复制/依赖说明。
- [ ] 运行新版公开 D-01/D-02/D-05 smoke，回读 JSON、三指标、图片、源码和运行记录。
- [ ] 更新中文注释、版本文档和 v3 适配记录，明确本地公开样例不等于官方榜分。

### Task 5: 全量验证

**Files:**
- Modify: `docs/新版官方材料对照-2026-10-20.md`
- Create: `docs/2026-10-20-v3适配验证报告.md`

- [ ] 运行专项测试、完整 pytest、compileall、dispatch 预检查和新版公开 D smoke。
- [ ] 记录每个命令的退出码、通过/跳过/失败数量和 Windows 环境限制。
- [ ] 检查 Docker 构建路径和依赖清单；若 Docker 引擎不可用，明确标注未完成项，不把本机测试写成容器验收。
- [ ] 在验证报告中列出仍需真实平台确认的模型网关、私有数据、可信回执和正式时间限制。

