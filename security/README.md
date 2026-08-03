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

The matching public key is `security/ota-signing.pub`. Keep the private key offline/out of the repo.
