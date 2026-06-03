from autoteam import config, runtime_config
from autoteam.mail import cf_temp_email


def _use_runtime_file(monkeypatch, tmp_path):
    monkeypatch.setattr(runtime_config, "RUNTIME_CONFIG_FILE", tmp_path / "runtime_config.json")


def test_cf_temp_email_rotates_register_domains(monkeypatch, tmp_path):
    _use_runtime_file(monkeypatch, tmp_path)
    monkeypatch.setattr(config, "CLOUDMAIL_DOMAINS", ("a.example", "b.example"), raising=False)
    monkeypatch.setattr(config, "CLOUDMAIL_DOMAIN", "", raising=False)

    client = cf_temp_email.CfTempEmailClient()
    captured_domains = []

    class _Resp:
        status_code = 200

        def __init__(self, domain):
            self._domain = domain

        def json(self):
            return {"address": f"user@{self._domain}", "address_id": len(captured_domains)}

    def fake_post(path, data=None):
        assert path == "/admin/new_address"
        captured_domains.append(data["domain"])
        return _Resp(data["domain"])

    monkeypatch.setattr(client, "_admin_post", fake_post)

    assert client.create_temp_email(prefix="user")[1] == "user@a.example"
    assert client.create_temp_email(prefix="user")[1] == "user@b.example"
    assert client.create_temp_email(prefix="user")[1] == "user@a.example"
    assert captured_domains == ["a.example", "b.example", "a.example"]


def test_static_proxy_pool_rotates_before_ipv6(monkeypatch, tmp_path):
    _use_runtime_file(monkeypatch, tmp_path)
    monkeypatch.setattr(config, "PLAYWRIGHT_PROXY_URLS", ("socks5://p1.example:1080", "socks5://p2.example:1080"), raising=False)

    from autoteam import manager

    monkeypatch.setattr(config, "AUTOTEAM_IPV6_POOL_REQUIRED", True, raising=False)
    monkeypatch.setattr(
        "autoteam.ipv6_pool.ipv6_pool.ensure",
        lambda _email: (_ for _ in ()).throw(AssertionError("IPv6 should not be used when static proxy pool exists")),
    )

    assert manager._ensure_account_ipv6_proxy("a@example.com") == (
        "socks5://p1.example:1080",
        "socks5://p1.example:1080",
    )
    assert manager._ensure_account_ipv6_proxy("b@example.com") == (
        "socks5://p2.example:1080",
        "socks5://p2.example:1080",
    )
    assert manager._ensure_account_ipv6_proxy("c@example.com") == (
        "socks5://p1.example:1080",
        "socks5://p1.example:1080",
    )
