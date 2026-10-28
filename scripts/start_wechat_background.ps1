param([switch]$Tunnel, [ValidateSet('fixed', 'async', 'poll', 'direct')][string]$ReplyMode = 'fixed',
      [ValidateRange(1024, 65535)][int]$Port = 8080)
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
$taskLogDir = Join-Path $taskRoot 'logs'
New-Item -ItemType Directory -Path $taskLogDir -Force | Out-Null
$taskLogSuffix = Get-Date -Format 'yyyyMMdd-HHmmss'

if ($Tunnel) {
    $taskOrigin = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
    if ($taskOrigin.Count -ne 1 -or $taskOrigin[0].LocalAddress -ne '127.0.0.1') { throw "Expected this project callback on loopback port $Port." }
    $taskOriginProcess = Get-CimInstance Win32_Process -Filter "ProcessId=$($taskOrigin[0].OwningProcess)"
    $taskCallbackPaths = @('services\start_wechat_fixed.py', 'services\wechat_public_bot.py') | ForEach-Object { [regex]::Escape((Join-Path $taskRoot $_)) }
    if ($taskOriginProcess.CommandLine -notmatch ($taskCallbackPaths -join '|')) { throw 'The origin is not this project callback.' }
    $taskExe = Join-Path $taskRoot 'deploy\installers\cloudflared-windows-amd64-2026.10.2.exe'
    if (-not (Test-Path -LiteralPath $taskExe)) { throw 'Verified cloudflared executable is missing.' }
    if ((Get-FileHash -LiteralPath $taskExe -Algorithm SHA256).Hash.ToLowerInvariant() -ne '12c7c887f8aaab779aef1dfa9b60673530a5adb88ecc55dd6df8f56e0b70f199') {
        throw 'cloudflared SHA256 differs from the verified official release.'
    }
    if (Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -eq $taskExe }) { throw 'This project tunnel is already running.' }
    $taskLog = Join-Path $taskLogDir "cloudflared-$taskLogSuffix.log"
    $taskArguments = @('tunnel', '--url', "http://127.0.0.1:$Port", '--protocol', 'http2', '--no-autoupdate', '--metrics', '127.0.0.1:0', '--loglevel', 'info', '--retries', '2', '--logfile', $taskLog)
} else {
    if (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) {
        throw "Port $Port is occupied; the existing service was left unchanged."
    }
    if ($ReplyMode -eq 'fixed' -and $Port -ne 8080) { throw 'The fixed-mode entry uses port 8080.' }
    $taskExe = Join-Path $taskRoot '.venv\Scripts\python.exe'
    $taskArguments = if ($ReplyMode -ne 'fixed') {
        @('-X', 'utf8', (Join-Path $taskRoot 'services\wechat_public_bot.py'), '--env-file', (Join-Path $taskRoot '.env'), '--host', '127.0.0.1', '--port', "$Port")
    } else { @('-X', 'utf8', (Join-Path $taskRoot 'services\start_wechat_fixed.py')) }
    $taskLog = Join-Path $taskLogDir "wechat-$ReplyMode-$taskLogSuffix.log"
}

# WMI owns the new hidden process; it does not inherit this tool command's lifetime.
function Quote-TaskLiteral([string]$taskText) { "'" + $taskText.Replace("'", "''") + "'" }
$taskConsoleLog = if ($Tunnel) { $taskLog + '.console.log' } else { $taskLog }
$taskInvocation = "Get-ChildItem Env: | Where-Object Name -match '^(HTTP_PROXY|HTTPS_PROXY|ALL_PROXY|NO_PROXY)$' | Remove-Item; " +
    '$env:NO_PROXY = ''*''; ' + $(if (-not $Tunnel -and $ReplyMode -ne 'fixed') { '$env:WECHAT_REPLY_MODE = ' + (Quote-TaskLiteral $ReplyMode) + '; $env:WECHAT_MESSAGE_MODE = ''plain''; ' } else { '' }) +
    '& ' + (Quote-TaskLiteral $taskExe) + ' ' + (($taskArguments | ForEach-Object { Quote-TaskLiteral $_ }) -join ' ') + ' *> ' + (Quote-TaskLiteral $taskConsoleLog)
$taskEncoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($taskInvocation))
$taskStartup = New-CimInstance -ClassName Win32_ProcessStartup -ClientOnly -Property @{
    ShowWindow = [uint16]0
}
$taskShell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$taskCreated = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{
    CommandLine = '"' + $taskShell + '" -NoProfile -WindowStyle Hidden -EncodedCommand ' + $taskEncoded
    CurrentDirectory = $taskRoot
    ProcessStartupInformation = $taskStartup
}
if ($taskCreated.ReturnValue -ne 0) { throw "Hidden process creation failed: $($taskCreated.ReturnValue)" }
[pscustomobject]@{ ParentProcessId = $taskCreated.ProcessId; Kind = $(if ($Tunnel) { 'quick-tunnel' } else { "$ReplyMode-callback" }); Log = $taskLog } | ConvertTo-Json -Compress
