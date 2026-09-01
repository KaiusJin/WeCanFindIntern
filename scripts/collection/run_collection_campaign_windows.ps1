param(
    [int]$BatchSize = 250,
    [int]$Concurrency = 4,
    [int]$MaxRetries = 3
)

$ErrorActionPreference = "Stop"
$ProjectDir = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$Python = Join-Path $ProjectDir ".venv\Scripts\python.exe"
$LogDir = Join-Path $ProjectDir "logs"
$StdoutLog = Join-Path $LogDir "collector.log"
$StderrLog = Join-Path $LogDir "collector-error.log"

if (-not (Test-Path -LiteralPath $Python)) {
    throw "Python virtual environment not found at $Python."
}
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

Push-Location $ProjectDir
try {
    $env:PYTHONPATH = Join-Path $ProjectDir "src"
    $env:PYTHONUNBUFFERED = "1"
    $campaign = Join-Path $ProjectDir "scripts\collection\run_collection_campaign.py"
    & $Python $campaign --batch-size $BatchSize --concurrency $Concurrency --max-retries $MaxRetries >> $StdoutLog 2>> $StderrLog
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    $terms = Join-Path $ProjectDir "scripts\maintenance\backfill_recruiting_terms.py"
    & $Python $terms >> $StdoutLog 2>> $StderrLog
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
finally {
    Pop-Location
}
