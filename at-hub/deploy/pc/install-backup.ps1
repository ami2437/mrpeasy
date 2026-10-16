# Install (or remove) the "AT-HUB Backup" scheduled task: athub-backup.ps1 at log-on and every day at 12:30.
#   powershell -ExecutionPolicy Bypass -File install-backup.ps1            install (+ the server side) and run once now
#   powershell -ExecutionPolicy Bypass -File install-backup.ps1 -Remove    remove the task (backups already saved stay)
param([switch]$Remove, [switch]$NoRun)
$ErrorActionPreference = "Stop"
$name = "AT-HUB Backup"
$script = Join-Path $PSScriptRoot "athub-backup.ps1"
$ssh = "$env:WINDIR\System32\OpenSSH\ssh.exe"
$scp = "$env:WINDIR\System32\OpenSSH\scp.exe"
$keygen = "$env:WINDIR\System32\OpenSSH\ssh-keygen.exe"
$key = "$env:USERPROFILE\.ssh\athub_backup"
$root = "$env:USERPROFILE\.ssh\athub_server"
$server = "192.241.247.199"

if (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) { Unregister-ScheduledTask -TaskName $name -Confirm:$false }
if ($Remove) { "Removed: $name"; return }

# a key that can only fetch backups, and its account on the server
if (-not (Test-Path $key)) { & $keygen -t ed25519 -N '""' -C "athub-pc-backup" -f $key -q }
$serverDir = Join-Path (Split-Path $PSScriptRoot) "server"
& $scp -i $root -o BatchMode=yes -q "$key.pub" "$serverDir\athub-pc-backup.sh" "$serverDir\athub-pc-backup-setup.sh" "root@${server}:/root/incoming/"
& $ssh -i $root -o BatchMode=yes "root@$server" "set -e; sed -i 's/\r`$//' /root/incoming/athub-pc-backup*.sh; install -m 700 /root/incoming/athub-pc-backup.sh /opt/athub-bin/athub-pc-backup.sh; bash /root/incoming/athub-pc-backup-setup.sh /root/incoming/athub_backup.pub"
if ($LASTEXITCODE -ne 0) { throw "server setup failed" }

$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$script`""
$atLogOn = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$atLogOn.Delay = "PT10M"
$daily = New-ScheduledTaskTrigger -Daily -At "12:30"
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 1) `
  -MultipleInstances IgnoreNew -StartWhenAvailable
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $name -Action $action -Trigger @($atLogOn, $daily) -Settings $settings -Principal $principal `
  -Description "Saves AT-HUB backups (database every 3 days, everything every 20) into the OneDrive AT-HUB Backup folder." | Out-Null
"Installed: $name (log: $env:LOCALAPPDATA\AT-HUB\backup.log)"
if (-not $NoRun) { Start-ScheduledTask -TaskName $name; "Running the first backup now." }
