# Blockvase

Blockvase turns a Raspberry Pi into a **Bitcoin node with a simple web portal and HDMI display**.

After install (or on a manufactured clone’s first boot) it creates its own Wi-Fi network so you can set it up from your phone. Then it joins your home Wi-Fi, syncs the Bitcoin blockchain, and shows status on the screen and in a browser.

| | |
|---|---|
| **OS** | [Raspberry Pi OS 64-bit](https://www.raspberrypi.com/software/) (Bookworm or later), Desktop or Lite |
| **Login user** | Must be named **`blockvase`** |
| **Display** | Full-screen kiosk (not the Pi desktop) |
| **Networking** | NetworkManager |
| **Node** | [Bitcoin Knots](https://github.com/bitcoinknots/bitcoin/releases/tag/v29.3.knots20260508) (full archival, local RPC only) |

---

## Choose a setup path

| Path | Who it’s for | What you do |
|------|----------------|-------------|
| **A: Fresh install** | One Pi, or your first device | Flash Pi OS → install Blockvase → set up Wi-Fi from the QR code |
| **B: Manufactured clone** | Building many identical devices | Prepare one SD master → image it → flash each NVMe → each unit self-configures on first boot |

Both paths need user **`blockvase`**. If Raspberry Pi Imager created `pi` (or `ubuntu`) instead:

```bash
./nvme-clone-tools/rename-user-to-blockvase.sh
```

Follow the prompts (temporary admin → rename → log in as `blockvase`).

Run install commands over **SSH** if a desktop is already on screen. Bootstrap turns the desktop off and switches to kiosk-only boot.

---

## Path A: Fresh install

1. Flash **Raspberry Pi OS 64-bit** and create user **`blockvase`**.
2. On the Pi:
   ```bash
   cd ~
   git clone https://github.com/happyrobotshop/blockvase.git
   cd blockvase
   sudo ./scripts/bootstrap.sh
   sudo reboot
   ```
3. After reboot, join the device’s setup Wi-Fi (QR on the HDMI screen, or scan with your phone).
4. Open the setup page, create an admin username and password, and enter your home Wi-Fi. The Pi will join that network and reboot.

**What bootstrap installs:** packages, Bitcoin Knots, the mining stack (miner enabled for board monitoring; hashing starts after you save a payout address), systemd services, and kiosk mode. It also records a device fingerprint in `/var/lib/blockvase/device-identity.env`. Re-running bootstrap updates the software and does **not** wipe the blockchain.

**Disk space:** a full archival node needs **hundreds of GB** free (SSD/NVMe recommended).

---

## Path B: Manufactured clone (SD → NVMe)

Use this when one prepared SD image should become many unique devices.

1. **Build a master** with Path A on an SD card. Optionally let the chain sync first. Keep the SD root **compact**: do **not** expand it to fill a large disk before imaging.
2. **Prepare for imaging** (resets setup/Wi-Fi/admin/mining, records the master fingerprint, leaves `/var/lib/bitcoind` alone):
   ```bash
   cd ~/blockvase
   sudo ./scripts/ap-mode.sh prepare-clone
   ```
3. **Power off cleanly**, image the SD card, flash that image to each NVMe.
4. **First boot is automatic.** When the hardware/storage fingerprint differs from the master, the device:
   - gets a new machine-id, SSH host keys, and hostname
   - starts setup AP + QR
   - expands the root filesystem to fill the NVMe
   - keeps the Bitcoin datadir if it was on the master

Repeat steps 3 to 4 for more units. You do **not** need to run bootstrap on each clone for identity, expand, or setup. Run it later only if you want package or code updates.

---

## Using the device

After setup:

| Open | Purpose |
|------|---------|
| `http://<pi-ip>/` | Main portal |
| `http://<hostname>.local/` | Same, via Avahi |
| `/display` | Kiosk-style view |
| `/mempool` | Mempool view |
| `/settings` | Admin settings (password from setup) |

**Setup Wi-Fi (before home Wi-Fi is saved)**

| | |
|---|---|
| **SSID** | `blockvase-` + 6 characters (shown on the QR / Settings) |
| **Password** | `blockvase1234` |
| **Setup page** | Usually `http://192.168.4.1/setup?token=...` while connected to the hotspot |

If home Wi-Fi join fails (at setup or later if Wi-Fi drops), the device returns to setup AP mode and shows the setup QR on the display so you can reconnect and fix it.

---

## How it works

```text
Phone / browser / HDMI kiosk
            |
            v
   blockvase.service  (web portal)
            |
            v
   Bitcoin Knots (local node)     + optional external fee APIs
```

At a glance:

- The **portal** (`blockvase.service`) serves the UI and APIs.
- The **node** (`bitcoind`) stores the chain under `/var/lib/bitcoind`.
- The **kiosk** is a full-screen Chromium session on HDMI (not the Pi desktop).
- **Setup networking** uses a temporary hotspot, then a saved home Wi-Fi profile.
- On every boot, **clone safety** checks whether this image was cloned onto new hardware; if so, it refreshes identity and expands the disk before the node starts.

### Services

| Unit | Role |
|------|------|
| `blockvase-ap.service` | Clone safety + setup hotspot / home Wi-Fi |
| `bitcoind.service` | Bitcoin Knots (starts after `blockvase-ap`) |
| `blockvase.service` | Web portal |
| `blockvase-kiosk.service` | HDMI kiosk |
| `blockvase-switch-to-kiosk-vt.service` | Switches to the kiosk screen early in boot |
| `blockvase-chain-guard.timer` | Auto-recovery if chainstate corrupts |
| `blockvase-wifi-watch.timer` | If home Wi-Fi stays down, recover to setup AP + QR |
| `datum-gateway.service` / `blockvase-miner.service` | Miner on by default; DATUM/hashing after payout address |

```bash
sudo systemctl status blockvase-ap bitcoind blockvase blockvase-kiosk
```

### For developers

| Piece | Detail |
|-------|--------|
| App | Flask + Waitress (`app/server.py`), metrics cache in `app/state.py` |
| Config | `data/config.json`; RPC secrets mirrored from `/etc/bitcoin/bitcoin.conf` (localhost only) |
| Kiosk | `startx` + `scripts/kiosk-session.sh` on `:0` / VT7 |
| AP / Wi-Fi | `scripts/ap-mode.sh` via NetworkManager (`nmcli`) |
| Clone / expand | `scripts/clone-safety.sh` (from `ap-mode ensure` and once from bootstrap) |
| Port | `80` by default (`BLOCKVASE_PORT`) |
| System actions | `/api/reboot`, `/api/factory-reset`, `/api/device-update` need admin session **and** `ENABLE_SYSTEM_ACTIONS=true` |
| Device update | Settings → **Update device** runs `scripts/device-update.sh` (`git pull` + `bootstrap.sh`); kiosk + portal show a full-screen updating overlay (same idea as the setup QR / loading screens). A background check (~every 30m, `BLOCKVASE_UPDATE_CHECK_SEC`) compares `HEAD` to `origin/<branch>` and highlights the button when commits are available. |

**Fee tiers** (`/api/blockchain-info`): external APIs → local mempool → `estimatesmartfee`. Response field `fee_source` is `external`, `local_mempool`, or `node`.

**Python stack:** [Flask](https://flask.palletsprojects.com/), [Jinja2](https://jinja.palletsprojects.com/), [Waitress](https://waitress.readthedocs.io/), [requests](https://requests.readthedocs.io/), [qrcode](https://github.com/lincolnloop/python-qrcode).

### Core API

| Method | Path | Notes |
|--------|------|--------|
| GET | `/api/blockchain-info` | Cached metrics + fees |
| GET | `/api/mempool-txs` | Mempool for display |
| GET | `/api/tx/<txid>` | Decoded tx |
| GET | `/api/stats` | Admin |
| GET | `/api/setup-status` | Setup URL only before setup completes |
| GET | `/api/ap-info` | Hotspot SSID/password + QR info |
| GET | `/api/setup-qr.svg?kind=settings\|connect` | QR image; admin after setup |
| GET/POST | `/api/admin-auth/*` | Login / credentials |
| POST | `/api/save-all` | Setup + Wi-Fi save |
| GET | `/api/rpc` | Local RPC status (password never returned) |
| GET/POST | `/api/device-name`, `/api/display-offset` | Device label / display offset |
| GET/POST | `/api/device-update` | Status / start git pull + bootstrap (admin + system actions) |

Setup auth: setup token (`?token=`, `X-Setup-Token`, or JSON `token`) or admin cookie after login.

---

## Optional: quieter boot

Bootstrap already skips the “Welcome to Raspberry Pi” wizard on kiosk installs.

To hide the rainbow / Plymouth splash:

```bash
sudo ~/blockvase/scripts/disable-boot-splash.sh
sudo reboot
```

Or during bootstrap:

```bash
sudo env BLOCKVASE_DISABLE_BOOT_SPLASH=1 ./scripts/bootstrap.sh && sudo reboot
```

For less kernel/systemd text on HDMI (harder to debug without SSH):

| Level | Command |
|-------|---------|
| Moderate | `sudo env BLOCKVASE_KERNEL_QUIET=1 ~/blockvase/scripts/disable-boot-splash.sh && sudo reboot` |
| Stronger | `sudo env BLOCKVASE_SILENT_BOOT=1 ~/blockvase/scripts/disable-boot-splash.sh && sudo reboot` |

Splash off + silent in one bootstrap:

```bash
sudo env BLOCKVASE_DISABLE_BOOT_SPLASH=1 BLOCKVASE_SILENT_BOOT=1 ~/blockvase/scripts/bootstrap.sh && sudo reboot
```

A fully black screen from power-on is not guaranteed on stock Pi OS without a custom Plymouth theme.

---

## Troubleshooting

### Quick fixes

| Problem | Try |
|---------|-----|
| Display blank / stuck | `sudo systemctl restart blockvase-kiosk.service` |
| Portal not loading | `sudo systemctl restart blockvase.service` |
| Setup Wi-Fi missing | `sudo systemctl restart blockvase-ap.service` |
| Need diagnostics | `bash ~/blockvase/scripts/kiosk-debug.sh` or `sudo ~/blockvase/scripts/ap-debug.sh` |

### Setup Wi-Fi / QR

1. SSH over Ethernet: `ssh blockvase@<pi-eth0-ip>`
2. `sudo ~/blockvase/scripts/ap-debug.sh`
3. `sudo systemctl restart blockvase-ap.service`
4. Connect manually to `blockvase-<suffix>`, password `blockvase1234`
5. Confirm NetworkManager: `sudo systemctl status NetworkManager`
6. Outside the US: set Wi-Fi country with `sudo raspi-config` → Localisation Options → WLAN Country

Wrong home Wi-Fi password usually shows as `ap-mode ensure failed` in `journalctl -u blockvase.service`. The portal should put you back in AP mode. A "disconnected" node during first sync is often just IBD. Check `journalctl -u bitcoind.service`.

### Kiosk / display

After bootstrap the HDMI UI is **only** the Blockvase kiosk, not the Pi desktop. Prefer a reboot after first install.

1. `bash ~/blockvase/scripts/kiosk-debug.sh`
2. Logs: `~/logs/kiosk-browser.log` and `journalctl -u blockvase-kiosk.service -n 80`
3. After `git pull`, reinstall units with `sudo ./scripts/bootstrap.sh` (or copy from `systemd/`, replacing `__PROJECT_DIR__`, `__SERVICE_USER__`, `__USER_UID__`)
4. Pi 5 “Cannot run in framebuffer mode”: bootstrap copies `xorg-conf/99-vc4.conf` on install; if it’s missing, run  
   `sudo ~/blockvase/scripts/install-pi5-xorg-fix.sh && sudo systemctl restart blockvase-kiosk.service`

**Crash loop (black → TTY → repeat) on Pi 5:** stop the kiosk, install the vc4 fix above, start it again:

```bash
sudo systemctl stop blockvase-kiosk.service
cd ~/blockvase && sudo ./scripts/install-pi5-xorg-fix.sh
sudo systemctl start blockvase-kiosk.service
```

**Console permission errors** (`xf86OpenConsole`, no FD for console): the kiosk unit must use `SupplementaryGroups=tty video input` and attach `startx` to a VT (`StandardInput=tty`, `TTYPath=/dev/tty7`, etc.). Reinstall the unit from `systemd/` via bootstrap. If something else holds tty7, switch the unit to tty8 / `vt8`.

**Polkit / keyring popups:** current `kiosk-session.sh` avoids XDG autostart and uses `--password-store=basic`. Restart the kiosk (or reboot once) after updating.

### Kiosk-only vs full Pi desktop

Bootstrap enables kiosk-only boot (`multi-user.target`, display manager off).

| Goal | Command |
|------|---------|
| Re-apply kiosk-only | `sudo ~/blockvase/scripts/enable-kiosk-only-boot.sh --yes blockvase` then reboot |
| Restore full desktop (debug) | `sudo ~/blockvase/scripts/restore-desktop-boot.sh blockvase ~/blockvase` then reboot |

Desktop restore uses `kiosk-desktop.sh` inside the desktop session. Prefer kiosk-only for normal operation.
