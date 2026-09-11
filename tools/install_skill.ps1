#Requires -Version 5.1
<#
.SYNOPSIS
    Install (or refresh) the maya-autorig skill where each consumer reads it.
    The PowerShell twin of tools/install_skill.sh.

.DESCRIPTION
    1. Copies skill/maya-autorig into %USERPROFILE%\.dcc-mcp\maya\skills\maya-autorig
       as a real directory. A junction or symlink is NOT enough: the skill
       scanner (Rust, walkdir) does not follow linked directories, so a linked
       skill is silently invisible -- it discovers zero skills and says nothing.
    2. Copies SKILL.md into %USERPROFILE%\.codex\skills\maya-autorig and
       %USERPROFILE%\.claude\skills\maya-autorig. The gateway's load_skill
       registers the tools but never returns the instructions, so the workflow
       text has to reach the agent as a skill of its own.

    Re-run after editing the skill, then rescan without restarting Maya:
        run_script tools/mcp_rescan.py  argv=["maya-autorig", "--force"]

.PARAMETER Destination
    Skills directory to install into. Defaults to the dcc-mcp user skills dir.

.PARAMETER DryRun
    Report what would be copied and change nothing.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File tools\install_skill.ps1
#>
[CmdletBinding()]
param(
    [string] $Destination,
    [switch] $DryRun
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Get-HomeDirectory {
    # USERPROFILE on Windows; HOME when this is dry-run from a shell on macOS
    # or Linux, so the script can be exercised off-Windows.
    if ($env:USERPROFILE) { return $env:USERPROFILE }
    if ($env:HOME) { return $env:HOME }
    throw 'Neither USERPROFILE nor HOME is set; pass -Destination explicitly.'
}

$repoRoot = Split-Path -Parent $PSScriptRoot
$source = Join-Path $repoRoot 'skill\maya-autorig'
if (-not (Test-Path -LiteralPath $source)) {
    # Off-Windows the separator differs; Join-Path with a forward slash works.
    $source = Join-Path (Join-Path $repoRoot 'skill') 'maya-autorig'
}
if (-not (Test-Path -LiteralPath $source)) {
    throw "skill source not found at '$source'"
}
$source = (Resolve-Path -LiteralPath $source).Path

if (-not $Destination) {
    $Destination = Join-Path (Join-Path (Join-Path (Join-Path (Get-HomeDirectory) '.dcc-mcp') 'maya') 'skills') 'maya-autorig'
}

Write-Host "source      : $source"
Write-Host "destination : $Destination"

# A previous install may have left a link behind; robocopy would follow it and
# mirror into the wrong place.
$existing = Get-Item -LiteralPath $Destination -Force -ErrorAction SilentlyContinue
if ($existing -and $existing.Attributes -band [IO.FileAttributes]::ReparsePoint) {
    Write-Host 'destination is a link (junction/symlink): removing it, the scanner cannot follow links'
    if (-not $DryRun) { [IO.Directory]::Delete($existing.FullName, $true) }
    $existing = $null
}

# The version travels with every copy, so an update can tell a stale copy
# from a current one without opening the repo.
$versionFile = Join-Path $repoRoot 'VERSION'
# Only when it differs: a fresh copy every run would bump the timestamp,
# robocopy would count it as changed, and every run would say "restart Maya".
$versionInSkill = Join-Path $source 'VERSION'
if ((Test-Path -LiteralPath $versionFile) -and -not $DryRun) {
    $same = (Test-Path -LiteralPath $versionInSkill) -and
        ((Get-Content -LiteralPath $versionFile -Raw) -eq (Get-Content -LiteralPath $versionInSkill -Raw))
    if (-not $same) { Copy-Item -LiteralPath $versionFile -Destination $versionInSkill -Force }
}

$files = @(Get-ChildItem -LiteralPath $source -Recurse -File |
    Where-Object { $_.FullName -notmatch '__pycache__' -and $_.Extension -ne '.pyc' })
Write-Host ("files to install: {0}" -f $files.Count)

# The agent-side copies. An agent counts as present when its CLI is on PATH
# or its home dir exists (a CLI installed a minute ago has no home dir until
# its first run).
$agentHomes = @()
foreach ($agent in 'codex', 'claude') {
    $home_ = Join-Path (Get-HomeDirectory) ('.' + $agent)
    if ((Get-Command $agent -ErrorAction SilentlyContinue) -or (Test-Path -LiteralPath $home_)) {
        $agentHomes += @{ agent = $agent; dir = Join-Path (Join-Path $home_ 'skills') 'maya-autorig' }
    }
}
foreach ($a in $agentHomes) { Write-Host ("agent skill ({0}): {1}\SKILL.md" -f $a.agent, $a.dir) }

if ($DryRun) {
    Write-Host 'dry run: nothing copied'
    return
}

if (-not (Test-Path -LiteralPath $Destination)) {
    New-Item -ItemType Directory -Path $Destination -Force | Out-Null
}

$robocopy = Get-Command robocopy.exe -ErrorAction SilentlyContinue
if ($robocopy) {
    # /MIR mirrors (deletes what the source dropped), /NFL /NDL /NJH /NJS quiet
    # it down. Robocopy's exit code is a bitmask: 0-7 are success, 8+ are real
    # failures, and PowerShell would otherwise read any non-zero as an error.
    & $robocopy.Source $source $Destination /MIR /NFL /NDL /NJH /NJS /NP `
        /XD '__pycache__' /XF '*.pyc' | Out-Null
    $code = $LASTEXITCODE
    if ($code -ge 8) { throw "robocopy failed with exit code $code" }
    # Bit 1 of the bitmask: files were copied. The running Maya is then behind
    # and needs a rescan (tools/mcp_rescan.py) or a restart.
    if ($code -band 1) { Write-Host 'skill changed: restart Maya, or run tools/mcp_rescan.py, so the gateway picks it up' }
} else {
    # pwsh on macOS/Linux, or a Windows install without robocopy.
    Get-ChildItem -LiteralPath $Destination -Force -ErrorAction SilentlyContinue |
        Remove-Item -Recurse -Force
    foreach ($f in $files) {
        $rel = $f.FullName.Substring($source.Length).TrimStart('\', '/')
        $target = Join-Path $Destination $rel
        $dir = Split-Path -Parent $target
        if (-not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
        Copy-Item -LiteralPath $f.FullName -Destination $target -Force
    }
}

$installed = @(Get-ChildItem -LiteralPath $Destination -Recurse -File).Count
Write-Host ("installed: {0} -> {1} ({2} files)" -f $source, $Destination, $installed)
if ($installed -ne $files.Count) {
    Write-Warning ("expected {0} files, found {1} -- check the destination" -f $files.Count, $installed)
}

foreach ($a in $agentHomes) {
    if (-not (Test-Path -LiteralPath $a.dir)) { New-Item -ItemType Directory -Path $a.dir -Force | Out-Null }
    Copy-Item -LiteralPath (Join-Path $source 'SKILL.md') -Destination (Join-Path $a.dir 'SKILL.md') -Force
    if (Test-Path -LiteralPath (Join-Path $source 'VERSION')) { Copy-Item -LiteralPath (Join-Path $source 'VERSION') -Destination (Join-Path $a.dir 'VERSION') -Force }
}

Write-Host 'next: rescan without restarting Maya ->  run_script tools/mcp_rescan.py argv=["maya-autorig","--force"]'
