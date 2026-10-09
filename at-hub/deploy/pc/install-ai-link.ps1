# Install (or remove) the "AT-HUB AI Link" scheduled task: starts athub-ai-link.ps1 hidden whenever you log on.
#   powershell -ExecutionPolicy Bypass -File install-ai-link.ps1            install + start now
#   powershell -ExecutionPolicy Bypass -File install-ai-link.ps1 -Remove    stop + remove
param([switch]$Remove)
$name = "AT-HUB AI Link"
$script = Join-Path $PSScriptRoot "athub-ai-link.ps1"

Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" | Where-Object { $_.CommandLine -like "*athub-ai-link.ps1*" } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Get-CimInstance Win32_Process -Filter "Name='ssh.exe'" | Where-Object { $_.CommandLine -like "*11435:127.0.0.1:11434*" } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
if (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) { Unregister-ScheduledTask -TaskName $name -Confirm:$false }
if ($Remove) { "Removed: $name"; return }

$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$script`""
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) `
  -MultipleInstances IgnoreNew -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -StartWhenAvailable
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings -Principal $principal `
  -Description "Lends this PC's local AI (Ollama) to the cloud AT-HUB over an encrypted SSH link while this PC is on." | Out-Null
Start-ScheduledTask -TaskName $name
"Installed and started: $name (log: $env:LOCALAPPDATA\AT-HUB\ai-link.log)"
