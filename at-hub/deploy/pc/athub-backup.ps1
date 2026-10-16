# AT-HUB automatic backups to your own cloud folder (user, 2026-10-16): the real site's DATABASE every 3 days and
# EVERYTHING (database + attached files) every 20 days, saved into a OneDrive-synced folder -- quietly, with a small
# notice while it runs. Same .zip as the in-app "download a backup", so it restores the same way.
# Run by the scheduled task "AT-HUB Backup" (install-backup.ps1) at log-on and every day at 12:30; it does nothing when
# no copy is due. The key (~/.ssh/athub_backup) can only fetch backups (server: athub-pc-backup.sh).
param([switch]$Force, [ValidateSet("", "db", "full")][string]$Kind = "")
$ErrorActionPreference = "Stop"
$Folder = "C:\Users\mitta\OneDrive\AMI\American Traders LLC\ATIND Supplies\AT-HUB Backup"
$DbEveryDays = 3
$FullEveryDays = 20
$KeepDb = 10
$KeepFull = 3
$ssh = "$env:WINDIR\System32\OpenSSH\ssh.exe"
$key = "$env:USERPROFILE\.ssh\athub_backup"
$server = "athubbak@192.241.247.199"
$log = "$env:LOCALAPPDATA\AT-HUB\backup.log"
New-Item -ItemType Directory -Force (Split-Path $log) | Out-Null

function Note($msg) {
  if ((Test-Path $log) -and (Get-Item $log).Length -gt 1MB) { Move-Item $log "$log.old" -Force }
  Add-Content -Path $log -Value "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')  $msg"
}

# a small notice in the corner of the screen (no extra modules needed)
Add-Type -AssemblyName System.Windows.Forms, System.Drawing
$tray = New-Object System.Windows.Forms.NotifyIcon
$tray.Icon = [System.Drawing.SystemIcons]::Information
$tray.Text = "AT-HUB Backup"
function Notice($title, $text, $icon = "Info") {
  $tray.Visible = $true
  $tray.ShowBalloonTip(6000, $title, $text, [System.Windows.Forms.ToolTipIcon]::$icon)
}

function Newest($pattern) {
  Get-ChildItem -Path $Folder -Filter $pattern -File -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 1
}

try {
  New-Item -ItemType Directory -Force $Folder | Out-Null
  $lastFull = Newest "AT-HUB-backup-*-everything.zip"
  $lastAny = @(Newest "AT-HUB-backup-*-everything.zip"; Newest "AT-HUB-backup-*-database.zip") | Where-Object { $_ } |
    Sort-Object LastWriteTime -Descending | Select-Object -First 1
  if (-not $Kind) {
    if (-not $lastFull -or $lastFull.LastWriteTime -lt (Get-Date).AddDays(-$FullEveryDays)) { $Kind = "full" }
    elseif ($Force -or -not $lastAny -or $lastAny.LastWriteTime -lt (Get-Date).AddDays(-$DbEveryDays)) { $Kind = "db" }
  }
  if (-not $Kind) { Note "nothing due (last copy $($lastAny.Name))"; return }

  $label = if ($Kind -eq "full") { "everything" } else { "database" }
  Notice "AT-HUB backup" "Backing up the $label to your OneDrive folder..."
  Note "starting $label backup"
  $tmp = Join-Path $env:TEMP "athub-backup-$PID.zip"
  $err = Join-Path $env:TEMP "athub-backup-$PID.err"
  # Start-Process writes ssh's output to the file byte for byte (a PowerShell pipe would mangle the .zip)
  $p = Start-Process -FilePath $ssh -NoNewWindow -Wait -PassThru -RedirectStandardOutput $tmp -RedirectStandardError $err -ArgumentList @(
    "-i", "`"$key`"", "-o", "BatchMode=yes", "-o", "ConnectTimeout=20", "-o", "StrictHostKeyChecking=accept-new", $server, $Kind)
  if ($p.ExitCode -ne 0) { throw "the server said: $((Get-Content $err -Raw -ErrorAction SilentlyContinue) -replace '\s+', ' ')" }

  Add-Type -AssemblyName System.IO.Compression.FileSystem
  $zip = [System.IO.Compression.ZipFile]::OpenRead($tmp)
  try {
    $db = $zip.Entries | Where-Object { $_.FullName -eq "at_hub.db" }
    if (-not $db -or $db.Length -lt 100KB) { throw "the copy has no database in it" }
    $files = @($zip.Entries | Where-Object { $_.FullName -like "uploads/*" }).Count
  } finally { $zip.Dispose() }

  $name = "AT-HUB-backup-$(Get-Date -Format 'yyyy-MM-dd-HHmm')-$label.zip"
  $dest = Join-Path $Folder $name
  Move-Item $tmp $dest -Force
  $size = (Get-Item $dest).Length
  & $ssh -i $key -o BatchMode=yes -o ConnectTimeout=20 $server "saved $Kind $size" | Out-Null

  # keep the newest copies of each kind
  Get-ChildItem $Folder -Filter "AT-HUB-backup-*-database.zip" -File | Sort-Object LastWriteTime -Descending | Select-Object -Skip $KeepDb | Remove-Item -Force
  Get-ChildItem $Folder -Filter "AT-HUB-backup-*-everything.zip" -File | Sort-Object LastWriteTime -Descending | Select-Object -Skip $KeepFull | Remove-Item -Force

  $mb = [math]::Round($size / 1MB, 1)
  Note "saved $name ($mb MB$(if ($Kind -eq 'full') { ", $files files" }))"
  Notice "AT-HUB backup saved" "$label, $mb MB -- $name"
  Start-Sleep -Seconds 7
} catch {
  Note "FAILED: $($_.Exception.Message)"
  Notice "AT-HUB backup failed" "$($_.Exception.Message) -- it tries again tomorrow; AT-HUB will remind you if a copy gets old." "Warning"
  Start-Sleep -Seconds 7
} finally {
  if ($tmp -and (Test-Path $tmp)) { Remove-Item $tmp -Force -ErrorAction SilentlyContinue }
  if ($err -and (Test-Path $err)) { Remove-Item $err -Force -ErrorAction SilentlyContinue }
  $tray.Dispose()
}
