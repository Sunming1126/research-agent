# 0.4.13 C 类输入与接口修复

镜像：`research-assistant:0.4.13-c-20261026`。
文件：`team_share/image/research-assistant-0.4.13-c-20261026.tar`。
SHA-256：`3cd86ada7c1dd3782dac1c56e7c05e4bd7ca23e54d3d57faaaec0ddb9e92ff36`。
导出的 tar 已用 docker load 回读；测试镜像 ID 与源码核对记录一致。完整身份与测试摘要见 manifest.json。

## 已修复

- 本地 C 类测试 expected_output 使用输出要求描述，output_schema_ref 才是 Survey。
- 写作、审查、修订完整传入官方八字段；标题和输出要求也参与材料片段检索。
- 原始模型回复和八字段任务写入 report_trace，方便区分入口、模型响应与最终答卷。
- 模型正文类型或证据编号类型错误不再直接抛出未处理类型异常；首次 JSON 不合法/截断可在原有次数和时间预算内重新生成。不用空白或模板正文冒充成功。
- 本地请求的 Idempotency-Key 和 X-Research-Task-ID 使用同一 UUID。

## 八字段核对

| 字段 | 输入类型及处理 |
|---|---|
| case_id | 非空字符串，编号与 Survey/C 类一致；最终答卷原样保留 |
| function | Survey，用于路由 |
| title | 非空字符串，保留至写作/审查/修订，参与片段检索 |
| input | 非空字符串，原样传递题目要求 |
| materials | 字符串数组，按授权目录解析，原样保留在模型任务中 |
| constraints | 字符串数组，原样保留并参与检索 |
| expected_output | 非空字符串，具体输出要求，原样保留并参与检索 |
| output_schema_ref | Survey，与 function 一致 |

官方入口仍为 AgentRuntime；POST /solve 直接接收题目 JSON。最终响应只含 case_id、path、markdown，三个值都是字符串。目录使用 RESEARCH_WORKSPACE_TEAM，不使用真实报名队名。

## 最终镜像真实 API 实测

测试在官方 SDK 容器中通过 HTTP /solve 发起，使用官方模型 API，1 CPU / 1 GiB，PDF 与 assigned 题目只读挂载。题目自拟，不是平台隐藏题。

| 题目 | HTTP | 秒数 | 模型调用 | 正文字符 | 引用次数 | 格式与文件 |
|---|---|---|---|---|---|---|
| C-01 | 200 | 108.067 | 2 | 2301 | 28 | 通过 |
| C-03 | 200 | 98.253 | 2 | 2486 | 36 | 通过 |

检查了返回三字段、实际 JSON 文件、Markdown 文件、全文逐字一致、至少五篇指定 PDF 引用、正文长度、引用上限、官方 SDK 运行追踪。98 项相关自动测试通过（72 项报告回归、9 项输出/材料契约、17 项原生 SDK HTTP 回归）。宿主机缺少依赖及单元测试未进入 SDK 上下文造成的测试启动失败，已改用镜像环境及对应测试模式验证；未修改官方 SDK。

原始请求、HTTP 原始响应、独立检查结果：`output/official-live-0413-c-final-20261026/cases/`。
实际保存文件：同目录下 `agent-output/data/workspace-live-audit/` 与 `agent-output/data/expert/`。
源码、SDK 与凭据隔离校验：`output/release-0413-c/image-verification.json`。
输入到模型任务及文件一致性复核：`output/release-0413-c/validated-summary.json`。

## 边界

本次修复覆盖已确认的字段遗漏和可复现模型格式异常。平台 eval-20261026-db4bd5fd 未公开原始异常/答卷，因此不能声称已确定其唯一根因，也不能将本地通过当作平台隐藏题通过。

C-03 特意将要求分散在 title、constraints、expected_output，并使用绝对挂载路径。生成内容覆盖了“实验条件与局限”，但模型使用了带编号的三级标题，而不是自拟题目要求的精确二级标题；该内容遵循细节尚不完美，未将其计为官方三字段/文件校验失败。语义审查意见保留在 evidence.json，不承诺事实评分或内容满分。

含主题的“指定论文”描述仍需真实派题明确文件或提供授权映射，不会擅自扩大到全部论文。遇到真实模型超时或持续非法响应仍如实失败，不制造合规假答卷。
