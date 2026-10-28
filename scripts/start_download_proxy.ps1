# Forward the existing local FlClash proxy to this project's VM loopback only.
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
$taskKey = Join-Path $taskRoot 'vm\openEuler-dify\dify_ed25519'
$taskKnown = Join-Path $taskRoot 'vm\openEuler-dify\known_hosts'
$taskLog = Join-Path $taskRoot 'vm\openEuler-dify\download-proxy.log'
Get-NetTCPConnection -LocalAddress 127.0.0.1 -LocalPort 7890 -State Listen | Out-Null
$taskSid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
icacls.exe $taskKey /inheritance:r /grant:r ('*' + $taskSid + ':(F)') | Out-Null
$taskSshArgs = @('-F', 'NUL', '-N', '-T', '-oBatchMode=yes', '-oExitOnForwardFailure=yes',
    '-oStrictHostKeyChecking=yes', "-oUserKnownHostsFile=$taskKnown", '-oServerAliveInterval=30',
    '-oServerAliveCountMax=3', '-i', $taskKey, '-R', '127.0.0.1:17890:127.0.0.1:7890', 'dify@192.168.18.176')
$taskProcess = Start-Process -FilePath "$env:WINDIR\System32\OpenSSH\ssh.exe" -ArgumentList $taskSshArgs -WindowStyle Hidden -RedirectStandardError $taskLog -PassThru
Write-Output "Download tunnel process: $($taskProcess.Id)"
