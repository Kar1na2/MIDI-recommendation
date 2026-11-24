$ErrorActionPreference = 'Stop'
$here = $PSScriptRoot
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class ReplicationPower {
    [DllImport("kernel32.dll", SetLastError=true)]
    public static extern uint SetThreadExecutionState(uint esFlags);
}
'@
$pidFile = Join-Path $here 'keep_awake.pid'
$PID | Set-Content -LiteralPath $pidFile
try {
    if ([ReplicationPower]::SetThreadExecutionState([uint32]2147483649) -eq 0) {
        throw 'SetThreadExecutionState failed; no power settings were changed.'
    }
    while (-not (Test-Path -LiteralPath (Join-Path $here 'keep_awake.stop'))) {
        Start-Sleep -Seconds 30
    }
} finally {
    [void][ReplicationPower]::SetThreadExecutionState([uint32]2147483648)
    Remove-Item -LiteralPath $pidFile -ErrorAction SilentlyContinue
}
