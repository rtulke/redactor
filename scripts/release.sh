#!/usr/bin/env bash
# release.sh — cut a redactor release
#
# Usage: ./scripts/release.sh [VERSION]   e.g. ./scripts/release.sh 1.4.0
#        ./scripts/release.sh             interactive wizard
#
# Named release.sh, not update_homebrew.sh: Homebrew is steps 4-5 of 6. The
# script runs the tests, bumps the version everywhere it appears, commits, tags
# and pushes -- and the tag push is what starts .github/workflows/release.yml,
# which builds the deb/rpm. Updating the tap is the tail end of that, not the job.

set -euo pipefail

# ── Config ────────────────────────────────────────────────────────────────────
REDACTOR_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TAP_DIR="${TAP_DIR:-$HOME/dev/homebrew-redactor}"
FORMULA="$TAP_DIR/Formula/redactor.rb"
GITHUB_REPO="rtulke/redactor"
EMPTY_SHA="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"

# Both READMEs carry the version and the package download URLs. Miss one and its
# install instructions point at files that no longer exist after this release.
READMES=("$REDACTOR_DIR/README.md" "$REDACTOR_DIR/README_de.md")

# ── Colors (only when attached to a terminal) ─────────────────────────────────
if [[ -t 1 ]]; then
  RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
  BLUE='\033[0;34m'; BOLD='\033[1m'; DIM='\033[2m'; NC='\033[0m'
else
  RED=''; GREEN=''; YELLOW=''; BLUE=''; BOLD=''; DIM=''; NC=''
fi

# ── Helpers ───────────────────────────────────────────────────────────────────
step() { echo -e "\n${BLUE}▸ [${1}]${NC} ${2}"; }
ok()   { echo -e "  ${GREEN}✓${NC} ${1}"; }
warn() { echo -e "  ${YELLOW}⚠${NC}  ${1}"; }
die()  { echo -e "\n${RED}Error:${NC} ${1}" >&2; exit 1; }

# ── Preflight ─────────────────────────────────────────────────────────────────
[[ -f "$REDACTOR_DIR/redactor.py" ]] || die "redactor.py not found in $REDACTOR_DIR"
for readme in "${READMES[@]}"; do
  [[ -f "$readme" ]] || die "$(basename "$readme") not found — both READMEs must exist"
done
[[ -f "$FORMULA" ]] || die "Homebrew formula not found at $FORMULA
  Create the tap first, see docs/RELEASING.md
  (or point TAP_DIR at your checkout: TAP_DIR=/path ./scripts/release.sh)"
command -v git     >/dev/null || die "git not in PATH"
command -v curl    >/dev/null || die "curl not in PATH"
command -v python3 >/dev/null || die "python3 not in PATH"

# A dirty tree means the commit below would sweep up unrelated changes into
# "Bump version to X.Y.Z" -- and that commit is what gets tagged.
cd "$REDACTOR_DIR"
[[ -z "$(git status --porcelain)" ]] \
  || die "Working tree is dirty. Commit or stash first:
$(git status --short | sed 's/^/    /')"

# ── Detect current version ────────────────────────────────────────────────────
CURRENT_VERSION=$(python3 -c "
import re, sys
m = re.search(r\"__version__\s*=\s*'([^']+)'\", open('$REDACTOR_DIR/redactor.py').read())
print(m.group(1)) if m else sys.exit(1)
") || die "Could not detect current version from redactor.py"

SUGGESTED=$(python3 -c "
v = '${CURRENT_VERSION}'.split('.')
v[-1] = str(int(v[-1]) + 1)
print('.'.join(v))
")

# ── Version input ─────────────────────────────────────────────────────────────
if [[ $# -eq 0 ]]; then
  echo ""
  echo -e "${BOLD}  redactor — Release Wizard${NC}"
  echo    "  ────────────────────────────────────────────"
  echo -e "  Current version : ${YELLOW}${CURRENT_VERSION}${NC}"
  echo -e "  ${DIM}Leave blank to use suggested: ${SUGGESTED}${NC}"
  echo ""
  read -r -p "  New version: " NEW_VERSION
  [[ -z "$NEW_VERSION" ]] && NEW_VERSION="$SUGGESTED"
  echo ""
else
  NEW_VERSION="${1}"
fi

# ── Validate ──────────────────────────────────────────────────────────────────
[[ "$NEW_VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] \
  || die "Version must be in X.Y.Z format (got: '$NEW_VERSION')"
[[ "$NEW_VERSION" != "$CURRENT_VERSION" ]] \
  || die "New version is identical to current ($CURRENT_VERSION)"
git rev-parse "v${NEW_VERSION}" >/dev/null 2>&1 \
  && die "Tag v${NEW_VERSION} already exists"

# ── Step 0: Tests ─────────────────────────────────────────────────────────────
# Before the bump, not after: a pushed tag is the one step that is awkward to
# take back.
step "0/5" "Running test suite"
python3 -m unittest discover -s tests >/dev/null 2>&1 \
  || { python3 -m unittest discover -s tests; die "Tests failed — not releasing."; }
ok "All tests pass"

# ── Confirm ───────────────────────────────────────────────────────────────────
echo ""
echo -e "  ${BOLD}${YELLOW}${CURRENT_VERSION}${NC}  →  ${BOLD}${GREEN}${NEW_VERSION}${NC}"
echo ""
echo    "  What will happen:"
echo    "    1  Update redactor.py, README.md, README_de.md, CHANGELOG.md,"
echo    "       man/redactor.1"
echo    "    2  git commit  +  tag v${NEW_VERSION}  +  push  [redactor]"
echo    "       -> the tag push starts .github/workflows/release.yml,"
echo    "          which builds the deb/rpm and attaches them to the release"
echo    "    3  Download release tarball, compute SHA256"
echo    "    4  Update Homebrew formula (url + sha256)"
echo    "    5  git commit  +  push  [homebrew-redactor]"
echo ""
read -r -p "  Proceed? [y/N] " CONFIRM
echo ""
[[ "$CONFIRM" =~ ^[Yy]$ ]] || { echo "  Aborted."; exit 0; }

# ── Step 1: Update source files ───────────────────────────────────────────────
step "1/5" "Updating source files"

# redactor.py — the single source of truth. release.yml reads the version from
# here too, so this one sed also drives the package filenames.
sed -i.bak "s/__version__ = '${CURRENT_VERSION}'/__version__ = '${NEW_VERSION}'/" \
  "$REDACTOR_DIR/redactor.py"
rm -f "$REDACTOR_DIR/redactor.py.bak"

# Paranoia, not ceremony: if the sed above silently matched nothing (someone
# reformatted the line, switched to double quotes), everything downstream would
# happily build and tag the OLD version.
GOT=$(python3 -c "
import re
m = re.search(r\"__version__\s*=\s*'([^']+)'\", open('$REDACTOR_DIR/redactor.py').read())
print(m.group(1) if m else '')
")
[[ "$GOT" == "$NEW_VERSION" ]] \
  || die "redactor.py still reports '${GOT}' — the version line did not update.
  Check it still looks exactly like:  __version__ = '${NEW_VERSION}'"
ok "redactor.py  (__version__ = '${NEW_VERSION}')"

# Both READMEs: the version line AND the download URLs, which carry the version
# in the package filename (redactor_X.Y.Z_all.deb / redactor-X.Y.Z-1.noarch.rpm
# / redactor-X.Y.Z.tar.gz).
for readme in "${READMES[@]}"; do
  sed -i.bak \
    -e "s/\*\*Version:\*\* ${CURRENT_VERSION}/**Version:** ${NEW_VERSION}/" \
    -e "s/_${CURRENT_VERSION}_/_${NEW_VERSION}_/g" \
    -e "s/-${CURRENT_VERSION}-1\./-${NEW_VERSION}-1./g" \
    -e "s/redactor-${CURRENT_VERSION}\.tar\.gz/redactor-${NEW_VERSION}.tar.gz/g" \
    -e "s/redactor-${CURRENT_VERSION}$/redactor-${NEW_VERSION}/g" \
    "$readme"
  rm -f "${readme}.bak"
  grep -q "\*\*Version:\*\* ${NEW_VERSION}" "$readme" \
    || warn "$(basename "$readme"): no '**Version:** ${NEW_VERSION}' line — check it by hand"
  ok "$(basename "$readme")  (version + package download URLs)"
done

# man/redactor.1 — the .TH line carries the version AND the date that `man` shows
# in the footer. Nothing else reads it, which is exactly why it would rot
# unnoticed: a stale man page still renders fine, it just lies about its version.
MAN_DATE=$(date +%Y-%m-%d)
sed -i.bak \
  -e "s/^\.TH REDACTOR 1 \"[0-9-]*\" \"redactor ${CURRENT_VERSION}\"/.TH REDACTOR 1 \"${MAN_DATE}\" \"redactor ${NEW_VERSION}\"/" \
  "$REDACTOR_DIR/man/redactor.1"
rm -f "$REDACTOR_DIR/man/redactor.1.bak"
grep -q "\"redactor ${NEW_VERSION}\"" "$REDACTOR_DIR/man/redactor.1" \
  || die "man/redactor.1: the .TH line did not update.
  It must still look like:  .TH REDACTOR 1 \"YYYY-MM-DD\" \"redactor X.Y.Z\" \"User Commands\""
ok "man/redactor.1  (.TH -> ${MAN_DATE}, redactor ${NEW_VERSION})"

# CHANGELOG.md — insert placeholder section above the current release entry
python3 - <<PYEOF
with open('${REDACTOR_DIR}/CHANGELOG.md', 'r') as f:
    content = f.read()

old_marker  = '## [${CURRENT_VERSION}] — Current'
new_marker  = '## [${CURRENT_VERSION}]'
new_section = (
    '## [${NEW_VERSION}] — Current\n\n'
    '### New Features\n\n'
    '- <!-- describe changes here -->\n\n'
    '---\n\n'
)

if old_marker in content:
    content = content.replace(old_marker, new_section + new_marker, 1)
else:
    # fallback: no "— Current" suffix on the old version
    fallback = '## [${CURRENT_VERSION}]'
    content = content.replace(fallback, new_section + fallback, 1)

with open('${REDACTOR_DIR}/CHANGELOG.md', 'w') as f:
    f.write(content)
PYEOF
ok "CHANGELOG.md  (placeholder section added for ${NEW_VERSION})"

echo -e "\n  ${YELLOW}Opening CHANGELOG.md — fill in the [${NEW_VERSION}] section, save and quit to continue.${NC}"
${EDITOR:-vi} "$REDACTOR_DIR/CHANGELOG.md" || true

if grep -q "describe changes here" "$REDACTOR_DIR/CHANGELOG.md"; then
  warn "CHANGELOG still contains the placeholder text."
  read -r -p "  Ship it anyway? [y/N] " SHIP
  [[ "$SHIP" =~ ^[Yy]$ ]] || die "Aborted. Nothing was committed."
fi

# ── Step 2: Commit, tag, push ─────────────────────────────────────────────────
step "2/5" "Committing, tagging, pushing redactor"

cd "$REDACTOR_DIR"
git add redactor.py README.md README_de.md CHANGELOG.md man/redactor.1
git commit -m "Bump version to ${NEW_VERSION}"
git tag "v${NEW_VERSION}"
git push origin main
git push origin "v${NEW_VERSION}"
ok "Pushed  main  +  tag v${NEW_VERSION}  →  github.com/${GITHUB_REPO}"
echo -e "  ${DIM}Pipeline: https://github.com/${GITHUB_REPO}/actions${NC}"

# ── Step 3: Download tarball and compute SHA256 ───────────────────────────────
step "3/5" "Fetching release tarball SHA256"

# Note this does NOT wait for the workflow: the formula builds from GitHub's
# source tarball, which exists the moment the tag lands. The deb/rpm show up on
# the release later, independently.
TARBALL_URL="https://github.com/${GITHUB_REPO}/archive/refs/tags/v${NEW_VERSION}.tar.gz"
SHA256=""
MAX_ATTEMPTS=10

for ((i=1; i<=MAX_ATTEMPTS; i++)); do
  echo -e "  ${DIM}Attempt ${i}/${MAX_ATTEMPTS} — waiting for GitHub to publish tarball...${NC}"
  sleep 6
  TARBALL=$(mktemp)
  # -f: fail on 4xx/5xx instead of writing GitHub's error page to the file --
  # without it, shasum happily hashes the 404 HTML and that hash lands in the formula.
  if curl -fsL "$TARBALL_URL" -o "$TARBALL"; then
    SHA256=$(shasum -a 256 "$TARBALL" | awk '{print $1}')
    if [[ -n "$SHA256" && "$SHA256" != "$EMPTY_SHA" ]]; then
      rm -f "$TARBALL"
      ok "SHA256: ${SHA256}"
      break
    fi
  fi
  rm -f "$TARBALL"
  SHA256=""
done

[[ -n "$SHA256" ]] || die \
  "Could not download release tarball after ${MAX_ATTEMPTS} attempts.
  The tag IS pushed, so the release itself is fine -- only the tap is behind.
  URL: ${TARBALL_URL}
  Compute manually:  curl -sL \"${TARBALL_URL}\" | shasum -a 256
  Then update:       ${FORMULA}"

# ── Step 4: Update Homebrew formula ──────────────────────────────────────────
step "4/5" "Updating Homebrew formula"

# Simpler than the sshscan equivalent: the formula has no `resource` block
# (nothing to install from PyPI), so there is exactly one url and one sha256 in
# the file, and no need to split the content to protect a resource's hash.
python3 - <<PYEOF
import re, sys

with open('${FORMULA}', 'r') as f:
    content = f.read()

content, n_url = re.subn(
    r'(  url "https://github\.com/${GITHUB_REPO}/archive/refs/tags/v)[^"]+',
    r'\g<1>${NEW_VERSION}.tar.gz',
    content,
    count=1,
)
content, n_sha = re.subn(
    r'(  sha256 ")[a-f0-9]{64}(")',
    r'\g<1>${SHA256}\g<2>',
    content,
    count=1,
)
if not (n_url and n_sha):
    sys.exit('formula: matched url=%d sha256=%d, expected 1 each' % (n_url, n_sha))

with open('${FORMULA}', 'w') as f:
    f.write(content)
PYEOF
ok "Formula: url → v${NEW_VERSION}, sha256 → ${SHA256:0:16}..."

# ── Step 5: Commit and push tap ───────────────────────────────────────────────
step "5/5" "Committing and pushing homebrew-redactor"

cd "$TAP_DIR"
git add Formula/redactor.rb
git commit -m "Update redactor formula to v${NEW_VERSION}"
git push origin main
ok "Pushed  →  github.com/rtulke/homebrew-redactor"

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}${BOLD}  ✓  Release v${NEW_VERSION} complete${NC}"
echo ""
echo    "  GitHub release : https://github.com/${GITHUB_REPO}/releases/tag/v${NEW_VERSION}"
echo    "  Pipeline       : https://github.com/${GITHUB_REPO}/actions"
echo    "  Homebrew tap   : brew upgrade redactor"
echo ""
echo -e "  ${DIM}The deb/rpm appear on the release once the workflow finishes.${NC}"
echo ""
