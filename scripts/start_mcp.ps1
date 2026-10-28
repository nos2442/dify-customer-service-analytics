$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
if (Get-NetTCPConnection -LocalPort 8095 -State Listen -ErrorAction SilentlyContinue) {
    throw 'Port 8095 is already in use; existing service was left unchanged.'
}
$taskLogDir = Join-Path $taskRoot 'logs'
New-Item -ItemType Directory -Path $taskLogDir -Force | Out-Null
$taskLog = Join-Path $taskLogDir ('mcp-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.log')
function Quote-TaskLiteral([string]$taskText) { "'" + $taskText.Replace("'", "''") + "'" }
$taskInvocation = '& ' + (Quote-TaskLiteral (Join-Path $taskRoot '.venv\Scripts\python.exe')) +
    ' -X utf8 ' + (Quote-TaskLiteral (Join-Path $taskRoot 'services\mysql_server.py')) +
    ' *> ' + (Quote-TaskLiteral $taskLog)
$taskEncoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($taskInvocation))
# WMI keeps this hidden service independent of the launching tool's lifetime.
$taskStartup = New-CimInstance -ClassName Win32_ProcessStartup -ClientOnly -Property @{ ShowWindow = [uint16]0 }
$taskShell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$taskCreated = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{
    CommandLine = '"' + $taskShell + '" -NoProfile -WindowStyle Hidden -EncodedCommand ' + $taskEncoded
    CurrentDirectory = $taskRoot
    ProcessStartupInformation = $taskStartup
}
if ($taskCreated.ReturnValue -ne 0) { throw "Hidden MCP creation failed: $($taskCreated.ReturnValue)" }
[pscustomobject]@{ ParentProcessId = $taskCreated.ProcessId; Log = $taskLog } | ConvertTo-Json -Compress
