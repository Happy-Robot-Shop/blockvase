# OTA signing

Device updates refuse to apply unless:

1. `git remote get-url origin` matches `ota-allowed-remotes.txt`
2. The tip commit on `origin/<branch>` verifies with `git verify-commit` against `ota-allowed-signers`

## Signing releases (maintainers)

Use an SSH key listed in `ota-allowed-signers` (ed25519). Example one-shot commit (does not rewrite git config):

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

The matching public key is `security/ota-signing.pub`. Keep the private key offline/out of the repo.

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
