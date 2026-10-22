# 运行依赖清单与 Docker 部署记录

更新日期：2026-10-20

## 当前运行时

- 模型客户端：`openai==1.109.1`。
- 网关协议：OpenAI 兼容的 Chat Completions；环境变量由 `.env.local` 提供，密钥不写入源码、日志、论文库或镜像。
- 文档解析：`PyMuPDF==1.24.14`，`pypdf==5.1.0`。
- 公开 D 类实验还使用 requirements.txt 中的 NumPy、SciPy、scikit-learn、Matplotlib、Pillow 等包。
- Matplotlib 的运行时传递依赖 `python-dateutil==2.9.0.post0`、`six==1.17.0` 也已显式固定；第 10 轮曾因遗漏它们导致 D 类失败，补齐后五个公开实验均真实返回 `completed`。
- 本地回归工具另见 `requirements-test.txt`（pytest 及其插件依赖），不装入正式运行镜像。
- `openai` 的传递依赖在 requirements.txt 中一并固定，便于基础镜像没有 SDK 时复现：anyio、httpx、httpcore、h11、pydantic、pydantic-core、jiter、certifi、distro、sniffio、tqdm、typing-extensions、typing-inspection、annotated-types、idna、colorama。

## 本机私有依赖目录

已将上述 Python 包安装到 `local-workspace/python-deps/`，仿真和 Python 3.12 回归通过 `PYTHONPATH=local-workspace/python-deps;src` 使用。该目录是开发缓存，不作为未知论文或密钥的容器输入。

## Docker 构建

`Dockerfile` 从 `agenthack/base:1.1.1` 安装根目录 `requirements.txt`，因此正式构建不依赖本机缓存。构建前应运行：

1. 检查 requirements.txt 中所有版本可在基础镜像的 Python 版本安装；
2. 构建后检查 `/health`，再用一条 A 或 B 公开题调用 `/solve`；
3. 正式平台网关环境变量覆盖本地配置，镜像不复制 `.env.local`。

## 后备通道

`src/research_agent/integrations/llm.py` 在 `openai` SDK 不可导入时使用标准库 `urllib` 调用同一 `/chat/completions` 接口。这只用于避免环境缺包时静默生成假答案；正式 Docker 仍应按 requirements.txt 安装 SDK，并保留网关短暂连接错误的有限重试。
