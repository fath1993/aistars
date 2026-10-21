#!/bin/bash

set -e

APP_DIR=/var/www/aistars

# Optional: the commit to deploy. GitHub Actions passes the commit that passed
# its tests (via deploy/ci-deploy.sh). Without it, the latest main is deployed,
# as before. Pinning matters because a second push can land while the first
# one's tests run; deploying "latest" would ship that untested commit.
TARGET="${1:-}"

echo "=== Starting AIstars deployment ==="

if [ "$EUID" -ne 0 ]; then
    echo "Please run as root:"
    echo "sudo ./dp.sh [commit]"
    exit 1
fi

if [ -n "$TARGET" ] && ! [[ "$TARGET" =~ ^[0-9a-f]{7,40}$ ]]; then
    echo "FATAL: '$TARGET' is not a commit ID."
    exit 1
fi

echo "=== Updating repository ==="

# Arguments passed positionally: sudo's env_reset rejects command-line env
# assignments. The heredoc stays quoted so nothing is substituted early.
sudo -u ubuntu bash -s "$APP_DIR" "$TARGET" <<'EOF'
set -e

APP_DIR="$1"
TARGET="$2"
cd "$APP_DIR"

git fetch origin
# Keeps `origin` meaning the remote's current default branch.
git remote set-head origin --auto >/dev/null 2>&1 || true

if ! git rev-parse --verify --quiet origin >/dev/null; then
    echo "FATAL: 'origin' does not resolve to a ref on this checkout."
    echo "Fix with:  git remote set-head origin --auto"
    exit 1
fi

if [ -n "$TARGET" ]; then
    # Only commits already on main: a branch commit, or anything git cannot
    # find, is refused rather than deployed.
    if ! git merge-base --is-ancestor "$TARGET" origin 2>/dev/null; then
        echo "FATAL: $TARGET is not a commit on the remote's default branch."
        exit 1
    fi
    git reset --hard "$TARGET"
else
    git reset --hard origin
fi
echo "deployed commit: $(git rev-parse --short HEAD) $(git log -1 --format=%s)"

# Reported, not deleted: untracked files survive every deploy.
UNTRACKED=$(git ls-files --others --exclude-standard)
if [ -n "$UNTRACKED" ]; then
    echo ""
    echo "WARNING: untracked files in the working tree:"
    echo "$UNTRACKED" | sed 's/^/    /'
    echo ""
fi

echo "=== Building and starting containers ==="
docker compose up -d --build
# nginx is recreated every time: compose leaves it running when only the app
# changed, and a running nginx keeps the config file and static folder it was
# started with (git replaces files, it does not edit them in place).
docker compose up -d --no-deps --force-recreate nginx
EOF

# No host nginx step: the project's nginx runs in its own container (config
# in deploy/nginx/), and the server's edge proxy finds it by its labels.

echo "=== Fixing ownership and permissions ==="
# Scoped to this app only; /var/www holds other stacks.
chown -R ubuntu:www-data "$APP_DIR"
find "$APP_DIR" -type d -exec chmod 2775 {} +
find "$APP_DIR" -type f -exec chmod 0664 {} +
find "$APP_DIR" -maxdepth 1 -type f -name '*.sh' -exec chmod 0775 {} +

# Secrets: owner and group only, never other.
if [ -f "$APP_DIR/aistars/.env" ]; then
    chmod 0640 "$APP_DIR/aistars/.env"
fi

# Every docker compose call below needs the project folder: GitHub Actions
# runs this script from root's home.
cd "$APP_DIR"

echo "=== Waiting for the app to be ready ==="
READY=0
for _ in $(seq 1 45); do
    if docker compose exec -T aistars python manage.py migrate --check >/dev/null 2>&1; then
        READY=1
        break
    fi
    sleep 2
done
if [ "$READY" -eq 1 ]; then
    echo "app is up and migrations are applied"
else
    echo "  WARNING: migrations were still outstanding after 90s."
fi

echo "=== Verifying the deploy ==="
# Walks configuration, Telegram's view of the webhook, database and kill
# switch, and prints the fix for each failure. The containers stay up either
# way; a failure only makes the script exit non-zero at the end, so a deploy
# from GitHub Actions shows red instead of a green run over a broken app.
DOCTOR_OK=1
if docker compose exec -T aistars python manage.py doctor --strict; then
    echo "verification: all checks passed"
else
    DOCTOR_OK=0
    echo ""
    echo "######################################################################"
    echo "#  DEPLOY FINISHED, BUT THE APP IS NOT FULLY WORKING                 #"
    echo "#  Read the FAIL lines above -- each one prints its own fix.         #"
    echo "######################################################################"
    echo ""
fi

echo "=== Docker Compose status ==="
docker compose ps

if [ "$DOCTOR_OK" -ne 1 ]; then
    echo "=== Deployment completed, verification FAILED ==="
    exit 1
fi
echo "=== Deployment completed ==="
