#!/bin/bash
# deploy-linux.sh — Install Linux systemd services from platform/linux/
# Usage: bash deploy-linux.sh
# Run from: nautilus-trading root directory on 1700 (Linux)

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLATFORM_DIR="$SCRIPT_DIR/platform"
SYSTEMD_USER_DIR="$HOME/.config/systemd/user"
SERVICE_SOURCE="$PLATFORM_DIR/linux"

echo "=== Nautilus Linux Deployment (1700) ==="
echo "Source: $SERVICE_SOURCE"
echo "Target: $SYSTEMD_USER_DIR"
echo ""

# Check source exists
if [ ! -d "$SERVICE_SOURCE" ]; then
    echo "ERROR: $SERVICE_SOURCE not found. Run from nautilus-trading root."
    exit 1
fi

# Count service files
SERVICE_COUNT=$(ls "$SERVICE_SOURCE"/*.service 2>/dev/null | wc -l | tr -d ' ')
echo "Found $SERVICE_COUNT service files"

# Symlink each service to systemd user dir
LINKED=0
for svc in "$SERVICE_SOURCE"/*.service; do
    if [ -f "$svc" ]; then
        fname=$(basename "$svc")
        target="$SYSTEMD_USER_DIR/$fname"
        if [ -L "$target" ]; then
            rm "$target"
            echo "  Updated: $fname"
        elif [ -f "$target" ]; then
            echo "  WARNING: $fname exists and is not a symlink — skipping"
            continue
        fi
        ln -s "$svc" "$target"
        echo "  Linked: $fname"
        ((LINKED++)) || true
    fi
done

echo ""
echo "Linked $LINKED service files to $SYSTEMD_USER_DIR"

# Daemon-reload and enable
echo ""
echo "Reloading systemd daemon..."
systemctl --user daemon-reload

echo "Enabling and starting services..."
for svc in "$SERVICE_SOURCE"/*.service; do
    if [ -f "$svc" ]; then
        fname=$(basename "$svc")
        svcname="${fname%.service}"
        systemctl --user enable "$svcname" 2>/dev/null && echo "  Enabled: $svcname" || echo "  Enable failed: $svcname"
        systemctl --user restart "$svcname" 2>/dev/null && echo "  Restarted: $svcname" || echo "  Restart failed (may already be running): $svcname"
    fi
done

echo ""
echo "Done."
echo "Check status: systemctl --user status <service-name>"
