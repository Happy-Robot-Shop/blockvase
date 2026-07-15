#!/bin/bash
# Rename user 'pi' to 'blockvase' using a temporary admin user.
# Run as: ./rename-user-to-blockvase.sh

set -e

TEMP_USER="tempadmin"
NEW_USER="blockvase"
# Prefer pi (Raspberry Pi OS); fall back to ubuntu if that is the login user.
OLD_USER="pi"
id pi &>/dev/null || { id ubuntu &>/dev/null && OLD_USER="ubuntu"; }

# Phase 2: Do the actual rename (run as tempadmin after logging out of pi)
if [[ "$1" == "--phase2" ]]; then
    echo "=== Phase 2: Renaming user ==="
    if [[ -f /tmp/blockvase-rename-old-user ]]; then
        OLD_USER=$(cat /tmp/blockvase-rename-old-user)
    fi

    if [[ "$(whoami)" != "$TEMP_USER" ]]; then
        echo "Error: Phase 2 must be run as $TEMP_USER."
        echo "Please log out of $OLD_USER, log in as $TEMP_USER, then run:"
        echo "  sudo $(readlink -f "$0") --phase2"
        exit 1
    fi

    echo "Renaming user $OLD_USER to $NEW_USER..."
    sudo usermod -l "$NEW_USER" "$OLD_USER"
    echo "Renaming group $OLD_USER to $NEW_USER..."
    sudo groupmod -n "$NEW_USER" "$OLD_USER"
    echo "Moving home directory to /home/$NEW_USER..."
    sudo usermod -d /home/"$NEW_USER" -m "$NEW_USER"

    echo ""
    echo "=== Success! ==="
    echo "Log out and log in as '$NEW_USER'."
    echo ""
    echo "After logging in as $NEW_USER, remove the temporary user:"
    echo "  sudo deluser $TEMP_USER"
    echo "  sudo rm -rf /home/$TEMP_USER"
    exit 0
fi

# Phase 1: Create temporary admin user (run as pi)
echo "=== Phase 1: Creating temporary admin user ==="

if [[ "$(whoami)" != "$OLD_USER" ]]; then
    echo "Error: Phase 1 must be run as $OLD_USER."
    exit 1
fi

if ! id "$TEMP_USER" &>/dev/null; then
    echo "Creating user '$TEMP_USER' (you will be prompted for a password)..."
    sudo adduser --gecos "" "$TEMP_USER"
else
    echo "User '$TEMP_USER' already exists."
fi
sudo usermod -aG sudo "$TEMP_USER"

SCRIPT_COPY="/tmp/rename-user-to-blockvase.sh"
sudo cp "$(readlink -f "$0")" "$SCRIPT_COPY"
sudo chmod +x "$SCRIPT_COPY"
echo "$OLD_USER" | sudo tee /tmp/blockvase-rename-old-user >/dev/null

echo ""
echo "=== Phase 1 complete ==="
echo ""
echo "Next steps:"
echo "  1. Log out of $OLD_USER (close all sessions)"
echo "  2. Log in as $TEMP_USER (use the password you just set)"
echo "  3. Run:  sudo $SCRIPT_COPY --phase2"
echo "  4. Log out and log in as $NEW_USER"
echo "  5. Remove temp user:  sudo deluser $TEMP_USER"
echo ""
