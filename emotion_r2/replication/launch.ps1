$ErrorActionPreference = 'Stop'
$repo = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$stopFile = Join-Path $PSScriptRoot 'keep_awake.stop'
if (Test-Path -LiteralPath $stopFile) { Remove-Item -LiteralPath $stopFile }
$pidFile = Join-Path $PSScriptRoot 'keep_awake.pid'
$active = $false
if (Test-Path -LiteralPath $pidFile) {
    $helperId = [int](Get-Content -LiteralPath $pidFile)
    $process = Get-CimInstance Win32_Process -Filter "ProcessId=$helperId" -ErrorAction SilentlyContinue
    $active = $process -and $process.CommandLine -like '*replication*keep_awake.ps1*'
}
if (-not $active) {
    $scriptPath = Join-Path $PSScriptRoot 'keep_awake.ps1'
    Start-Process -FilePath powershell.exe -WindowStyle Hidden -ArgumentList @(
        '-NoProfile','-ExecutionPolicy','Bypass','-File',('"' + $scriptPath + '"')) `
        -RedirectStandardOutput (Join-Path $PSScriptRoot 'keep_awake.log') `
        -RedirectStandardError (Join-Path $PSScriptRoot 'keep_awake_error.log')
}
Push-Location -LiteralPath $repo
try {
    & wsl.exe -e bash -lc 'cd "/mnt/d/code files/personal/MIDI-recommendation" && /home/usr/venvs/midi/bin/python -B -m emotion_r2.replication.supervisor start'
    if ($LASTEXITCODE -ne 0) { throw "Task K supervisor launch failed with exit $LASTEXITCODE" }
} finally { Pop-Location }
