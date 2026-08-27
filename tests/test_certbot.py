import subprocess

import pytest

from app.utils import certbot
from app.utils.certbot import (CertbotError, build_issue_args, parse_certificates,
                               validate_domain, validate_domains)

CERTBOT_OUTPUT = """
Saving debug log to /var/log/letsencrypt/letsencrypt.log

Found the following certs:
  Certificate Name: panel.example.com
    Serial Number: 3f7a
    Key Type: ECDSA
    Domains: panel.example.com sub.example.com
    Expiry Date: 2026-11-25 10:12:41+00:00 (VALID: 89 days)
    Certificate Path: /etc/letsencrypt/live/panel.example.com/fullchain.pem
    Private Key Path: /etc/letsencrypt/live/panel.example.com/privkey.pem
  Certificate Name: old.example.com
    Domains: old.example.com
    Expiry Date: 2026-08-01 10:12:41+00:00 (INVALID: EXPIRED)
    Certificate Path: /etc/letsencrypt/live/old.example.com/fullchain.pem
    Private Key Path: /etc/letsencrypt/live/old.example.com/privkey.pem
"""


@pytest.fixture(autouse=True)
def enabled(monkeypatch):
    monkeypatch.setattr(certbot, "CERTBOT_ENABLED", True)
    monkeypatch.setattr(certbot, "CERTBOT_EMAIL", "")
    monkeypatch.setattr(certbot, "CERTBOT_WEBROOT", "")
    monkeypatch.setattr(certbot, "CERTBOT_STAGING", False)


class TestDomainValidation:
    @pytest.mark.parametrize("domain", ["example.com", "panel.example.com", "a-b.example.co.uk"])
    def test_valid_domains_are_normalised(self, domain):
        assert validate_domain(domain.upper() + ".") == domain

    @pytest.mark.parametrize(
        "domain",
        [
            "localhost",
            "-bad.example.com",
            "bad-.example.com",
            "exam ple.com",
            "example.com; rm -rf /",
            "$(whoami).example.com",
            "--server=http://evil",
            "",
        ],
    )
    def test_invalid_domains_are_rejected(self, domain):
        with pytest.raises(CertbotError):
            validate_domain(domain)

    def test_wildcards_are_refused_with_an_explanation(self):
        with pytest.raises(CertbotError, match="DNS-01"):
            validate_domain("*.example.com")

    def test_duplicates_are_dropped(self):
        assert validate_domains(["example.com", "EXAMPLE.com"]) == ["example.com"]

    def test_empty_list_is_rejected(self):
        with pytest.raises(CertbotError):
            validate_domains([])


class TestIssueArguments:
    def test_standalone_arguments(self):
        args = build_issue_args(["example.com"], email="ops@example.com")

        assert args[:4] == ["certonly", "--non-interactive", "--agree-tos", "--keep-until-expiring"]
        assert "--standalone" in args
        assert args[-2:] == ["-d", "example.com"]

    def test_each_domain_gets_its_own_flag(self):
        args = build_issue_args(["a.example.com", "b.example.com"], email="ops@example.com")

        assert args.count("-d") == 2
        assert "a.example.com" in args and "b.example.com" in args

    def test_webroot_requires_an_absolute_path(self):
        with pytest.raises(CertbotError, match="absolute path"):
            build_issue_args(["example.com"], email="ops@example.com", method="webroot", webroot="relative/path")

    def test_webroot_arguments(self):
        args = build_issue_args(["example.com"], email="ops@example.com", method="webroot", webroot="/var/www/html")

        assert "--webroot" in args
        assert args[args.index("--webroot-path") + 1] == "/var/www/html"
        assert "--standalone" not in args

    def test_unknown_method_is_rejected(self):
        with pytest.raises(CertbotError):
            build_issue_args(["example.com"], method="dns-cloudflare")

    def test_missing_email_registers_without_one(self):
        args = build_issue_args(["example.com"])

        assert "--register-unsafely-without-email" in args
        assert "--email" not in args

    def test_configured_email_is_used_as_the_default(self, monkeypatch):
        monkeypatch.setattr(certbot, "CERTBOT_EMAIL", "ops@example.com")
        args = build_issue_args(["example.com"])

        assert args[args.index("--email") + 1] == "ops@example.com"

    def test_invalid_email_is_rejected(self):
        with pytest.raises(CertbotError):
            build_issue_args(["example.com"], email="not-an-email")

    def test_staging_flag_follows_the_config(self, monkeypatch):
        monkeypatch.setattr(certbot, "CERTBOT_STAGING", True)

        assert "--staging" in build_issue_args(["example.com"])


class TestParsing:
    def test_every_lineage_is_found(self):
        certificates = parse_certificates(CERTBOT_OUTPUT)

        assert [certificate.name for certificate in certificates] == ["panel.example.com", "old.example.com"]

    def test_fields_are_extracted(self):
        certificate = parse_certificates(CERTBOT_OUTPUT)[0]

        assert certificate.domains == ["panel.example.com", "sub.example.com"]
        assert certificate.expires_at.year == 2026
        assert certificate.certificate_path.endswith("fullchain.pem")
        assert certificate.private_key_path.endswith("privkey.pem")

    def test_days_left_is_derived_from_the_expiry(self):
        certificate = parse_certificates(CERTBOT_OUTPUT)[1]

        assert certificate.days_left < 0  # already expired

    def test_empty_output_yields_nothing(self):
        assert parse_certificates("No certificates found.") == []


class TestRunning:
    def test_disabled_panel_refuses_to_run(self, monkeypatch):
        monkeypatch.setattr(certbot, "CERTBOT_ENABLED", False)

        with pytest.raises(CertbotError, match="disabled"):
            certbot.list_certificates()

    def test_missing_binary_is_reported(self, monkeypatch):
        def missing(*args, **kwargs):
            raise FileNotFoundError()

        monkeypatch.setattr(subprocess, "run", missing)

        with pytest.raises(CertbotError, match="not found"):
            certbot.list_certificates()

    def test_timeout_is_reported(self, monkeypatch):
        def slow(*args, **kwargs):
            raise subprocess.TimeoutExpired(cmd="certbot", timeout=1)

        monkeypatch.setattr(subprocess, "run", slow)

        with pytest.raises(CertbotError, match="did not finish"):
            certbot.list_certificates()

    def test_failure_surfaces_the_last_useful_line(self, monkeypatch):
        class Result:
            returncode = 1
            stdout = ""
            stderr = "Saving debug log to /var/log/letsencrypt/letsencrypt.log\nSome challenges have failed.\n"

        monkeypatch.setattr(subprocess, "run", lambda *a, **k: Result())

        with pytest.raises(CertbotError, match="Some challenges have failed."):
            certbot.list_certificates()

    def test_command_is_never_run_through_a_shell(self, monkeypatch):
        captured = {}

        class Result:
            returncode = 0
            stdout = CERTBOT_OUTPUT
            stderr = ""

        def record(args, **kwargs):
            captured["args"] = args
            captured["kwargs"] = kwargs
            return Result()

        monkeypatch.setattr(subprocess, "run", record)
        certbot.list_certificates()

        assert isinstance(captured["args"], list)
        assert captured["kwargs"].get("shell") is not True

    def test_certificate_name_is_validated_before_use(self):
        with pytest.raises(CertbotError):
            certbot.delete_certificate("../../etc/passwd")

        with pytest.raises(CertbotError):
            certbot.renew_certificate("name; rm -rf /")
