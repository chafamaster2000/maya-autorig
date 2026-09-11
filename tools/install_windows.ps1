#Requires -Version 5.1
<#
.SYNOPSIS
    Set up maya-autorig on Windows end to end: the DCC-MCP packages, the Maya
    adapter, this skill, and the agent connections. Works with Codex AND
    Claude Code: both get the `maya` MCP server and the skill.

    The usual way to run this is not by hand: paste the repository link to
    Codex or Claude Code ("install https://github.com/chafamaster2000/maya-autorig")
    and the agent runs tools\bootstrap.ps1, which clones (or updates) and calls
    this script. Takes about 5-10 minutes the first time (Python, Node and the
    agent CLIs download), about a minute afterwards, a few seconds when nothing
    changed.

.DESCRIPTION
    Every step is idempotent and reports OK / SKIP / FAIL; the script exits
    non-zero if any required step failed, so it can gate a machine setup.
    Nothing is changed under -DryRun.

    The chain it builds:

        Claude Code / Codex --HTTP--> dcc-mcp gateway (127.0.0.1:9765) --> Maya adapter
                                        |                              |
                                        +-- scans skills --------------+
                                            ...including maya-autorig

    AdvancedSkeleton is content, not a package: this script checks for it and
    tells you where to put it, but does not install it.

.PARAMETER DryRun
    Report every step and change nothing.

.PARAMETER SkipPrereqs
    Do not install Python, Node.js, Claude Code or Codex even when they are missing
    (they are installed through winget by default).

.PARAMETER SkipPackages
    Do not touch pip (the DCC-MCP packages are already installed).

.PARAMETER SkipAdapter
    Do not run 'dcc-mcp-maya install' (the Maya module is already hooked up).

.PARAMETER SkipClaude
    Do not register the MCP server with Claude Code (nor install it).

.PARAMETER SkipCodex
    Do not register the MCP server with Codex (nor install it).

.PARAMETER GatewayUrl
    MCP endpoint the agents should talk to. Default http://127.0.0.1:9765/mcp

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
    [switch] $SkipPrereqs,
    [switch] $SkipPackages,
    [switch] $SkipAdapter,
    [switch] $SkipClaude,
    [switch] $SkipCodex,
    [string] $GatewayUrl = 'http://127.0.0.1:9765/mcp',
    [string] $Python
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$script:Steps = New-Object System.Collections.ArrayList
$script:RepoRoot = Split-Path -Parent $PSScriptRoot

$script:Version = 'unknown'
try { $script:Version = (Get-Content -LiteralPath (Join-Path (Split-Path -Parent $PSScriptRoot) 'VERSION') -ErrorAction Stop | Select-Object -First 1).Trim() } catch { }
# Only things that actually changed, each with the restart or login it costs:
# maya | restart-codex | restart-claude | login-codex | login-claude | shell
$script:Changes = @()
function Add-Change { param([string] $What, [string] $Why) $script:Changes += [pscustomobject]@{ What = $What; Why = $Why } }
function Test-MayaRunning { return [bool]@(Get-Process -Name 'maya' -ErrorAction SilentlyContinue).Count }

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
    # Which Python owns the DCC-MCP packages matters: the adapter's lifecycle
    # CLI runs its preflight with mayapy, and a second install under another
    # Python leaves two copies of the CLI, one of them broken. Preference:
    #   -Python  >  the Python of an existing dcc-mcp-maya (its Scripts dir)
    #            >  mayapy.exe of the newest Maya found  >  py -3 / python
    if ($Python) {
        $parts = $Python -split '\s+'
        return [pscustomobject]@{ File = $parts[0]; Prefix = @($parts[1..($parts.Count - 1)] | Where-Object { $_ }); Why = '-Python' }
    }
    $appData = if ($env:APPDATA) { $env:APPDATA } else { Join-Path (Get-HomeDirectory) 'AppData\Roaming' }
    foreach ($scripts in @(Get-ChildItem -LiteralPath (Join-Path $appData 'Python') -Directory -ErrorAction SilentlyContinue |
            ForEach-Object { Join-Path $_.FullName 'Scripts' })) {
        if (Test-Path -LiteralPath (Join-Path $scripts 'dcc-mcp-maya.exe')) {
            # ...\Python\Python313\Scripts -> the interpreter whose user site
            # this is; `py -3.13` resolves it.
            if ((Split-Path -Leaf (Split-Path -Parent $scripts)) -match 'Python(\d)(\d+)') {
                $py = Get-Command py.exe -ErrorAction SilentlyContinue
                if ($py) { return [pscustomobject]@{ File = $py.Source; Prefix = @('-' + $Matches[1] + '.' + $Matches[2]); Why = "owns the existing dcc-mcp-maya in $scripts" } }
            }
        }
    }
    foreach ($root in @($script:MayaRoots | Sort-Object -Descending)) {
        $mayapy = Join-Path (Join-Path $root 'bin') 'mayapy.exe'
        if (Test-Path -LiteralPath $mayapy) { return [pscustomobject]@{ File = $mayapy; Prefix = @(); Why = "mayapy of $root" } }
    }
    $py = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($py) { return [pscustomobject]@{ File = $py.Source; Prefix = @('-3'); Why = 'py -3' } }
    foreach ($name in 'python.exe', 'python3', 'python') {
        $c = Get-Command $name -ErrorAction SilentlyContinue
        if ($c) { return [pscustomobject]@{ File = $c.Source; Prefix = @(); Why = "$name on PATH" } }
    }
    return $null
}

function Update-SessionPath {
    <# winget writes the new PATH to the registry; a process that is already
       running keeps the PATH it started with, so anything just installed is
       invisible until the shell is reopened. Re-read both scopes instead. #>
    $machine = [Environment]::GetEnvironmentVariable('Path', 'Machine')
    $user = [Environment]::GetEnvironmentVariable('Path', 'User')
    $env:Path = (@($machine, $user) | Where-Object { $_ }) -join ';'
}

function Install-WithWinget {
    <# Install a package by winget id. Returns $true when winget reports
       success or already-installed. #>
    param([string] $Id, [string] $Label)
    $winget = Get-Command winget.exe -ErrorAction SilentlyContinue
    if (-not $winget) {
        Add-Step $Label 'FAIL' ('winget is not available on this machine (Windows 10 1709+ / App Installer). ' +
            'Install {0} by hand and re-run.' -f $Id) -Required
        return $false
    }
    $r = Invoke-Tool $winget.Source @('install', '--id', $Id, '--exact', '--source', 'winget',
        '--accept-package-agreements', '--accept-source-agreements', '--disable-interactivity')
    # winget exits non-zero for "already installed"; that is a success here.
    $already = $r.Output -match 'already installed|No available upgrade'
    if ($r.ExitCode -eq 0 -or $already) {
        Update-SessionPath
        Add-Step $Label 'OK' $(if ($already) { 'already installed' } else { $Id }) -Required
        return $true
    }
    Add-Step $Label 'FAIL' $r.Output -Required
    return $false
}

Write-Host ''
Write-Host 'maya-autorig - Windows setup  (works with Codex and Claude Code)' -ForegroundColor Cyan
Write-Host 'Usually run for you by an agent after you paste the repo link; first time ~5-10 min, later runs ~1 min.' -ForegroundColor DarkGray
$repoVersion = 'not a git clone'
if (Get-Command git -ErrorAction SilentlyContinue) {
    try { $repoVersion = (& git -C (Split-Path -Parent $PSScriptRoot) describe --tags --always 2>$null | Out-String).Trim() } catch { }
}
Write-Host ("version: {0}  (git: {1})" -f $script:Version, $repoVersion)
Write-Host ('repo: {0}' -f $script:RepoRoot)
if ($DryRun) { Write-Host 'DRY RUN - nothing will be changed' -ForegroundColor Yellow }
Write-Host ''

# --------------------------------------------------------------------------- #
Write-Host '1. Preflight'
Add-Step 'PowerShell 5.1+' 'OK' ("version {0}" -f $PSVersionTable.PSVersion) -Required

# Maya: needed to run anything, but not to lay the files down.
$script:MayaRoots = @()
foreach ($base in @("$env:ProgramFiles\Autodesk", 'C:\Program Files\Autodesk')) {
    if ($base -and (Test-Path -LiteralPath $base)) {
        $script:MayaRoots += @(Get-ChildItem -LiteralPath $base -Directory -Filter 'Maya*' -ErrorAction SilentlyContinue |
            ForEach-Object { $_.FullName })
    }
}
$script:MayaRoots = @($script:MayaRoots | Sort-Object -Unique)
if ($script:MayaRoots.Count -gt 0) {
    Add-Step 'Maya' 'OK' ($script:MayaRoots -join '; ')
} else {
    Add-Step 'Maya' 'WARN' 'no Maya found under Program Files\Autodesk; the skill installs anyway, but nothing can run'
}

# Anything that can be installed from a package source, is. What is left out
# is left out for a reason, not for lack of trying: Maya and AdvancedSkeleton
# are licensed products this script has no right to fetch.
$py = Resolve-Python
if (-not $py -and -not $SkipPrereqs -and -not $DryRun) {
    Write-Host '   Python missing: installing it' -ForegroundColor DarkGray
    if (Install-WithWinget -Id 'Python.Python.3.12' -Label 'install Python') {
        $py = Resolve-Python
    }
} elseif (-not $py -and $DryRun) {
    Add-Step 'install Python' 'SKIP' 'dry run: would winget install Python.Python.3.12'
}
if ($py) {
    $v = Invoke-Tool $py.File (@($py.Prefix) + @('--version'))
    Add-Step 'Python' 'OK' ("{0} {1} -> {2} ({3})" -f $py.File, ($py.Prefix -join ' '), $v.Output, $py.Why) -Required
} elseif ($SkipPrereqs) {
    Add-Step 'Python' 'FAIL' 'no Python and -SkipPrereqs was passed' -Required
} else {
    Add-Step 'Python' 'FAIL' 'Python is still not on PATH after the install; reopen the shell and re-run' -Required
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
    Add-Step 'AdvancedSkeleton' 'WARN' ("not found. It is licensed content from Animation Studios, " +
        "not a package, so this installer will not fetch it: get it from " +
        "https://www.animationstudios.com.au/advanced-skeleton and run its setup into " +
        ("'{0}\scripts', " -f $docsMaya) +
        'or set ADVANCEDSKELETON_DIR to the folder holding AdvancedSkeleton.mel. No rig can be built without it.')
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
    # Rust gateway binary) with it. The version before and after tells whether
    # Maya is now running an old adapter.
    function Get-AdapterVersion {
        $o = Invoke-Tool $py.File (@($py.Prefix) + @('-m', 'pip', 'show', 'dcc-mcp-maya'))
        if ($o.Output -match 'Version:\s*(\S+)') { return $Matches[1] }
        return ''
    }
    $before = Get-AdapterVersion
    $r = Invoke-Tool $py.File (@($py.Prefix) + @('-m', 'pip', 'install', '--user', '--upgrade', 'dcc-mcp-maya'))
    if ($r.ExitCode -eq 0) {
        $after = Get-AdapterVersion
        if (-not $DryRun -and $before -ne $after) {
            Add-Step 'pip install dcc-mcp-maya' 'OK' ("{0} -> {1}" -f $(if ($before) { $before } else { 'none' }), $after) -Required
            Add-Change ("dcc-mcp-maya {0} -> {1}" -f $(if ($before) { $before } else { 'none' }), $after) 'maya'
        } else {
            Add-Step 'pip install dcc-mcp-maya' 'OK' ("already {0}" -f $(if ($after) { $after } else { 'installed' })) -Required
        }
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
        Add-Change ("user Scripts dir added to PATH: {0}" -f $userScripts) 'shell'
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
        # The CLI's plain output is "install: failed" and nothing else; --json
        # carries the reason. Its preflight can fail on a machine where the
        # module is already in place and working (seen on macOS: mayapy 3.13 +
        # Maya 2027, `maya.cmds` has no `about` before maya.standalone is
        # initialised) -- that is a warning with the reason, not a failed install.
        $r = Invoke-Tool $file @('install', '--yes', '--json')
        $reason = $r.Output
        try { $j = $r.Output | ConvertFrom-Json; if ($j.failure_message) { $reason = ("{0}" -f $j.failure_message).Split("`n")[-1] } } catch { }
        $modulesDir = Join-Path (Join-Path ([Environment]::GetFolderPath('MyDocuments')) 'maya') 'modules'
        if ($env:MAYA_APP_DIR) { $modulesDir = Join-Path $env:MAYA_APP_DIR 'modules' }
        $moduleInPlace = [bool]@(Get-ChildItem -LiteralPath $modulesDir -Filter 'dcc_mcp_maya*.mod' -ErrorAction SilentlyContinue).Count
        if ($r.ExitCode -eq 0) {
            Add-Step 'dcc-mcp-maya install' 'OK' 'Maya module + userSetup.py' -Required
            $s = Invoke-Tool $file @('status')
            Add-Step 'dcc-mcp-maya status' $(if ($s.ExitCode -eq 0) { 'OK' } else { 'WARN' }) $s.Output
        } elseif ($moduleInPlace) {
            Add-Step 'dcc-mcp-maya install' 'WARN' ("module already in place at {0}; the CLI's own preflight failed: {1}" -f $modulesDir, $reason)
        } else {
            Add-Step 'dcc-mcp-maya install' 'FAIL' $reason -Required
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
            if ($out -match 'skill changed') { Add-Change 'skill copy for the gateway updated' 'maya' }
            $line = ($out -split "`n" | Where-Object { $_ -match '^installed:' } | Select-Object -First 1)
            Add-Step 'install maya-autorig' 'OK' $(if ($line) { $line.Trim() } else { 'copied' }) -Required
        }
    } catch {
        Add-Step 'install maya-autorig' 'FAIL' $_.Exception.Message -Required
    }
}

# --------------------------------------------------------------------------- #
Write-Host ''
Write-Host '5. Agents: Claude Code and Codex'
# Both CLIs are npm packages, so Node comes first. Returns the npm command
# or $null; installing Node is skipped under -SkipPrereqs / -DryRun.
function Get-Npm {
    $npm = Get-Command npm.cmd -ErrorAction SilentlyContinue
    if (-not $npm) { $npm = Get-Command npm -ErrorAction SilentlyContinue }
    if ($npm -or $SkipPrereqs -or $DryRun) { return $npm }
    [void](Install-WithWinget -Id 'OpenJS.NodeJS.LTS' -Label 'install Node.js')
    $npm = Get-Command npm.cmd -ErrorAction SilentlyContinue
    if (-not $npm) { $npm = Get-Command npm -ErrorAction SilentlyContinue }
    return $npm
}

# Install an agent CLI when missing, then register the gateway with it.
# $Register is the CLI's own "add MCP server" argument list; $ByHand is what
# to print when the CLI is not there.
function Register-Agent {
    param([string] $Name, [string] $Package, [string[]] $Register, [string] $ByHand)
    $cli = Get-Command $Name -ErrorAction SilentlyContinue
    if (-not $cli -and -not $SkipPrereqs -and -not $DryRun) {
        Write-Host ("   {0} missing: installing it with npm" -f $Name) -ForegroundColor DarkGray
        $npm = Get-Npm
        if ($npm) {
            $r = Invoke-Tool $npm.Source @('install', '-g', $Package)
            Update-SessionPath
            Add-Step ("install {0}" -f $Name) $(if ($r.ExitCode -eq 0) { 'OK' } else { 'WARN' }) `
                $(if ($r.ExitCode -eq 0) { $Package } else { $r.Output })
            if ($r.ExitCode -eq 0) { Add-Change ("{0} CLI installed" -f $Name) ('login-' + $Name) }
            $cli = Get-Command $Name -ErrorAction SilentlyContinue
        } else {
            Add-Step ("install {0}" -f $Name) 'WARN' 'npm not on PATH after installing Node; reopen the shell and re-run'
        }
    }
    if (-not $cli) {
        Add-Step ("register MCP server ({0})" -f $Name) 'WARN' ("{0} CLI not found. {1}" -f $Name, $ByHand)
        return
    }
    # Was it there before? That decides whether the agent needs a restart.
    $had = (Invoke-Tool $cli.Source @('mcp', 'get', 'maya')).ExitCode -eq 0
    $r = Invoke-Tool $cli.Source $Register
    if ($r.ExitCode -eq 0) {
        if ($had) {
            Add-Step ("register MCP server ({0})" -f $Name) 'OK' 'already registered'
        } else {
            Add-Step ("register MCP server ({0})" -f $Name) 'OK' ("{0} (new)" -f $GatewayUrl)
            Add-Change ("maya MCP server registered in {0}" -f $Name) ('restart-' + $Name)
        }
    } else {
        # Already registered is the common non-zero, and it is fine.
        $status = if ($r.Output -match 'already') { 'OK' } else { 'WARN' }
        Add-Step ("register MCP server ({0})" -f $Name) $status $r.Output
    }
}

if ($SkipClaude) {
    Add-Step 'register MCP server (claude)' 'SKIP' '-SkipClaude'
} else {
    Register-Agent -Name 'claude' -Package '@anthropic-ai/claude-code' `
        -Register @('mcp', 'add', '--transport', 'http', 'maya', $GatewayUrl) `
        -ByHand ('Add by hand to ~\.claude.json: "maya": { "type": "http", "url": "' + $GatewayUrl + '" }')
}
if ($SkipCodex) {
    Add-Step 'register MCP server (codex)' 'SKIP' '-SkipCodex'
} else {
    Register-Agent -Name 'codex' -Package '@openai/codex' `
        -Register @('mcp', 'add', 'maya', '--url', $GatewayUrl) `
        -ByHand ('Add by hand to ~\.codex\config.toml: [mcp_servers.maya]  url = "' + $GatewayUrl + '"')
}
# The agent-side SKILL.md goes wherever an agent now exists: install_skill.ps1
# ran in step 4, possibly before the CLIs above were installed.
if (-not $DryRun -and ((Get-Command codex -ErrorAction SilentlyContinue) -or (Get-Command claude -ErrorAction SilentlyContinue))) {
    try { & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $skillScript | Out-Null } catch { }
}

# --------------------------------------------------------------------------- #
Write-Host ''
Write-Host '6. Verify'
if (-not $py) {
    Add-Step 'verify' 'SKIP' 'no Python'
} elseif ($DryRun) {
    Add-Step 'verify' 'SKIP' 'dry run'
} else {
    # The server runs a STRICT scan of the user skills directory at startup:
    # one invalid skill there and Maya's adapter raises on boot. Catching that
    # here, before Maya ever opens, is the check that matters.
    $probe = @'
import json, sys
try:
    from dcc_mcp_core._core import scan_and_load_strict as scan
except Exception as exc:
    print(json.dumps({"ok": None, "why": "dcc_mcp_core not importable from this Python: %s" % exc}))
    sys.exit(0)
try:
    found = scan(extra_paths=[sys.argv[1]], dcc_name="maya")
    print(json.dumps({"ok": True, "found": str(found)[:200]}))
except Exception as exc:
    print(json.dumps({"ok": False, "why": "%s: %s" % (type(exc).__name__, exc)}))
'@
    $probeFile = Join-Path ([IO.Path]::GetTempPath()) 'maya_autorig_scan_probe.py'
    Set-Content -LiteralPath $probeFile -Value $probe -Encoding UTF8
    $skillsDir = Split-Path -Parent (Join-Path (Join-Path (Join-Path (Get-HomeDirectory) '.dcc-mcp') 'maya') 'skills\maya-autorig')
    $r = Invoke-Tool $py.File (@($py.Prefix) + @($probeFile, $skillsDir))
    Remove-Item -LiteralPath $probeFile -Force -ErrorAction SilentlyContinue
    try { $verdict = $r.Output | ConvertFrom-Json } catch { $verdict = $null }
    if ($verdict -and $verdict.ok -eq $true) {
        Add-Step 'skill scans clean' 'OK' $verdict.found -Required
    } elseif ($verdict -and $null -eq $verdict.ok) {
        Add-Step 'skill scans clean' 'SKIP' $verdict.why
    } else {
        $why = if ($verdict) { $verdict.why } else { $r.Output }
        Add-Step 'skill scans clean' 'FAIL' $why -Required
    }

    $cliV = Get-Command 'dcc-mcp-maya' -ErrorAction SilentlyContinue
    if ($cliV -and -not $SkipAdapter) {
        $v = Invoke-Tool $cliV.Source @('verify')
        Add-Step 'dcc-mcp-maya verify' $(if ($v.ExitCode -eq 0) { 'OK' } else { 'WARN' }) $v.Output
    }

    # The development tree ships offline tests; a released copy does not.
    $testsDir = Join-Path $script:RepoRoot 'tests'
    if (Test-Path -LiteralPath $testsDir) {
        Push-Location $script:RepoRoot
        try {
            $env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
            $r = Invoke-Tool $py.File (@($py.Prefix) + @('-m', 'unittest', 'discover', '-s', 'tests', '-p', 'test_*.py'))
            $tail = ($r.Output -split "`n" | Select-Object -Last 3) -join ' '
            Add-Step 'offline tests' $(if ($r.ExitCode -eq 0) { 'OK' } else { 'WARN' }) $tail
        } finally { Pop-Location }
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

# The part the person actually needs: what changed, and what each change
# costs them. Nothing changed -> say so, and that nothing needs a restart.
Write-Host 'What changed -> what to do' -ForegroundColor Cyan
if ($DryRun) {
    Write-Host '   (dry run: nothing was changed)'
} elseif (@($script:Changes).Count -eq 0) {
    Write-Host ("   nothing changed: everything was already installed at version {0}." -f $script:Version)
    Write-Host '   -> nothing to restart. If Maya is closed, open it; then ask the agent to rig.'
} else {
    $why = @($script:Changes | ForEach-Object { $_.Why })
    foreach ($c in $script:Changes) { Write-Host ("   - {0}" -f $c.What) }
    $needMaya = $why -contains 'maya'
    $loginCodex = $why -contains 'login-codex'; $loginClaude = $why -contains 'login-claude'
    $needCodex = $loginCodex -or ($why -contains 'restart-codex')
    $needClaude = $loginClaude -or ($why -contains 'restart-claude')
    Write-Host ''
    Write-Host '   To do, in this order:'
    $n = 0
    if ($loginCodex) { $n++; Write-Host ("   {0}. Codex was just installed: run 'codex login' once (it opens the browser)." -f $n) }
    if ($loginClaude) { $n++; Write-Host ("   {0}. Claude Code was just installed: run 'claude' once and log in." -f $n) }
    if ($needMaya) {
        $n++
        if (Test-MayaRunning) { Write-Host ("   {0}. Maya is open and is running the old copy: close it and open it again (one window)." -f $n) }
        else { Write-Host ("   {0}. Open Maya (one window). It reads the new copy at start-up." -f $n) }
    }
    if ($needCodex -and -not $loginCodex) { $n++; Write-Host ("   {0}. Codex: start a new session (its MCP servers and skills are read at start-up). A session that is already open will not see 'maya'." -f $n) }
    if ($needClaude -and -not $loginClaude) { $n++; Write-Host ("   {0}. Claude Code: type /mcp in the open session, or start a new one." -f $n) }
    if (-not $needMaya) { $n++; Write-Host ("   {0}. Maya: nothing to do (leave it open if it is open)." -f $n) }
}
Write-Host ''
Write-Host 'Then, in Codex or Claude Code:  load_skill(skill_name="maya-autorig")'
Write-Host 'and rig a character:            gauntlet_run(source="C:\path\to\character.fbx", pose="A")'
Write-Host 'If the MCP goes quiet with Maya open, the gateway lost its owner:'
Write-Host '  check    curl http://127.0.0.1:9765/health   (000 = nobody listening)'
Write-Host '  recover  tools\repair_gateway.ps1'
if ($DryRun) { Write-Host ''; Write-Host 'DRY RUN - nothing was changed' -ForegroundColor Yellow }
exit 0
