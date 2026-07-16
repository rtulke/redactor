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
  `@path`, `@sshkey`, `@phone`, `@secret`, `@keep`.
- **Stable pseudonyms**: the same value always becomes the same placeholder, so
  a redacted log still shows that two lines concern the same client. `--map`
  keeps that mapping consistent across runs and files; `--unredact` reverses it.
- **`@secret`**: vendor tokens (AWS, GitHub, GitLab, Slack, Google, Stripe,
  OpenAI, npm), JWTs, query-string parameters, Bearer/Basic headers, cookies,
  session IDs, crypt/bcrypt/argon2 password hashes, `password=` assignments,
  SNMP community strings and whole private-key blocks. One-way by design.
- **`--audit`**: reports values that look sensitive but no rule touched -
  a net for the shapes you did not think of.
- **Profiles** for apache/nginx, sshd, dhcpd, ftp, mail and config files.
- **Modes**: `--ask` (y/n/a per change, over `/dev/tty` so it works mid-pipe),
  `--blank`, `--keep-length`, `--in-place` (atomic, preserves mode),
  `--diff`, `--check` (exit 1, for pre-commit hooks), `--stats`, `--list-rules`.
- **Speed**: every rule carries a literal gate, so a rule that cannot match is
  skipped without running its regex.

---
