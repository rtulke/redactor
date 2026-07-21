#!/usr/bin/env python3
"""redactor - anonymize texts, logs, READMEs and code.

Think of it as "sed with a predefined rule list": the substitution rules come
from config files and/or -e flags, all of them are applied to every line, and
it pseudonymizes IPs, e-mails, hosts, users, URLs and paths stably - the same
value always becomes the same placeholder, so a log stays readable and the
README still matches the code next to it.

Config files are read, in this order, and their rules are applied in this order:

    1. /etc/redactor.conf
    2. $XDG_CONFIG_HOME/redactor.conf   (default: ~/.config/redactor.conf)
    3. the nearest .redactor found by walking up from the current directory
    4. any file(s) passed with -f/--file
    5. any rule(s) passed with -e/--expr   (applied last, good for ad-hoc tweaks)

Rule syntax (one rule per line; also used as the value for -e):

    # comment lines start with '#'
    company.com       example.com        # literal search -> replacement
    robert            user1              # plain substring, replaced everywhere
    foo@email.com     redacted@x.com
    secret                               # no replacement given -> [REDACTED]
    "two words"       "one word"         # quote values containing spaces

    @word robert      user1              # whole word only: roberta stays intact

    @ip                                  # every IPv4/IPv6 -> stable pseudo-IP
    @email                               # every e-mail   -> redactedN@example.com
    @mac                                 # every MAC addr -> 02:00:00:xx:xx:xx
    @hostname                            # this host's names -> hostN
    @hostname web01 db01.corp.local      # only these hostnames -> hostN
    @hostname /srv[0-9]{3}/               # a whole fleet by pattern -> hostN
    @user                                # current username -> userN
    @user robert alice                   # only these usernames -> userN
    @user /svc-.*/                       # ...or by pattern -> userN
    @uri                                 # scheme://host/path -> scheme://hostN/path
    @path                                # /home/alice/... -> /home/user1/...
    @path /home/alice/myproject/ PROJECT/  # literal prefix, no regex ambiguity
    @sshkey                              # SHA256: fingerprints, ssh-rsa AAAA... -> sshkeyN
    @phone                               # +41 79 123 45 67 -> phoneN (international only)
    @phone ch de                         # ...plus these countries' national formats
    @secret                              # api keys, tokens, password=... -> [SECRET]
    @jwt                                 # JSON Web Tokens eyJ..eyJ..sig -> [SECRET]
    @field password authorization        # a named field's value (json/kv/header) -> [REDACTED]
    @creditcard                          # 13-19 digits passing Luhn -> [CARD]
    @iban                                # IBANs passing the mod-97 check -> [IBAN]

    @keep 127.0.0.1 localhost github.com # never redact these, they are public

    /sess_[0-9a-f]{32}/   SESSION        # regex (wrap in slashes); \\1 backrefs OK

    # multi-line: everything from start to end marker becomes one replacement
    @block /-----BEGIN [A-Z ]*PRIVATE KEY-----/ /-----END [A-Z ]*PRIVATE KEY-----/ [SECRET]

With -a/--ask every single replacement is shown and confirmed with y (yes),
n (no) or a (all remaining, stop asking).  The prompt is read from the
controlling terminal, so it also works in the middle of a pipe, where stdin
is the data being filtered.

Usage:
    cat /var/log/messages | redactor
    echo foo | redactor -e 'foo bar' -e '@ip'
    redactor -p webserver /var/log/apache2/access.log    # ready-made rule set
    cat /var/log/messages | redactor -a > clean.log      # confirm each change
    redactor -d README.md                                # preview as a diff
    redactor -i -m .redactor.map README.md src/*.py      # rewrite files in place
    redactor -r -d .                                     # preview a whole tree
    redactor -r -i . --exclude 'tests/*'                 # then scrub it
    redactor -c README.md || echo "still contains secrets"
    redactor -A access.log                               # what did I forget?
    redactor -u -m .redactor.map answer.txt              # map applied backwards
    redactor -l          # which rules are loaded, and from where?
"""

import argparse
import difflib
import fnmatch
import getpass
import json
import math
import os
import re
import shutil
import socket
import sys

# Single source of truth: release.yml and development/update_homebrew.sh both
# read the version straight out of this file with
#   __version__\s*=\s*'([^']+)'
# so the quotes have to stay single and the assignment on one line.
__version__ = '1.3.1'
__author__ = 'Robert Tulke'
__email__ = 'rt@debian.sh'
__license__ = 'MIT'
__url__ = 'https://github.com/rtulke/redactor'

CONFIG_BASENAME = ".redactor"
MAP_BASENAME = ".redactor.map"
DEFAULT_REPLACEMENT = "[REDACTED]"
BUILTINS = (
    "ip", "ipv6", "email", "mac", "hostname", "user",
    "word", "secret", "uri", "url", "path", "block", "sshkey",
    "phone", "keep", "jwt", "field", "creditcard", "iban",
)

# Values @keep marked as public.  Consulted at match time, so a @keep anywhere
# in any config file applies to every rule, regardless of line order.
KEEP = set()

# --------------------------------------------------------------------------
# stable pseudonym mapping
# --------------------------------------------------------------------------


class Mapper:
    """Assigns a stable placeholder to each distinct input value."""

    def __init__(self, table, used, fmt):
        self._table = table  # dict owned (and possibly persisted) by MapStore
        self._used = used  # placeholders already handed out for this category
        self._fmt = fmt

    def value(self, key):
        if key in KEEP:
            return key  # @keep: public value, never pseudonymize
        known = self._table.get(key)
        if known is not None:
            return known
        if key in self._used:
            # `key` is a placeholder we handed out earlier, not real data.  This
            # keeps redaction idempotent: running redactor twice, or letting
            # @hostname and @uri both touch the same URL, must not turn host1
            # into host2.
            return key
        n = len(self._table) + 1
        new = self._fmt(n)
        while new in self._used:  # a hand-edited map may already use this one
            n += 1
            new = self._fmt(n)
        self._table[key] = new
        self._used.add(new)
        return new

    def __call__(self, match):
        return self.value(match.group(0))


class MapStore:
    """The value -> placeholder tables, optionally backed by a JSON file.

    Without --map the tables live for one run only.  With --map they are loaded
    up front and written back afterwards, which is what keeps a README and the
    code next to it consistent: redacted in two runs, the same hostname would
    otherwise become host1 in one file and host3 in the other."""

    def __init__(self):
        self.reset()

    def reset(self):
        self.path = None
        self.tables = {}
        self._used = {}

    def load(self, path):
        self.path = path
        self.tables = {}
        self._used = {}
        if not os.path.isfile(path):
            return  # first run: start empty, save() creates the file
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError) as exc:
            raise SystemExit("redactor: cannot read map %s: %s" % (path, exc))
        if not isinstance(data, dict):
            raise SystemExit("redactor: map %s is not a JSON object" % path)
        # hand-editing the map is expected, so complain properly instead of
        # throwing a traceback at whoever mistyped it
        for category, table in data.items():
            if not isinstance(table, dict) or not all(
                isinstance(v, str) for v in table.values()
            ):
                raise SystemExit(
                    "redactor: map %s: %r must be an object of string -> string"
                    % (path, category)
                )
        self.tables = {str(k): dict(v) for k, v in data.items()}

    def save(self):
        if not self.path:
            return
        tmp = self.path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(self.tables, fh, indent=2, sort_keys=True, ensure_ascii=False)
                fh.write("\n")
            os.replace(tmp, self.path)  # atomic: never leave a half-written map
        except OSError as exc:
            sys.stderr.write("redactor: cannot write map %s: %s\n" % (self.path, exc))

    def mapper(self, category, fmt):
        table = self.tables.setdefault(category, {})
        used = self._used.setdefault(category, set(table.values()))
        return Mapper(table, used, fmt)


STORE = MapStore()


def _ip4_pseudo(n):
    return "10.%d.%d.%d" % ((n >> 16) & 255, (n >> 8) & 255, n & 255)


def _ip6_pseudo(n):
    return "fd00::%x" % n


def _mac_pseudo(n):
    return "02:00:00:%02x:%02x:%02x" % ((n >> 16) & 255, (n >> 8) & 255, n & 255)


def _host_pseudo(n):
    return "host%d" % n


def _user_pseudo(n):
    return "user%d" % n


def _email_pseudo(n):
    return "redacted%d@example.com" % n


def _sshkey_pseudo(n):
    return "sshkey%d" % n


# --------------------------------------------------------------------------
# detectors for the @builtins
# --------------------------------------------------------------------------

_OCTET = r"(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)"
IPV4_RE = re.compile(r"\b(?:%s\.){3}%s\b" % (_OCTET, _OCTET))

IPV6_RE = re.compile(
    r"""(?xi)
    (?<![:.\w])
    (?:
        (?:[a-f0-9]{1,4}:){7}[a-f0-9]{1,4}
      | (?:[a-f0-9]{1,4}:){1,7}:
      | (?:[a-f0-9]{1,4}:){1,6}:[a-f0-9]{1,4}
      | (?:[a-f0-9]{1,4}:){1,5}(?::[a-f0-9]{1,4}){1,2}
      | (?:[a-f0-9]{1,4}:){1,4}(?::[a-f0-9]{1,4}){1,3}
      | (?:[a-f0-9]{1,4}:){1,3}(?::[a-f0-9]{1,4}){1,4}
      | (?:[a-f0-9]{1,4}:){1,2}(?::[a-f0-9]{1,4}){1,5}
      | [a-f0-9]{1,4}:(?::[a-f0-9]{1,4}){1,6}
      | :(?:(?::[a-f0-9]{1,4}){1,7}|:)
    )
    (?![:.\w])
    """
)

EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
MAC_RE = re.compile(r"\b(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}\b")

# scheme://[userinfo@]host[:port][/path][?query][#fragment]
URI_RE = re.compile(
    r"""(?xi)
    \b([a-z][a-z0-9+.-]*)://        # 1 scheme
    (?:[^/@\s]+@)?                  #   userinfo - dropped, it holds credentials
    ([^/?\#\s:]+)                   # 2 host
    (?::(\d+))?                     # 3 port
    ([^\s?\#]*)                     # 4 path
    (?:\?[^\s\#]*)?                 #   query    - dropped, it holds tokens
    (?:\#\S*)?                      #   fragment - dropped
    """
)

# /home/<user>/... and friends.  The lookbehind keeps us out of /var/lib/home/x.
HOME_RE = re.compile(r"(?<!\w)(/home/|/Users/|/var/home/)([A-Za-z0-9._-]+)")

# @sshkey - a fingerprint in auth.log identifies one specific key, i.e. one
# person.  Pseudonymized rather than destroyed: you still want to see that the
# same key logged in five times.  The legacy MD5 form has 16 octets, so it can
# not collide with MAC_RE, which insists on exactly 6.
SSH_KEY_PATTERNS = [
    r"\bSHA256:[A-Za-z0-9+/]{43}=*",
    r"\b(?:[0-9a-f]{2}:){15}[0-9a-f]{2}\b",
    r"\bssh-(?:rsa|ed25519|dss)\s+AAAA[A-Za-z0-9+/=]+",
    r"\becdsa-sha2-nistp\d+\s+AAAA[A-Za-z0-9+/=]+",
]
SSH_KEY_RE = re.compile("|".join(SSH_KEY_PATTERNS))

# @phone - by far the most dangerous detector here.  Every other class has a
# distinctive shape (AKIA.., SHA256:, ://); a phone number is just digits, and
# logs are made of digits: timestamps, pids, byte counts, ports, ids.  So the
# default only takes formats carrying an international prefix, where the + or 00
# is the signal.  National formats are opt-in per country, because that is where
# the false positives live.  The lookbehind keeps us out of longer numbers.
PHONE_INTL_RE = re.compile(
    r"(?<![\d.])(?:\+|\b00)\d{1,3}(?:[\s./-]?\(?\d{1,4}\)?){2,6}(?![\d])"
)
PHONE_NATIONAL = {
    # 079 123 45 67 / 079/123 45 67 / 0791234567 / 044 123 45 67
    "ch": r"(?<![\d.])0[1-9]\d[\s./-]?\d{3}(?:[\s./-]?\d{2}){2}(?![\d])",
    # 030 12345678 / 0171 1234567 - length varies a lot, so this one is greedy
    "de": r"(?<![\d.])0[1-9]\d{1,4}[\s./-]?\d{3,9}(?![\d])",
    "at": r"(?<![\d.])0[1-9]\d{1,3}[\s./-]?\d{3,9}(?![\d])",
    # (555) 123-4567 / 555-123-4567
    "us": r"(?<![\d.])\(?[2-9]\d{2}\)?[\s.-]?[2-9]\d{2}[\s.-]?\d{4}(?![\d])",
}


def _phone_pseudo(n):
    return "phone%d" % n


# @creditcard / @iban - like @phone, a shape made only of digits is a minefield
# of false positives, so both gate on a checksum: a 16-digit id that is not a
# card fails Luhn, a random AT.. string fails mod-97, and neither is touched.
# Fixed markers, not pseudonyms: for a card or an account number you want it
# gone, never correlated.
CREDITCARD_RE = re.compile(r"(?<!\d)\d(?:[ -]?\d){12,18}(?!\d)")
IBAN_RE = re.compile(r"\b[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]){11,30}\b")


def _luhn_ok(digits):
    total, alt = 0, False
    for ch in reversed(digits):
        d = ord(ch) - 48
        if alt:
            d *= 2
            if d > 9:
                d -= 9
        total += d
        alt = not alt
    return total % 10 == 0


def _creditcard_repl(match):
    text = match.group(0)
    digits = re.sub(r"\D", "", text)
    if 13 <= len(digits) <= 19 and _luhn_ok(digits):
        return "[CARD]"
    return text  # a long id or a phone number, not a card


def _iban_ok(value):
    value = value.replace(" ", "").upper()
    if not 15 <= len(value) <= 34:
        return False
    rearranged = value[4:] + value[:4]
    digits = []
    for ch in rearranged:
        if ch.isdigit():
            digits.append(ch)
        elif "A" <= ch <= "Z":
            digits.append(str(ord(ch) - 55))  # A=10 .. Z=35
        else:
            return False
    return int("".join(digits)) % 97 == 1


def _iban_repl(match):
    text = match.group(0)
    return "[IBAN]" if _iban_ok(text) else text

# A JWT is header.payload.signature, and the header and payload both start with
# eyJ because they base64url-encode a JSON object ('{"').  Shared by @secret and
# the standalone @jwt builtin.
JWT_PATTERN = r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]+"

# @secret: credentials get a fixed marker, not a pseudonym - for a token you
# want it gone, you never want to correlate it.  Deliberately trigger-happy:
# over-redacting is cosmetic, missing a live key is not.
# (label, pattern, replacement, gates) - see Rule for what gates do.
SECRET_PATTERNS = [
    # --- vendor tokens: distinctive prefixes, so these are near zero-false-positive
    ("aws-key", r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b", "[SECRET]", ("akia", "asia")),
    ("github-token", r"\bgh[pousr]_[A-Za-z0-9]{20,}\b", "[SECRET]",
     ("ghp_", "gho_", "ghu_", "ghs_", "ghr_")),
    ("gitlab-token", r"\bglpat-[0-9A-Za-z_-]{20,}\b", "[SECRET]", ("glpat-",)),
    ("slack-token", r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b", "[SECRET]", ("xox",)),
    ("google-key", r"\bAIza[0-9A-Za-z_-]{35}\b", "[SECRET]", ("aiza",)),
    ("stripe-key", r"\b[sr]k_live_[0-9A-Za-z]{16,}\b", "[SECRET]",
     ("sk_live_", "rk_live_")),
    ("openai-key", r"\bsk-[A-Za-z0-9_-]{20,}\b", "[SECRET]", ("sk-",)),
    ("npm-token", r"\bnpm_[A-Za-z0-9]{30,}\b", "[SECRET]", ("npm_",)),
    ("github-fine", r"\bgithub_pat_[A-Za-z0-9_]{22,}\b", "[SECRET]", ("github_pat_",)),
    ("google-oauth", r"\bya29\.[A-Za-z0-9_-]{20,}", "[SECRET]", ("ya29.",)),
    ("sendgrid-key", r"\bSG\.[A-Za-z0-9_-]{22}\.[A-Za-z0-9_-]{43}\b", "[SECRET]",
     ("sg.",)),
    ("mailgun-key", r"\bkey-[0-9a-f]{32}\b", "[SECRET]", ("key-",)),
    ("digitalocean", r"\bdop_v1_[0-9a-f]{64}\b", "[SECRET]", ("dop_v1_",)),
    ("vault-token", r"\bhvs\.[A-Za-z0-9_-]{24,}", "[SECRET]", ("hvs.",)),
    ("shopify-token", r"\bshp(?:at|ca|pa|ss)_[0-9a-f]{32}\b", "[SECRET]", ("shp",)),
    ("square-token", r"\bsq0(?:atp|csp)-[0-9A-Za-z_-]{22,}", "[SECRET]", ("sq0",)),
    ("twilio-key", r"\bSK[0-9a-f]{32}\b", "[SECRET]", ("sk",)),
    ("jwt", JWT_PATTERN, "[SECRET]", ("eyj",)),

    # --- HTTP: what actually leaks in apache/nginx logs
    # A query string in an access log has no scheme, so @uri never sees it:
    #   "GET /admin?token=abc HTTP/1.1"  <- this is the common web-server leak.
    (
        "query-param",
        r"(?i)([?&](?:token|api[_-]?key|apikey|key|access[_-]?token|refresh[_-]?token"
        r"|auth|password|passwd|pwd|secret|sig|signature|session|sid|jwt|code|state)=)"
        r"[^&\s\"'<>]*",
        r"\1[SECRET]",
        ("=",),
    ),
    ("bearer", r"(?i)\b(bearer\s+)[A-Za-z0-9._~+/-]{16,}=*", r"\1[SECRET]", ("bearer",)),
    # base64 of user:password - as good as a plaintext credential
    ("basic-auth", r"(?i)\b(basic\s+)[A-Za-z0-9+/]{12,}={0,2}", r"\1[SECRET]",
     ("basic",)),
    ("cookie-header", r"(?i)^(\s*Cookie\s*:\s*).*$", r"\1[SECRET]", ("cookie",)),
    ("set-cookie", r"(?i)\b(Set-Cookie\s*:\s*[^=;\s]+=)[^;\s]+", r"\1[SECRET]",
     ("set-cookie",)),
    (
        "session-id",
        r"(?i)\b((?:PHPSESSID|JSESSIONID|ASP\.NET_SessionId|connect\.sid|sessionid)"
        r"\s*=\s*)[A-Za-z0-9%._-]{8,}",
        r"\1[SECRET]",
        ("phpsessid", "jsessionid", "asp.net_sessionid", "connect.sid", "sessionid"),
    ),

    # --- config files: /etc/shadow, .htpasswd, .env, connection strings
    # crypt(3) / bcrypt / argon2 hashes are crackable offline - treat as secret
    (
        "password-hash",
        r"\$(?:1|2[aby]?|5|6|y|argon2(?:i|d|id)?)\$[A-Za-z0-9./$=,+-]{10,}",
        "[SECRET]",
        ("$",),
    ),
    (
        "assignment",
        r"(?i)\b(password|passwd|pwd|secret|token|api[_-]?key|apikey|access[_-]?key"
        r"|private[_-]?key|auth[_-]?token|client[_-]?secret)(\s*[=:]\s*)"
        r"(\"[^\"]*\"|'[^']*'|\S+)",
        r"\1\2[SECRET]",
        ("password", "passwd", "pwd", "secret", "token", "api", "apikey",
         "access", "private", "auth", "client"),
    ),
    # SNMP is plaintext auth and 'private' is a real default community string
    (
        "snmp-community",
        r"(?i)\b((?:rocommunity|rwcommunity|community)\s+)\S+",
        r"\1[SECRET]",
        ("community",),
    ),
]

# the body of a key spans many lines, so it needs a block rule, not a line rule
SECRET_BLOCKS = [
    (
        "private-key",
        r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY( BLOCK)?-----",
        r"-----END [A-Z0-9 ]*PRIVATE KEY( BLOCK)?-----",
        "[SECRET: private key removed]",
        ("-----begin",),
    ),
]


# --------------------------------------------------------------------------
# --audit: "what did I forget?"
# --------------------------------------------------------------------------
#
# Every rule in this file only protects against what you thought of.  @secret is
# a net for known shapes; audit is a net for unknown ones.  It never redacts and
# never decides - it runs over the *output* and asks whether you meant to keep
# what survived.  Heuristic and chatty on purpose: a miss here is a leak, a false
# positive is one line of noise.
#
# (kind, regex, group-to-test).  The group matters: for a URL we must test the
# host, not the whole URL, or every https://host1/x we produced looks suspicious.
AUDIT_PATTERNS = [
    ("email", EMAIL_RE, 0),
    ("ipv4", IPV4_RE, 0),
    ("mac", MAC_RE, 0),
    ("url-host", URI_RE, 2),
    ("home-path", HOME_RE, 2),
    ("ssh-key", SSH_KEY_RE, 0),
    (
        "internal-host",
        re.compile(
            r"\b(?:[a-z0-9][a-z0-9-]*\.)+(?:local|internal|corp|lan|intranet"
            r"|home|test|localdomain)\b",
            re.I,
        ),
        0,
    ),
    (
        "domain",
        re.compile(
            r"\b(?:[a-z0-9][a-z0-9-]*\.)+"
            r"(?:com|ch|de|at|org|net|io|dev|ai|co|uk|fr|it|eu|info|biz)\b",
            re.I,
        ),
        0,
    ),
    ("long-digits", re.compile(r"(?<![\d.])\d{9,}(?![\d])"), 0),
    ("hex-blob", re.compile(r"\b[0-9a-fA-F]{32,}\b"), 0),
    ("base64-blob", re.compile(r"\b[A-Za-z0-9+/]{24,}={0,2}(?![A-Za-z0-9+/=])"), 0),
    ("key-marker", re.compile(r"-----BEGIN [A-Z0-9 ]+-----"), 0),
]

# --entropy: the shape patterns above are a net for KNOWN token shapes; this is
# the net for the ones with no shape at all - a random API key or password in a
# format nobody wrote a pattern for.  A run of token characters with high Shannon
# entropy (bits/char) is what a credential looks like and ordinary text does not.
# Threshold and length are deliberately conservative so camelCase identifiers and
# short hashes stay quiet; opt-in because it is the noisiest of the audit checks.
ENTROPY_RE = re.compile(r"(?<![A-Za-z0-9+/=_-])[A-Za-z0-9+/=_-]{20,}(?![A-Za-z0-9+/=_-])")
ENTROPY_MIN_BITS = 4.2


def _shannon(s):
    """Shannon entropy of s in bits per character (0 for a constant string)."""
    counts = {}
    for ch in s:
        counts[ch] = counts.get(ch, 0) + 1
    n = len(s)
    return -sum((c / n) * math.log2(c / n) for c in counts.values())


class Auditor:
    """Collects suspicious values that no rule touched."""

    def __init__(self, position, entropy=False):
        self.position = position
        self.entropy = entropy
        self.findings = {}  # kind -> {value: [count, first_seen]}

    def _note(self, kind, value):
        seen = self.findings.setdefault(kind, {})
        if value in seen:
            seen[value][0] += 1
        else:
            seen[value] = [1, str(self.position)]

    def scan(self, text):
        for kind, regex, group in AUDIT_PATTERNS:
            for match in regex.finditer(text):
                value = match.group(group)
                if value:
                    self._note(kind, value)
        if self.entropy:
            for match in ENTROPY_RE.finditer(text):
                value = match.group(0)
                if _shannon(value) >= ENTROPY_MIN_BITS:
                    self._note("high-entropy", value)

    def report(self, store, out, full=False):
        # Anything we produced ourselves is not a finding: our own placeholders
        # (10.0.0.1 looks exactly like an IP) and everything @keep spared.
        ignore = set(KEEP)
        for table in store.tables.values():
            ignore.update(table.values())

        kinds = []
        for kind, values in self.findings.items():
            live = {v: c for v, c in values.items() if v not in ignore}
            if live:
                kinds.append((kind, live))
        if not kinds:
            out.write("redactor: audit found nothing unusual\n")
            return 0

        # -A caps each category at its five loudest values and clips anything
        # over 60 chars: enough to notice a leak, short enough to skim.  -AA
        # (full) lifts both caps and prints every finding at full length - the
        # mode you switch to once -A has told you there is something worth the
        # whole list.
        total = sum(len(v) for _, v in kinds)
        out.write(
            "redactor: audit - %d value(s) no rule touched, in %d categor(ies)\n"
            % (total, len(kinds))
        )
        for kind, values in sorted(kinds, key=lambda kv: -len(kv[1])):
            hits = sum(c for c, _ in values.values())
            out.write("\n  %s  (%d distinct, %d hit(s))\n" % (kind, len(values), hits))
            ranked = sorted(values.items(), key=lambda kv: -kv[1][0])
            for value, (count, where) in (ranked if full else ranked[:5]):
                shown = value if (full or len(value) <= 60) else value[:57] + "..."
                out.write("    %-62s %dx  first at %s\n" % (shown, count, where))
            if not full and len(ranked) > 5:
                out.write("    ... and %d more\n" % (len(ranked) - 5))
        out.write(
            "\n  Not necessarily leaks - audit only asks whether you meant to keep\n"
            "  these. Add a rule for the real ones, @keep the intentional ones.\n"
        )
        return total


# --------------------------------------------------------------------------
# rules
# --------------------------------------------------------------------------


class Rule:
    """A single-line search/replace.

    `gates` is a tuple of lowercase literals of which at least one MUST be
    present for the pattern to have any chance of matching (@uri cannot match
    without "://").  The Processor skips the rule when none of them appear,
    which turns a regex scan plus a string allocation into one `in` test.  That
    is what makes multi-GB logs bearable: on a typical line almost every rule is
    gated out.  A gate must never be narrower than the pattern - a wrong gate is
    a silent leak, so when in doubt leave it None."""

    __slots__ = (
        "label", "regex", "repl", "count", "source", "self_counting", "gates",
    )

    def __init__(self, label, regex, repl, source, gates=None):
        self.label = label
        self.regex = regex
        self.repl = repl  # str (may use \1 backrefs) or callable(match)->str
        self.count = 0
        self.source = source
        self.gates = gates
        # in --ask mode the wrapper counts accepted replacements itself, since
        # subn() also counts the ones the user declined (replaced by themselves)
        self.self_counting = False

    def apply(self, text):
        text, n = self.regex.subn(self.repl, text)
        if not self.self_counting:
            self.count += n
        return text


class BlockRule:
    """A multi-line match: everything from a start to an end marker.

    Kept line-based on purpose - slurping the whole input to run a DOTALL regex
    would break streaming for large logs."""

    __slots__ = (
        "label", "start_re", "end_re", "repl", "count", "source", "blank", "gates",
    )

    def __init__(self, label, start_re, end_re, repl, source, gates=None):
        self.label = label
        self.start_re = start_re
        self.end_re = end_re
        self.repl = repl
        self.count = 0
        self.source = source
        self.blank = False
        self.gates = gates  # gates the START marker only; see Processor.feed


# --------------------------------------------------------------------------
# --ask: confirm every replacement on the terminal
# --------------------------------------------------------------------------


class Position:
    """Where we currently are, for the --ask prompt."""

    __slots__ = ("path", "lineno")

    def __init__(self):
        self.path = None
        self.lineno = 0

    def __str__(self):
        if self.path:
            return "%s:%d" % (self.path, self.lineno)
        return "line %d" % self.lineno


class Asker:
    """Ask y/n/a for each replacement, reading from the controlling terminal.

    stdin is usually the data being filtered and stdout the result, so the
    dialog goes to /dev/tty instead of either of them."""

    def __init__(self, position):
        self.position = position
        self.yes_to_all = False
        try:
            self._tty = open("/dev/tty", "r+")
        except OSError as exc:
            raise SystemExit("redactor: -a/--ask needs a terminal (/dev/tty): %s" % exc)

    def _prompt(self, label, before, after, detail):
        if self.yes_to_all:
            return True

        sys.stdout.flush()  # keep already-emitted output ahead of the prompt
        write = self._tty.write
        write("\n--- %s   [%s]\n" % (self.position, label))
        for line in before.split("\n"):
            write("  - %s\n" % line)
        for line in after.split("\n"):
            write("  + %s\n" % line)
        if detail:
            write("  %s\n" % detail)

        while True:
            write("  replace? [y/n/a] ")
            self._tty.flush()
            answer = self._tty.readline()
            if not answer:  # EOF on the terminal: stop asking, keep redacting
                self.yes_to_all = True
                return True
            answer = answer.strip().lower()
            if answer in ("", "y", "yes"):
                return True
            if answer in ("n", "no"):
                return False
            if answer in ("a", "all"):
                self.yes_to_all = True
                return True
            write("  answer y (yes), n (no) or a (yes to all remaining)\n")

    def confirm(self, rule, match, new):
        line = match.string
        before = line.rstrip("\n")
        after = (line[: match.start()] + new + line[match.end():]).rstrip("\n")
        return self._prompt(
            rule.label, before, after, "%r  ->  %r" % (match.group(0), new)
        )

    def confirm_block(self, block, buffered, replacement):
        head = buffered[0].rstrip("\n")
        before = head if len(buffered) == 1 else "%s\n  ... (%d more lines)" % (
            head, len(buffered) - 1
        )
        return self._prompt(
            block.label, before, replacement, "%d line block" % len(buffered)
        )


# --------------------------------------------------------------------------
# rule wrappers (-b, --keep-length, -a)
# --------------------------------------------------------------------------


def wrap_for_blank(rule):
    """Replace every match with as many spaces as it was long.

    Same length, not an empty string: that keeps columns in logs and the
    indentation in code lined up."""
    if isinstance(rule, BlockRule):
        rule.blank = True
    else:
        rule.repl = lambda match: " " * len(match.group(0))


def wrap_for_keep_length(rule):
    """Pad a replacement with spaces so it is at least as long as the original."""
    if isinstance(rule, BlockRule):
        return
    original = rule.repl

    def repl(match):
        new = original(match) if callable(original) else match.expand(original)
        missing = len(match.group(0)) - len(new)
        return new + " " * missing if missing > 0 else new

    rule.repl = repl


def wrap_for_ask(rule, asker):
    """Route a rule's replacement through the y/n/a prompt."""
    if isinstance(rule, BlockRule):
        return  # blocks are asked in the Processor, where the extent is known
    original = rule.repl

    def repl(match):
        # str replacements may carry \1 backrefs; expand them for the preview
        new = original(match) if callable(original) else match.expand(original)
        if asker.confirm(rule, match, new):
            rule.count += 1
            return new
        return match.group(0)

    rule.repl = repl
    rule.self_counting = True


# --------------------------------------------------------------------------
# building rules
# --------------------------------------------------------------------------


def _bounded(pattern):
    """Wrap a pattern so it only matches as a whole word.

    Deliberately not \\b...\\b: a \\b next to a non-word character asserts that
    the *neighbour* is a word character, so \\b/etc/\\b never matches in
    " /etc/passwd" - it fails silently.  Lookarounds say what we actually mean,
    "no word character may touch this", and behave for any pattern."""
    return r"(?<!\w)(?:%s)(?!\w)" % pattern


def _regex_token(token, what):
    if len(token) >= 2 and token.startswith("/") and token.endswith("/"):
        try:
            return re.compile(token[1:-1])
        except re.error as exc:
            raise ValueError("bad regex %s: %s" % (token, exc))
    raise ValueError("%s must be a /regex/, got %r" % (what, token))


def _split_ident_args(args, what):
    """Separate plain names from /regex/ patterns, validating the latter."""
    literals, patterns = [], []
    for arg in args:
        if len(arg) >= 2 and arg.startswith("/") and arg.endswith("/"):
            body = arg[1:-1]
            try:
                re.compile(body)
            except re.error as exc:
                raise ValueError("%s: bad regex %s: %s" % (what, arg, exc))
            patterns.append(body)
        elif arg:
            literals.append(arg)
    return literals, patterns


def _ident_rule(label, category, values, fmt, source):
    """Word-bounded matcher over `values`, each mapped stably via fmt.

    `values` may mix plain names with /regex/ patterns:

        @hostname web01 db01 /srv[0-9]{3}/

    Patterns exist for fleets with a naming scheme, where listing every machine
    means the next new one leaks.  A plain regex rule could match them too, but
    it would hand every host the same fixed replacement - here they keep going
    through the mapper, so host1/host2/host3 stay stable and you can still see
    which lines concern the same machine.

    Literals and patterns become TWO rules sharing ONE mapper.  They cannot be
    merged: a literal is its own gate (see Rule), while a regex has no character
    that must be present, so it cannot be gated at all.  Merging them would mean
    either dropping the gate for the literals - slow - or inventing a gate for
    the pattern - a silent leak.  Same category, so the pseudonyms agree either
    way."""
    literals, patterns = _split_ident_args(values, label)
    if not literals and not patterns:
        return []

    mapper = STORE.mapper(category, fmt)
    rules = []

    if literals:
        literals = list(dict.fromkeys(literals))
        # longest first so fqdn wins over short host, "robert" over "rob", etc.
        literals.sort(key=len, reverse=True)
        rules.append(Rule(
            label,
            re.compile(_bounded("|".join(re.escape(v) for v in literals))),
            mapper,
            source,
            tuple(v.lower() for v in literals),  # the values are their own gate
        ))

    if patterns:
        patterns = list(dict.fromkeys(patterns))
        rules.append(Rule(
            "%s %s" % (label, " ".join("/%s/" % p for p in patterns)),
            re.compile(_bounded("|".join("(?:%s)" % p for p in patterns))),
            mapper,
            source,
            None,  # deliberately ungated; see the docstring
        ))

    return rules


def _uri_repl(mapper):
    """Keep scheme, port and path; pseudonymize the host; drop the rest.

    userinfo and query are where credentials and tokens live, so they go.  The
    path usually carries the information you kept the log for, so it stays."""

    def repl(match):
        scheme, host, port, path = match.group(1, 2, 3, 4)
        if host in KEEP:
            # a kept host means "this URL is public": leave it fully alone,
            # query included, or every github.com link in a README gets mangled
            return match.group(0)
        out = "%s://%s" % (scheme, mapper.value(host))
        if port:
            out += ":" + port
        return out + (path or "")

    return repl


def _home_repl(mapper):
    """Rewrite the user part of /home/<user>/... via the shared user mapping."""

    def repl(match):
        return match.group(1) + mapper.value(match.group(2))

    return repl


def _phone_repl(mapper):
    """Validate the digit count, then map on the normalized number.

    Normalizing first means "+41 79 123 45 67" and "+41791234567" - the same
    number written two ways - land on the same pseudonym instead of two."""

    def repl(match):
        text = match.group(0)
        digits = re.sub(r"\D", "", text)
        if not 8 <= len(digits) <= 15:  # E.164 allows at most 15
            return text  # a timestamp or an id, not a phone number
        return mapper.value("+" + digits)

    return repl


def _field_rule(keys, source):
    """@field KEY... - redact a named field's VALUE, whatever shape it has.

    Where @secret matches by the value's shape (AKIA.., eyJ..), @field matches by
    the key, for the sensitive values that have no shape at all - a password,
    a session token, an X-Api-Key header.  The assignment rule inside @secret
    does this too, but only for a fixed built-in key list; here the keys are
    yours.  Each key becomes three rules, one per syntax, so the same @field
    authorization catches all of:

        {"authorization": "Bearer x"}         JSON
        authorization=Bearer%20x              logfmt / query string
        Authorization: Bearer eyJ...          HTTP header / YAML

    Case-insensitive on the key.  One-way: the value is replaced with a fixed
    marker, not a pseudonym, so --unredact does not bring it back."""
    mark = DEFAULT_REPLACEMENT
    rules = []
    for key in keys:
        k = re.escape(key)
        gate = (key.lower(),)
        # JSON scalar: "key": "v" | 123 | true | null  ->  "key": "[REDACTED]"
        rules.append(Rule(
            "@field %s (json)" % key,
            re.compile(
                r'("%s"\s*:\s*)(?:"[^"]*"|-?\d[\d.eE+-]*|true|false|null)' % k, re.I
            ),
            r'\1"%s"' % mark,
            source, gate,
        ))
        # logfmt / query string: key=value | key="value"  ->  key=[REDACTED]
        rules.append(Rule(
            "@field %s (kv)" % key,
            re.compile(r'((?<!\w)%s\s*=\s*)(?:"[^"]*"|[^\s,;&]+)' % k, re.I),
            r"\1%s" % mark,
            source, gate,
        ))
        # header / yaml: key: value-to-end-of-line  ->  key: [REDACTED]
        # value may hold spaces ("Bearer x"), so it runs to a comma or the line
        # end; the quoted alternative keeps a JSON "key": from over-reaching.
        rules.append(Rule(
            "@field %s (colon)" % key,
            re.compile(r'((?<!\w)%s\s*:\s*)(?:"[^"]*"|[^\n,]+)' % k, re.I),
            r"\1%s" % mark,
            source, gate,
        ))
    return rules


def _default_hostnames():
    names = []
    try:
        names.append(socket.getfqdn())
    except Exception:
        pass
    try:
        h = socket.gethostname()
        names.append(h)
        names.append(h.split(".")[0])
    except Exception:
        pass
    return names


def _default_users():
    try:
        return [getpass.getuser()]
    except Exception:
        return []


def _builtin(name, args, source):
    """Return the Rule/BlockRule objects for an @builtin."""
    if name == "word":
        if not args:
            raise ValueError("@word needs a search text: @word <search> [replacement]")
        search = args[0]
        repl = args[1] if len(args) > 1 else DEFAULT_REPLACEMENT
        rx = re.compile(_bounded(re.escape(search)))
        return [
            Rule("@word %s" % search, rx, (lambda m, r=repl: r), source,
                 (search.lower(),))
        ]

    if name == "path":
        if args:
            # a literal prefix.  This is the whole point of @path: written as a
            # bare rule, "/home/alice/myproject/ X" would be read as a /regex/.
            search = args[0]
            repl = args[1] if len(args) > 1 else DEFAULT_REPLACEMENT
            rx = re.compile(re.escape(search))
            return [
                Rule("@path %s" % search, rx, (lambda m, r=repl: r), source,
                     (search.lower(),))
            ]
        mapper = STORE.mapper("user", _user_pseudo)  # shared with @user
        return [
            Rule("@path (home dirs)", HOME_RE, _home_repl(mapper), source,
                 ("/home/", "/users/", "/var/home/"))
        ]

    if name in ("uri", "url"):
        mapper = STORE.mapper("hostname", _host_pseudo)  # shared with @hostname
        return [Rule("@uri", URI_RE, _uri_repl(mapper), source, ("://",))]

    if name in ("ip", "ipv6"):
        rules = []
        if name == "ip":
            rules.append(Rule("@ip", IPV4_RE, STORE.mapper("ip", _ip4_pseudo), source))
        rules.append(Rule("@ipv6", IPV6_RE, STORE.mapper("ipv6", _ip6_pseudo), source))
        return rules

    if name == "email":
        return [
            Rule("@email", EMAIL_RE, STORE.mapper("email", _email_pseudo), source,
                 ("@",))
        ]

    if name == "mac":
        return [Rule("@mac", MAC_RE, STORE.mapper("mac", _mac_pseudo), source)]

    if name == "sshkey":
        # ":" covers SHA256: and the legacy aa:bb: form; "ssh-"/"ecdsa-" cover the
        # public-key forms, which carry no colon at all
        return [
            Rule("@sshkey", SSH_KEY_RE, STORE.mapper("sshkey", _sshkey_pseudo),
                 source, (":", "ssh-", "ecdsa-"))
        ]

    if name == "keep":
        if not args:
            raise ValueError("@keep needs values: @keep 127.0.0.1 localhost github.com")
        KEEP.update(args)
        return []  # registers values only, it is not a rule of its own

    if name == "phone":
        mapper = STORE.mapper("phone", _phone_pseudo)
        # the international form always carries a + or 00 - that is exactly the
        # signal the detector is built on, so it doubles as a good gate
        rules = [
            Rule("@phone", PHONE_INTL_RE, _phone_repl(mapper), source, ("+", "00"))
        ]
        for country in args:
            key = country.lower()
            if key not in PHONE_NATIONAL:
                raise ValueError(
                    "@phone: unknown country %r, known: %s"
                    % (country, " ".join(sorted(PHONE_NATIONAL)))
                )
            rules.append(
                Rule(
                    "@phone %s" % key,
                    re.compile(PHONE_NATIONAL[key]),
                    _phone_repl(mapper),  # same mapper: one number, one pseudonym
                    source,
                )
            )
        return rules

    if name == "hostname":
        values = args or _default_hostnames()
        return _ident_rule("@hostname", "hostname", values, _host_pseudo, source)

    if name == "user":
        values = args or _default_users()
        return _ident_rule("@user", "user", values, _user_pseudo, source)

    if name == "secret":
        rules = [
            Rule("@secret %s" % lbl, re.compile(pat), repl, source, gates)
            for (lbl, pat, repl, gates) in SECRET_PATTERNS
        ]
        rules += [
            BlockRule(
                "@secret %s" % lbl, re.compile(start), re.compile(end), repl,
                source, gates,
            )
            for (lbl, start, end, repl, gates) in SECRET_BLOCKS
        ]
        return rules

    if name == "jwt":
        return [Rule("@jwt", re.compile(JWT_PATTERN), "[SECRET]", source, ("eyj",))]

    if name == "field":
        if not args:
            raise ValueError(
                "@field needs key names: @field password authorization api_key"
            )
        return _field_rule(args, source)

    if name == "creditcard":
        return [Rule("@creditcard", CREDITCARD_RE, _creditcard_repl, source)]

    if name == "iban":
        return [Rule("@iban", IBAN_RE, _iban_repl, source)]

    if name == "block":
        if len(args) < 2:
            raise ValueError("@block needs /start/ /end/ [replacement]")
        start = _regex_token(args[0], "@block start")
        end = _regex_token(args[1], "@block end")
        repl = args[2] if len(args) > 2 else DEFAULT_REPLACEMENT
        label = "@block %s" % args[0]
        return [BlockRule(label, start, end, repl, source)]

    return []


def tokenize(line):
    """Split on whitespace, honouring '...' and \"...\" quotes.

    Backslashes are left untouched (so regex replacements keep their \\1
    backreferences and paths survive)."""
    tokens = []
    i, n = 0, len(line)
    while i < n:
        while i < n and line[i].isspace():
            i += 1
        if i >= n:
            break
        if line[i] in ("'", '"'):
            quote = line[i]
            i += 1
            start = i
            while i < n and line[i] != quote:
                i += 1
            tokens.append(line[start:i])
            i += 1  # skip closing quote
        else:
            start = i
            while i < n and not line[i].isspace():
                i += 1
            tokens.append(line[start:i])
    return tokens


def parse_rule(line, source):
    """Parse one rule line into a list of Rule/BlockRule objects (0..n)."""
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return []
    tokens = tokenize(stripped)
    if not tokens:
        return []
    head = tokens[0]

    # @builtin
    if head.startswith("@"):
        name = head[1:].lower()
        if name in BUILTINS:
            return _builtin(name, tokens[1:], source)
        # unknown @directive: fall through and treat it as a literal search

    # /regex/  replacement
    if len(head) >= 2 and head.startswith("/") and head.endswith("/"):
        pattern = head[1:-1]
        repl = tokens[1] if len(tokens) > 1 else DEFAULT_REPLACEMENT
        try:
            rx = re.compile(pattern)
        except re.error as exc:
            raise ValueError("bad regex %r: %s" % (pattern, exc))
        return [Rule("/%s/" % pattern, rx, repl, source)]

    # literal  replacement   (plain substring, replacement taken verbatim)
    search = head
    repl = tokens[1] if len(tokens) > 1 else DEFAULT_REPLACEMENT
    rx = re.compile(re.escape(search))
    # a literal is its own gate, which is what makes a config with hundreds of
    # names cheap: each one costs an `in` test, not a regex scan
    return [Rule(search, rx, (lambda m, r=repl: r), source, (search.lower(),))]


def load_rules_from_file(path):
    rules = []
    with open(path, "r", encoding="utf-8", errors="surrogateescape") as fh:
        for lineno, line in enumerate(fh, 1):
            try:
                rules.extend(parse_rule(line, "%s:%d" % (path, lineno)))
            except ValueError as exc:
                sys.stderr.write("redactor: %s:%d: %s\n" % (path, lineno, exc))
    return rules


def _walk_up(basename):
    directory = os.getcwd()
    while True:
        candidate = os.path.join(directory, basename)
        if os.path.isfile(candidate):
            return candidate
        parent = os.path.dirname(directory)
        if parent == directory:
            return None
        directory = parent


def find_config_files():
    paths = ["/etc/redactor.conf"]
    xdg = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    paths.append(os.path.join(xdg, "redactor.conf"))
    local = _walk_up(CONFIG_BASENAME)
    if local:
        paths.append(local)
    return [p for p in paths if os.path.isfile(p)]


def find_map_file():
    """Auto-discovery only ever picks up an existing .redactor.map.

    Creating one is left to an explicit --map, so a stray run never starts
    silently persisting a mapping you did not ask for."""
    return _walk_up(MAP_BASENAME)


# --------------------------------------------------------------------------
# -r/--recursive: expand directory arguments into their files
# --------------------------------------------------------------------------
#
# -r is a pure file-expansion step: it turns a directory into the list of text
# files under it and then hands off to the exact same -i/-d/-c/-A code paths a
# list of files would take.  Everything here is about NOT touching the wrong
# file - a redaction sweep that rewrites .git internals or your .redactor.map
# is worse than one that skips a file it should have caught.

# Version-control metadata is never redactable text; rewriting it corrupts the
# repository.  Pruned from the walk, always.
_SKIP_DIRS = frozenset((".git", ".hg", ".svn", ".bzr", "CVS", "_darcs"))


def _is_redactor_file(name):
    """redactor's own files, which a tree sweep must never rewrite: the config
    holds your raw secrets by design, the .map is the un-redaction key, and a
    *.redactor.tmp is a half-written output from an interrupted run."""
    return (
        name == CONFIG_BASENAME
        or name.endswith(MAP_BASENAME)
        or name.endswith(".redactor.tmp")
    )


def _looks_binary(path):
    """A NUL byte in the first 8 kB means "not text".  Real UTF-8 / Latin-1
    text never contains NUL, so this never skips a file it should redact; it
    only spares images, binaries and the like from being mangled."""
    try:
        with open(path, "rb") as fh:
            return b"\x00" in fh.read(8192)
    except OSError:
        return True  # unreadable: skip it, same as binary


def _matches_glob(relpath, name, globs):
    # Match against the path-relative form (tests/x.py) OR the bare name (x.py),
    # so both --exclude 'tests/*' and --exclude '*.py' do what you expect.
    for pat in globs:
        if fnmatch.fnmatch(relpath, pat) or fnmatch.fnmatch(name, pat):
            return True
    return False


def walk_files(paths, recursive, exclude, include, skipped):
    """Expand command-line paths into the list of files to process.

    A named file is passed through untouched (only --exclude/--include filter
    it - an explicitly named file is the user's choice, so it is never dropped
    for being binary or a symlink).  A directory is walked only with -r; without
    it a directory is an error, like grep.  The walk skips VCS metadata,
    symlinks, binary files and redactor's own .redactor/.redactor.map, and
    honours --exclude/--include.  `skipped` is a counter dict the caller reads
    for its summary."""
    exclude = exclude or []
    include = include or []
    result = []
    for path in paths:
        if os.path.isdir(path):
            if not recursive:
                raise IsADirectoryError(
                    "%s is a directory (use -r to recurse into it)" % path
                )
            for root, dirs, files in os.walk(path):
                # prune in place so os.walk does not descend into them
                dirs[:] = sorted(
                    d for d in dirs
                    if d not in _SKIP_DIRS
                    and not _matches_glob(
                        os.path.relpath(os.path.join(root, d), path), d, exclude)
                )
                for name in sorted(files):
                    full = os.path.join(root, name)
                    rel = os.path.relpath(full, path)
                    if _is_redactor_file(name):
                        skipped["redactor"] += 1
                    elif _matches_glob(rel, name, exclude):
                        skipped["excluded"] += 1
                    elif include and not _matches_glob(rel, name, include):
                        pass  # not matched by --include: simply not ours
                    elif os.path.islink(full):
                        skipped["symlink"] += 1
                    elif _looks_binary(full):
                        skipped["binary"] += 1
                    else:
                        result.append(full)
        else:
            name = os.path.basename(path)
            if _matches_glob(path, name, exclude):
                skipped["excluded"] += 1
            elif include and not _matches_glob(path, name, include):
                pass
            else:
                result.append(path)
    return result


def profile_dirs():
    """Where --profile looks, most specific first.

    The last entry is the profiles/ dir next to the script, so a git checkout
    and the Homebrew install (script in libexec, profiles beside it) both work
    with no system-wide install; realpath so a symlink into ~/bin still finds
    it."""
    xdg = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    here = os.path.dirname(os.path.realpath(__file__))
    return [
        os.path.join(xdg, "redactor", "profiles"),
        "/usr/share/redactor/profiles",
        os.path.join(here, "profiles"),
    ]


def list_profiles():
    found = {}
    for directory in profile_dirs():
        if not os.path.isdir(directory):
            continue
        try:
            names = os.listdir(directory)
        except OSError:
            continue
        for name in names:
            if name.endswith(".conf"):
                found.setdefault(name[:-5], os.path.join(directory, name))
    return found


def resolve_profile(name):
    for directory in profile_dirs():
        candidate = os.path.join(directory, name + ".conf")
        if os.path.isfile(candidate):
            return candidate
    available = sorted(list_profiles())
    raise SystemExit(
        "redactor: unknown profile %r\n  available: %s\n  looked in: %s"
        % (
            name,
            ", ".join(available) if available else "(none found)",
            ", ".join(profile_dirs()),
        )
    )


def build_rules(args):
    rules = []
    if not args.no_config:
        for path in find_config_files():
            rules.extend(load_rules_from_file(path))
    for name in args.profile or []:
        # before -f, so your own rule files can still override a profile
        rules.extend(load_rules_from_file(resolve_profile(name)))
    for path in args.file or []:
        if not os.path.isfile(path):
            sys.stderr.write("redactor: rule file not found: %s\n" % path)
            continue
        rules.extend(load_rules_from_file(path))
    for expr in args.expr or []:
        try:
            rules.extend(parse_rule(expr, "-e"))
        except ValueError as exc:
            sys.stderr.write("redactor: -e %r: %s\n" % (expr, exc))
    return rules


def build_unredact_rules(store):
    """Turn the map around: placeholder -> original value.

    @secret has no entry here by design - it writes a fixed [SECRET] marker
    instead of a pseudonym, so it is deliberately one-way."""
    rules = []
    for category, table in sorted(store.tables.items()):
        reverse = {placeholder: original for original, placeholder in table.items()}
        if not reverse:
            continue
        # longest first, so host10 is tried before host1
        keys = sorted(reverse, key=len, reverse=True)
        pattern = _bounded("|".join(re.escape(k) for k in keys))
        rules.append(
            Rule(
                "unredact %s (%d)" % (category, len(reverse)),
                re.compile(pattern),
                (lambda m, r=reverse: r[m.group(0)]),
                store.path,
            )
        )
    return rules


# --------------------------------------------------------------------------
# processing
# --------------------------------------------------------------------------


class Processor:
    """Applies block rules (stateful, across lines) and line rules (per line)."""

    def __init__(self, rules, blocks, asker=None):
        self.rules = rules
        self.blocks = blocks
        self.asker = asker
        self.active = None
        self.buffer = []
        self.gated = any(r.gates for r in rules) or any(r.gates for r in blocks)

    def reset(self):
        self.active = None
        self.buffer = []

    def feed(self, line, emit):
        if self.active is not None:
            self.buffer.append(line)
            if self.active.end_re.search(line):
                self._close(emit)
            return

        # one allocation per line, in exchange for skipping most rules below
        lowered = line.lower() if self.gated else None

        for block in self.blocks:
            if block.gates and not self._passes(block.gates, lowered):
                continue
            found = block.start_re.search(line)
            if not found:
                continue
            self.active = block
            self.buffer = [line]
            if block.end_re.search(line, found.end()):  # start and end same line
                self._close(emit)
            return

        for rule in self.rules:
            if rule.gates and not self._passes(rule.gates, lowered):
                continue
            new = rule.apply(line)
            if new != line:
                # a rule can *create* text a later gate looks for, so the gate
                # must see the current line, not the one we started with
                line = new
                if self.gated:
                    lowered = line.lower()
        emit(line)

    @staticmethod
    def _passes(gates, lowered):
        for gate in gates:
            if gate in lowered:
                return True
        return False

    def finish(self, emit):
        if self.active is not None:
            # EOF inside a block: keep it redacted rather than leak the tail
            self._close(emit)

    def _close(self, emit):
        block, buffered = self.active, self.buffer
        self.active, self.buffer = None, []

        if block.blank:
            block.count += 1
            emit("".join(_blank_line(line) for line in buffered))
            return

        newline = "\n" if buffered[-1].endswith("\n") else ""
        if self.asker and not self.asker.confirm_block(block, buffered, block.repl):
            emit("".join(buffered))
            return
        block.count += 1
        emit(block.repl + newline)


def _blank_line(line):
    stripped = line.rstrip("\n")
    return " " * len(stripped) + line[len(stripped):]


# --------------------------------------------------------------------------
# colored --diff
#
# American spelling throughout, to match the --color flag and want_color().
# --colour is accepted on the command line as an alias, and nowhere else.
# --------------------------------------------------------------------------

_RED = "\033[31m"
_GREEN = "\033[32m"
_CYAN = "\033[36m"
_BOLD = "\033[1m"
_INVERT = "\033[7m"
_INVERT_OFF = "\033[27m"  # inverse off only, so the line's red/green survives
_RESET = "\033[0m"


def want_color(choice, stream):
    """auto = only when a human is actually looking at a terminal."""
    if choice == "always":
        return True
    if choice == "never":
        return False
    if os.environ.get("NO_COLOR"):  # no-color.org: set and non-empty
        return False
    if os.environ.get("TERM") == "dumb":
        return False
    try:
        return stream.isatty()
    except (AttributeError, ValueError):
        return False


def _paint(color, line):
    """Color a line, keeping the reset BEFORE the newline.

    Resetting after the newline makes terminals paint the rest of the row,
    which is very visible with inverse video."""
    body = line.rstrip("\n")
    return color + body + _RESET + line[len(body):]


def _mark_spans(old, new):
    """Invert only the characters that actually differ between two lines.

    autojunk=False matters: SequenceMatcher's heuristic writes off 'popular'
    elements on sequences longer than 200 characters, and a log line is mostly
    spaces. With it on, long lines get nonsense spans."""
    matcher = difflib.SequenceMatcher(None, old, new, autojunk=False)
    o, n = [], []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            o.append(old[i1:i2])
            n.append(new[j1:j2])
            continue
        if i2 > i1:
            o.append(_INVERT + old[i1:i2] + _INVERT_OFF)
        if j2 > j1:
            n.append(_INVERT + new[j1:j2] + _INVERT_OFF)
    return "".join(o), "".join(n)


def _emit_change(minus, plus, write):
    if len(minus) != len(plus):
        # No 1:1 pairing exists - a @block collapsing five lines into one gets
        # here. Nothing sensible to align, so plain line colors.
        for line in minus:
            write(_paint(_RED, line))
        for line in plus:
            write(_paint(_GREEN, line))
        return

    pairs = []
    for old, new in zip(minus, plus):
        old_body, new_body = old.rstrip("\n"), new.rstrip("\n")
        o, n = _mark_spans(old_body[1:], new_body[1:])  # drop the -/+ prefix
        pairs.append((
            _RED + "-" + o + _RESET + old[len(old_body):],
            _GREEN + "+" + n + _RESET + new[len(new_body):],
        ))
    # all minus, then all plus: keep the output a structurally valid unified
    # diff rather than interleaving the pairs
    for old, _ in pairs:
        write(old)
    for _, new in pairs:
        write(new)


def emit_diff(original, out, name, write, color):
    """Unified diff, optionally with the changed span within a line inverted.

    Whole-line red/green is nearly useless on a log: in a 200-character line
    where one IP moved, you can see that something changed but not what. So
    equal-length -/+ runs are re-diffed per character and only the parts that
    really differ are marked."""
    diff = list(difflib.unified_diff(
        original, out, fromfile=name, tofile=name + " (redacted)"
    ))
    if not color:
        write("".join(diff))
        return

    i, n = 0, len(diff)
    while i < n:
        line = diff[i]
        # unified_diff always emits ---/+++ first, so this is positional: a
        # content line may legitimately start with --- too
        if i < 2 and (line.startswith("---") or line.startswith("+++")):
            write(_paint(_BOLD, line))
            i += 1
        elif line.startswith("@@"):
            write(_paint(_CYAN, line))
            i += 1
        elif line.startswith("-"):
            minus = []
            while i < n and diff[i].startswith("-"):
                minus.append(diff[i])
                i += 1
            plus = []
            while i < n and diff[i].startswith("+"):
                plus.append(diff[i])
                i += 1
            _emit_change(minus, plus, write)
        elif line.startswith("+"):
            write(_paint(_GREEN, line))
            i += 1
        else:
            write(line)  # context
            i += 1


# --------------------------------------------------------------------------


def _reset_state():
    """Drop everything main() accumulates in module globals.

    Irrelevant to the CLI (one process, one run) but the test suite calls main()
    dozens of times in the same interpreter, and without this the @keep values
    and the pseudonym counters of one case would bleed into the next."""
    KEEP.clear()
    STORE.reset()


def main(argv=None):
    _reset_state()
    parser = argparse.ArgumentParser(
        prog="redactor",
        description="Anonymize texts, logs, READMEs and code.",
    )
    parser.add_argument("files", nargs="*", help="input files (default: stdin)")
    parser.add_argument(
        "-e", "--expr", action="append", metavar="RULE",
        help="add a rule, same syntax as a config line (repeatable)",
    )
    parser.add_argument(
        "-f", "--file", action="append", metavar="PATH",
        help="load an additional rule file (repeatable)",
    )
    parser.add_argument(
        "-p", "--profile", action="append", metavar="NAME",
        help="load a ready-made rule set, e.g. webserver, sshd, dhcpd, ftp, "
             "mail, configfiles (repeatable; see --list-profiles)",
    )
    parser.add_argument(
        "--list-profiles", action="store_true",
        help="print the available profiles and where they came from, then exit",
    )
    parser.add_argument(
        "-n", "--no-config", action="store_true",
        help="ignore /etc, ~/.config and .redactor; use only -e/-f rules",
    )
    parser.add_argument(
        "-a", "--ask", action="store_true",
        help="confirm every replacement on the terminal: y=yes, n=no, a=all remaining",
    )
    parser.add_argument(
        "-b", "--blank", action="store_true",
        help="replace every match with as many spaces as it was long, "
             "ignoring the configured replacements (keeps columns aligned)",
    )
    parser.add_argument(
        "-k", "--keep-length", action="store_true",
        help="pad replacements with spaces to the length of the original, "
             "so columns stay aligned",
    )
    parser.add_argument(
        "-i", "--in-place", action="store_true",
        help="rewrite the given files instead of writing to stdout",
    )
    parser.add_argument(
        "-r", "--recursive", action="store_true",
        help="descend into directory arguments and process every text file "
             "found (skips .git, symlinks and binary files). Combine with -i to "
             "rewrite a whole tree, or -d/-c/-A to preview it first",
    )
    parser.add_argument(
        "--exclude", metavar="GLOB", action="append",
        help="skip files or directories matching GLOB while recursing "
             "(matched against the name or the relative path; repeatable)",
    )
    parser.add_argument(
        "--include", metavar="GLOB", action="append",
        help="while recursing, process only files matching GLOB "
             "(repeatable; --exclude still wins over --include)",
    )
    parser.add_argument(
        "-m", "--map", metavar="PATH",
        help="keep the value->placeholder mapping in PATH, so the same host/ip/user "
             "gets the same placeholder across runs and files (created if missing; "
             "an existing .redactor.map is picked up automatically)",
    )
    parser.add_argument(
        "-u", "--unredact", action="store_true",
        help="apply the mapping backwards, turning placeholders back into the "
             "original values (needs a map; @secret is one-way and stays)",
    )
    parser.add_argument(
        "-d", "--diff", action="store_true",
        help="show a unified diff of what would change, write no output",
    )
    parser.add_argument(
        "--color", "--colour", choices=("auto", "always", "never"), default="auto",
        metavar="WHEN",
        help="colorize --diff and mark the changed span within a line: "
             "auto (default, only on a terminal), always, never. "
             "NO_COLOR is honoured",
    )
    parser.add_argument(
        "-c", "--check", action="store_true",
        help="report what would be replaced, write no output, exit 1 on any match "
             "(for pre-commit hooks and CI)",
    )
    parser.add_argument(
        "-A", "--audit", action="count", default=0,
        help="do not redact: report values that look sensitive but no rule "
             "touched. Heuristic - it asks, it does not decide. Repeat as -AA "
             "(or use --audit-all) to list every finding in full",
    )
    parser.add_argument(
        "--audit-all", action="store_true",
        help="like -A but without the summary: list every value in every "
             "category at full length (no top-5 cap, no clipping). Same as -AA",
    )
    parser.add_argument(
        "--strict", action="store_true",
        help="with --audit, exit 1 when anything is found (so audit can gate a "
             "commit or CI run, like --check does for the known shapes)",
    )
    parser.add_argument(
        "--entropy", action="store_true",
        help="with --audit, additionally flag high-entropy token-like strings - "
             "the net for credentials whose shape no pattern knows",
    )
    parser.add_argument(
        "-l", "--list-rules", action="store_true",
        help="print the loaded rules (with their source) and exit",
    )
    parser.add_argument(
        "-s", "--stats", action="store_true",
        help="after processing, print per-rule match counts to stderr",
    )
    parser.add_argument(
        "-V", "--version", action="version",
        version="redactor %s\n%s <%s>\n%s" % (__version__, __author__, __email__, __url__),
    )
    args = parser.parse_args(argv)

    # -A counts (so -AA -> 2); --audit-all is the long form of level 2.  Collapse
    # both into one bool the rest of main() can test, plus audit_full for report().
    audit_level = max(args.audit, 2 if args.audit_all else 0)
    args.audit = audit_level >= 1
    audit_full = audit_level >= 2

    if args.list_profiles:
        found = list_profiles()
        if not found:
            print("(no profiles found; looked in: %s)" % ", ".join(profile_dirs()))
        for name, path in sorted(found.items()):
            print("%-16s  <- %s" % (name, path))
        return 0

    exclusive = [n for n in ("in_place", "diff", "check", "audit") if getattr(args, n)]
    if len(exclusive) > 1:
        parser.error(
            "--%s cannot be combined" % " and --".join(n.replace("_", "-") for n in exclusive)
        )
    if args.in_place and not args.files:
        parser.error("-i/--in-place needs file arguments, it cannot rewrite a pipe")
    if args.unredact and (args.blank or args.check or args.audit):
        parser.error("--unredact cannot be combined with -b/--blank, --check or --audit")
    if (args.strict or args.entropy) and not args.audit:
        parser.error("--strict and --entropy only apply to --audit")

    # -r expands directory arguments into their files, so everything downstream
    # (-i/-d/-c/-A and plain stdout alike) just sees a flat list of files.  A
    # directory without -r is an error, like grep.
    if args.recursive and not args.files:
        parser.error("-r/--recursive needs at least one directory or file")
    if (args.exclude or args.include) and not args.files:
        parser.error("--exclude/--include only apply to file arguments")
    had_paths = bool(args.files)
    skipped = {"binary": 0, "symlink": 0, "excluded": 0, "redactor": 0}
    if args.files:
        try:
            args.files = walk_files(
                args.files, args.recursive, args.exclude, args.include, skipped
            )
        except IsADirectoryError as exc:
            parser.error(str(exc))

    def skipped_note():
        parts = [
            "%d %s" % (n, kind) for kind, n in (
                ("binary", skipped["binary"]),
                ("symlink", skipped["symlink"]),
                ("excluded", skipped["excluded"]),
                ("redactor-owned", skipped["redactor"]),
            ) if n
        ]
        if parts:
            sys.stderr.write("redactor: skipped %s\n" % ", ".join(parts))

    # paths were given but every one was filtered/skipped: do nothing rather than
    # fall through to reading stdin (which -d/-c/-A would otherwise do on []).
    if had_paths and not args.files:
        skipped_note()
        sys.stderr.write("redactor: no files to process\n")
        return 0

    # -n means "only what I passed on the command line", so it has to switch off
    # the .redactor.map auto-discovery too - otherwise a map file somewhere up
    # the tree silently joins a run that asked for no config at all.
    map_path = args.map or (None if args.no_config else find_map_file())
    if map_path:
        # must happen before build_rules(): the @builtins pick up their mapper here
        STORE.load(map_path)

    if args.unredact:
        if not map_path:
            parser.error("--unredact needs a mapping: pass --map PATH")
        rules = build_unredact_rules(STORE)
        if not rules:
            sys.stderr.write("redactor: map %s is empty, nothing to undo\n" % map_path)
    else:
        rules = build_rules(args)

    blocks = [r for r in rules if isinstance(r, BlockRule)]
    lines = [r for r in rules if not isinstance(r, BlockRule)]

    if args.list_rules:
        if not rules:
            print("(no rules loaded)")
        for rule in rules:
            print("%-34s  <- %s" % (rule.label, rule.source))
        if KEEP:
            # never leave this implicit: "why was that not redacted?" must be
            # answerable from --list-rules alone
            print("%-34s  <- %s" % ("@keep", " ".join(sorted(KEEP))))
        if map_path:
            print("%-34s  <- %s" % ("(mapping)", map_path))
        return 0

    if not rules:
        sys.stderr.write("redactor: no rules loaded; passing input through unchanged\n")

    for stream in (sys.stdin, sys.stdout):
        try:
            stream.reconfigure(encoding="utf-8", errors="surrogateescape")
        except (AttributeError, ValueError):
            pass

    if args.blank:
        for rule in rules:
            wrap_for_blank(rule)
    elif args.keep_length:
        for rule in rules:
            wrap_for_keep_length(rule)

    position = Position()
    asker = None
    if args.ask:  # after blank/keep-length, so the preview shows what really lands
        asker = Asker(position)
        for rule in lines:
            wrap_for_ask(rule, asker)

    processor = Processor(lines, blocks, asker)
    auditor = Auditor(position, entropy=args.entropy) if args.audit else None

    def run(source, path, emit):
        processor.reset()
        position.path = path
        for lineno, line in enumerate(source, 1):
            position.lineno = lineno
            processor.feed(line, emit)
        processor.finish(emit)

    def run_buffered(path):
        with open(path, "r", encoding="utf-8", errors="surrogateescape") as fh:
            original = fh.readlines()  # read fully before touching the file
        out = []
        run(original, path, out.append)
        return original, out

    def write_back(path, original, out):
        if out == original:
            return False  # nothing matched: leave mtime alone
        tmp = path + ".redactor.tmp"
        with open(tmp, "w", encoding="utf-8", errors="surrogateescape") as fh:
            fh.writelines(out)
        shutil.copymode(path, tmp)
        os.replace(tmp, path)  # atomic: no half-redacted file on crash
        return True

    changed = 0
    try:
        if args.in_place:
            for path in args.files:
                original, out = run_buffered(path)
                if write_back(path, original, out):
                    changed += 1
        elif args.diff:
            color = want_color(args.color, sys.stdout)
            for path in args.files or [None]:
                if path is None:
                    original = sys.stdin.readlines()
                    out = []
                    run(original, None, out.append)
                    name = "<stdin>"
                else:
                    original, out = run_buffered(path)
                    name = path
                emit_diff(original, out, name, sys.stdout.write, color)
        elif args.check or args.audit:
            # both are dry runs over the stream; --check only wants the counters,
            # --audit inspects what came out the far end
            emit = auditor.scan if auditor else (lambda text: None)
            for path in args.files or [None]:
                source = sys.stdin if path is None else open(
                    path, "r", encoding="utf-8", errors="surrogateescape"
                )
                try:
                    run(source, path, emit)
                finally:
                    if path is not None:
                        source.close()
        elif args.files:
            for path in args.files:
                with open(path, "r", encoding="utf-8", errors="surrogateescape") as fh:
                    run(fh, path, sys.stdout.write)
        else:
            run(sys.stdin, None, sys.stdout.write)
    except BrokenPipeError:
        try:
            sys.stdout.close()
        except Exception:
            pass
        return 0
    except KeyboardInterrupt:
        return 130
    except OSError as exc:
        sys.stderr.write("redactor: %s\n" % exc)
        return 2

    skipped_note()

    # --check/--diff/--audit are dry runs: don't let them burn placeholder
    # numbers. --unredact only reads the map.
    if not (args.check or args.diff or args.audit or args.unredact):
        STORE.save()

    if args.audit:
        found = auditor.report(STORE, sys.stderr, full=audit_full)
        return 1 if (args.strict and found) else 0

    if args.in_place:
        sys.stderr.write(
            "redactor: %d of %d file(s) changed\n" % (changed, len(args.files))
        )

    if args.stats:
        sys.stderr.write("redactor: match counts\n")
        for rule in rules:
            sys.stderr.write("  %6d  %s\n" % (rule.count, rule.label))

    if args.check:
        total = sum(rule.count for rule in rules)
        if total:
            sys.stderr.write("redactor: %d match(es) would be redacted\n" % total)
            for rule in rules:
                if rule.count:
                    sys.stderr.write("  %6d  %s\n" % (rule.count, rule.label))
            return 1
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
