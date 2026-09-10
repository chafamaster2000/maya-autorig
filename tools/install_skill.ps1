#Requires -Version 5.1
<#
.SYNOPSIS
    Install (or refresh) the maya-autorig skill into the dcc-mcp user skills
    directory on Windows. The PowerShell twin of tools/install_skill.sh.

.DESCRIPTION
    Copies skill/maya-autorig into %USERPROFILE%\.dcc-mcp\maya\skills\maya-autorig
    as a real directory. A junction or symlink is NOT enough: the skill scanner
    (Rust, walkdir) does not follow linked directories, so a linked skill is
    silently invisible -- it discovers zero skills and says nothing.

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

$files = @(Get-ChildItem -LiteralPath $source -Recurse -File |
    Where-Object { $_.FullName -notmatch '__pycache__' -and $_.Extension -ne '.pyc' })
Write-Host ("files to install: {0}" -f $files.Count)

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
Write-Host 'next: rescan without restarting Maya ->  run_script tools/mcp_rescan.py argv=["maya-autorig","--force"]'
