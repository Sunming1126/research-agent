# 科研助手官方 Docker 部署流程

本文按 `科研助手赛题材料/contest-basic-rules.md` 的要求整理，适用于 Windows Docker Desktop + PowerShell。正式提交前，以赛事平台最后发布的端口、环境变量、配额和上传规则为准。

## 0. 当前工程已经满足的官方约束

- 基础镜像：`agenthack/base:1.1.1`。
- 容器内服务端口：`8080`，由官方 AgentHack 运行时提供 `/health` 和 `/solve`。
- 依赖在构建阶段安装，运行阶段不下载依赖。
- 容器使用非 root 用户 `agent` 启动。
- 论文、题目、结果和本地密钥不会通过 Dockerfile 复制进镜像。
- 正式评测不能依赖主机文件或运行时联网下载；题目材料由平台按授权目录挂载。

相关文件：

- `Dockerfile`
- `requirements.txt`
- `config.container.example.json`
- `科研助手赛题材料/contest-basic-rules.md`

## 1. 启动 Docker Desktop 并确认架构

先启动 Docker Desktop，等待状态显示为 Running，然后在项目根目录执行：

```powershell
Set-Location C:\Users\sun\Desktop\anic
docker version
docker info --format '{{.OSType}}/{{.Architecture}}'
```

正式镜像必须构建为 `linux/amd64`。如果 `docker info` 报无法连接 Docker daemon，说明 Docker Desktop 尚未启动或当前用户没有 Docker 引擎权限。

## 2. 导入赛事基础镜像

项目中已保存基础镜像归档：

```text
build/agenthack-base-1.1.1.tar
```

首次使用或本机尚未有基础镜像时执行：

```powershell
docker load -i .\build\agenthack-base-1.1.1.tar
docker image inspect agenthack/base:1.1.1 --format '{{.RepoTags}} {{.Os}}/{{.Architecture}}'
```

输出应包含 `agenthack/base:1.1.1 linux/amd64`。如果基础镜像归档来自组委会的新版本，应使用组委会归档替换本地文件，不要自行从公共仓库猜测版本。

## 3. 构建参赛镜像

在项目根目录执行：

```powershell
docker build --platform linux/amd64 --progress=plain -t research-assistant:0.2.0 .
```

构建阶段允许访问 PyPI 以安装 `requirements.txt` 中的依赖；正式评测阶段不能在线安装依赖。构建后检查镜像信息：

```powershell
docker image inspect research-assistant:0.2.0 --format '{{.RepoTags}} {{.Os}}/{{.Architecture}} {{.Config.User}}'
```

应看到 `linux/amd64`，并且启动用户为 `agent`。

## 4. 本地启动容器

`.env.local` 只用于本地测试，不能提交到代码仓库。当前项目的模型变量名为：

- `PLATFORM_LLM_URL`
- `PLATFORM_API_KEY`
- `AGENT_MODEL_NAME`

使用 PowerShell 在后台启动：

```powershell
docker rm -f research-agent-local 2>$null

docker run -d --name research-agent-local `
  --cpus 1 --memory 1024m `
  --env-file .env.local `
  -e RESEARCH_CONFIG=/app/settings.json `
  -e AGENTHACK_TELEMETRY_REQUIRED=false `
  -p 127.0.0.1:18080:8080 `
  -v "${PWD}\config.container.example.json:/app/settings.json:ro" `
  -v "${PWD}\local-workspace:/workspace:ro" `
  -v "${PWD}\output:/output" `
  research-assistant:0.2.0
```

这里的 `18080` 是本机测试端口，容器内部仍然监听官方要求的 `8080`。`AGENTHACK_TELEMETRY_REQUIRED=false` 仅用于本地开发，正式评测不要自行设置该开发选项。

## 5. 接口冒烟测试

### 健康检查

```powershell
Invoke-RestMethod http://127.0.0.1:18080/health | ConvertTo-Json -Depth 10
```

应返回健康状态。若失败，先查看日志：

```powershell
docker logs --tail 200 research-agent-local
```

### 单题测试

```powershell
$payload = @{
  task_id = 'smoke-a-01'
  constraints = @{ timeout_ms = 300000 }
  input = @{
    case_id = 'A-01'
    function = 'Chat'
    title = '城市绿地科研对话'
    input = '解释城市绿地与地表温度的可能关系，并提出一个关键澄清问题。'
    materials = @()
    constraints = @()
    expected_output = '回答和澄清问题'
    output_schema_ref = 'Chat'
  }
} | ConvertTo-Json -Depth 10

$response = Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:18080/solve `
  -ContentType 'application/json' `
  -Body $payload

$response | ConvertTo-Json -Depth 20
```

检查响应中存在 `output.answer`。本项目的 `output.answer` 是题型 JSON 字符串，落盘结果是题型 JSON 对象。

### 结束本地容器

```powershell
docker rm -f research-agent-local
```

## 6. 导出参赛镜像

完成本地健康检查、单题测试和公开样例回归后，重新导出镜像：

```powershell
New-Item -ItemType Directory -Force .\build | Out-Null
docker save research-assistant:0.2.0 -o .\build\research-assistant-0.2.0.tar
Get-FileHash .\build\research-assistant-0.2.0.tar -Algorithm SHA256
```

需要上传到赛事平台的是 `research-assistant-0.2.0.tar`，不是组委会提供的 `agenthack-base-1.1.1.tar`。

## 7. 正式平台提交前的检查

- 使用 `linux/amd64` 构建。
- 容器启动后能在 `8080` 提供 `/health` 和 `/solve`。
- `solve()` 返回包含 `answer` 的官方 SDK 响应。
- 依赖、代码和必要的离线资源都在镜像内或由平台授权挂载提供。
- 不把 `local-workspace`、本地 `output`、测试答案和论文库强行打入正式镜像。
- 自定义工具不访问互联网，所有论文只能来自平台授权材料或允许的镜像内离线资源。
- 正式模型地址、模型名和 API Key 使用组委会最终公告规定的方式。当前工程本地采用运行时环境变量注入，不会把 `.env.local` 复制进镜像。
- 保留官方 AgentHack SDK；自定义工具继续使用 `@tool`。

正式平台的容器端口是 `8080`，本地的 `18080:8080` 只是为了避免占用主机端口，上传镜像前不需要修改 Dockerfile。

## 8. 常见问题

### `permission denied while trying to connect to the Docker API`

启动 Docker Desktop，确认当前 Windows 用户有 Docker Desktop 权限；然后重新执行 `docker info`。这不是 Python 代码或镜像构建错误。

### `pull access denied for agenthack/base`

说明本机没有导入赛事基础镜像，先执行第 2 步的 `docker load`。不要在正式提交前自行替换基础镜像。

### 容器能启动但找不到论文

检查本地测试是否挂载了 `local-workspace`，并确认 `config.container.example.json` 中的 `material_roots` 与实际目录一致。正式环境应使用平台挂载的授权目录，不要扩大材料根目录。

### 本地能跑，正式评测失败

重点检查是否依赖了主机路径、运行时下载依赖、外网检索、本地密钥文件或本机安装的实验包。正式评测只使用镜像内已安装内容和平台授权材料。
