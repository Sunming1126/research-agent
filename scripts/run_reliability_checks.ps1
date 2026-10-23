param(
    [string]$Image = 'research-assistant:0.4.1-source-v1'
)
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path -Parent $PSScriptRoot)
$repositoryRoot = (Get-Location).Path
$results = Join-Path $repositoryRoot 'output/reliability-checks'
$testDependencies = Join-Path $repositoryRoot 'output/reliability-test-deps'
$publicRoot = Join-Path $repositoryRoot '科研助手赛题材料/research-experiments'
New-Item -ItemType Directory -Force -Path $results, $testDependencies | Out-Null

# 仅测试依赖需要联网；正式镜像不安装 pytest。
& docker run --rm --user root --mount "type=bind,source=$testDependencies,target=/testdeps" --mount "type=bind,source=$repositoryRoot/requirements-test.txt,target=/test-requirements.txt,readonly" --entrypoint python $Image -m pip install --disable-pip-version-check --target /testdeps -r /test-requirements.txt
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

# 完整测试包含离线算法夹具，挂载当前源码；真实模型镜像验收单独执行。
& docker run --rm --init --network none --cpus 1 --memory 1024m --read-only --tmpfs /tmp:rw,size=256m -e RESEARCH_RUNTIME=local -e PUBLIC_EXPERIMENT_ROOT=/materials -e PYTHONPATH=/src:/src/src:/src/tests:/testdeps -e AGENTHACK_TELEMETRY_REQUIRED=false --mount "type=bind,source=$repositoryRoot,target=/src,readonly" --mount "type=bind,source=$publicRoot,target=/materials,readonly" --mount "type=bind,source=$results,target=/results" --mount "type=bind,source=$testDependencies,target=/testdeps,readonly" -w /src --entrypoint python $Image -m pytest -q -p no:cacheprovider --basetemp /results/pytest-tmp --junitxml /results/regression.xml tests
exit $LASTEXITCODE
