# redactor

**Version:** 1.3.2
**Author:** Robert Tulke <rt@debian.sh>
**License:** MIT

*[Deutsche Version](README_de.md)*

Anonymize texts, logs, READMEs and code — quickly and simply.

Redactor is a local privacy tool for safely using logs, text, and source code with AI
tools. Before content is sent to an AI service, it removes credentials and consistently
pseudonymizes private and internal company data. This keeps the technical context intact
so the AI can still understand and analyze the content without receiving the original
sensitive values.

Redactor makes logs, text, and code safer to use with AI tools — without unnecessarily
exposing private or internal company data.

`sed` with a predefined rule list: reads text from stdin (or from files), applies every
rule from your config files, and writes the result to stdout.

```bash
cat /var/log/messages | redactor                       # filter a log
redactor --diff README.md                              # preview
redactor -i --map .redactor.map README.md src/*.py     # rewrite files in place
redactor -r --diff .                                   # preview a whole tree
redactor -r -i . --exclude 'tests/*'                   # scrub the tree in place
redactor --check README.md                             # check only, for pre-commit
redactor --audit access.log                            # what did I forget?
redactor --unredact --map .redactor.map answer.txt     # map applied backwards
```

No dependencies, just Python 3 (stdlib). No venv, no PyPI.

## Installation

**Debian / Ubuntu**

```bash
curl -LO https://github.com/rtulke/redactor/releases/latest/download/redactor_1.3.2_all.deb
sudo apt install ./redactor_1.3.2_all.deb
```

**RHEL / Rocky / Alma / Fedora**

```bash
curl -LO https://github.com/rtulke/redactor/releases/latest/download/redactor-1.3.2-1.noarch.rpm
sudo dnf install ./redactor-1.3.2-1.noarch.rpm
```

**macOS**

```bash
brew install rtulke/redactor/redactor
```

**Without installing**

```bash
curl -LO https://github.com/rtulke/redactor/releases/latest/download/redactor-1.3.2.tar.gz
tar xzf redactor-1.3.2.tar.gz && cd redactor-1.3.2
./redactor.py --version
```

Then create a config — redactor runs without one, but it only redacts what you tell it to:

```bash
cp /usr/share/doc/redactor/redactor.conf.example ~/.config/redactor.conf
```

The packages are **architecture-independent** (`all` / `noarch`) — one artifact for
amd64, arm64 and everything else. The only dependency is `python3` (>= 3.8).

Checksums: `SHA256SUMS` on the release.

## Ready-made profiles

Commented rule sets for the standard services, each with real sample log lines
(they live in `profiles/`):

```bash
redactor -p webserver    /var/log/apache2/access.log
redactor -p sshd         /var/log/auth.log
redactor -p dhcpd        /var/log/syslog
redactor -p ftp          /var/log/vsftpd.log
redactor -p mail         /var/log/mail.log
redactor -p configfiles --diff .env
redactor --list-profiles
```

## Tests

```bash
./test.sh                      # or: python3 -m unittest discover -s tests -v
./test.sh -k Phone             # a single class
```

Worth running after every rule change. A redaction tool fails **silently**: when a regex
breaks, the log still looks fine — it just has real data in it. So the interesting cases
in the suite are the negative ones: what must **not** be touched (timestamps, pids, byte
counts, `roberta`, `/var/lib/home/`).

CI runs the same suite across Python 3.8 through 3.13.

## Speed

Relevant for multi-GB logs: every rule carries a **literal gate** — a cheap substring
test that settles up front whether the rule *can* match at all. `@uri` without `://`,
`AKIA…` without `akia`, `@path` without `/home/` → the regex never starts. On a typical
log line almost all of the ~25 rules drop out, instead of each costing a full scan **plus
a string allocation**.

Deliberately *not* done: bundling the literals into one big alternation. That would break
the "rules operate on the output of the previous ones" semantics — the saving is not worth
the price.

---

# Typical workflows

## 1. Piping a log through

```bash
cat /var/log/messages | redactor | less
journalctl -u nginx | redactor > ticket-attachment.txt
journalctl -u nginx --since today | redactor | mail -s "nginx errors today" support@example.com
ssh web01 'tail -n 500 /var/log/syslog' | redactor | less
docker logs app 2>&1 | redactor > app.log
kubectl logs deploy/api | redactor | gh issue create -F - -t "api crash loop"
xclip -o | redactor | xclip -i          # anonymize the clipboard (Linux)
pbpaste | redactor | pbcopy             # same on macOS
```

## 2. Handing it to an AI

Every AI CLI that reads stdin takes redactor as a filter in front of it — the prompt
still carries the full technical context, just not the real values:

```bash
journalctl -u nginx | redactor | claude -p "why do these requests fail?"    # Claude Code
cat error.log      | redactor | gemini -p "find the root cause"             # Gemini CLI
{ echo "Review this log:"; redactor < app.log; } | codex exec -             # Codex CLI
docker logs app 2>&1     | redactor | llm "summarize the errors"            # llm
kubectl logs deploy/api  | redactor | mods "what is crashing here?"         # mods
cat access.log | redactor | ollama run llama3.2 "summarize this log"        # local model
```

With `--map` this becomes a round trip: the AI reasons about `host1` and `10.0.0.1`,
and its answer is translated back to your real machines afterwards. Two steps, not one
pipeline — `--unredact` loads the map when it starts, so it has to run *after* the
redacting step has written it:

```bash
journalctl -u nginx | redactor -m .redactor.map > clean.log
claude -p "why do these requests fail?" < clean.log | redactor -u -m .redactor.map
```

## 3. Anonymizing several files that belong together

**This is where `--map` matters.** Without a mapping file the numbering restarts on every
invocation: `web01` becomes `host1` in the README and `host3` in the code — and your
bundle contradicts itself. With `--map` it stays consistent across all files and runs:

```bash
redactor --diff --map .redactor.map README.md src/*.py    # look first
redactor -i    --map .redactor.map README.md src/*.py     # then do it
```

An existing `.redactor.map` is **found automatically** (searched upwards, like
`.redactor`) — then you no longer have to type `--map` at all. It is only *created* by an
explicit `--map`, so no stray run silently starts persisting a mapping you did not ask for.

The map is plain JSON; you can read it and pre-seed it by hand:

```json
{
  "hostname": { "web01.corp.local": "host1", "web01": "host2" },
  "ip":       { "192.168.1.50": "10.0.0.1" }
}
```

> **Careful:** the map is the key to reversing the redaction. It does **not** belong in
> the repo — it is already in `.gitignore`.

## 4. Checking instead of replacing (pre-commit / CI)

`--check` writes no output; it only reports what *would* be replaced, and exits 1 on any
match:

```bash
$ redactor --check README.md
redactor: 3 match(es) would be redacted
       2  @ip
       1  @secret query-param
$ echo $?
1
```

As a git hook in `.git/hooks/pre-commit`:

```bash
#!/bin/bash
files=$(git diff --cached --name-only --diff-filter=ACM)
[ -z "$files" ] && exit 0
if ! redactor --check $files; then
    echo "Commit blocked: there is still real data in there."
    echo "Fix:  redactor -i --map .redactor.map $files"
    exit 1
fi
```

## 5. Reversing

When someone replies to your ticket referring to `host1`, you want to know which machine
that was:

```bash
redactor --unredact --map .redactor.map answer.txt
```

This applies the map backwards. **`@secret` is excluded** — secrets are set to a hard
`[SECRET]` marker rather than pseudonymized, so there is no mapping for them. That is
deliberate: a token should be gone, not recoverable.

---

# Config files

Read in this order, and their rules applied in this order — all files accumulate, later
rules run last:

1. `/etc/redactor.conf`
2. `$XDG_CONFIG_HOME/redactor.conf` (default: `~/.config/redactor.conf`)
3. the nearest `.redactor`, searched upwards from `$PWD` (like `.gitignore`)
4. profiles from `-p/--profile`
5. files from `-f/--file`
6. rules from `-e/--expr`

`--no-config` skips 1–3. `--list-rules` shows what was actually loaded (including where
it came from) — the fastest way to answer "why isn't my rule matching?".

One `.redactor` per project in the repo root is the usual pattern: the project-specific
names there, your personal ones (`@user`, `@hostname`) in `~/.config/redactor.conf`.

---

# Rule syntax

One rule per line: `<search> <replacement>`. A `#` at the start of a line is a comment.
The same syntax applies to `-e`, so `-e 'robert user1'` is identical to the config line
`robert user1`.

| Form | Example | What it does |
|---|---|---|
| Literal | `robert user1` | substring, everywhere |
| Word boundary | `@word robert user1` | whole words only |
| Class detection | `@ip`, `@uri`, `@secret` | detects a whole category |
| Regex | `/sess_[0-9a-f]+/ X` | when the three above are not enough |
| Block | `@block /BEGIN/ /END/ X` | multi-line |

## 1. Literal — the default

The search text is taken **literally**. Not a regex, so `.`, `@`, `+`, `(` need no
escaping. That is deliberate: `foo@email.com` must not accidentally be read as a regex.

```
company.com             example.com
foo@email.com           redacted@example.com
"Robert Mueller"        "Max Mustermann"     # quote values containing spaces
hunter2                                       # no replacement -> [REDACTED]
```

A literal matches **inside words too**:

```
Rule:   robert user1

  robert logged in                ->  user1 logged in                ✓
  from roberta                    ->  from user1a                    <- substring!
  user robert123 failed           ->  user user1123 failed           <- substring!
```

## 2. `@word` — whole words only

```
Rule:   @word robert user1

  robert logged in                ->  user1 logged in                ✓
  from roberta                    ->  from roberta                   <- untouched!
  user robert123 failed           ->  user robert123 failed          <- untouched!
  hello robert, how are you?      ->  hello user1, how are you?      ✓ (comma is a boundary)
  (robert)                        ->  (user1)                        ✓
  robert@company.ch               ->  user1@company.ch               ✓ (@ is a boundary)
  xrobert                         ->  xrobert                        <- untouched!
```

"Word" means: no letters, digits or `_` directly before or after. Punctuation, spaces,
`@`, `/`, start of line and end of line are all valid boundaries.

### Which form when?

**When in doubt, literal.** The two failure directions are not equally bad:

- Literal too broad → over-redacts (`roberta` → `user1a`). Ugly, but **nothing leaks**.
- `@word` too narrow → under-redacts (`robert123` survives). That is a **real leak**.

Use `@word` when the search term is short and common and appears as a syllable inside
other words (`rob`, `admin`, `test`, `max`) — then check with `--stats` that the rule
still matches anything at all.

## 3. Class detection

These detect a whole category and assign a **stable** pseudonym per distinct value.

| Rule | Effect |
|---|---|
| `@ip` | every IPv4/IPv6 → `10.0.0.1`, `fd00::1` |
| `@email` | every address → `redacted1@example.com` |
| `@mac` | every MAC → `02:00:00:00:00:01` |
| `@hostname` | this host's names → `host1`. With arguments: `@hostname web01 /srv[0-9]{3}/` |
| `@user` | current user → `user1`. With arguments: `@user robert /svc-.*/` |
| `@uri` | `https://git.corp/x?token=a` → `https://host1/x` |
| `@path` | `/home/alice/...` → `/home/user1/...`. With arguments: prefix replacement |
| `@sshkey` | `SHA256:47DEQ...`, `ssh-rsa AAAA...` → `sshkey1` |
| `@phone` | `+41 79 123 45 67` → `phone1` (international only; national per country) |
| `@secret` | API keys, tokens, hashes, `password=…` → `[SECRET]` |
| `@jwt` | JSON Web Tokens `eyJ….eyJ….sig` → `[SECRET]` |
| `@field KEY…` | a named field's value (JSON / `key=` / header) → `[REDACTED]` |
| `@creditcard` | 13–19 digits passing Luhn → `[CARD]` |
| `@iban` | IBANs passing the mod-97 check → `[IBAN]` |
| `@keep` | the opposite: **never** replace these values |

`@hostname`, `@user` and `@sshkey` are **word-bounded automatically**.

### Names or patterns

`@hostname` and `@user` take plain names, `/regex/` patterns, or a mix:

```
@hostname web01 db01.corp.local /srv[0-9]{3}/
@user robert /svc-.*/
```

A pattern is for a **fleet with a naming scheme**, where listing every machine
means the next one someone racks leaks the first time it appears in a log — and
you find out afterwards.

Why not just a regex rule? Because `/srv[0-9]{3}/ HOST` would call every machine
`HOST`. A pattern here still goes through the mapping:

```
Rule:   @hostname /srv[0-9]{3}/

  srv208.example.com  ->  host1.example.com
  srv113.example.com  ->  host2.example.com
  srv208.example.com  ->  host1.example.com     <- the 1 again
```

So you keep the thing that makes redactor worth using over `sed`: you can still
see which lines concern the same machine, without knowing which one.

Names and patterns share one mapping table, so `@hostname web01 /srv[0-9]{3}/`
numbers them in one sequence. Internally they become two rules — a literal is
its own cheap gate, a regex cannot be gated at all (see [Speed](#speed)) — but
that is invisible except in `--list-rules`.

**Stable means:** the same value always becomes the same pseudonym.

```
Rule:   @ip

  10:03 login from 192.168.1.50   ->  10:03 login from 10.0.0.1
  10:04 login from 8.8.8.8        ->  10:04 login from 10.0.0.2
  10:07 logout   192.168.1.50     ->  10:07 logout   10.0.0.1     <- the 1 again!
```

That is the difference from `sed s/ip/x/`: in the redacted log you can still see that
line 1 and line 3 concern the **same** client — without revealing which one. Across
several runs this only holds with `--map`.

The pseudonymization is **idempotent**: running redactor twice over the same file does
not turn `host1` into `host2`.

### `@uri`

```
  https://bob:pw@git.corp.local/team/proj?token=abc#frag
    -> https://host1/team/proj
```

Scheme, port and path are kept; the host is pseudonymized (using the **same** table as
`@hostname`, so it stays consistent); **userinfo, query and fragment are dropped** —
that is exactly where credentials and tokens live. The path stays, because it is usually
the information you kept the log for in the first place.

Handy for DSNs: `postgres://app:s3cr3t@db.corp:5432/prod` → `postgres://host1:5432/prod`.

> `@uri` needs `://`. An access-log request (`"GET /admin?token=abc HTTP/1.1"`) has no
> scheme — the query string is caught by `@secret`, not `@uri`.

### `@path`

Without arguments it detects home directories and maps the user part through the **same**
table as `@user`:

```
  /home/alice/myproject/dev/x.py  ->  /home/user1/myproject/dev/x.py
  /Users/robert/Documents         ->  /Users/user2/Documents
  /var/lib/home/cache             ->  /var/lib/home/cache   <- not a home dir, stays
```

With arguments it is a **literal prefix replacement**:

```
@path /home/alice/myproject/    PROJECT/

  /home/alice/myproject/dev/x.py  ->  PROJECT/dev/x.py
```

> **Why `@path` and not just a literal rule?** Because `/home/alice/myproject/ PROJECT/`
> would be parsed as a *regex* — the search pattern starts with `/` and ends with `/`.
> Quoting does not help. `@path` sidesteps the ambiguity because the directive comes
> first.

### `@phone`

The most delicate detector in the tool. Every other class has a distinctive shape
(`AKIA…`, `SHA256:`, `://`) — a phone number is just a run of digits, and logs are made
of digit runs. So the default is deliberately narrow:

```
Rule:   @phone                        (default: international only)

  +41 79 123 45 67          ->  phone1
  +41791234567              ->  phone1        <- same number, same pseudonym
  0041791234567             ->  phone1
  079 123 45 67             ->  079 123 45 67 <- national, off by default
  1712913802                ->  1712913802    <- timestamp, stays
  sshd[1234]                ->  sshd[1234]    <- pid, stays
  sent 1234567 bytes        ->  sent 1234567 bytes
```

Two safeguards: the `+`/`00` prefix as the signal, and a digit count (8–15, per E.164) —
anything that does not fit is left alone. On top of that the mapping happens on the
**normalized** number, so `+41 79 123 45 67` and `+41791234567` get the same pseudonym
instead of two.

National formats are **opt-in per country**:

```
@phone ch          # 079 123 45 67
@phone ch de at    # several
```

Known: `ch`, `de`, `at`, `us`. Other countries → use a regex rule. `de`/`at` are
inherently fuzzier than `ch` (lengths vary a lot there) — verify with `--stats`.

### `@keep` — the opposite

Without it, `@ip` also pseudonymizes `127.0.0.1` (which tells nobody anything) and `@uri`
turns every `https://github.com/foo` in your README into `https://host1/foo` — destroying
the document.

```
@keep 127.0.0.1 ::1 0.0.0.0 localhost
@keep github.com stackoverflow.com

  127.0.0.1                        ->  127.0.0.1                      <- stays
  8.8.8.8                          ->  10.0.0.1                       <- replaced
  https://github.com/x?tab=readme  ->  https://github.com/x?tab=readme <- fully untouched
  https://git.corp/x?token=abc     ->  https://host1/x
```

A kept host leaves the **whole** URL alone, query included — "kept" means "this is
public", not "the host is fine".

`@keep` applies **globally, regardless of line order** (it is consulted at match time,
not at parse time) and shows up in `--list-rules` — "why was that not redacted?" has to
stay answerable. The tool itself ships **no** built-in exceptions; the loopback line sits
active in `redactor.conf.example` instead, so nothing happens invisibly.

### `@secret` — the safety net

Your config only protects you from what you **thought of**. `@secret` catches the
classics. Unlike the other classes it does **not** pseudonymize but sets a hard
`[SECRET]` — you want a token gone, not correlated. That is also why `@secret` is **not**
reversible with `--unredact`.

| detected | example |
|---|---|
| Vendor tokens | `AKIA…`, `ghp_…`, `github_pat_…`, `glpat-…`, `xoxb-…`, `AIza…`, `ya29.…`, `sk_live_…`, `sk-…`, `npm_…`, `SG.…`, `key-…` (Mailgun), `dop_v1_…`, `hvs.…` (Vault), `shpat_…`, `sq0…` |
| JWTs | `eyJhbGci….eyJzdWI….xxx` |
| **Query parameters** | `?token=abc`, `&api_key=xyz`, `&session=…` |
| Bearer / Basic | `Authorization: Bearer …`, `Basic dXNlcjpwdw==` |
| Cookies | `Cookie: …`, `Set-Cookie: x=…`, `PHPSESSID=…` |
| Password hashes | `$6$rounds=5000$…` (shadow), `$apr1$…` (htpasswd), `$2y$…` (bcrypt) |
| Assignments | `password=hunter2`, `api_key: "xyz"`, `client_secret = 'abc'` |
| SNMP | `rocommunity public` |
| Private keys | the **whole block** from BEGIN to END (multi-line) |

The query parameter is the most important of these: in apache/nginx logs
`"GET /x?token=abc"` is the most common real leak, and `@uri` never sees it (no `://`).

Password hashes count as secrets: a `$6$` hash from `/etc/shadow` is crackable offline —
publishing it is publishing the password with a delay.

> **`@secret` is deliberately trigger-happy.** In code the assignment rule also hits
> `token = get_token()` → `token = [SECRET]`. Over-redacting is cosmetic; a live key that
> slipped through is not. Check with `--diff` or `--stats`.

`@jwt` is the JWT part of `@secret` on its own, for when you want to strip tokens and
nothing else. The ready-made **`secrets` profile** bundles `@secret` with `@field` for the
common auth keys — `redactor -p secrets` is the "scrub the credentials before I paste this"
button.

### `@field KEY…` — redact a value by its key

`@secret` matches by the value's **shape** (`AKIA…`, `eyJ…`). But the most sensitive values
have no shape — a password, a session token, an `X-Api-Key`. `@field` matches by the **key**
instead, and understands the three ways a key/value pair is written:

```bash
redactor -e '@field password authorization api_key' app.log
```

| input | output |
|---|---|
| `{"password": "hunter2"}` | `{"password": "[REDACTED]"}` |
| `password=hunter2` / `?api_key=abc&x=1` | `password=[REDACTED]` / `?api_key=[REDACTED]&x=1` |
| `Authorization: Bearer eyJ… x` | `Authorization: [REDACTED]` |

The header form runs to the end of the line, so a value with spaces (`Bearer x`) is caught
whole — the one thing `@secret`'s fixed `assignment` rule can't do. Case-insensitive on the
key, one-way (not restored by `--unredact`).

### `@creditcard` / `@iban` — checksum-gated PII

Both are pure digits, the false-positive minefield `@phone` lives in — so each is gated on a
**checksum**. `@creditcard` (13–19 digits, spaced or dashed) only fires when the number
passes **Luhn**; a 16-digit id that isn't a card is left alone. `@iban` validates **mod-97**.
Fixed markers `[CARD]` / `[IBAN]`, one-way.

## 4. Regex

Wrap the pattern **in slashes**. `\1` backreferences work, `(?i)` makes it
case-insensitive.

```
/sess_[0-9a-f]{32}/             SESSION_REDACTED
/(password=)\S+/                "\1REDACTED"      # -> password=REDACTED
/(?i)secret/                    X                 # also hits SECRET, Secret
```

## 5. `@block` — multi-line

```
@block /-----BEGIN CERTIFICATE-----/ /-----END CERTIFICATE-----/ "[CERT]"
```

Everything from the start marker to the end marker is swapped for **one** replacement
text. Implemented line-based on purpose (a state machine rather than a DOTALL regex over
the whole file), so streaming still works on large logs. If the file ends inside a block,
the remainder stays redacted — when in doubt, too much.

`@secret` ships such a block for private keys already.

---

# Options

| Option | Purpose |
|---|---|
| `-e RULE` | add a rule directly (repeatable) |
| `-f PATH` | load an additional rule file (repeatable) |
| `-p NAME`, `--profile` | load a ready-made rule set (repeatable) |
| `-a`, `--ask` | confirm every replacement individually |
| `-b`, `--blank` | replace every match with as many spaces as it was long |
| `-k`, `--keep-length` | pad the replacement with spaces to the original length |
| `-i`, `--in-place` | rewrite the files directly |
| `-r`, `--recursive` | descend into directory arguments (skips `.git`, symlinks, binaries) |
| `--exclude GLOB` | skip files/dirs matching GLOB while recursing (repeatable) |
| `--include GLOB` | while recursing, take only files matching GLOB (repeatable) |
| `-d`, `--diff` | unified diff of the changes, no output |
| `--color WHEN` | `auto` (default) / `always` / `never` — colors the diff and marks the changed span |
| `-c`, `--check` | check only, exit 1 on any match |
| `-A`, `--audit` | report what looks sensitive and has no rule |
| `-AA`, `--audit-all` | same, but list every finding in full (no cap, no clipping) |
| `--strict` | with `--audit`, exit 1 on any finding (gate a commit / CI) |
| `--entropy` | with `--audit`, also flag high-entropy token-like strings |
| `-m PATH`, `--map` | keep the mapping persistent |
| `-u`, `--unredact` | apply the map backwards |
| `-s`, `--stats` | match count per rule to stderr |
| `-l`, `--list-rules` | loaded rules + where they came from |
| `--list-profiles` | show the available profiles |
| `-n`, `--no-config` | use only `-e`/`-f` |
| `-V`, `--version` | version |

`-i`, `-d`, `-c` and `-A` are mutually exclusive. `-V` is uppercase so `-v` stays free
for a future `--verbose`; `-A` is uppercase because `-a` is already `--ask`.

## `-d` / `--diff` and `--color`

```bash
redactor -d access.log            # color when it is a terminal
redactor -d access.log | less -R  # -R makes less pass the escapes through
redactor -d --color=never access.log > review.patch
```

Whole-line red and green is close to useless on a log: in a 200-character line
where one IP moved, you can see that *something* changed but not *what*. So the
`-`/`+` pairs are diffed a second time per character, and only the characters
that actually differ are inverted:

```
--- access.log
+++ access.log (redacted)
@@ -1,2 +1,2 @@
-Jul 16 13:56:56 srv208.example.com sshd[2764440]: Accepted publickey for robert from 10.0.10.113
+Jul 16 13:56:56 host1.example.com sshd[2764440]: Accepted publickey for user1 from 10.0.0.1
                 ^^^^^^^^^^^^^^^^^^                                       ^^^^^^      ^^^^^^^^^^^
                 inverted, the rest of the line is plain red / green
```

`auto` colors only when stdout is a terminal, so redirecting to a file or
piping into `patch` stays clean. `NO_COLOR` (set and non-empty) and `TERM=dumb`
turn `auto` off; an explicit `--color=always` overrides both — an environment
variable should not veto what you typed on the command line.

Line counts stay 1:1 except for `@block`, which collapses many lines into one.
There is no sensible character-level pairing for that, so those hunks get plain
line colors.

## `-A` / `--audit` — "what did I forget?"

This addresses the real weakness of the whole approach: **redactor only protects you from
what you thought of.** `@secret` is a net for known shapes — `--audit` is one for unknown
shapes.

```
$ redactor -p webserver --audit access.log
redactor: audit - 6 value(s) no rule touched, in 3 categor(ies)

  internal-host  (2 distinct, 41 hit(s))
    db.corp.local                     28x  first at access.log:12
    mail.corp.local                   13x  first at access.log:88

  long-digits  (3 distinct, 3 hit(s))
    41791234567                        1x  first at access.log:5

  base64-blob  (1 distinct, 1 hit(s))
    dXNlcjpwYXNzd29yZA==               1x  first at access.log:8

  Not necessarily leaks - audit only asks whether you meant to keep
  these. Add a rule for the real ones, @keep the intentional ones.
```

Three design decisions make it usable:

- It runs over the **output**, not the input — what is already redacted is not a finding.
- It ignores **our own pseudonyms**. `10.0.0.1` looks exactly like an IP; without that
  filter `--audit` would flag every successful replacement as suspicious.
- It ignores everything `@keep` spared — that was a decision.

It **redacts nothing** and writes no output; the exit code is always 0. `--audit` asks,
it does not decide — which is why it is deliberately chatty: a missed value is a leak, a
false positive is one line of noise.

The report is a summary on purpose: each category shows its five loudest values and clips
anything past 60 characters, so a noisy log stays skimmable. When `-A` tells you a category
is worth reading in full, `-AA` (or `--audit-all`) lifts both caps and prints every value
at full length:

```bash
redactor -p webserver --audit access.log      # skim: top 5 per category
redactor -p webserver --audit-all access.log  # the whole list, untruncated
```

It looks for: internal hostnames (`.local`, `.corp`, `.lan`, …), domains, e-mail
addresses, IPs, MACs, URL hosts, home paths, SSH keys, long digit runs (9+), hex blobs
(32+), base64 blobs (24+) and `-----BEGIN` markers.

Two modifiers make audit do more than talk:

```bash
redactor -A --strict  file    # exit 1 if anything was found — gate a commit / CI
redactor -A --entropy file    # also flag high-entropy token-like strings
```

`--strict` turns audit into a gate for the **unknown** shapes, the way `--check` gates the
known ones. `--entropy` adds the net for credentials whose shape no pattern knows — a random
API key gets flagged on its randomness alone. It is conservative (a camelCase identifier
stays quiet) and off by default because it is the noisiest of the checks.

## `-p` / `--profile`

```bash
redactor -p webserver access.log
redactor -p sshd -p configfiles /var/log/auth.log
redactor --list-profiles
```

Looked up in this order:

1. `$XDG_CONFIG_HOME/redactor/profiles/<name>.conf`
2. `/usr/share/redactor/profiles/<name>.conf`
3. `profiles/<name>.conf` next to the script (so a git checkout works with no install)

So drop your own profiles into `~/.config/redactor/profiles/`. Profiles are loaded
**before** `-f`, so your own rule files can extend them.

## `-a` / `--ask`

```
--- /var/log/messages:42   [@ip]
  - Apr 12 10:03 sshd: accepted from 192.168.1.50 port 22
  + Apr 12 10:03 sshd: accepted from 10.0.0.1 port 22
  '192.168.1.50'  ->  '10.0.0.1'
  replace? [y/n/a]
```

`y` or Enter = yes, `n` = no, `a` = all remaining without asking.

The dialog goes over `/dev/tty`, **not** stdin/stdout — which is why it works in the
middle of a pipe, where stdin is the data and stdout the result:

```bash
cat /var/log/messages | redactor -a > clean.log    # prompts still appear
```

Without a terminal (a cron job) `-a` aborts with a clear error instead of silently
replacing everything. Blocks are asked once as a whole.

## `-b` / `--blank` and `-k` / `--keep-length`

```
Rule:   @ip

  normal:  10:03 from 192.168.1.50 port 22
  -b:      10:03 from              port 22
  -k:      10:03 from 10.0.0.1     port 22
```

`-b` throws the configured replacements away and emits **as many spaces as the match was
long** — same length, not empty, so columns in logs and indentation in code survive.
`-k` keeps the pseudonym and only pads it to the original length.

## `-i` / `--in-place`

Needs file arguments (you cannot rewrite a pipe in place). Writes via a temp file plus
`os.replace`, so it is atomic — a crash never leaves a half-redacted file. File modes are
preserved, and files with no matches are not touched at all (mtime stays).

> `-i` overwrites your original. Run `--diff` first the first time.

## `-r` / `--recursive` — a whole tree at once

`-r` turns a directory argument into the list of text files under it, then hands that list
to whatever mode you asked for. It is *pure file expansion*: `-r -i` rewrites the tree,
`-r -d` previews it as one big diff, `-r -c` fails CI if anything in the tree still leaks,
`-r -A` audits the lot. A directory without `-r` is an error, the same way `grep` refuses.

```bash
redactor -r -d -m .redactor.map .                 # preview the whole project
redactor -r -i -m .redactor.map . --exclude 'tests/*'   # then rewrite it
```

The walk deliberately never touches:

- **version-control metadata** (`.git`, `.hg`, `.svn`, …) — rewriting it corrupts the repo;
- **symbolic links** — a redaction could otherwise escape the tree or hit a shared file;
- **binary files** — anything with a NUL byte in the first 8 kB (images, objects, archives);
- **redactor's own `.redactor` and `.redactor.map`** — the config holds your raw secrets and
  the map is the un-redaction key; scrubbing either would be self-defeating.

`--exclude GLOB` prunes more (matched against the file name *or* the path relative to the
directory, so both `--exclude '*.min.js'` and `--exclude 'vendor/*'` work; a matching
directory is never entered). `--include GLOB` narrows to only matching files; `--exclude`
wins over `--include`. Both are repeatable. Anything skipped is summarised on stderr.

A file you name **explicitly** is always processed — the binary/symlink guards apply only to
files discovered by walking, never to one you asked for by name.

> `-r -i` is a big hammer over a source tree: your full rule set runs on *every* file, so a
> broad literal like `@user robert` will rewrite that word wherever it appears. Run `-r -d` or
> `-r -c` first, and reach for `--exclude` to spare docs and fixtures. Git-tracked files only?
> `git ls-files -z | xargs -0 redactor -i` composes with the existing in-place mode.

---

# Known edges

- **Order matters.** `foo bar` followed by `bar baz` turns `foo` into `baz` in the end.
- **FQDN and short name** get different pseudonyms (`web01.corp.local` → `host1`,
  `web01` → `host2`), because they are different strings.
- **A search text wrapped in slashes is always a regex.** For paths, use `@path`.
- **`--ask` shows a per-match preview** that only accounts for the current rule.
- **Line-based**, except for `@block`. Patterns spanning newlines need `@block`.
- **`@secret` is one-way** and cannot be reversed with `--unredact`.
- **`--map` is the key to reversing the redaction.** Do not commit it.
- **DHCP client hostnames** (`roberts-laptop`) cannot be detected automatically —
  arbitrary free text with no shape. See `profiles/dhcpd.conf`.

# Development

```
├── redactor.py               the tool (stdlib only)
├── profiles/                 ready-made rule sets
├── man/redactor.1            man page
├── completion/               bash completion
├── tests/                    python -m unittest discover -s tests
├── packaging/
│   ├── nfpm.yaml             deb + rpm
│   └── homebrew/redactor.rb  master copy for the tap
├── scripts/release.sh        release automation
├── ruff.toml                 lint config (narrow on purpose, see the comments)
├── docs/RELEASING.md         how a release runs, and why there is no -bin
└── .github/workflows/        ci.yml (push/PR) + release.yml (tag)
```

Releasing: see [docs/RELEASING.md](docs/RELEASING.md).

The suite guards the sync points that rot silently: the `__version__` line the
packaging greps for, the man page's version, the `@builtin` list the completion
hardcodes, and the fact that nothing outside the standard library is imported.

## Linting

```bash
ruff check .
```

`ruff.toml` explains why the rule set is narrow. ruff is a **dev-time tool only** —
it never becomes a runtime dependency, and `./test.sh` does not need it.

Installing it on Ubuntu 24.04 / Debian 12+ is the one dev-setup snag: those mark the
system Python as *externally managed* (PEP 668), so `pip install ruff` fails with
`error: externally-managed-environment`, and ruff is not in apt. Use `pipx`, which
exists for exactly this:

```bash
sudo apt install pipx && pipx ensurepath
pipx install ruff
```

Or, without pipx: a throwaway venv (`python3 -m venv /tmp/rv && /tmp/rv/bin/pip
install ruff`), or ruff's standalone binary from <https://astral.sh/ruff/install.sh>.

CI is unaffected — `actions/setup-python` provides a Python that is not
externally managed, so plain `pip install ruff` works there.

# License

MIT — see [LICENSE](LICENSE).
