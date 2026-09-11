#Requires -Version 5.1
<#
.SYNOPSIS
    Recover the DCC-MCP gateway when it stops answering with Maya open.

.DESCRIPTION
    Symptom: Maya is running, the plugin is loaded, and every MCP call fails
    with a transport error. Nothing is listening on port 9765.

    Cause (seen for real): a Maya crash, or two Maya instances started at once,
    leaves rows for dcc_type "__gateway__" in the file registry claiming a port
    that nobody is listening on. Every gateway that starts afterwards reads
    those rows, probes the "resident gateway", gets no answer, and instead of
    taking the port over it logs "found an existing owner; exiting" and quits.
    Nobody binds the port, each sidecar waits 15 s and exits, and the rows
    survive every restart: a gateway row carries no pid, so the reaper has
    nothing to prove it dead with.

    That missing pid is why a gateway row is judged by its PORT, not by a
    process: if something answers on the port the row claims, the row is live
    and is left alone -- including the row of a gateway that is working right
    now. Other rows are judged by their owning pid. services.json is backed up
    next to itself before anything is rewritten.

.PARAMETER RegistryDir
    Registry directory. Default: %TEMP%\dcc-mcp-registry

.PARAMETER Port
    Gateway port to health-check. Default 9765.

.PARAMETER DryRun
    Report what would be pruned and change nothing.

.PARAMETER Force
    Prune even when the gateway is answering (normally that is left alone).

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File tools\repair_gateway.ps1 -DryRun
#>
[CmdletBinding()]
param(
    [string] $RegistryDir,
    [int] $Port = 9765,
    [switch] $DryRun,
    [switch] $Force
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Test-GatewayHealth {
    param([int] $Port)
    try {
        $r = Invoke-WebRequest -Uri ("http://127.0.0.1:{0}/health" -f $Port) -TimeoutSec 3 -UseBasicParsing
        return [int]$r.StatusCode
    } catch {
        return 0
    }
}

$script:PortCache = @{}
function Test-PortAnswers {
    <# Does anything answer HTTP on this port? A gateway row that claims a
       silent port is the ghost that deadlocks the election. #>
    param([int] $Port)
    if ($script:PortCache.ContainsKey($Port)) { return $script:PortCache[$Port] }
    $answers = (Test-GatewayHealth -Port $Port) -gt 0
    $script:PortCache[$Port] = $answers
    return $answers
}

function Test-PidAlive {
    param($ProcessId)
    if (-not $ProcessId) { return $false }
    try { return [bool](Get-Process -Id ([int]$ProcessId) -ErrorAction Stop) } catch { return $false }
}

if (-not $RegistryDir) {
    $tmp = $env:TEMP
    if (-not $tmp) { $tmp = $env:TMPDIR }
    if (-not $tmp) { $tmp = '/tmp' }
    $RegistryDir = Join-Path $tmp 'dcc-mcp-registry'
}

Write-Host ''
Write-Host 'DCC-MCP gateway repair' -ForegroundColor Cyan
Write-Host ("registry : {0}" -f $RegistryDir)

$code = Test-GatewayHealth -Port $Port
Write-Host ("health   : {0} on port {1}" -f $(if ($code) { $code } else { '000 (nobody listening)' }), $Port)

if ($code -eq 200 -and -not $Force) {
    Write-Host 'Gateway is healthy; nothing to repair. Use -Force to prune anyway.' -ForegroundColor Green
    exit 0
}

if (-not (Test-Path -LiteralPath $RegistryDir)) {
    Write-Host 'No registry directory: nothing to prune. Open Maya and the sidecar will create one.' -ForegroundColor Yellow
    exit 0
}

$servicesPath = Join-Path $RegistryDir 'services.json'
$pruned = @()
$kept = @()

if (Test-Path -LiteralPath $servicesPath) {
    $rows = @(Get-Content -LiteralPath $servicesPath -Raw | ConvertFrom-Json)
    foreach ($row in $rows) {
        $rowPid = $null
        foreach ($field in 'host_pid', 'sidecar_pid') {
            if ($row.PSObject.Properties.Name -contains $field -and $row.$field) { $rowPid = $row.$field; break }
        }
        $isGatewayRow = ($row.PSObject.Properties.Name -contains 'dcc_type') -and ($row.dcc_type -eq '__gateway__')
        if ($isGatewayRow) {
            # Judged by its port: a gateway row carries no pid, so "no pid"
            # would condemn the healthy gateway's own row too.
            $rowPort = 0
            if ($row.PSObject.Properties.Name -contains 'port' -and $row.port) { $rowPort = [int]$row.port }
            $ghost = ($rowPort -le 0) -or (-not (Test-PortAnswers $rowPort))
        } else {
            $ghost = (-not $rowPid) -or (-not (Test-PidAlive $rowPid))
        }
        if ($ghost) { $pruned += $row } else { $kept += $row }
    }

    Write-Host ''
    Write-Host ("rows: {0} total, {1} ghost, {2} live" -f $rows.Count, $pruned.Count, $kept.Count)
    foreach ($row in $pruned) {
        $id = if ($row.PSObject.Properties.Name -contains 'instance_id') { "$($row.instance_id)".Substring(0, 8) } else { '?' }
        Write-Host ("  prune {0,-12} {1}" -f $row.dcc_type, $id) -ForegroundColor Yellow
    }
    foreach ($row in $kept) {
        $id = if ($row.PSObject.Properties.Name -contains 'instance_id') { "$($row.instance_id)".Substring(0, 8) } else { '?' }
        Write-Host ("  keep  {0,-12} {1}" -f $row.dcc_type, $id) -ForegroundColor DarkGray
    }

    if ($pruned.Count -gt 0 -and -not $DryRun) {
        $backup = "$servicesPath.bak"
        Copy-Item -LiteralPath $servicesPath -Destination $backup -Force
        # ConvertTo-Json on a single object drops the array; force one.
        $json = if ($kept.Count -eq 0) { '[]' } else { ConvertTo-Json -InputObject @($kept) -Depth 12 }
        Set-Content -LiteralPath $servicesPath -Value $json -Encoding UTF8
        Write-Host ("services.json rewritten ({0} rows kept); backup at {1}" -f $kept.Count, $backup)
    }
} else {
    Write-Host 'no services.json'
}

# Lock files outlive their rows and keep the ghosts addressable.
$locksDir = Join-Path $RegistryDir 'locks'
$staleLocks = @()
if (Test-Path -LiteralPath $locksDir) {
    $liveIds = @($kept | ForEach-Object {
        if ($_.PSObject.Properties.Name -contains 'instance_id') { "$($_.instance_id)" }
    })
    $staleLocks = @(Get-ChildItem -LiteralPath $locksDir -File -ErrorAction SilentlyContinue | Where-Object {
        $name = $_.BaseName
        -not ($liveIds | Where-Object { $name -like "*$_*" })
    })
    foreach ($lock in $staleLocks) {
        Write-Host ("  prune lock  {0}" -f $lock.Name) -ForegroundColor Yellow
        if (-not $DryRun) { Remove-Item -LiteralPath $lock.FullName -Force }
    }
}

# Sentinels are named by pid: any whose process is gone is dead weight.
$sentDir = Join-Path $RegistryDir 'sentinels'
$staleSent = @()
if (Test-Path -LiteralPath $sentDir) {
    $staleSent = @(Get-ChildItem -LiteralPath $sentDir -File -ErrorAction SilentlyContinue | Where-Object {
        $sentinelPid = ($_.BaseName -split '-')[0]
        ($sentinelPid -match '^\d+$') -and -not (Test-PidAlive $sentinelPid)
    })
    foreach ($s in $staleSent) {
        if (-not $DryRun) { Remove-Item -LiteralPath $s.FullName -Force }
    }
    if ($staleSent.Count -gt 0) {
        Write-Host ("  prune {0} dead sentinel(s)" -f $staleSent.Count) -ForegroundColor Yellow
    }
}

$launchLock = Join-Path $RegistryDir 'gateway-launch.lock'
if (Test-Path -LiteralPath $launchLock) {
    Write-Host '  prune gateway-launch.lock' -ForegroundColor Yellow
    if (-not $DryRun) { Remove-Item -LiteralPath $launchLock -Force }
}

Write-Host ''
if ($DryRun) {
    Write-Host 'DRY RUN - nothing was changed' -ForegroundColor Yellow
    exit 0
}
if ($pruned.Count -eq 0 -and $staleLocks.Count -eq 0 -and $staleSent.Count -eq 0) {
    Write-Host 'Nothing to prune: the registry is clean, so the gateway is down for another reason.' -ForegroundColor Yellow
    Write-Host ("Check the sidecar log:  {0}\logs" -f $RegistryDir)
    exit 0
}
Write-Host 'Registry cleaned.' -ForegroundColor Green
Write-Host 'Now reload the plugin so its sidecar spawns a gateway that can win the election:'
Write-Host '  Maya > Windows > Settings/Preferences > Plug-in Manager > untick and re-tick dcc_mcp_maya_plugin'
Write-Host '  (or restart Maya - one instance at a time, two racing is what creates the ghosts)'
Write-Host 'Then reconnect: in Claude Code type /mcp; in Codex start a new session (MCP servers are read at start-up).'
exit 0
