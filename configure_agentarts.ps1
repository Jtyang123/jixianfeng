param(
    [ValidateSet('demand_agent', 'matching_agent', 'verifier_agent', 'delivery_agent')]
    [string]$Agent = '',
    [string]$Endpoint = '',
    [switch]$NoPromptForKey
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$EnvFile = Join-Path $Root '.env'

function Set-EnvLine {
    param([string[]]$Lines, [string]$Key, [string]$Value)
    $found = $false
    $result = @()
    foreach ($line in $Lines) {
        if ($line -match ('^\s*' + [regex]::Escape($Key) + '\s*=')) {
            if (-not $found) {
                $result += ($Key + '=' + $Value)
                $found = $true
            }
            continue
        }
        $result += $line
    }
    if (-not $found) { $result += ($Key + '=' + $Value) }
    return $result
}

function Get-EnvValue {
    param([string[]]$Lines, [string]$Key)
    $line = $Lines | Where-Object { $_ -match ('^\s*' + [regex]::Escape($Key) + '\s*=') } | Select-Object -Last 1
    if (-not $line) { return '' }
    return ($line -split '=', 2)[1].Trim()
}

$agentLabels = @{
    demand_agent = 'A01 Demand Analysis'
    matching_agent = 'A02 Resource Matching'
    verifier_agent = 'A03 Independent Verification'
    delivery_agent = 'A04 Delivery Package'
}

if (-not $Agent) {
    Write-Host 'Select an AgentArts agent to configure:' -ForegroundColor Cyan
    $keys = @('demand_agent', 'matching_agent', 'verifier_agent', 'delivery_agent')
    for ($i = 0; $i -lt $keys.Count; $i++) {
        Write-Host ("  {0}. {1} ({2})" -f ($i + 1), $agentLabels[$keys[$i]], $keys[$i])
    }
    $choice = (Read-Host 'Enter 1-4').Trim()
    $index = [int]$choice - 1
    if ($index -lt 0 -or $index -ge $keys.Count) { throw 'Invalid selection' }
    $Agent = $keys[$index]
}

if (-not $Endpoint -and (Test-Path $EnvFile)) {
    $savedLines = @(Get-Content -LiteralPath $EnvFile -Encoding UTF8)
    $savedJson = Get-EnvValue $savedLines 'AGENTARTS_AGENT_ENDPOINTS_JSON'
    if ($savedJson) {
        try {
            $savedMap = $savedJson | ConvertFrom-Json
            $savedEndpoint = $savedMap.PSObject.Properties[$Agent].Value
            if ($savedEndpoint) {
                $Endpoint = [string]$savedEndpoint
                Write-Host ('Reusing saved endpoint for ' + $Agent) -ForegroundColor DarkGray
            }
        } catch {}
    }
}
if (-not $Endpoint) {
    $Endpoint = (Read-Host ('Enter the full /invocations URL for ' + $agentLabels[$Agent])).Trim()
}
if (-not $Endpoint.StartsWith('https://')) { throw 'Endpoint must start with https://' }

$lines = @()
if (Test-Path $EnvFile) { $lines = @(Get-Content -LiteralPath $EnvFile -Encoding UTF8) }

$endpointJson = Get-EnvValue $lines 'AGENTARTS_AGENT_ENDPOINTS_JSON'
$endpointMap = @{}
if ($endpointJson) {
    try {
        $parsed = $endpointJson | ConvertFrom-Json
        foreach ($property in $parsed.PSObject.Properties) { $endpointMap[$property.Name] = [string]$property.Value }
    } catch {
        Write-Host 'Existing endpoint JSON cannot be parsed; rebuilding endpoint map.' -ForegroundColor Yellow
    }
}
$endpointMap[$Agent] = $Endpoint

$globalKey = Get-EnvValue $lines 'AGENTARTS_API_KEY'
$agentKeysJson = Get-EnvValue $lines 'AGENTARTS_AGENT_KEYS_JSON'
$agentKeyMap = @{}
if ($agentKeysJson) {
    try {
        $parsedKeys = $agentKeysJson | ConvertFrom-Json
        foreach ($property in $parsedKeys.PSObject.Properties) { $agentKeyMap[$property.Name] = [string]$property.Value }
    } catch {
        Write-Host 'Existing agent key JSON cannot be parsed; rebuilding key map.' -ForegroundColor Yellow
    }
}

$agentKey = $agentKeyMap[$Agent]
if (-not $NoPromptForKey) {
    if ($agentKey) {
        $update = (Read-Host ('Update the runtime-specific key for ' + $Agent + '? (y/N)')).Trim().ToLowerInvariant()
        if ($update -eq 'y' -or $update -eq 'yes') { $agentKey = '' }
    }
    if (-not $agentKey) {
        $secureKey = Read-Host ('Paste the runtime-specific inbound API key for ' + $Agent + ' (hidden; Enter = use global fallback)') -AsSecureString
        $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureKey)
        try { $agentKey = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr) } finally { [Runtime.InteropServices.Marshal]::FreeBSTR($bstr) }
    }
}
if ($agentKey) { $agentKeyMap[$Agent] = $agentKey }
if (-not $globalKey -and -not $agentKey) { throw 'No API key available. Paste this runtime API key or configure AGENTARTS_API_KEY.' }

$lines = Set-EnvLine $lines 'AGENT_RUNTIME' 'agentarts'
$lines = Set-EnvLine $lines 'AGENTARTS_ENABLED' 'true'
$lines = Set-EnvLine $lines 'AGENTARTS_INVOKE_PATH' '/invocations'
$lines = Set-EnvLine $lines 'AGENTARTS_TIMEOUT' '60'
$endpointJsonOut = ConvertTo-Json -InputObject $endpointMap -Compress
$lines = Set-EnvLine $lines 'AGENTARTS_AGENT_ENDPOINTS_JSON' $endpointJsonOut
$agentKeysOut = ConvertTo-Json -InputObject $agentKeyMap -Compress
$lines = Set-EnvLine $lines 'AGENTARTS_AGENT_KEYS_JSON' $agentKeysOut
if (-not (Get-EnvValue $lines 'AGENTARTS_AUTH_HEADER')) { $lines = Set-EnvLine $lines 'AGENTARTS_AUTH_HEADER' 'Authorization' }
if (-not (Get-EnvValue $lines 'AGENTARTS_AUTH_PREFIX')) { $lines = Set-EnvLine $lines 'AGENTARTS_AUTH_PREFIX' 'Bearer ' }
if (-not (Get-EnvValue $lines 'AGENTARTS_SESSION_HEADER')) { $lines = Set-EnvLine $lines 'AGENTARTS_SESSION_HEADER' 'x-hw-agentarts-session-id' }

[IO.File]::WriteAllLines($EnvFile, $lines, (New-Object System.Text.UTF8Encoding($false)))

$configured = @($endpointMap.Keys)
$expected = @('demand_agent', 'matching_agent', 'verifier_agent', 'delivery_agent')
$missing = @($expected | Where-Object { $configured -notcontains $_ })
$keyCount = @($agentKeyMap.Keys).Count
Write-Host ('Configured: ' + $agentLabels[$Agent] + ' (' + $Agent + ')') -ForegroundColor Green
Write-Host ('AgentArts endpoint coverage: ' + $configured.Count + '/4') -ForegroundColor Cyan
Write-Host ('Runtime-specific keys configured: ' + $keyCount + '/4') -ForegroundColor Cyan
if ($missing.Count) {
    Write-Host ('Missing endpoints: ' + ($missing -join ', ')) -ForegroundColor Yellow
} else {
    Write-Host 'All four AgentArts endpoints are configured.' -ForegroundColor Green
}
if (-not $agentKey) { Write-Host 'This agent will use the global fallback key.' -ForegroundColor Yellow }
Write-Host 'The API key was not echoed. Restart start.cmd and run AgentArts check in the app.' -ForegroundColor DarkGray
