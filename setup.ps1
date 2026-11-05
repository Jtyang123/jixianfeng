param(
    [switch]$SkipFrontend
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

$pythonCmd = Get-Command python -ErrorAction SilentlyContinue
if (-not $pythonCmd) { throw 'Python 3.10+ is required. Install Python and enable Add Python to PATH.' }
$nodeCmd = Get-Command node -ErrorAction SilentlyContinue
$npmCmd = Get-Command npm.cmd -ErrorAction SilentlyContinue
if (-not $SkipFrontend -and (-not $nodeCmd -or -not $npmCmd)) { throw 'Node.js 18+ and npm are required for the frontend.' }

$venv = Join-Path $Root '.venv'
$venvPython = Join-Path $venv 'Scripts\python.exe'
if (-not (Test-Path $venvPython)) {
    Write-Host 'Creating Python virtual environment...' -ForegroundColor Cyan
    & python -m venv $venv
}
Write-Host 'Installing backend dependencies...' -ForegroundColor Cyan
$pipIndex = if ($env:PIP_INDEX_URL) { $env:PIP_INDEX_URL } else { 'https://repo.huaweicloud.com/repository/pypi/simple/' }
& $venvPython -m pip install --disable-pip-version-check -i $pipIndex -r (Join-Path $Root 'backend\requirements.txt')
if ($LASTEXITCODE -ne 0) {
    Write-Warning 'Primary Python package index failed; retrying with the official PyPI.'
    & $venvPython -m pip install --disable-pip-version-check -i 'https://pypi.org/simple' -r (Join-Path $Root 'backend\requirements.txt')
}
if ($LASTEXITCODE -ne 0) { throw 'Backend dependency installation failed.' }

if (-not $SkipFrontend) {
    Push-Location (Join-Path $Root 'frontend')
    try {
        Write-Host 'Installing frontend dependencies...' -ForegroundColor Cyan
        if (Test-Path 'package-lock.json') { & $npmCmd.Source ci --no-audit --no-fund } else { & $npmCmd.Source install --no-audit --no-fund }
        if ($LASTEXITCODE -ne 0) {
            Write-Warning 'Default npm registry failed; retrying with npmmirror.'
            $npmRegistry = if ($env:NPM_CONFIG_REGISTRY) { $env:NPM_CONFIG_REGISTRY } else { 'https://registry.npmmirror.com' }
            if (Test-Path 'package-lock.json') { & $npmCmd.Source ci --no-audit --no-fund --registry $npmRegistry } else { & $npmCmd.Source install --no-audit --no-fund --registry $npmRegistry }
        }
        if ($LASTEXITCODE -ne 0) { throw 'Frontend dependency installation failed.' }
        Write-Host 'Building frontend...' -ForegroundColor Cyan
        & $npmCmd.Source run build
        if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed.' }
    } finally {
        Pop-Location
    }
}

$envPath = Join-Path $Root '.env'
$examplePath = Join-Path $Root '.env.example'
if (-not (Test-Path $envPath) -and (Test-Path $examplePath)) {
    Copy-Item -LiteralPath $examplePath -Destination $envPath
    Write-Host 'Created .env from .env.example.' -ForegroundColor Green
}

Write-Host '' 
Write-Host 'Setup complete.' -ForegroundColor Green
Write-Host 'Start with: .\start.cmd' -ForegroundColor Yellow
Write-Host 'Open: http://127.0.0.1:8010' -ForegroundColor Yellow