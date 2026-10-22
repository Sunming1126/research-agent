$ErrorActionPreference = 'Stop'
Set-Location (Split-Path -Parent $PSScriptRoot)
$experimentRoot = Join-Path (Get-Location).Path '科研助手赛题材料/research-experiments'
if (-not (Test-Path -LiteralPath (Join-Path $experimentRoot 'experiment.py') -PathType Leaf) -or
    -not (Test-Path -LiteralPath (Join-Path $experimentRoot 'publicdata/D-01.json') -PathType Leaf)) {
    throw '公开实验目录不完整，无法进行 D 类回归。'
}
docker build --platform linux/amd64 -t research-assistant:0.2.0 .
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
docker run --rm --network none --cpus 1 --memory 1024m -e PYTHONPATH=/src:/src/src -e PUBLIC_EXPERIMENT_ROOT=/materials -e AGENTHACK_TELEMETRY_REQUIRED=false -v "${PWD}:/src:ro" -v "${experimentRoot}:/materials:ro" -v "${PWD}/output:/results" -w /src --entrypoint python research-assistant:0.2.0 scripts/checks.py
exit $LASTEXITCODE

