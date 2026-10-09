#!/bin/bash
# One-time: the "ailink" account a PC uses to lend its local AI (Ollama) to AT-HUB over SSH.
#   athub-ai-link-setup.sh /path/to/the-pc-key.pub
# The key may do exactly one thing: forward the PC's Ollama to 127.0.0.1:11435 on this server (remote forward).
# No shell (nologin), no commands, no other ports, nothing reachable from outside (127.0.0.1 only).
set -euo pipefail
PUB=${1:?public key file}
id ailink >/dev/null 2>&1 || useradd --system --create-home --home-dir /home/ailink --shell /usr/sbin/nologin ailink
install -d -m 700 -o ailink -g ailink /home/ailink/.ssh
printf 'restrict,port-forwarding,permitlisten="127.0.0.1:11435" %s\n' "$(tr -d '\r\n' < "$PUB")" > /home/ailink/.ssh/authorized_keys
chown ailink:ailink /home/ailink/.ssh/authorized_keys
chmod 600 /home/ailink/.ssh/authorized_keys
cut -c1-110 /home/ailink/.ssh/authorized_keys
sshd -T -C user=ailink,host=x,addr=1.2.3.4 | grep -E '^(allowtcpforwarding|gatewayports) '
