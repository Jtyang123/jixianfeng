param(
    [switch]$Build,
    [switch]$NoPostgres,
    [int]$Port = 8010
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path

if (-not (Test-Path (Join-Path $Root 'frontend\dist\index.html')) -or $Build) {
    $nodeHome = 'D:\node'
    if (Test-Path (Join-Path $nodeHome 'npm.cmd')) {
        $env:PATH = "$nodeHome;$env:PATH"
    }
    Push-Location (Join-Path $Root 'frontend')
    try {
        npm.cmd install --no-audit --no-fund
        npm.cmd run build
    } finally {
        Pop-Location
    }
}

# Start a project-local PostgreSQL instance when DATABASE_URL is not supplied.
if (-not $NoPostgres -and -not $env:DATABASE_URL) {
    $pgBin = 'D:\postgresql\bin'
    $pgData = Join-Path $Root 'data\pgdata'
    $pgLog = Join-Path $Root 'data\postgres.log'
    if (Test-Path (Join-Path $pgBin 'pg_ctl.exe')) {
        if (-not (Test-Path (Join-Path $pgData 'PG_VERSION'))) {
            New-Item -ItemType Directory -Force -Path $pgData | Out-Null
            & (Join-Path $pgBin 'initdb.exe') -D $pgData -U farmmatch -A trust --encoding=UTF8 --locale=C | Out-Host
        }
        & (Join-Path $pgBin 'pg_ctl.exe') status -D $pgData *> $null
        if ($LASTEXITCODE -ne 0) {
            & (Join-Path $pgBin 'pg_ctl.exe') -D $pgData -l $pgLog -o '"-p 55432"' start | Out-Host
            for ($i = 0; $i -lt 20; $i++) {
                & (Join-Path $pgBin 'pg_isready.exe') -h 127.0.0.1 -p 55432 *> $null
                if ($LASTEXITCODE -eq 0) { break }
                Start-Sleep -Milliseconds 500
            }
        }
        $dbExists = & (Join-Path $pgBin 'psql.exe') -w -h 127.0.0.1 -p 55432 -U farmmatch -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='farmmatch'"
        if (($dbExists | Out-String).Trim() -ne '1') {
            & (Join-Path $pgBin 'createdb.exe') -w -h 127.0.0.1 -p 55432 -U farmmatch farmmatch | Out-Host
        }
        $env:DATABASE_URL = 'postgresql://farmmatch@127.0.0.1:55432/farmmatch'
        Write-Host 'Database: PostgreSQL 127.0.0.1:55432/farmmatch' -ForegroundColor Cyan
    } else {
        Write-Host 'PostgreSQL not found. Falling back to local SQLite.' -ForegroundColor Yellow
    }
}

Push-Location (Join-Path $Root 'backend')
try {
    Write-Host '急先锋已启动: http://127.0.0.1:8000' -ForegroundColor Green
    python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
} finally {
    Pop-Location
}
