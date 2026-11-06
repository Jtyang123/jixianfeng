param(
    [switch]$Build,
    [switch]$NoPostgres,
    [switch]$NoBuild,
    [int]$Port = 8010
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

if (-not (Test-Path (Join-Path $Root '.env')) -and (Test-Path (Join-Path $Root '.env.example'))) {
    Copy-Item -LiteralPath (Join-Path $Root '.env.example') -Destination (Join-Path $Root '.env')
}

$distIndex = Join-Path $Root 'frontend\dist\index.html'
if (-not $NoBuild -and ($Build -or -not (Test-Path $distIndex))) {
    $npm = Get-Command npm.cmd -ErrorAction SilentlyContinue
    if (-not $npm) {
        $nodeCandidates = @(
            'D:\node\node.exe',
            'C:\Program Files\nodejs\node.exe',
            (Join-Path $env:LOCALAPPDATA 'Programs\nodejs\node.exe')
        )
        $nodePath = $nodeCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
        if ($nodePath) {
            $nodeDir = Split-Path -Parent $nodePath
            $env:PATH = "$nodeDir;$env:PATH"
            $npm = Get-Command npm.cmd -ErrorAction SilentlyContinue
        }
    }
    if (-not $npm) { throw 'Frontend build is missing and npm was not found. Run setup.cmd first.' }
    Push-Location (Join-Path $Root 'frontend')
    try {
        if (-not (Test-Path 'node_modules')) {
            if (Test-Path 'package-lock.json') { & $npm.Source ci --no-audit --no-fund } else { & $npm.Source install --no-audit --no-fund }
        }
        & $npm.Source run build
    } finally {
        Pop-Location
    }
}

$venvPython = Join-Path $Root '.venv\Scripts\python.exe'
if (Test-Path $venvPython) { $python = $venvPython } else { $python = 'python' }

Push-Location (Join-Path $Root 'backend')
try {
    Write-Host "FarmMatch is starting: http://127.0.0.1:$Port" -ForegroundColor Green
    & $python -m uvicorn app.main:app --host 0.0.0.0 --port $Port
} finally {
    Pop-Location
}