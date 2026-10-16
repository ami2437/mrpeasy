#!/bin/bash
# One-time: the "athubbak" account the PC's automatic backups use (deploy/pc/install-backup.ps1 runs this).
#   athub-pc-backup-setup.sh /path/to/the-pc-key.pub
# The key can do exactly one thing: run athub-pc-backup.sh (as root, through one sudo rule) -- no shell, no forwarding,
# no other command. Whatever the PC asks for is checked by that script (db | full | saved ...).
set -euo pipefail
PUB=${1:?public key file}
id athubbak >/dev/null 2>&1 || useradd --system --create-home --home-dir /home/athubbak --shell /bin/sh athubbak
passwd -l athubbak >/dev/null
install -d -m 700 -o athubbak -g athubbak /home/athubbak/.ssh
printf 'restrict,command="sudo -n /opt/athub-bin/athub-pc-backup.sh \\"$SSH_ORIGINAL_COMMAND\\"" %s\n' "$(tr -d '\r\n' < "$PUB")" > /home/athubbak/.ssh/authorized_keys
chown athubbak:athubbak /home/athubbak/.ssh/authorized_keys
chmod 600 /home/athubbak/.ssh/authorized_keys
echo 'athubbak ALL=(root) NOPASSWD: /opt/athub-bin/athub-pc-backup.sh' > /etc/sudoers.d/athub-pc-backup
chmod 440 /etc/sudoers.d/athub-pc-backup
visudo -cf /etc/sudoers.d/athub-pc-backup
cut -c1-120 /home/athubbak/.ssh/authorized_keys
