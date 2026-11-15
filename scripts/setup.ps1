$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot

foreach ($commandName in @("conda", "git")) {
    if (-not (Get-Command $commandName -ErrorAction SilentlyContinue)) {
        throw "Required command '$commandName' was not found on PATH."
    }
}

$gitRepository = git rev-parse --is-inside-work-tree 2>$null
if (-not $gitRepository) {
    git init --initial-branch=main
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to initialize the Git repository."
    }
}

$environmentPrefix = Join-Path $projectRoot ".conda"
$environmentPython = Join-Path $environmentPrefix "python.exe"

if (Test-Path -LiteralPath $environmentPython) {
    conda env update --prefix $environmentPrefix --file environment.yml
} else {
    conda env create --prefix $environmentPrefix --file environment.yml
}
if ($LASTEXITCODE -ne 0) {
    throw "Failed to create or update the project Conda environment."
}

if (-not (Test-Path -LiteralPath ".env")) {
    Copy-Item -LiteralPath ".env.example" -Destination ".env"
    Write-Host "Created .env from .env.example. Review it before starting the API."
}

conda run --no-capture-output --prefix $environmentPrefix python -m pre_commit install
if ($LASTEXITCODE -ne 0) {
    throw "Failed to install the Git pre-commit hook."
}

Write-Host "Conda environment: $environmentPrefix"
Write-Host "VS Code will use .conda\python.exe. Restart the integrated terminal if needed."
Write-Host "Git pre-commit checks are installed."
