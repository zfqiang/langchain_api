$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot
$environmentPrefix = Join-Path $projectRoot ".conda"

if (-not (Test-Path -LiteralPath (Join-Path $environmentPrefix "python.exe"))) {
    throw "Project Conda environment not found. Run .\scripts\setup.ps1 first."
}

conda run --no-capture-output --prefix $environmentPrefix python -m pytest
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

conda run --no-capture-output --prefix $environmentPrefix python -m ruff check .
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

conda run --no-capture-output --prefix $environmentPrefix python -m ruff format --check .
exit $LASTEXITCODE
