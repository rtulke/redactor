# Releasing redactor

## One-time: create the Homebrew tap

Homebrew taps must live in their own repo, named `homebrew-<name>`:

```bash
gh repo create rtulke/homebrew-redactor --public --clone ~/dev/homebrew-redactor
cd ~/dev/homebrew-redactor
mkdir -p Formula
cp ~/dev/redactor/packaging/homebrew/redactor.rb Formula/
git add Formula/redactor.rb
git commit -m "Add redactor formula"
git push origin main
```

`scripts/release.sh` expects the tap at `$HOME/dev/homebrew-redactor`. Elsewhere:

```bash
TAP_DIR=/path/to/homebrew-redactor ./scripts/release.sh
```

Users then install with:

```bash
brew install rtulke/redactor/redactor
```

The `sha256` in the committed formula is a placeholder of zeroes — the first
release run replaces it. Until then `brew install` from the tap fails, which is
correct: there is no tagged tarball to hash yet.

## Every release

```bash
cd ~/dev/redactor
./scripts/release.sh          # interactive
./scripts/release.sh 1.4.0    # or explicit
```

What it does:

0. **Refuses to start on a dirty tree**, then runs the test suite. Both before
   anything is bumped — a pushed tag is the one step that is awkward to undo.
1. Bumps `__version__` in `redactor.py`, then the version line and the
   package-download URLs in **both** `README.md` and `README_de.md`, then
   inserts a CHANGELOG section and opens `$EDITOR`.
2. Commits, tags `vX.Y.Z`, pushes both. **The tag push is what starts
   `.github/workflows/release.yml`**, which builds the packages and attaches
   them to the GitHub release.
3. Polls GitHub for the source tarball and hashes it.
4. Rewrites `url` + `sha256` in the tap's formula.
5. Commits and pushes the tap.

Steps 3–5 do **not** wait for the workflow. The Homebrew formula builds from
GitHub's **source tarball**, which exists the moment the tag lands; the deb/rpm
appear on the release a few minutes later, independently. If step 3 times out,
the release itself is still fine — only the tap is behind, and the error message
tells you the two commands to finish it by hand.

## Version: single source of truth

`redactor.py` is the only place the version is written:

```python
__version__ = '1.3.0'
```

`release.yml`, `ci.yml` and `scripts/release.sh` all read it with the regex
`__version__\s*=\s*'([^']+)'`. So:

- **single quotes** — not double
- **one line** — no line continuation
- **a literal** — not computed

Two guards, because a silent failure here builds and tags the *wrong version*
while looking entirely successful:

- `tests/test_redactor.py::Meta::test_version_is_single_quoted_on_one_line`
  fails if the line stops matching the packaging regex.
- `release.sh` re-reads the version after its `sed` and aborts if it did not
  actually change.

## Why there is no `-bin` package

sshscan ships `sshscan` *and* `sshscan-bin` because it needs PyYAML: on a host
without `python3-yaml` the script flavour has a dependency that may not resolve,
so the self-contained PyInstaller build earns its keep.

redactor imports nothing outside the standard library — `tests/test_redactor.py`
asserts this by parsing the AST, so it cannot quietly stop being true. The
distro's `python3` is therefore always enough, and a bundled binary would add
~8.5 MB, PyInstaller, an AV/EDR false-positive surface and — the expensive part
— the whole glibc-floor problem: a PyInstaller binary only runs on glibc ≥ the
one it was built against, and RHEL/Rocky 9 ship an *older* glibc (2.34) than the
Ubuntu runner (2.35). That is what forces sshscan to build inside `almalinux:8`
and assert the floor with `objdump`.

None of that exists here. Which is also why:

- the package is `arch: all` / `noarch` — one artifact for amd64, arm64 and
  everything else, so no build matrix;
- there is no macOS build job — nothing to compile, Homebrew handles macOS;
- the Homebrew formula needs no venv.

If a hard requirement for a Python-less target ever appears, sshscan's
`release.yml` is the blueprint to copy back.

## Workflows

| | runs on | does |
|---|---|---|
| `ci.yml` | push to main, PRs | tests on Python 3.8–3.13, builds the packages, installs the deb and uses it |
| `release.yml` | tag `v*`, manual | tests, builds deb/rpm/tarball, verifies on Debian 12/13, Ubuntu, Rocky 9, Fedora (amd64 + arm64), attaches to the release |

`ci.yml` exists because `release.yml` only fires on a tag: without it, a broken
commit on main is discovered on release day, and the fix costs a tag.

`ci.yml` also checks that every file in `profiles/` is listed in
`packaging/nfpm.yaml` — otherwise a new profile works in a git checkout and is
silently missing from the package.

## Checklist for a release that is not routine

- `./test.sh` green locally, not just in CI
- CHANGELOG section actually filled in (the script warns if the placeholder
  survives, but it lets you ship anyway)
- New profile? It needs a line in `packaging/nfpm.yaml` — CI will fail otherwise
- New CLI flag? The README pair and the profile comments are the docs; there is
  no man page to forget
