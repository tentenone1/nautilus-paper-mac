#!/bin/bash
# deploy-mac.sh — Install Mac launchd plists from platform/mac/
# Usage: bash deploy-mac.sh
# Run from: nautilus-trading root directory

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLATFORM_DIR="$SCRIPT_DIR/platform"
LAUNCH_AGENTS="$HOME/Library/LaunchAgents"
PLIST_SOURCE="$PLATFORM_DIR/mac"

echo "=== Nautilus Mac Deployment ==="
echo "Source: $PLIST_SOURCE"
echo "Target: $LAUNCH_AGENTS"
echo ""

# Check source exists
if [ ! -d "$PLIST_SOURCE" ]; then
    echo "ERROR: $PLIST_SOURCE not found. Run from nautilus-trading root."
    exit 1
fi

# Count plist files
PLIST_COUNT=$(ls "$PLIST_SOURCE"/com.nautilus.*.plist 2>/dev/null | wc -l | tr -d ' ')
echo "Found $PLIST_COUNT plist files"

# Symlink each plist to LaunchAgents
LINKED=0
for plist in "$PLIST_SOURCE"/com.nautilus.*.plist; do
    if [ -f "$plist" ]; then
        fname=$(basename "$plist")
        target="$LAUNCH_AGENTS/$fname"
        if [ -L "$target" ]; then
            rm "$target"
            echo "  Updated: $fname"
        elif [ -f "$target" ]; then
            echo "  WARNING: $fname exists and is not a symlink — skipping"
            continue
        fi
        ln -s "$plist" "$target"
        echo "  Linked: $fname"
        ((LINKED++)) || true
    fi
done

echo ""
echo "Linked $LINKED plist files to $LAUNCH_AGENTS"

# Load services
echo ""
echo "Loading services..."
for plist in "$LAUNCH_AGENTS"/com.nautilus.*.plist; do
    if [ -L "$plist" ]; then
        fname=$(basename "$plist")
        # Extract service name from plist (Label key)
        label=$(defaults read "$plist" Label 2>/dev/null || echo "$fname")
        launchctl load "$plist" 2>/dev/null && echo "  Loaded: $label" || echo "  Already loaded: $label"
    fi
done

echo ""
echo "Done. Services will auto-start on reboot."
echo "To start now: launchctl start <Label>"
