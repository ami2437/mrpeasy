# AT-HUB AI link: lends this PC's local AI (Ollama, 127.0.0.1:11434) to the cloud AT-HUB while this PC is on.
# An outgoing, encrypted SSH connection forwards it to 127.0.0.1:11435 on the server (only there, nowhere else).
# The key (~/.ssh/athub_ai_link) can do nothing on the server except this one forward. AT-HUB's AI_ENGINE=auto uses
# it whenever it answers and falls back to Claude (text PDFs only) when it doesn't.
# Started at log-on by the scheduled task "AT-HUB AI Link" (install-ai-link.ps1); reconnects after sleep / network drops.
$ErrorActionPreference = "Continue"
$ssh = "$env:WINDIR\System32\OpenSSH\ssh.exe"
$key = "$env:USERPROFILE\.ssh\athub_ai_link"
$server = "ailink@192.241.247.199"
$log = "$env:LOCALAPPDATA\AT-HUB\ai-link.log"
New-Item -ItemType Directory -Force (Split-Path $log) | Out-Null

function Note($msg) {
  if ((Test-Path $log) -and (Get-Item $log).Length -gt 1MB) { Move-Item $log "$log.old" -Force }
  Add-Content -Path $log -Value "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')  $msg"
}

while ($true) {
  Note "connecting"
  $out = & $ssh -N -T -i $key -o BatchMode=yes -o ExitOnForwardFailure=yes -o ServerAliveInterval=20 -o ServerAliveCountMax=3 `
    -o ConnectTimeout=15 -o StrictHostKeyChecking=accept-new -R 127.0.0.1:11435:127.0.0.1:11434 $server 2>&1
  Note "link ended (exit $LASTEXITCODE) $($out -join ' ') -- retrying in 15 s"
  Start-Sleep -Seconds 15
}
