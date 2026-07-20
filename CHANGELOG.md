# Changelog

All notable changes to redactor are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

> The `## [X.Y.Z] — Current` marker on the top entry is load-bearing:
> `development/update_homebrew.sh` looks for it when it inserts the next
> section. Keep the em-dash.

## [1.3.0] — Current

### New Features

- Initial public release.
- **Rule syntax**: literal (default, no regex surprises), `@word` for whole-word
  matches, `/regex/` when needed, `@block` for multi-line markers.
- **Classes**: `@ip`, `@ipv6`, `@email`, `@mac`, `@hostname`, `@user`, `@uri`,
  `@path`, `@sshkey`, `@phone`, `@secret`, `@jwt`, `@field`, `@creditcard`,
  `@iban`, `@keep`.
- **`@field KEY…`**: redact a named field's value whatever its shape - JSON
  (`"key":"v"`), logfmt/query (`key=v`), and HTTP-header/YAML (`Key: value to
  end of line`, so `Authorization: Bearer x` is caught whole). Matches by the
  key, for the sensitive values that have no shape of their own.
- **`@creditcard` / `@iban`**: digit-only shapes gated on a checksum (Luhn /
  mod-97), so a 16-digit id or a random `AT..` string is left untouched.
- **`@jwt`**: the JWT part of `@secret` on its own.
- **`@hostname` / `@user` take `/regex/` patterns**, mixed with plain names:
  `@hostname web01 /srv[0-9]{3}/`. For a fleet with a naming scheme, where
  listing every machine means the next one leaks. Unlike a plain regex rule,
  matches still go through the mapping, so the pseudonyms stay stable and
  distinct instead of collapsing to one fixed replacement.
- **Stable pseudonyms**: the same value always becomes the same placeholder, so
  a redacted log still shows that two lines concern the same client. `--map`
  keeps that mapping consistent across runs and files; `--unredact` reverses it.
- **`@secret`**: vendor tokens (AWS, GitHub incl. fine-grained, GitLab, Slack,
  Google incl. OAuth, Stripe, OpenAI, npm, SendGrid, Mailgun, DigitalOcean,
  HashiCorp Vault, Shopify, Square, Twilio), JWTs, query-string parameters,
  Bearer/Basic headers, cookies, session IDs, crypt/bcrypt/argon2 password
  hashes, `password=` assignments, SNMP community strings and whole private-key
  blocks. One-way by design.
- **`--audit`**: reports values that look sensitive but no rule touched -
  a net for the shapes you did not think of. `-AA`/`--audit-all` drops the
  per-category cap and clipping; `--strict` makes it exit 1 on findings (so it
  can gate a commit or CI, like `--check`); `--entropy` adds a Shannon-entropy
  net for credentials whose shape no pattern knows.
- **Profiles** for apache/nginx, sshd, dhcpd, ftp, mail, config files, and a
  credentials-only `secrets` profile.
- **Modes**: `--ask` (y/n/a per change, over `/dev/tty` so it works mid-pipe),
  `--blank`, `--keep-length`, `--in-place` (atomic, preserves mode),
  `-r`/`--recursive` (walk a directory tree, skipping VCS metadata, symlinks,
  binaries and redactor's own files) with `--exclude`/`--include` globs,
  `--diff`, `--check` (exit 1, for pre-commit hooks), `--stats`, `--list-rules`.
- **`--color`** (`auto`/`always`/`never`, honours `NO_COLOR` and `TERM=dumb`):
  colours `--diff` and inverts the characters that actually differ within a
  line. Whole-line red/green tells you a 200-character log line changed, not
  what changed in it.
- **Speed**: every rule carries a literal gate, so a rule that cannot match is
  skipped without running its regex.

---
