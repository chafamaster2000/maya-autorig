#Requires -Version 5.1
<#
.SYNOPSIS
    Set up maya-autorig on Windows end to end: the DCC-MCP packages, the Maya
    adapter, this skill, and the Claude Code connection.

.DESCRIPTION
    Every step is idempotent and reports OK / SKIP / FAIL; the script exits
    non-zero if any required step failed, so it can gate a machine setup.
    Nothing is changed under -DryRun.

    The chain it builds:

        Claude Code --HTTP--> dcc-mcp gateway (127.0.0.1:9765) --> Maya adapter
                                        |                              |
                                        +-- scans skills --------------+
                                            ...including maya-autorig

    AdvancedSkeleton is content, not a package: this script checks for it and
    tells you where to put it, but does not install it.

.PARAMETER DryRun
    Report every step and change nothing.

.PARAMETER SkipPackages
    Do not touch pip (the DCC-MCP packages are already installed).

.PARAMETER SkipAdapter
    Do not run 'dcc-mcp-maya install' (the Maya module is already hooked up).

.PARAMETER SkipClaude
    Do not register the MCP server with Claude Code.

.PARAMETER GatewayUrl
    MCP endpoint Claude Code should talk to. Default http://127.0.0.1:9765/mcp

.PARAMETER Python
    Python launcher to use. Default: 'py -3' when present, else 'python'.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File tools\install_windows.ps1 -DryRun

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File tools\install_windows.ps1
#>
[CmdletBinding()]
param(
    [switch] $DryRun,
    [switch] $SkipPackages,
    [switch] $SkipAdapter,
    [switch] $SkipClaude,
    [string] $GatewayUrl = 'http://127.0.0.1:9765/mcp',
    [string] $Python
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$script:Steps = New-Object System.Collections.ArrayList
$script:RepoRoot = Split-Path -Parent $PSScriptRoot

function Add-Step {
    param([string] $Name, [string] $Status, [string] $Detail = '', [switch] $Required)
    [void]$script:Steps.Add([pscustomobject]@{
        Name = $Name; Status = $Status; Detail = $Detail; Required = [bool]$Required
    })
    $colour = switch ($Status) { 'OK' { 'Green' } 'SKIP' { 'DarkGray' } 'WARN' { 'Yellow' } default { 'Red' } }
    Write-Host ("  [{0,-4}] {1}" -f $Status, $Name) -ForegroundColor $colour
    if ($Detail) { Write-Host ("         {0}" -f $Detail) -ForegroundColor DarkGray }
}

function Get-HomeDirectory {
    if ($env:USERPROFILE) { return $env:USERPROFILE }
    if ($env:HOME) { return $env:HOME }
    throw 'Neither USERPROFILE nor HOME is set.'
}

function Invoke-Tool {
    <# Run an external command, capture stdout+stderr, never throw on a
       non-zero exit: the caller decides what a failure means. #>
    param([string] $File, [string[]] $Arguments)
    if ($DryRun) {
        return [pscustomobject]@{ ExitCode = 0; Output = "(dry run) $File $($Arguments -join ' ')" }
    }
    $out = & $File @Arguments 2>&1 | Out-String
    return [pscustomobject]@{ ExitCode = $LASTEXITCODE; Output = $out.Trim() }
}

function Resolve-Python {
    if ($Python) {
        $parts = $Python -split '\s+'
        return [pscustomobject]@{ File = $parts[0]; Prefix = @($parts[1..($parts.Count - 1)] | Where-Object { $_ }) }
    }
    $py = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($py) { return [pscustomobject]@{ File = $py.Source; Prefix = @('-3') } }
    foreach ($name in 'python.exe', 'python3', 'python') {
        $c = Get-Command $name -ErrorAction SilentlyContinue
        if ($c) { return [pscustomobject]@{ File = $c.Source; Prefix = @() } }
    }
    return $null
}

Write-Host ''
Write-Host 'maya-autorig - Windows setup' -ForegroundColor Cyan
Write-Host ('repo: {0}' -f $script:RepoRoot)
if ($DryRun) { Write-Host 'DRY RUN - nothing will be changed' -ForegroundColor Yellow }
Write-Host ''

# --------------------------------------------------------------------------- #
Write-Host '1. Preflight'
Add-Step 'PowerShell 5.1+' 'OK' ("version {0}" -f $PSVersionTable.PSVersion) -Required

$py = Resolve-Python
if ($py) {
    $v = Invoke-Tool $py.File (@($py.Prefix) + @('--version'))
    Add-Step 'Python' 'OK' ("{0} {1} -> {2}" -f $py.File, ($py.Prefix -join ' '), $v.Output) -Required
} else {
    Add-Step 'Python' 'FAIL' 'no py.exe or python.exe on PATH; install Python 3.9+ and re-run' -Required
}

# Maya: needed to run anything, but not to lay the files down.
$mayaRoots = @()
foreach ($base in @("$env:ProgramFiles\Autodesk", 'C:\Program Files\Autodesk')) {
    if ($base -and (Test-Path -LiteralPath $base)) {
        $mayaRoots += @(Get-ChildItem -LiteralPath $base -Directory -Filter 'Maya*' -ErrorAction SilentlyContinue |
            ForEach-Object { $_.FullName })
    }
}
$mayaRoots = @($mayaRoots | Sort-Object -Unique)
if ($mayaRoots.Count -gt 0) {
    Add-Step 'Maya' 'OK' ($mayaRoots -join '; ')
} else {
    Add-Step 'Maya' 'WARN' 'no Maya found under Program Files\Autodesk; the skill installs anyway, but nothing can run'
}

# AdvancedSkeleton is content: check, never install.
$asRoots = @()
$docsMaya = Join-Path (Get-HomeDirectory) 'Documents\maya'
foreach ($cand in @(
    (Join-Path $docsMaya 'scripts\AdvancedSkeleton'),
    (Join-Path $docsMaya 'scripts'),
    (Join-Path (Get-HomeDirectory) 'maya\scripts\AdvancedSkeleton'))) {
    if (Test-Path -LiteralPath $cand) {
        $hit = @(Get-ChildItem -LiteralPath $cand -Recurse -Depth 1 -Filter 'AdvancedSkeleton.mel' -ErrorAction SilentlyContinue) |
            Select-Object -First 1
        if ($hit) { $asRoots += $hit.DirectoryName }
    }
}
$asRoots = @($asRoots | Sort-Object -Unique)
if ($asRoots.Count -gt 0) {
    Add-Step 'AdvancedSkeleton' 'OK' ($asRoots -join '; ')
} else {
    Add-Step 'AdvancedSkeleton' 'WARN' ("not found. Install AdvancedSkeleton 6.x into '{0}\scripts', " -f $docsMaya +
        'or set ADVANCEDSKELETON_DIR to the folder holding AdvancedSkeleton.mel. The rig cannot be built without it.')
}

# --------------------------------------------------------------------------- #
Write-Host ''
Write-Host '2. DCC-MCP packages'
if ($SkipPackages) {
    Add-Step 'pip install dcc-mcp-maya' 'SKIP' '-SkipPackages'
} elseif (-not $py) {
    Add-Step 'pip install dcc-mcp-maya' 'FAIL' 'no Python' -Required
} else {
    # dcc-mcp-maya pulls dcc-mcp-core (skill runtime) and dcc-mcp-server (the
    # Rust gateway binary) with it.
    $r = Invoke-Tool $py.File (@($py.Prefix) + @('-m', 'pip', 'install', '--user', '--upgrade', 'dcc-mcp-maya'))
    if ($r.ExitCode -eq 0) {
        Add-Step 'pip install dcc-mcp-maya' 'OK' 'core + server + adapter' -Required
    } else {
        Add-Step 'pip install dcc-mcp-maya' 'FAIL' $r.Output -Required
    }
}

# The console scripts land in the per-user Scripts dir, which Windows does not
# put on PATH by default: the adapter CLI is then "not found" for no reason.
$userScripts = $null
if ($py) {
    $r = Invoke-Tool $py.File (@($py.Prefix) + @('-c', 'import site,os;print(os.path.join(site.USER_BASE,"Scripts"))'))
    if ($r.ExitCode -eq 0 -and $r.Output -and -not $DryRun) { $userScripts = $r.Output.Trim() }
}
if ($userScripts) {
    $onPath = ($env:Path -split ';') -contains $userScripts
    if ($onPath) {
        Add-Step 'user Scripts on PATH' 'OK' $userScripts
    } else {
        # Prepend for this process so the rest of the script can call the CLI,
        # and persist for the user so the next shell has it too.
        $env:Path = "$userScripts;$env:Path"
        if (-not $DryRun) {
            $userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
            if (-not $userPath) { $userPath = '' }
            if (($userPath -split ';') -notcontains $userScripts) {
                [Environment]::SetEnvironmentVariable('Path', ($userScripts + ';' + $userPath).TrimEnd(';'), 'User')
            }
        }
        Add-Step 'user Scripts on PATH' 'OK' ("added {0} (this session and the user PATH)" -f $userScripts)
    }
}

# --------------------------------------------------------------------------- #
Write-Host ''
Write-Host '3. Maya adapter'
if ($SkipAdapter) {
    Add-Step 'dcc-mcp-maya install' 'SKIP' '-SkipAdapter'
} else {
    $cli = Get-Command 'dcc-mcp-maya' -ErrorAction SilentlyContinue
    if (-not $cli -and -not $DryRun) {
        Add-Step 'dcc-mcp-maya install' 'FAIL' 'dcc-mcp-maya CLI not on PATH after install; open a new shell and re-run' -Required
    } else {
        # Drops the Maya module (+ .mod) and a userSetup.py that starts the
        # embedded server whenever Maya opens. Idempotent.
        $file = if ($cli) { $cli.Source } else { 'dcc-mcp-maya' }
        $r = Invoke-Tool $file @('install', '--yes')
        if ($r.ExitCode -eq 0) {
            Add-Step 'dcc-mcp-maya install' 'OK' 'Maya module + userSetup.py' -Required
            $s = Invoke-Tool $file @('status')
            Add-Step 'dcc-mcp-maya status' $(if ($s.ExitCode -eq 0) { 'OK' } else { 'WARN' }) $s.Output
        } else {
            Add-Step 'dcc-mcp-maya install' 'FAIL' $r.Output -Required
        }
    }
}

# --------------------------------------------------------------------------- #
Write-Host ''
Write-Host '4. Skill'
$skillScript = Join-Path $PSScriptRoot 'install_skill.ps1'
if (-not (Test-Path -LiteralPath $skillScript)) {
    Add-Step 'install maya-autorig' 'FAIL' "missing $skillScript" -Required
} else {
    try {
        if ($DryRun) {
            & $skillScript -DryRun | Out-Null
            Add-Step 'install maya-autorig' 'OK' '(dry run)' -Required
        } else {
            $out = & $skillScript | Out-String
            $line = ($out -split "`n" | Where-Object { $_ -match '^installed:' } | Select-Object -First 1)
            Add-Step 'install maya-autorig' 'OK' $(if ($line) { $line.Trim() } else { 'copied' }) -Required
        }
    } catch {
        Add-Step 'install maya-autorig' 'FAIL' $_.Exception.Message -Required
    }
}

# --------------------------------------------------------------------------- #
Write-Host ''
Write-Host '5. Claude Code'
if ($SkipClaude) {
    Add-Step 'register MCP server' 'SKIP' '-SkipClaude'
} else {
    $claude = Get-Command 'claude' -ErrorAction SilentlyContinue
    if (-not $claude) {
        Add-Step 'register MCP server' 'WARN' ("claude CLI not found. Add by hand to ~\.claude.json: " +
            '"maya": { "type": "http", "url": "' + $GatewayUrl + '" }')
    } else {
        $r = Invoke-Tool $claude.Source @('mcp', 'add', '--transport', 'http', 'maya', $GatewayUrl)
        if ($r.ExitCode -eq 0) {
            Add-Step 'register MCP server' 'OK' $GatewayUrl
        } else {
            # Already registered is the common non-zero, and it is fine.
            $status = if ($r.Output -match 'already') { 'OK' } else { 'WARN' }
            Add-Step 'register MCP server' $status $r.Output
        }
    }
}

# --------------------------------------------------------------------------- #
Write-Host ''
Write-Host '6. Offline smoke test'
if (-not $py) {
    Add-Step 'offline tests' 'SKIP' 'no Python'
} elseif ($DryRun) {
    Add-Step 'offline tests' 'SKIP' 'dry run'
} else {
    Push-Location $script:RepoRoot
    try {
        # These need numpy only; they prove the tool catalog and the pure
        # geometry are intact on this machine, without opening Maya.
        $env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
        $r = Invoke-Tool $py.File (@($py.Prefix) + @('-m', 'unittest', 'discover', '-s', 'tests', '-p', 'test_*.py'))
        $tail = ($r.Output -split "`n" | Select-Object -Last 3) -join ' '
        if ($r.ExitCode -eq 0) { Add-Step 'offline tests' 'OK' $tail }
        else { Add-Step 'offline tests' 'WARN' $tail }
    } finally {
        Pop-Location
    }
}

# --------------------------------------------------------------------------- #
Write-Host ''
Write-Host 'Summary' -ForegroundColor Cyan
$script:Steps | Format-Table Name, Status, Required -AutoSize | Out-String | Write-Host
$failed = @($script:Steps | Where-Object { $_.Status -eq 'FAIL' })
$requiredFailed = @($failed | Where-Object { $_.Required })

if ($requiredFailed.Count -gt 0) {
    Write-Host ('{0} required step(s) failed.' -f $requiredFailed.Count) -ForegroundColor Red
    exit 1
}
if ($failed.Count -gt 0) {
    Write-Host ('{0} optional step(s) failed.' -f $failed.Count) -ForegroundColor Yellow
}

Write-Host 'Next:' -ForegroundColor Cyan
Write-Host '  1. Open Maya. The adapter registers itself and the gateway sees it.'
Write-Host '  2. In Claude Code:  load_skill(skill_name="maya-autorig")'
Write-Host '  3. Rig a character: gauntlet_run(source="C:\path\to\character.fbx", pose="A")'
Write-Host ''
Write-Host 'If the MCP goes quiet with Maya open, the gateway lost its owner:'
Write-Host '  check    curl http://127.0.0.1:9765/health   (000 = nobody listening)'
Write-Host '  recover  tools\repair_gateway.ps1'
if ($DryRun) { Write-Host ''; Write-Host 'DRY RUN - nothing was changed' -ForegroundColor Yellow }
exit 0
