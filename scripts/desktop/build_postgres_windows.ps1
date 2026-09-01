param(
    [string]$PostgresRoot = "C:\Program Files\PostgreSQL\16",
    [string]$VectorVersion = "0.8.1",
    [string]$Python = ".venv\Scripts\python.exe"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$PythonExecutable = if ([IO.Path]::IsPathRooted($Python)) {
    $Python
} else {
    Join-Path $ProjectRoot $Python
}
if (-not (Test-Path (Join-Path $PostgresRoot "bin\postgres.exe"))) {
    throw "PostgreSQL 16 runtime not found at $PostgresRoot"
}

$BuildRoot = Join-Path $env:TEMP ("wecanfindintern-pgvector-" + [guid]::NewGuid())
try {
    git clone --depth 1 --branch "v$VectorVersion" https://github.com/pgvector/pgvector.git $BuildRoot
    $env:PGROOT = $PostgresRoot
    Push-Location $BuildRoot
    try {
        nmake /NOLOGO /F Makefile.win
        nmake /NOLOGO /F Makefile.win install
    } finally {
        Pop-Location
    }
    & $PythonExecutable `
        (Join-Path $ProjectRoot "scripts\desktop\prepare_postgres.py") `
        --source $PostgresRoot --target win32-x64
} finally {
    if (Test-Path $BuildRoot) {
        Remove-Item -LiteralPath $BuildRoot -Recurse -Force
    }
}
