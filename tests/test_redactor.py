"""Test suite for redactor.

    python -m unittest discover -s tests -v

A redaction tool fails silently: when a regex breaks, the log still looks fine,
it just has real data in it. So the interesting cases here are the negative
ones - what must NOT be touched (timestamps, pids, byte counts, 'roberta',
/var/lib/home/) - and the ones where a leak would be invisible.

Every case passes -n/--no-config, otherwise the developer's own
~/.config/redactor.conf would decide whether the suite passes.
"""

import io
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import redactor  # noqa: E402


def run(argv, stdin_text=""):
    """Call main(argv) with stdin_text piped in -> (exit_code, stdout, stderr)."""
    old = sys.stdin, sys.stdout, sys.stderr
    sys.stdin = io.StringIO(stdin_text)
    sys.stdout = io.StringIO()
    sys.stderr = io.StringIO()
    try:
        try:
            code = redactor.main(argv)
        except SystemExit as exc:  # argparse --version / parser.error()
            code = exc.code if isinstance(exc.code, int) else 1
        return code, sys.stdout.getvalue(), sys.stderr.getvalue()
    finally:
        sys.stdin, sys.stdout, sys.stderr = old


def redact(rules, text):
    """Apply -e rules to text and return stdout, stripped of the final newline."""
    argv = ["-n"]
    for rule in rules:
        argv += ["-e", rule]
    _, out, _ = run(argv, text + "\n")
    return out.rstrip("\n")


class LiteralAndWord(unittest.TestCase):
    def test_literal_hits_substrings(self):
        self.assertEqual(redact(["robert user1"], "roberta"), "user1a")

    def test_word_spares_substrings(self):
        for text in ("roberta", "robert123", "xrobert", "roberto"):
            self.assertEqual(redact(["@word robert user1"], text), text)

    def test_word_hits_whole_words(self):
        cases = {
            "robert": "user1",
            "hi robert, x": "hi user1, x",
            "(robert)": "(user1)",
            "robert@a.ch": "user1@a.ch",
            "/home/robert": "/home/user1",
        }
        for text, want in cases.items():
            self.assertEqual(redact(["@word robert user1"], text), want, text)

    def test_missing_replacement_is_redacted_marker(self):
        self.assertEqual(redact(["hunter2"], "hunter2"), "[REDACTED]")

    def test_literal_needs_no_escaping(self):
        # the search text is not a regex, so the dot is a dot: as a regex,
        # "company.com" would also match "companyXcom". It must not.
        self.assertEqual(
            redact(["company.com example.com"], "company.com"), "example.com"
        )
        self.assertEqual(
            redact(["company.com example.com"], "companyXcom"), "companyXcom"
        )

    def test_quoted_values_with_spaces(self):
        self.assertEqual(
            redact(['"Robert Mueller" "Max Mustermann"'], "von Robert Mueller"),
            "von Max Mustermann",
        )


class Classes(unittest.TestCase):
    def test_ipv4(self):
        self.assertEqual(redact(["@ip"], "from 192.168.1.50"), "from 10.0.0.1")

    def test_not_an_ip(self):
        for text in ("999.1.1.1", "v1.2.3", "256.256.256.256"):
            self.assertEqual(redact(["@ip"], text), text)

    def test_email(self):
        self.assertEqual(
            redact(["@email"], "robert@company.com"), "redacted1@example.com"
        )

    def test_mac(self):
        self.assertEqual(
            redact(["@mac"], "00:11:22:33:44:55"), "02:00:00:00:00:01"
        )

    def test_uri_pseudonymizes_host_and_drops_secrets(self):
        cases = {
            "https://git.corp/team/x": "https://host1/team/x",
            "https://git.corp/x?token=abc": "https://host1/x",
            "https://bob:pw@git.corp/x#frag": "https://host1/x",
            "postgres://app:s3cr3t@db.corp:5432/prod": "postgres://host1:5432/prod",
        }
        for text, want in cases.items():
            self.assertEqual(redact(["@uri"], text), want, text)

    def test_path_home_dirs(self):
        self.assertEqual(redact(["@path"], "/home/alice/x.py"), "/home/user1/x.py")
        self.assertEqual(redact(["@path"], "/Users/rob/x"), "/Users/user1/x")

    def test_path_leaves_non_home_alone(self):
        self.assertEqual(redact(["@path"], "/var/lib/home/cache"), "/var/lib/home/cache")

    def test_path_prefix_form(self):
        # the reason @path exists: as a bare rule this would parse as a /regex/
        self.assertEqual(
            redact(
                ["@path /home/alice/myproject/ PROJECT/"],
                "/home/alice/myproject/dev/x.py",
            ),
            "PROJECT/dev/x.py",
        )

    def test_sshkey(self):
        fp = "SHA256:" + "A" * 43
        self.assertEqual(redact(["@sshkey"], "RSA " + fp), "RSA sshkey1")

    def test_hostname_and_uri_share_one_mapping(self):
        # both must land on host1, or the log contradicts itself
        out = redact(["@hostname git.corp", "@uri"], "git.corp and https://git.corp/x")
        self.assertEqual(out, "host1 and https://host1/x")


class Secrets(unittest.TestCase):
    def test_query_param(self):
        # the common web-server leak: no scheme, so @uri never sees it
        self.assertEqual(
            redact(["@secret"], 'GET /a?token=abc123 HTTP/1.1'),
            'GET /a?token=[SECRET] HTTP/1.1',
        )

    def test_assignment(self):
        self.assertEqual(redact(["@secret"], "password=hunter2"), "password=[SECRET]")

    def test_vendor_tokens(self):
        # AKIAIOSFODNN7EXAMPLE is AWS's own documentation key: AKIA + exactly 16.
        # Do not trim it - the pattern rightly rejects a 19-char near-miss.
        for text in ("AKIAIOSFODNN7EXAMPLE", "ghp_" + "a" * 30, "AIza" + "b" * 35):
            self.assertEqual(redact(["@secret"], text), "[SECRET]", text)

    def test_near_miss_key_is_not_a_key(self):
        # one char short of 20: not an AWS key, and must not be reported as one
        self.assertEqual(
            redact(["@secret"], "AKIAIOSFODNN7EXAMPL"), "AKIAIOSFODNN7EXAMPL"
        )

    def test_password_hash(self):
        self.assertEqual(
            redact(["@secret"], "robert:$6$rounds=5000$saltsalt$hashhashhash"),
            "robert:[SECRET]",
        )

    def test_bearer(self):
        self.assertEqual(
            redact(["@secret"], "Bearer abcdefghijklmnopqrst"), "Bearer [SECRET]"
        )

    def test_leaves_boring_things_alone(self):
        for text in ("listen 8080", "worker_processes 4", "ssl_protocols TLSv1.3"):
            self.assertEqual(redact(["@secret"], text), text, text)

    def test_private_key_block_is_multiline(self):
        text = (
            "a\n-----BEGIN RSA PRIVATE KEY-----\nMIIEow\nAAAA\n"
            "-----END RSA PRIVATE KEY-----\nb\n"
        )
        _, out, _ = run(["-n", "-e", "@secret"], text)
        self.assertEqual(out, "a\n[SECRET: private key removed]\nb\n")

    def test_unterminated_block_stays_redacted(self):
        # EOF inside a block must not leak the tail
        text = "a\n-----BEGIN RSA PRIVATE KEY-----\nMIIEow\n"
        _, out, _ = run(["-n", "-e", "@secret"], text)
        self.assertNotIn("MIIEow", out)


class Phone(unittest.TestCase):
    def test_international(self):
        for text in ("+41 79 123 45 67", "+41791234567", "0041791234567"):
            self.assertEqual(redact(["@phone"], text), "phone1", text)

    def test_same_number_two_spellings_one_pseudonym(self):
        _, out, _ = run(
            ["-n", "-e", "@phone"], "+41 79 123 45 67\n+41791234567\n"
        )
        self.assertEqual(out, "phone1\nphone1\n")

    def test_national_is_opt_in(self):
        self.assertEqual(redact(["@phone"], "079 123 45 67"), "079 123 45 67")
        self.assertEqual(redact(["@phone ch"], "079 123 45 67"), "phone1")

    def test_logs_are_full_of_numbers_that_are_not_phones(self):
        for text in ("1712913802", "sshd[1234]", "sent 1234567 bytes", "200 4711"):
            self.assertEqual(redact(["@phone"], text), text, text)

    def test_unknown_country_is_an_error(self):
        code, _, err = run(["-n", "-e", "@phone xx"], "x\n")
        self.assertIn("unknown country", err)


class Keep(unittest.TestCase):
    def test_keep_spares_the_value(self):
        self.assertEqual(redact(["@ip", "@keep 127.0.0.1"], "127.0.0.1"), "127.0.0.1")

    def test_keep_does_not_spare_everything_else(self):
        self.assertEqual(redact(["@ip", "@keep 127.0.0.1"], "8.8.8.8"), "10.0.0.1")

    def test_kept_uri_host_keeps_the_whole_url(self):
        # a kept host means "public", so the query survives too
        url = "https://github.com/x?tab=readme"
        self.assertEqual(redact(["@uri", "@keep github.com"], url), url)

    def test_keep_applies_regardless_of_line_order(self):
        # @keep is consulted at match time, so it may come after the rule
        self.assertEqual(redact(["@keep 127.0.0.1", "@ip"], "127.0.0.1"), "127.0.0.1")


class Stability(unittest.TestCase):
    def test_same_value_same_pseudonym(self):
        _, out, _ = run(["-n", "-e", "@ip"], "1.1.1.1\n8.8.8.8\n1.1.1.1\n")
        self.assertEqual(out, "10.0.0.1\n10.0.0.2\n10.0.0.1\n")

    def test_idempotent(self):
        # running redactor twice must not turn host1 into host2
        once = redact(["@ip"], "1.1.1.1")
        twice = redact(["@ip"], once)
        self.assertEqual(once, twice)


class Gates(unittest.TestCase):
    """The gates are a pure optimization; they must not change behaviour."""

    def test_gate_sees_the_rewritten_line(self):
        # rule 1 CREATES the text rule 2's gate looks for. Checking the gate
        # against the original line would skip rule 2 -> silent leak.
        self.assertEqual(
            redact(["XXX Bearer", "@secret"], "XXX abcdefghijklmnopqrst"),
            "Bearer [SECRET]",
        )

    def test_uppercase_token_behind_lowercase_gate(self):
        # the gate is the lowercase "akia"; the pattern is case-sensitive
        self.assertEqual(redact(["@secret"], "AKIAIOSFODNN7EXAMPLE"), "[SECRET]")

    def test_case_insensitive_literal_is_not_gated_away(self):
        # gates are lowercased; a case-sensitive literal must still match exactly
        self.assertEqual(redact(["Robert user1"], "Robert"), "user1")
        self.assertEqual(redact(["Robert user1"], "robert"), "robert")


class Blank(unittest.TestCase):
    def test_blank_keeps_columns(self):
        _, out, _ = run(["-n", "-e", "@ip", "-b"], "from 192.168.1.50 port\n")
        self.assertEqual(out, "from              port\n")

    def test_keep_length_pads(self):
        _, out, _ = run(["-n", "-e", "@ip", "-k"], "from 192.168.1.50 port\n")
        self.assertEqual(out, "from 10.0.0.1     port\n")


class Modes(unittest.TestCase):
    def test_check_exits_1_on_match(self):
        code, out, _ = run(["-n", "-e", "@ip", "-c"], "1.1.1.1\n")
        self.assertEqual(code, 1)
        self.assertEqual(out, "")  # --check writes no redacted output

    def test_check_exits_0_without_match(self):
        code, _, _ = run(["-n", "-e", "@ip", "-c"], "nothing here\n")
        self.assertEqual(code, 0)

    def test_diff(self):
        _, out, _ = run(["-n", "-e", "@ip", "-d"], "ping 1.1.1.1\n")
        self.assertIn("-ping 1.1.1.1", out)
        self.assertIn("+ping 10.0.0.1", out)

    def test_audit_flags_internal_host(self):
        _, _, err = run(["-n", "-e", "@ip", "-A"], "call db.corp.local now\n")
        self.assertIn("internal-host", err)
        self.assertIn("db.corp.local", err)

    def test_audit_ignores_our_own_pseudonyms(self):
        # 10.0.0.1 looks exactly like an IP; flagging it would make audit useless
        _, _, err = run(["-n", "-e", "@ip", "-A"], "from 192.168.1.50\n")
        self.assertNotIn("ipv4", err)

    def test_audit_ignores_kept_values(self):
        _, _, err = run(["-n", "-e", "@ip", "-e", "@keep 127.0.0.1", "-A"], "on 127.0.0.1\n")
        self.assertNotIn("ipv4", err)

    def test_audit_writes_no_output(self):
        _, out, _ = run(["-n", "-e", "@ip", "-A"], "from 192.168.1.50\n")
        self.assertEqual(out, "")

    def test_exclusive_modes(self):
        code, _, err = run(["-n", "-c", "-d"], "x\n")
        self.assertEqual(code, 2)
        self.assertIn("cannot be combined", err)


class MapAndUnredact(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.map = os.path.join(self.tmp.name, "m.json")

    def tearDown(self):
        self.tmp.cleanup()

    def test_map_is_stable_across_runs(self):
        run(["-n", "-m", self.map, "-e", "@ip", "-e", "@hostname web01"],
            "web01 1.1.1.1\n")
        _, out, _ = run(["-n", "-m", self.map, "-e", "@ip"], "1.1.1.1\n")
        self.assertEqual(out, "10.0.0.1\n")

    def test_map_file_is_json(self):
        run(["-n", "-m", self.map, "-e", "@ip"], "1.1.1.1\n")
        with open(self.map) as fh:
            data = json.load(fh)
        self.assertEqual(data["ip"]["1.1.1.1"], "10.0.0.1")

    def test_unredact_roundtrip(self):
        run(["-n", "-m", self.map, "-e", "@ip", "-e", "@hostname web01"],
            "web01 1.1.1.1\n")
        _, out, _ = run(["-n", "-m", self.map, "-u"], "host1 10.0.0.1\n")
        self.assertEqual(out, "web01 1.1.1.1\n")

    def test_unredact_needs_a_map(self):
        code, _, err = run(["-n", "-u"], "x\n")
        self.assertEqual(code, 2)

    def test_dry_runs_do_not_write_the_map(self):
        # --check must not burn placeholder numbers
        run(["-n", "-m", self.map, "-e", "@ip", "-c"], "1.1.1.1\n")
        self.assertFalse(os.path.exists(self.map))

    def test_longest_placeholder_wins_on_unredact(self):
        # host1 is a prefix of host10 - the alternation must try the long one first
        run(["-n", "-m", self.map, "-e", "@hostname " + " ".join(
            "h%d" % i for i in range(1, 12))],
            " ".join("h%d" % i for i in range(1, 12)) + "\n")
        _, out, _ = run(["-n", "-m", self.map, "-u"], "host10\n")
        self.assertEqual(out.strip(), "h10")


class InPlace(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "f.txt")

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, text):
        with open(self.path, "w") as fh:
            fh.write(text)

    def read(self):
        with open(self.path) as fh:
            return fh.read()

    def test_in_place(self):
        self.write("ip 1.1.1.1\n")
        run(["-n", "-i", "-e", "@ip", self.path])
        self.assertEqual(self.read(), "ip 10.0.0.1\n")

    def test_in_place_preserves_mode(self):
        self.write("ip 1.1.1.1\n")
        os.chmod(self.path, 0o600)
        run(["-n", "-i", "-e", "@ip", self.path])
        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o600)

    def test_in_place_needs_files(self):
        code, _, err = run(["-n", "-i", "-e", "@ip"], "x\n")
        self.assertEqual(code, 2)
        self.assertIn("cannot rewrite a pipe", err)

    def test_untouched_file_keeps_mtime(self):
        self.write("nothing to see\n")
        before = os.stat(self.path).st_mtime_ns
        run(["-n", "-i", "-e", "@ip", self.path])
        self.assertEqual(os.stat(self.path).st_mtime_ns, before)


class Profiles(unittest.TestCase):
    def test_list_profiles_finds_the_shipped_ones(self):
        _, out, _ = run(["--list-profiles"])
        for name in ("webserver", "sshd", "dhcpd", "ftp", "mail", "configfiles"):
            self.assertIn(name, out)

    def test_webserver_profile_redacts_query_token(self):
        _, out, _ = run(["-n", "-p", "webserver"], "GET /a?token=abc123\n")
        self.assertIn("[SECRET]", out)

    def test_unknown_profile_fails(self):
        code, _, _ = run(["-n", "-p", "doesnotexist"], "x\n")
        self.assertNotEqual(code, 0)


class Meta(unittest.TestCase):
    def test_version_is_single_quoted_on_one_line(self):
        # release.yml, ci.yml and scripts/release.sh all grep for exactly this
        import re
        with open(os.path.join(ROOT, "redactor.py")) as fh:
            source = fh.read()
        match = re.search(r"__version__\s*=\s*'([^']+)'", source)
        self.assertIsNotNone(match, "the packaging regex would not find the version")
        self.assertEqual(match.group(1), redactor.__version__)

    def test_version_output(self):
        _, out, _ = run(["-V"])
        self.assertIn(redactor.__version__, out)
        self.assertIn("rt@debian.sh", out)

    def test_man_page_version_matches(self):
        # A stale man page renders perfectly and simply lies about its version,
        # so nothing else would ever catch this. scripts/release.sh rewrites the
        # .TH line; this asserts it actually did.
        import re
        with open(os.path.join(ROOT, "man", "redactor.1")) as fh:
            head = fh.read(2000)
        match = re.search(r'^\.TH REDACTOR 1 "[\d-]+" "redactor ([^"]+)"', head, re.M)
        self.assertIsNotNone(match, "the .TH line is not in the shape release.sh seds")
        self.assertEqual(match.group(1), redactor.__version__)

    def test_man_page_documents_every_option(self):
        # The man page is the only reference a packaged user gets; a flag that
        # is not in it does not exist for them.
        with open(os.path.join(ROOT, "man", "redactor.1")) as fh:
            page = fh.read()
        for flag in ("--expr", "--file", "--profile", "--list-profiles", "--ask",
                     "--blank", "--keep-length", "--in-place", "--diff", "--check",
                     "--audit", "--map", "--unredact", "--stats", "--list-rules",
                     "--no-config", "--version"):
            self.assertIn(flag.replace("-", "\\-"), page, flag)

    def test_completion_offers_every_builtin(self):
        # The completion hardcodes the @builtin list; BUILTINS is the truth.
        with open(os.path.join(ROOT, "completion", "redactor.bash-completion")) as fh:
            script = fh.read()
        for name in redactor.BUILTINS:
            self.assertIn("@" + name, script, name)

    def test_no_third_party_imports(self):
        # the entire packaging story rests on this: stdlib only, no venv, noarch
        import ast
        with open(os.path.join(ROOT, "redactor.py")) as fh:
            tree = ast.parse(fh.read())
        allowed = set(sys.stdlib_module_names) if hasattr(
            sys, "stdlib_module_names") else {
            "argparse", "difflib", "getpass", "json", "os", "re", "shutil",
            "socket", "sys",
        }
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.assertIn(alias.name.split(".")[0], allowed, alias.name)
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                self.assertIn(node.module.split(".")[0], allowed, node.module)


if __name__ == "__main__":
    unittest.main()
