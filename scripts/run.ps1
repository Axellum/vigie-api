<#
.SYNOPSIS
  Starts the Vigie API on this Windows PC (PowerShell 5.1 compatible).

.DESCRIPTION
  1. Finds Python 3.12 or newer.
  2. Creates (once) a virtual environment in <repository>\.venv.
  3. Installs requirements.txt (only when the file changed since the last run).
  4. Starts: uvicorn app.main:app --host 0.0.0.0 --port 8080

  The script can be started from any folder: it locates the repository from its own path.
  Press Ctrl+C to stop the server.

.PARAMETER Port
  TCP port to listen on. Default 8080 (the port the display uses).

.PARAMETER BindAddress
  Address to listen on. Default 0.0.0.0 (all interfaces, so the display can reach the API on the LAN).

.PARAMETER LogFile
  Optional file that receives the server output (appended). Use it for a hidden start at logon.

.PARAMETER NoInstall
  Skip the requirements check (faster start when nothing changed).

.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File scripts\run.ps1
#>
[CmdletBinding()]
param(
    [int]$Port = 8080,
    [string]$BindAddress = "0.0.0.0",
    [string]$LogFile = "",
    [switch]$NoInstall
)

$ErrorActionPreference = "Stop"

$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo

function Find-Python {
    # Try the py launcher first, then python on PATH. Return the command and its arguments.
    $candidates = @(
        @{ Cmd = "py"; Args = @("-3") },
        @{ Cmd = "python"; Args = @() }
    )
    foreach ($c in $candidates) {
        if (-not (Get-Command $c.Cmd -ErrorAction SilentlyContinue)) { continue }
        $versionText = & $c.Cmd @($c.Args + @("-c", "import sys; print('%d.%d' % sys.version_info[:2])")) 2>$null
        if ($LASTEXITCODE -ne 0 -or -not $versionText) { continue }
        $parts = ([string]$versionText).Trim().Split(".")
        if ([int]$parts[0] -gt 3 -or ([int]$parts[0] -eq 3 -and [int]$parts[1] -ge 12)) {
            return $c
        }
    }
    throw "Python 3.12 or newer was not found. Install it from https://www.python.org/downloads/ and run this script again."
}

$venvDir = Join-Path $repo ".venv"
$venvPython = Join-Path $venvDir "Scripts\python.exe"

if (-not (Test-Path $venvPython)) {
    $py = Find-Python
    Write-Host "Creating the virtual environment in $venvDir ..."
    & $py.Cmd @($py.Args + @("-m", "venv", $venvDir))
    if ($LASTEXITCODE -ne 0) { throw "Could not create the virtual environment." }
}

if (-not $NoInstall) {
    $requirements = Join-Path $repo "requirements.txt"
    $stamp = Join-Path $venvDir "requirements.sha256"
    $sha = [System.Security.Cryptography.SHA256]::Create()
    $currentHash = [BitConverter]::ToString($sha.ComputeHash([System.IO.File]::ReadAllBytes($requirements))).Replace("-", "")
    $installedHash = ""
    if (Test-Path $stamp) { $installedHash = (Get-Content $stamp -Raw).Trim() }
    if ($currentHash -ne $installedHash) {
        Write-Host "Installing the requirements ..."
        & $venvPython -m pip install --disable-pip-version-check --quiet -r $requirements
        if ($LASTEXITCODE -ne 0) { throw "pip install failed." }
        Set-Content -Path $stamp -Value $currentHash
    }
}

Write-Host "Vigie API listening on http://${BindAddress}:${Port}  (frame: /v1/embedded/frame, dashboard: /dashboard)"

if ($LogFile) {
    $logDir = Split-Path -Parent $LogFile
    if ($logDir -and -not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir | Out-Null }
    # cmd.exe handles the append redirection of both streams without PowerShell turning stderr into errors.
    $command = '"' + $venvPython + '" -m uvicorn app.main:app --host ' + $BindAddress + ' --port ' + $Port + ' >> "' + $LogFile + '" 2>&1'
    & cmd.exe /c ('"' + $command + '"')
} else {
    & $venvPython -m uvicorn app.main:app --host $BindAddress --port $Port
}
exit $LASTEXITCODE
