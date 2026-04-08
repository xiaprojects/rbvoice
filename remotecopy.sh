#!/bin/bash
# This script is necessary to upload into the remote RB device if needed
exit 0

SOURCE="/opt/stratux/www/playback/"
REMOTE_USER="pi"
REMOTE_HOST="192.168.10.1"
REMOTE_DIR="/opt/stratux/www/playback/"
SSHPASS="raspberry"

#inotifywait -m -e close_write,moved_to,create --format '%w%f' "$SOURCE" | while read FILE; do
#  sshpass -p "$SSHPASS" rsync -a --progress -e ssh "$FILE" "$REMOTE_USER@$REMOTE_HOST:$REMOTE_DIR"
#done
FILE=$1
sshpass -p "$SSHPASS" rsync -a -e ssh "$FILE" "$REMOTE_USER@$REMOTE_HOST:$REMOTE_DIR"