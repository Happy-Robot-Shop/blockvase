#!/usr/bin/env bash
# Configure GitHub rules so main cannot be pushed to directly, while anyone
# can still open a pull request (public repo + forks).
#
# Requires: gh auth login (repo admin)
# Usage: scripts/configure-github-main-protection.sh
set -euo pipefail

OWNER="${GITHUB_OWNER:-Happy-Robot-Shop}"
REPO="${GITHUB_REPO:-blockvase}"
BRANCH="${GITHUB_DEFAULT_BRANCH:-main}"

if ! command -v gh >/dev/null 2>&1; then
  echo "ERROR: gh CLI not found. Install from https://cli.github.com/" >&2
  exit 1
fi

if ! gh auth status -h github.com >/dev/null 2>&1; then
  echo "ERROR: not logged into GitHub. Run: gh auth login -h github.com -p https -w" >&2
  exit 1
fi

PERMS="$(gh api "repos/${OWNER}/${REPO}" --jq '{admin:.permissions.admin,maintain:.permissions.maintain,push:.permissions.push,allow_forking:.allow_forking,pr_policy:.pull_request_creation_policy}')"
echo "Repo access: ${PERMS}"

IS_ADMIN="$(gh api "repos/${OWNER}/${REPO}" --jq '.permissions.admin')"
if [[ "${IS_ADMIN}" != "true" ]]; then
  echo >&2
  echo "ERROR: GitHub returned admin=false for your account on ${OWNER}/${REPO}." >&2
  echo "       Creating/updating branch rulesets requires repository Admin (or org owner)." >&2
  echo "       GitHub often reports that as HTTP 404 instead of 403." >&2
  echo >&2
  echo "Fix one of:" >&2
  echo "  1) Org owner: https://github.com/orgs/${OWNER}/people — set your role to Owner" >&2
  echo "     or grant Admin on https://github.com/${OWNER}/${REPO}/settings/access" >&2
  echo "  2) Or open (as an owner) Settings → Rules → Rulesets and create:" >&2
  echo "       - Target branch: ${BRANCH}" >&2
  echo "       - Require a pull request before merging" >&2
  echo "       - Block force pushes / Restrict deletions" >&2
  echo >&2
  echo "Forking is already public-friendly if allow_forking=true (anyone can PR via fork)." >&2
  exit 1
fi

ALLOW_FORKING="$(gh api "repos/${OWNER}/${REPO}" --jq '.allow_forking')"
if [[ "${ALLOW_FORKING}" != "true" ]]; then
  echo "Enabling forking on ${OWNER}/${REPO}..."
  gh api -X PATCH "repos/${OWNER}/${REPO}" -f allow_forking=true >/dev/null
else
  echo "Forking already enabled (external PRs OK)."
fi

RULESET_NAME="Protect ${BRANCH} (PR required)"

# Admin bypass (actor_id 5 = repository Admin) so maintainers can push an
# OTA-signed tip commit after merging a PR. Feature work should still go via PR.
PAYLOAD="$(cat <<EOF
{
  "name": "${RULESET_NAME}",
  "target": "branch",
  "enforcement": "active",
  "bypass_actors": [
    {
      "actor_id": 5,
      "actor_type": "RepositoryRole",
      "bypass_mode": "always"
    }
  ],
  "conditions": {
    "ref_name": {
      "include": ["refs/heads/${BRANCH}"],
      "exclude": []
    }
  },
  "rules": [
    {
      "type": "pull_request",
      "parameters": {
        "required_approving_review_count": 0,
        "dismiss_stale_reviews_on_push": true,
        "require_code_owner_review": false,
        "require_last_push_approval": false,
        "required_review_thread_resolution": false,
        "allowed_merge_methods": ["merge", "squash", "rebase"]
      }
    },
    { "type": "non_fast_forward" },
    { "type": "deletion" }
  ]
}
EOF
)"

EXISTING_ID="$(gh api "repos/${OWNER}/${REPO}/rulesets" --jq \
  ".[] | select(.name==\"${RULESET_NAME}\") | .id" 2>/dev/null || true)"

if [[ -n "${EXISTING_ID}" ]]; then
  echo "Updating ruleset id=${EXISTING_ID}..."
  echo "${PAYLOAD}" | gh api -X PUT "repos/${OWNER}/${REPO}/rulesets/${EXISTING_ID}" --input -
else
  echo "Creating ruleset..."
  echo "${PAYLOAD}" | gh api -X POST "repos/${OWNER}/${REPO}/rulesets" --input -
fi

echo
echo "Active branch rulesets:"
gh api "repos/${OWNER}/${REPO}/rulesets" --jq '.[] | {id,name,enforcement,target}'
echo
echo "Done. Direct pushes to ${BRANCH} are blocked for non-admins."
echo "Anyone can fork and open a PR. After merge, run scripts/ota-sign-tip.sh"
echo "and push the signed tip (admin bypass) so devices accept the update."
