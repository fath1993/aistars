#!/bin/bash
# The only thing the GitHub Actions deploy key may run on the server.
#
# It is the forced command on that key in root's authorized_keys, so SSH runs
# this script whatever the client asks for, and the client's request arrives
# in SSH_ORIGINAL_COMMAND. That request must be one full commit ID; anything
# else is refused. A leaked key can therefore only redeploy a commit that is
# already on main -- dp.sh checks that too.
#
# Called with `bash` from authorized_keys, so it works even though dp.sh's
# permission step leaves files below the project root non-executable.

set -euo pipefail

TARGET="${SSH_ORIGINAL_COMMAND:-}"

if ! [[ "$TARGET" =~ ^[0-9a-f]{40}$ ]]; then
    echo "refused: expected a 40-character commit ID, got '${TARGET:0:80}'" >&2
    exit 2
fi

# exec: nothing more is read from this file once dp.sh starts, so the deploy
# replacing this file on disk cannot affect the run.
exec bash /var/www/aistars/dp.sh "$TARGET"
