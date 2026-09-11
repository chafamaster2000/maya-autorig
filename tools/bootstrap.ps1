#Requires -Version 5.1
<#
.SYNOPSIS
    Install maya-autorig on Windows straight from the web, without a clone.

.DESCRIPTION
    The one-liner an agent (or a person) can run from any PowerShell:

        powershell -ExecutionPolicy Bypass -c "irm https://raw.githubusercontent.com/chafamaster2000/maya-autorig/main/tools/bootstrap.ps1 | iex"

    Installs git through winget when it is missing, clones the repository into
    %USERPROFILE%\maya-autorig (MAYA_AUTORIG_DIR to change it) or updates an
    existing clone, then runs tools\install_windows.ps1 from there -- the same
    installer install.bat wraps. Any arguments are passed through to it.
#>
[CmdletBinding()]
param([Parameter(ValueFromRemainingArguments = $true)] [string[]] $InstallerArgs)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$repoUrl = 'https://github.com/chafamaster2000/maya-autorig'
$rawBase = 'https://raw.githubusercontent.com/chafamaster2000/maya-autorig/main'
$dest = if ($env:MAYA_AUTORIG_DIR) { $env:MAYA_AUTORIG_DIR } else { Join-Path $env:USERPROFILE 'maya-autorig' }

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if (-not $winget) { throw 'git is required and winget is not available to install it. Install Git for Windows, then re-run.' }
    Write-Host 'git missing: winget install Git.Git'
    & $winget.Source install --id Git.Git -e --accept-source-agreements --accept-package-agreements --silent | Out-Null
    # winget writes PATH to the registry; this process never sees it.
    $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' + [Environment]::GetEnvironmentVariable('Path', 'User')
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) { throw 'git still not on PATH; open a new PowerShell and re-run.' }
}

# Pasting the link a second time must update, not reinstall blindly. The
# comparison is explicit: the VERSION file in the clone against the VERSION
# file on GitHub. Same version and everything in place -> nothing to do, and
# it says so (nothing to restart). -Reinstall forces the installer anyway.
$reinstall = $false
$pass = @()
foreach ($a in @($InstallerArgs)) { if ($a -eq '-Reinstall') { $reinstall = $true } else { $pass += $a } }
$remoteVersion = ''
try { $remoteVersion = (Invoke-WebRequest -Uri "$rawBase/VERSION" -UseBasicParsing -TimeoutSec 15).Content.Trim() } catch { }
function Read-Version([string] $path) { try { return (Get-Content -LiteralPath $path -ErrorAction Stop | Select-Object -First 1).Trim() } catch { return 'unknown' } }

if (Test-Path -LiteralPath (Join-Path $dest '.git')) {
    $localVersion = Read-Version (Join-Path $dest 'VERSION')
    Write-Host ("installed: {0}   available: {1}" -f $localVersion, $(if ($remoteVersion) { $remoteVersion } else { 'unknown (offline?)' }))
    if ($remoteVersion -and $localVersion -eq $remoteVersion -and -not $reinstall) {
        # Same version: only re-run the installer if something is missing.
        $healthy = (Read-Version (Join-Path $env:USERPROFILE '.dcc-mcp\maya\skills\maya-autorig\VERSION')) -eq $localVersion
        foreach ($agent in 'codex', 'claude') {
            if (Get-Command $agent -ErrorAction SilentlyContinue) {
                & $agent mcp get maya *> $null
                if ($LASTEXITCODE -ne 0) { $healthy = $false }
            }
        }
        if ($healthy) {
            Write-Host ("already up to date: version {0} is installed and registered in every agent found." -f $localVersion)
            Write-Host '-> nothing to do, nothing to restart. (Force a reinstall by appending -Reinstall)'
            exit 0
        }
        Write-Host ("version {0} is current but something is missing; running the installer." -f $localVersion)
    } else {
        $before = (& git -C $dest rev-parse --short HEAD).Trim()
        & git -C $dest fetch --quiet origin
        & git -C $dest pull --ff-only --quiet
        $after = (& git -C $dest rev-parse --short HEAD).Trim()
        $newVersion = Read-Version (Join-Path $dest 'VERSION')
        if ($before -eq $after) { Write-Host ("clone already at {0} ({1})" -f $after, $newVersion) }
        else { Write-Host ("updated: {0} ({1}) -> {2} ({3})" -f $localVersion, $before, $newVersion, $after) }
    }
} else {
    Write-Host "cloning $repoUrl into $dest"
    & git clone --depth 1 $repoUrl $dest | Out-Null
    Write-Host ("version: {0}" -f (Read-Version (Join-Path $dest 'VERSION')))
}

$installer = Join-Path (Join-Path $dest 'tools') 'install_windows.ps1'
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $installer @pass
exit $LASTEXITCODE
