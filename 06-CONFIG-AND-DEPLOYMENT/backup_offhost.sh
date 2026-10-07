#!/usr/bin/env bash
set -euo pipefail

ARCHIVE="${1:?usage: backup_offhost.sh <backup.tar.gz> <user@host> <remote-dir>}"
REMOTE="${2:?usage: backup_offhost.sh <backup.tar.gz> <user@host> <remote-dir>}"
REMOTE_DIR="${3:?usage: backup_offhost.sh <backup.tar.gz> <user@host> <remote-dir>}"
KEY="${AIVF_BACKUP_ENCRYPTION_KEY:?AIVF_BACKUP_ENCRYPTION_KEY must be set}"

command -v openssl >/dev/null
command -v scp >/dev/null
test -f "$ARCHIVE"

umask 077
encrypted="${ARCHIVE}.enc"
printf '%s' "$KEY" | openssl enc -aes-256-cbc -pbkdf2 -iter 200000 -salt -pass stdin -in "$ARCHIVE" -out "$encrypted"

sha256sum "$encrypted" > "${encrypted}.sha256"
scp "$encrypted" "$REMOTE:$REMOTE_DIR/"
scp "${encrypted}.sha256" "$REMOTE:$REMOTE_DIR/"

echo "off-host encrypted backup uploaded: $REMOTE:$REMOTE_DIR/$(basename "$encrypted")"
