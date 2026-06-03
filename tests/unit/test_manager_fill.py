from autoteam import manager


class _FakeChatGPT:
    def __init__(self):
        self.browser = True
        self.started = 0
        self.stopped = 0

    def start(self):
        self.browser = True
        self.started += 1

    def stop(self):
        self.browser = False
        self.stopped += 1


class _FakeMailClient:
    def login(self):
        return None


def test_cmd_fill_tries_other_reusable_accounts_before_creating_new(monkeypatch):
    import autoteam.config as config

    chatgpt = _FakeChatGPT()
    count_values = iter([2, 3])
    events = []

    monkeypatch.setattr(config, "ROTATE_SKIP_REUSE", False)
    monkeypatch.setattr(manager, "_select_reuse_candidates", lambda accounts, threshold, **kwargs: list(accounts))
    monkeypatch.setattr(manager, "ChatGPTTeamAPI", lambda: chatgpt)
    monkeypatch.setattr(manager, "CloudMailClient", lambda: _FakeMailClient())
    monkeypatch.setattr(manager, "get_team_member_count", lambda _chatgpt: next(count_values))
    monkeypatch.setattr(
        manager,
        "get_standby_accounts",
        lambda: [
            {"email": "old-1@example.com", "_quota_recovered": True},
            {"email": "old-2@example.com", "_quota_recovered": True},
        ],
    )

    def fake_reinvite(_chatgpt, _mail, acc):
        events.append(("reinvite", acc["email"]))
        return acc["email"] == "old-2@example.com"

    monkeypatch.setattr(manager, "reinvite_account", fake_reinvite)
    monkeypatch.setattr(
        manager,
        "create_new_account",
        lambda _chatgpt, _mail: events.append(("create", None)) or True,
    )
    monkeypatch.setattr(manager, "sync_to_cpa", lambda: events.append(("sync", None)))
    monkeypatch.setattr(manager, "cmd_status", lambda: events.append(("status", None)))

    manager.cmd_fill(target=3)

    assert events == [
        ("reinvite", "old-1@example.com"),
        ("reinvite", "old-2@example.com"),
        ("sync", None),
        ("status", None),
    ]
    assert chatgpt.stopped == 1


def test_cmd_fill_skips_google_accounts_during_auto_reuse(monkeypatch):
    import autoteam.config as config

    chatgpt = _FakeChatGPT()
    count_values = iter([2, 3])
    events = []

    monkeypatch.setattr(config, "ROTATE_SKIP_REUSE", False)
    monkeypatch.setattr(manager, "_select_reuse_candidates", lambda accounts, threshold, **kwargs: list(accounts))
    monkeypatch.setattr(manager, "ChatGPTTeamAPI", lambda: chatgpt)
    monkeypatch.setattr(manager, "CloudMailClient", lambda: _FakeMailClient())
    monkeypatch.setattr(manager, "get_team_member_count", lambda _chatgpt: next(count_values))
    monkeypatch.setattr(
        manager,
        "get_standby_accounts",
        lambda: [
            {"email": "bubblehuntr@gmail.com", "_quota_recovered": True},
            {"email": "old-2@example.com", "_quota_recovered": True},
        ],
    )

    def fake_reinvite(_chatgpt, _mail, acc):
        events.append(("reinvite", acc["email"]))
        return True

    monkeypatch.setattr(manager, "reinvite_account", fake_reinvite)
    monkeypatch.setattr(
        manager,
        "create_new_account",
        lambda _chatgpt, _mail: events.append(("create", None)) or True,
    )
    monkeypatch.setattr(manager, "sync_to_cpa", lambda: events.append(("sync", None)))
    monkeypatch.setattr(manager, "cmd_status", lambda: events.append(("status", None)))

    manager.cmd_fill(target=3)

    assert events == [
        ("reinvite", "old-2@example.com"),
        ("sync", None),
        ("status", None),
    ]
    assert chatgpt.stopped == 1


def test_auto_reuse_skip_reason_detects_google_provider_and_gmail():
    assert manager._auto_reuse_skip_reason({"email": "bubblehuntr@gmail.com"}) == "Google 登录账号暂不支持自动复用"
    assert (
        manager._auto_reuse_skip_reason({"email": "user@example.com", "login_provider": "google"})
        == "Google 登录账号暂不支持自动复用"
    )
    assert manager._auto_reuse_skip_reason({"email": "user@example.com"}) is None


def test_select_reuse_candidates_prefers_high_confidence_team_accounts(monkeypatch, tmp_path):
    import autoteam.config as config

    best_auth = tmp_path / "best-team.json"
    second_auth = tmp_path / "second-team.json"
    weak_auth = tmp_path / "weak-team.json"
    for path in (best_auth, second_auth, weak_auth):
        path.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(config, "ROTATE_REUSE_CANDIDATE_LIMIT", 2)
    monkeypatch.setattr(
        manager,
        "_find_team_auth_file",
        lambda email: {
            "best@example.com": str(best_auth),
            "second@example.com": str(second_auth),
            "weak@example.com": str(weak_auth),
            "paused@example.com": str(weak_auth),
        }.get(email),
    )

    selected = manager._select_reuse_candidates(
        [
            {
                "email": "weak@example.com",
                "_quota_recovered": True,
                "last_quota": {"primary_pct": 20, "primary_total": 1000},
                "last_quota_check_at": 100,
            },
            {
                "email": "best@example.com",
                "_quota_recovered": True,
                "seat_type": manager.SEAT_CHATGPT,
                "last_quota": {"primary_pct": 5, "primary_total": 1000},
                "last_quota_check_at": 300,
            },
            {
                "email": "second@example.com",
                "_quota_recovered": True,
                "plan_type_raw": "team",
                "last_quota": {"primary_pct": 10, "primary_total": 1000},
                "last_quota_check_at": 200,
            },
            {
                "email": "paused@example.com",
                "_quota_recovered": True,
                "seat_type": manager.SEAT_CHATGPT,
                "auth_retry_paused": True,
            },
        ],
        threshold=10,
        stage_label="[test]",
    )

    assert [acc["email"] for acc in selected] == ["best@example.com", "second@example.com"]
    assert [acc["auth_file"] for acc in selected] == [str(best_auth), str(second_auth)]


def test_select_reuse_candidates_allows_fresh_oauth_without_old_team_auth(monkeypatch):
    import autoteam.config as config

    monkeypatch.setattr(config, "ROTATE_REUSE_CANDIDATE_LIMIT", 4)
    monkeypatch.setattr(manager, "_find_team_auth_file", lambda _email: None)

    selected = manager._select_reuse_candidates(
        [
            {
                "email": "reusable@example.com",
                "password": "Password123!",
                "mail_provider": "cf_temp_email",
                "mail_account_id": "mail-1",
                "_quota_recovered": True,
                "seat_type": manager.SEAT_CHATGPT,
                "last_quota": {"primary_pct": 1, "primary_total": 1000},
            }
        ],
        threshold=10,
        stage_label="[test]",
    )

    assert [acc["email"] for acc in selected] == ["reusable@example.com"]
    assert "auth_file" not in selected[0]


def test_retire_team_auth_after_team_exit_moves_file_and_deletes_remote(tmp_path, monkeypatch):
    from autoteam import accounts as accounts_mod

    accounts_file = tmp_path / "accounts.json"
    auth_dir = tmp_path / "auths"
    auth_dir.mkdir()
    auth_file = auth_dir / "codex-child@example.com-team-deadbeef.json"
    auth_file.write_text('{"access_token":"stale"}', encoding="utf-8")

    monkeypatch.setattr(accounts_mod, "ACCOUNTS_FILE", accounts_file)
    monkeypatch.setattr(manager, "_find_team_auth_file", lambda _email: None)
    monkeypatch.setattr(manager, "_is_main_account_email", lambda _email: False)

    accounts_mod.save_accounts([
        {
            "email": "child@example.com",
            "password": "Password123!",
            "status": accounts_mod.STATUS_STANDBY,
            "auth_file": str(auth_file),
            "mail_account_id": "mail-1",
        }
    ])

    calls = []
    monkeypatch.setattr(
        manager,
        "delete_account_from_configured_targets",
        lambda email, **kwargs: calls.append((email, kwargs)) or {"cpa": {"deleted": kwargs.get("auth_names") or []}},
    )

    retired = manager._retire_team_auth_after_team_exit("child@example.com", reason="unit_test")

    assert retired == ["codex-child@example.com-team-deadbeef.json"]
    assert not auth_file.exists()
    backup_files = list((tmp_path / "auths_retired").glob("*/*.json"))
    assert len(backup_files) == 1
    acc = accounts_mod.find_account(accounts_mod.load_accounts(), "child@example.com")
    assert acc["auth_file"] is None
    assert calls == [
        (
            "child@example.com",
            {"auth_names": ["codex-child@example.com-team-deadbeef.json"], "include_disabled": True},
        )
    ]


def test_cmd_fill_respects_reuse_candidate_limit(monkeypatch, tmp_path):
    import autoteam.config as config

    chatgpt = _FakeChatGPT()
    count_values = iter([2, 3])
    events = []

    first_auth = tmp_path / "first-team.json"
    second_auth = tmp_path / "second-team.json"
    first_auth.write_text("{}", encoding="utf-8")
    second_auth.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(config, "ROTATE_SKIP_REUSE", False)
    monkeypatch.setattr(config, "ROTATE_REUSE_CANDIDATE_LIMIT", 1)
    monkeypatch.setattr(manager, "ChatGPTTeamAPI", lambda: chatgpt)
    monkeypatch.setattr(manager, "CloudMailClient", lambda: _FakeMailClient())
    monkeypatch.setattr(manager, "get_team_member_count", lambda _chatgpt: next(count_values))
    monkeypatch.setattr(
        manager,
        "_find_team_auth_file",
        lambda email: {
            "old-1@example.com": str(first_auth),
            "old-2@example.com": str(second_auth),
        }.get(email),
    )
    monkeypatch.setattr(
        manager,
        "get_standby_accounts",
        lambda: [
            {
                "email": "old-1@example.com",
                "_quota_recovered": True,
                "seat_type": manager.SEAT_CHATGPT,
                "last_quota": {"primary_pct": 0, "primary_total": 1000},
            },
            {
                "email": "old-2@example.com",
                "_quota_recovered": True,
                "seat_type": manager.SEAT_CHATGPT,
                "last_quota": {"primary_pct": 0, "primary_total": 1000},
            },
        ],
    )
    monkeypatch.setattr(
        manager,
        "reinvite_account",
        lambda _chatgpt, _mail, acc: events.append(("reinvite", acc["email"])) or False,
    )
    monkeypatch.setattr(
        manager,
        "create_new_account",
        lambda _chatgpt, _mail: events.append(("create", None)) or True,
    )
    monkeypatch.setattr(manager, "sync_to_cpa", lambda: events.append(("sync", None)))
    monkeypatch.setattr(manager, "cmd_status", lambda: events.append(("status", None)))

    manager.cmd_fill(target=3)

    assert events == [
        ("reinvite", "old-1@example.com"),
        ("create", None),
        ("sync", None),
        ("status", None),
    ]


def test_cmd_fill_passes_direct_parallel_and_releases_failed_validation(monkeypatch):
    import autoteam.config as config

    chatgpt = _FakeChatGPT()
    counts = iter([2, 2])
    events = []

    monkeypatch.setattr(config, "ROTATE_SKIP_REUSE", True)
    monkeypatch.setattr(manager, "ChatGPTTeamAPI", lambda: chatgpt)
    monkeypatch.setattr(manager, "CloudMailClient", lambda: _FakeMailClient())
    monkeypatch.setattr(manager, "get_team_member_count", lambda _chatgpt: next(counts))
    monkeypatch.setattr(manager, "get_standby_accounts", lambda: [])
    monkeypatch.setattr(
        manager,
        "create_new_account",
        lambda _chatgpt, _mail, *, parallel=None: events.append(("create", parallel)) or "new@example.com",
    )
    monkeypatch.setattr(manager, "_validate_managed_account_operational", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(
        manager,
        "remove_from_team",
        lambda _chatgpt, email, *, return_status=False, **_kwargs: events.append(("remove", email)) or "removed",
    )
    monkeypatch.setattr(
        manager,
        "update_account",
        lambda email, **kwargs: events.append(("update", email, kwargs.get("status"), kwargs.get("_reason"))),
    )
    monkeypatch.setattr(manager, "sync_to_cpa", lambda: events.append(("sync", None)))
    monkeypatch.setattr(manager, "cmd_status", lambda: events.append(("status", None)))

    manager.cmd_fill(target=3, direct_parallel=3)

    assert ("create", 3) in events
    assert ("remove", "new@example.com") in events
    assert ("update", "new@example.com", manager.STATUS_STANDBY, "fill_new_account_not_ready") in events
