# OTA signing

Device updates refuse to apply unless:

1. `git remote get-url origin` matches the allowlist
2. The tip commit on `origin/<branch>` verifies with `git verify-commit` against trusted signers

On devices, verification runs **only** via root-owned `/usr/lib/blockvase/verify-ota-update.sh`, using allowlists installed to:

- `/etc/blockvase/ota-allowed-remotes.txt`
- `/etc/blockvase/ota-allowed-signers`

Bootstrap copies those from `security/` as root. Do **not** keep the OTA private signing key on appliances (`~/.blockvase-secrets/ota-signing` is stripped by bootstrap, live hardening, and `prepare-clone`).

## Signing releases (maintainers)

Use an offline/dedicated signing host. Keep the private key listed in `ota-allowed-signers` (ed25519) out of manufactured images. Example one-shot commit (does not rewrite git config):

```bash
git -c gpg.format=ssh \
    -c user.signingkey="$HOME/.blockvase-secrets/ota-signing" \
    commit -S -m "Your message"
```

Or after a GitHub PR merge (UI merges are usually not OTA-signed):

```bash
scripts/ota-sign-tip.sh
scripts/verify-ota-update.sh HEAD
git push origin HEAD:main   # requires repo Admin (ruleset bypass)
```

The matching public key is `security/ota-signing.pub`. Keep the private key offline/out of the repo and off customer devices.

## Portal TLS (self-signed)

- nginx listens on LAN `:80` (always) and `:443` (when leaf + CA exist under `/etc/blockvase/tls/`).
- Waitress binds `127.0.0.1:8080` only; `X-Forwarded-Proto` makes `request.is_secure` / `Secure` cookies work on HTTPS.
- Trust model is a **device private CA** (`ca.crt` / `ca.key`) that signs the portal leaf (`portal.crt` / `portal.key`). Clients install **`ca.crt`** (not the leaf). On iOS, also enable Full Trust for that CA.
- **Prefer HTTPS redirect is opt-in** (`https_redirect` in config). The server cannot detect whether a browser trusted the CA; forcing redirect before trust bricks access with TLS warnings. Settings/setup and `/api/tls/*` stay reachable over HTTP for recovery.
- Regenerating TLS or changing the device hostname reissues CA/leaf/SANs; clients must re-install the CA.
- Clone prep wipes `/etc/blockvase/tls`; first boot / bootstrap recreates it via `ensure-portal-tls.sh`.

## Portal threat notes

- Do not port-forward `:80` or `:443` to the internet.
- Enable TOTP in Settings after setup; login and step-up are rate-limited.
- Changing admin password, mining payout, wallet send/backup, factory reset, device update, cert regenerate, and prefer-HTTPS require password re-entry (+ TOTP when enabled).
- Optional host firewall: only enable UFW if you understand your LAN management path (SSH/Cursor can be locked out).

## GitHub: PR-only `main`, open PRs for anyone

The public repo already allows forks, so anyone can open a pull request without write access.

To block direct commits/pushes to `main` (collaborators must use a PR):

```bash
gh auth login -h github.com -p https -w
scripts/configure-github-main-protection.sh
```

That creates an active ruleset:

- Require a pull request before merging into `main` (0 required approvals so a solo maintainer can merge)
- Block force-pushes and branch deletion
- Repository **Admin** may bypass (so you can push the OTA-signed tip after merge)

Do **not** put the OTA private key in GitHub Actions secrets unless you accept that trust model. Prefer signing tips on a trusted machine with `ota-sign-tip.sh`.

UI path (same effect): repo **Settings → Rules → Rulesets → New branch ruleset** targeting `main`, enable **Require a pull request before merging**, **Block force pushes**, **Restrict deletions**.
