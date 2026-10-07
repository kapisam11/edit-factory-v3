#!/usr/bin/env bash
set -euo pipefail

ARCHIVE="${1:?usage: backup_offhost.sh <backup.tar.gz> <user@host> <remote-dir>}"
REMOTE="${2:?usage: backup_offhost.sh <backup.tar.gz> <user@host> <remote-dir>}"
REMOTE_DIR="${3:?usage: backup_offhost.sh <backup.tar.gz> <user@host> <remote-dir>}"
KEY="${AIVF_BACKUP_ENCRYPTION_KEY:?AIVF_BACKUP_ENCRYPTION_KEY must be set}"
MAC_KEY="$(printf 'aivf-backup-mac:%s' "$KEY" | sha256sum | awk '{print $1}')"

command -v openssl >/dev/null
command -v scp >/dev/null
test -f "$ARCHIVE"

umask 077
encrypted="${ARCHIVE}.enc"
printf '%s' "$KEY" | openssl enc -aes-256-cbc -pbkdf2 -iter 200000 -salt -pass stdin -in "$ARCHIVE" -out "$encrypted"

sha256sum "$encrypted" > "${encrypted}.sha256"
openssl dgst -sha256 -hmac "$MAC_KEY" "$encrypted" | awk '{print $NF}' > "${encrypted}.hmac"
scp "$encrypted" "$REMOTE:$REMOTE_DIR/"
scp "${encrypted}.sha256" "$REMOTE:$REMOTE_DIR/"
scp "${encrypted}.hmac" "$REMOTE:$REMOTE_DIR/"

echo "off-host encrypted backup uploaded with SHA-256 and HMAC verification sidecars: $REMOTE:$REMOTE_DIR/$(basename "$encrypted")"
