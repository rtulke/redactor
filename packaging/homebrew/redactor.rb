# Master copy of the Homebrew formula.
#
# This file does NOT ship to users from here -- Homebrew taps must live in their
# own repo:
#
#   github.com/rtulke/homebrew-redactor  ->  Formula/redactor.rb
#
# It lives under packaging/ because a formula IS a package definition, exactly
# like nfpm.yaml next door: same job, different ecosystem. Copy it into the tap
# once when you create it, then let scripts/release.sh maintain url + sha256
# there on every release. See docs/RELEASING.md.
#
# Note what is NOT here, compared to Formula/sshscan.rb: no
# `include Language::Python::Virtualenv`, no `virtualenv_create`, no
# `resource "pyyaml"`. redactor imports nothing outside the standard library,
# so there is nothing to install into a venv -- the formula drops the script in
# libexec and points a shim at it.
class Redactor < Formula
  desc "Anonymize texts, logs, READMEs and code"
  homepage "https://github.com/rtulke/redactor"
  url "https://github.com/rtulke/redactor/archive/refs/tags/v1.3.0.tar.gz"
  sha256 "0000000000000000000000000000000000000000000000000000000000000000"
  license "MIT"

  head "https://github.com/rtulke/redactor.git", branch: "main"

  depends_on "python@3.13"

  def install
    # profiles/ must land NEXT TO redactor.py: profile_dirs() resolves profiles
    # relative to the script, and the Linux path /usr/share/redactor/profiles
    # does not exist under the Homebrew prefix.
    libexec.install "redactor.py", "profiles"

    python = Formula["python@3.13"]
    (bin/"redactor").write <<~SHELL
      #!/bin/bash
      exec "#{python.opt_bin}/python3.13" "#{libexec}/redactor.py" "$@"
    SHELL
    chmod 0755, bin/"redactor"

    # Homebrew's own prefixes, not /usr/share: man1 and bash_completion resolve
    # under the cellar and are symlinked into the prefix on link.
    man1.install "man/redactor.1"
    bash_completion.install "completion/redactor.bash-completion" => "redactor"

    doc.install "README.md", "README_de.md", "CHANGELOG.md", "LICENSE",
                "redactor.conf.example"
  end

  def caveats
    <<~EOS
      redactor runs with no config at all, but it only redacts what you tell it to.
      Start from the template:

        cp #{doc}/redactor.conf.example ~/.config/redactor.conf

      Ready-made rule sets:  redactor --list-profiles
      What did I forget?:    redactor --audit somefile.log
    EOS
  end

  test do
    assert_match version.to_s, shell_output("#{bin}/redactor --version")

    # the profiles must be findable from libexec, not merely present on disk
    assert_match "webserver", shell_output("#{bin}/redactor --list-profiles")

    # end-to-end, not just --version: prove it actually redacts
    assert_equal "ping 10.0.0.1\n",
                 pipe_output("#{bin}/redactor -n -e '@ip'", "ping 192.168.1.50\n")
    assert_equal "user1\n",
                 pipe_output("#{bin}/redactor -n -e '@word robert user1'", "robert\n")

    # --check must exit non-zero when it finds something; hooks depend on it
    output = pipe_output("#{bin}/redactor -n -e '@ip' -c 2>&1", "1.1.1.1\n")
    assert_match "would be redacted", output
  end
end
