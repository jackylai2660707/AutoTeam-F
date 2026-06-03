import base64
import json

from autoteam import chatgpt_api, codex_auth, invite, manager


class _FakeElement:
    def __init__(self, text="", *, visible=True, editable=True):
        self.text = text
        self._text = text
        self._visible = visible
        self._editable = editable
        self.clicked = False
        self.filled = None

    def is_visible(self, timeout=0):
        return self._visible

    def is_editable(self, timeout=0):
        return self._editable

    def inner_text(self, timeout=0):
        return self._text

    def click(self, timeout=0, force=False):
        self.clicked = True

    def fill(self, value):
        self.filled = value

    def get_attribute(self, _name):
        return ""


class _FakeCollection:
    def __init__(self, items=None, text=None):
        self._items = items or []
        self._text = text

    @property
    def first(self):
        if self._items:
            return self._items[0]
        return _FakeElement("", visible=False)

    def all(self):
        return list(self._items)

    def inner_text(self, timeout=0):
        if self._text is None:
            raise AssertionError("unexpected inner_text call")
        return self._text


class _FakePage:
    def __init__(self, *, url, body="", elements=None):
        self.url = url
        self._body = body
        self._elements = elements or []

    def locator(self, selector):
        if selector == "body":
            return _FakeCollection(text=self._body)
        return _FakeCollection(items=self._elements)

    def wait_for_load_state(self, *_args, **_kwargs):
        return None


def test_workspace_candidate_kind_filters_noise_and_marks_fallback():
    assert chatgpt_api._workspace_candidate_kind("Choose a workspace") is None
    assert chatgpt_api._workspace_candidate_kind("Terms of Use") is None
    assert chatgpt_api._workspace_candidate_kind("Idapro") == "preferred"
    assert chatgpt_api._workspace_candidate_kind("Personal account") == "fallback"


def test_wait_for_post_workspace_ready_accepts_chatgpt_blank_after_retries(monkeypatch):
    client = chatgpt_api.ChatGPTTeamAPI()
    client.page = _FakePage(url="https://chatgpt.com/")

    monkeypatch.setattr(client, "_extract_session_token", lambda: "")
    monkeypatch.setattr(client, "_body_excerpt", lambda limit=120: "")
    monkeypatch.setattr(chatgpt_api.time, "sleep", lambda _seconds: None)

    assert client._wait_for_post_workspace_ready(timeout=2) is True


def test_select_workspace_option_shortcuts_completed_on_chatgpt_home(monkeypatch):
    client = chatgpt_api.ChatGPTTeamAPI()
    client.page = _FakePage(url="https://chatgpt.com/")

    monkeypatch.setattr(client, "_list_workspace_options", lambda: [{"id": "0", "label": "Idapro"}])
    monkeypatch.setattr(client, "_click_workspace_option_by_label", lambda label: True)
    monkeypatch.setattr(client, "_wait_for_workspace_selection_exit", lambda timeout=15: True)
    monkeypatch.setattr(client, "_wait_for_post_workspace_ready", lambda timeout=12: True)
    monkeypatch.setattr(client, "_log_login_state", lambda label: None)
    monkeypatch.setattr(
        client,
        "_detect_login_step",
        lambda: (_ for _ in ()).throw(AssertionError("should not reach _detect_login_step")),
    )

    assert client.select_workspace_option(0) == {"step": "completed", "detail": None}


def test_codex_auth_workspace_wrappers_ignore_otp_and_noise_buttons():
    otp_page = _FakePage(
        url="https://auth.openai.com/email-verification",
        body="Check your inbox Enter the verification code we just sent to user@example.com",
    )
    assert codex_auth._is_workspace_selection_page(otp_page) is False
    assert codex_auth._select_team_workspace(otp_page, "Idapro") is False

    items = [
        _FakeElement("Cancel"),
        _FakeElement("Log in with a one-time code"),
        _FakeElement("Idapro"),
        _FakeElement("Personal account"),
    ]
    workspace_page = _FakePage(
        url="https://auth.openai.com/workspace",
        body="Choose a workspace Workspace Idapro Personal account",
        elements=items,
    )

    candidates = [text for text, _loc in codex_auth._workspace_label_candidates(workspace_page)]

    assert candidates == ["Idapro", "Personal account"]


class _FakeOrganizationPage:
    def __init__(self):
        self.dropdown_open = False
        self.selected_option = None
        self.trigger = _FakeElement("New organization")
        self.options = [_FakeElement("New organization"), _FakeElement("Existing Team")]

    def locator(self, selector):
        if "New organization" in selector or "新组织" in selector:
            original_click = self.trigger.click

            def click(timeout=0, force=False):
                original_click(timeout=timeout, force=force)
                self.dropdown_open = True

            self.trigger.click = click
            return _FakeCollection([self.trigger])
        if selector == '[role="option"]':
            if self.dropdown_open:
                option = self.options[1]
                original_click = option.click

                def click(timeout=0, force=False):
                    original_click(timeout=timeout, force=force)
                    self.selected_option = option.text

                option.click = click
                return _FakeCollection(self.options)
            return _FakeCollection([])
        return _FakeCollection([])


class _FakeChooseAccountPage:
    def __init__(self, email):
        self.url = "https://auth.openai.com/choose-an-account"
        self.selected_option = None
        self.email = email
        self.account_option = _FakeElement(email)
        self.other_button = _FakeElement("Log in to another account")

    def locator(self, selector):
        if '"' in selector:
            candidate = selector.split('"')[1]
            if candidate and candidate in self.account_option.text:
                original_click = self.account_option.click

                def click(timeout=0, force=False):
                    original_click(timeout=timeout, force=force)
                    self.selected_option = self.account_option.text

                self.account_option.click = click
                return _FakeCollection([self.account_option])
        if selector == 'button, [role="button"]':
            return _FakeCollection([self.account_option, self.other_button])
        return _FakeCollection([])


class _FakeTimeoutPage:
    def __init__(self, body_text="Oops, an error occurred! Operation timed out"):
        self.url = "https://auth.openai.com/log-in"
        self.clicked = False
        self.body_text = body_text
        self.try_again = _FakeElement("Try again")

    def locator(self, selector):
        if selector == "body":
            return _FakeCollection(text=self.body_text)
        if "Try again" in selector or "Retry" in selector:
            original_click = self.try_again.click

            def click(timeout=0, force=False):
                original_click(timeout=timeout, force=force)
                self.clicked = True

            self.try_again.click = click
            return _FakeCollection([self.try_again])
        return _FakeCollection([])


class _FakeLoginChallengePage:
    def __init__(self):
        self.url = "https://auth.openai.com/log-in"
        self.email_input = _FakeElement("")
        self.password_input = _FakeElement("")
        self.body = _FakeElement("Log in")

    def locator(self, selector):
        if selector == "body":
            return _FakeCollection(text=self.body.text)
        if 'input[name="email"]' in selector or 'input[id="email-input"]' in selector:
            return _FakeCollection([self.email_input])
        if 'input[name="password"]' in selector or 'input[type="password"]' in selector:
            return _FakeCollection([self.password_input])
        return _FakeCollection([])


class _DelayedCodePage:
    def __init__(self, selector, reveal_after=3):
        self.url = "https://auth.openai.com/email-verification"
        self._selector = selector
        self._polls = 0
        self._reveal_after = reveal_after
        self.code_inputs = [_FakeElement(editable=True) for _ in range(6)]
        self.body_text = "Enter verification code"

    def locator(self, selector):
        if selector == self._selector:
            self._polls += 1
            if self._polls >= self._reveal_after:
                return _FakeCollection(self.code_inputs)
            return _FakeCollection([])
        if selector == "input":
            return _FakeCollection(self.code_inputs if self._polls >= self._reveal_after else [])
        if selector == "body":
            return _FakeCollection(text=self.body_text)
        return _FakeCollection([])


class _FakeSessionPage:
    def __init__(self, sessions, *, quota_status=403):
        self.url = "about:blank"
        self.sessions = list(sessions)
        self.quota_status = quota_status
        self.goto_urls = []
        self.reloads = 0
        self.closed = 0

    def goto(self, url, wait_until=None, timeout=0):
        self.url = url
        self.goto_urls.append(url)

    def evaluate(self, script, *args):
        if args and "/backend-api/wham/usage" in script:
            return {"status": self.quota_status, "body": "{}" if self.quota_status == 200 else "forbidden"}
        if self.sessions:
            return self.sessions.pop(0)
        return {}

    def reload(self, wait_until=None, timeout=0):
        self.reloads += 1

    def close(self):
        self.closed += 1


class _FakeLoginPage:
    def __init__(self):
        self.url = "about:blank"
        self.goto_urls = []
        self.closed = 0
        self.email_input = _FakeElement("")
        self.password_input = _FakeElement("")
        self.login_button = _FakeElement("Log in")

    def goto(self, url, wait_until=None, timeout=0):
        self.url = url
        self.goto_urls.append(url)

    def locator(self, selector):
        if selector == "body":
            return _FakeCollection(text="")
        if 'input[name="email"]' in selector or 'input[id="email-input"]' in selector or 'input[id="email"]' in selector:
            return _FakeCollection([self.email_input])
        if 'input[name="password"]' in selector or 'input[type="password"]' in selector:
            return _FakeCollection([self.password_input])
        if 'button:has-text("登录")' in selector or 'button:has-text("Log in")' in selector:
            return _FakeCollection([self.login_button])
        return _FakeCollection([])

    def content(self):
        return ""

    def close(self):
        self.closed += 1


class _FakeContext:
    def __init__(self, page):
        self.page = page
        self.cookies = []
        self.closed = 0

    def add_cookies(self, cookies):
        self.cookies.extend(cookies)

    def new_page(self):
        return self.page

    def close(self):
        self.closed += 1


class _FakeBrowser:
    def __init__(self, page):
        self.page = page
        self.closed = 0

    def new_context(self, **_kwargs):
        return _FakeContext(self.page)

    def close(self):
        self.closed += 1


class _FakeChromium:
    def __init__(self, page):
        self.page = page

    def launch(self, **_kwargs):
        return _FakeBrowser(self.page)


class _FakePlaywright:
    def __init__(self, page):
        self.chromium = _FakeChromium(page)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def _fake_jwt(email="tmp@example.com", account_id="account-1", plan_type="team"):
    def encode(value):
        raw = json.dumps(value, separators=(",", ":")).encode("utf-8")
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")

    payload = {
        "email": email,
        "https://api.openai.com/auth": {
            "chatgpt_account_id": account_id,
            "chatgpt_plan_type": plan_type,
        },
    }
    return f"{encode({'alg': 'none', 'typ': 'JWT'})}.{encode(payload)}.sig"


def test_codex_oauth_trace_and_recovery_helpers(monkeypatch, tmp_path):
    monkeypatch.setattr(codex_auth.time, "sleep", lambda *_args, **_kwargs: None)

    org_page = _FakeOrganizationPage()
    assert codex_auth._select_existing_api_organization(org_page) is True
    assert org_page.selected_option == "Existing Team"

    choose_page = _FakeChooseAccountPage("tmp@example.com")
    assert codex_auth._select_choose_account(choose_page, "tmp@example.com") is True
    assert choose_page.selected_option == "tmp@example.com"

    trace_events = []
    codex_auth._append_oauth_trace(trace_events, kind="response", url="https://example.com/ignored", status=200)
    assert trace_events == []
    codex_auth._append_oauth_trace(
        trace_events,
        kind="response",
        url="https://auth.openai.com/sign-in-with-chatgpt/codex/consent",
        status=200,
        body_excerpt="  error_code:no_valid_organizations \n\n extra ",
    )
    assert trace_events[0]["body_excerpt"] == "error_code:no_valid_organizations extra"

    assert codex_auth._oauth_trace_has_login_challenge(
        [{"location": "https://auth.openai.com/api/accounts/login?login_challenge=abc"}]
    )
    assert codex_auth._is_oauth_login_challenge_page(_FakeTimeoutPage()) is True

    timeout_page = _FakeTimeoutPage()
    assert codex_auth._recover_oauth_timeout_page(timeout_page) is True
    assert timeout_page.clicked is True

    no_org_page = _FakeTimeoutPage("Oops, an error occurred! error_code: no_valid_organizations")
    assert codex_auth._recover_oauth_no_valid_organizations_page(no_org_page) is True

    error_type, detail, retryable = codex_auth._classify_oauth_failure(
        "https://auth.openai.com/error",
        "error_code:no_valid_organizations",
    )
    assert (error_type, retryable) == ("no_valid_organizations", True)
    assert "organization" in detail

    error_type, detail, retryable = codex_auth._classify_oauth_failure(
        "https://auth.openai.com/oauth/authorize",
        '{"error":{"code":"unsupported_country_region_territory"}}',
    )
    assert (error_type, retryable) == ("unsupported_region", True)
    assert "地区" in detail

    progress_page = _FakeTimeoutPage()
    progress_page.url = "https://auth.openai.com/api/accounts/consent?consent_challenge=abc"
    assert codex_auth._wait_for_otp_submit_result(progress_page, timeout=0.01) == ("accepted", None)

    cache_file = tmp_path / "otp_rejections.json"
    monkeypatch.setattr(codex_auth, "_OTP_REJECTION_FILE", cache_file)
    code_hash = codex_auth._record_otp_rejection("tmp@example.com", "123456", 42, now=1000)
    recent_hashes, recent_email_ids = codex_auth._load_recent_otp_rejections("tmp@example.com", now=1001)
    assert code_hash in recent_hashes
    assert 42 in recent_email_ids


def test_complete_oauth_login_challenge_password_path(monkeypatch):
    monkeypatch.setattr(codex_auth.time, "sleep", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(codex_auth, "_screenshot", lambda *args, **kwargs: None)
    clicked = []
    page = _FakeLoginChallengePage()

    def fake_click(_page, field, labels):
        clicked.append((field, tuple(labels)))
        return True

    monkeypatch.setattr(codex_auth, "_click_primary_auth_button", fake_click)

    assert codex_auth._complete_oauth_login_challenge(
        page,
        "tmp@example.com",
        "secret-password",
        mail_client=None,
        min_email_id=0,
        used_email_ids=set(),
    ) is True
    assert page.email_input.filled == "tmp@example.com"
    assert page.password_input.filled == "secret-password"
    assert clicked[0][0] is page.email_input
    assert clicked[1][0] is page.password_input


def test_split_code_helpers_handle_delayed_inputs(monkeypatch):
    fake_now = [0.0]
    monkeypatch.setattr(manager.time, "time", lambda: fake_now[0])
    monkeypatch.setattr(manager.time, "sleep", lambda seconds: fake_now.__setitem__(0, fake_now[0] + seconds))

    direct_page = _DelayedCodePage(manager._DIRECT_MULTI_CODE_SELECTOR, reveal_after=3)
    direct_target = manager._wait_for_direct_code_target(direct_page, timeout=5)
    assert direct_target["mode"] == "split"
    assert len(direct_target["target"]) == 6

    clicked = {}
    waited = {}
    monkeypatch.setattr(
        manager,
        "_click_primary_auth_button",
        lambda _page, field, labels: clicked.setdefault("call", (field, tuple(labels))) or True,
    )
    monkeypatch.setattr(manager, "_detect_direct_register_step", lambda _page: "code")
    monkeypatch.setattr(
        manager,
        "_wait_for_direct_step_change",
        lambda _page, current_step, timeout=0: (waited.setdefault("call", (current_step, timeout)), "profile")[1],
    )
    step = manager._submit_direct_verification_code(object(), direct_target, "123456")

    assert step == "profile"
    assert [element.filled for element in direct_target["target"]] == list("123456")
    assert clicked["call"][0] is direct_target["target"][0]
    assert waited["call"] == ("code", 20)

    fake_now[0] = 0.0
    monkeypatch.setattr(invite.time, "time", lambda: fake_now[0])
    monkeypatch.setattr(invite.time, "sleep", lambda seconds: fake_now.__setitem__(0, fake_now[0] + seconds))

    invite_page = _DelayedCodePage(invite.INVITE_MULTI_CODE_SELECTOR, reveal_after=3)
    invite_target = invite._wait_for_invite_code_target(invite_page, timeout=5)
    assert invite_target["mode"] == "split"
    assert len(invite_target["target"]) == 6


def test_codex_session_fallback_uses_presigned_cookies(monkeypatch):
    monkeypatch.setattr(codex_auth.time, "sleep", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(codex_auth, "get_chatgpt_account_id", lambda: "account-1")
    monkeypatch.setattr(codex_auth, "get_playwright_launch_options", lambda **_kwargs: {})
    monkeypatch.setattr(codex_auth, "get_playwright_context_options", lambda: {})
    monkeypatch.setattr(codex_auth, "check_codex_quota", lambda token, account_id=None, timeout=30: ("ok", {}))

    token = _fake_jwt(email="tmp@example.com", account_id="account-1", plan_type="team")
    page = _FakeSessionPage([{"accessToken": token}])
    monkeypatch.setattr(codex_auth, "sync_playwright", lambda: _FakePlaywright(page))

    result = codex_auth.login_codex_via_browser(
        "tmp@example.com",
        "",
        mail_client=None,
        return_result=True,
        pre_signed_in_cookies=[{"name": "session", "value": "ok", "domain": "chatgpt.com", "path": "/"}],
    )

    assert result["ok"] is True
    assert result["bundle"]["access_token"] == token
    assert page.goto_urls == ["https://chatgpt.com/admin/workspace/account-1"]


def test_codex_session_fallback_rejects_cookie_only_team_token(monkeypatch):
    monkeypatch.setattr(codex_auth.time, "sleep", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        codex_auth,
        "check_codex_quota",
        lambda token, account_id=None, timeout=30: ("auth_error", None),
    )

    token = _fake_jwt(email="tmp@example.com", account_id="account-1", plan_type="team")
    page = _FakeSessionPage([{"accessToken": token}], quota_status=200)
    context = _FakeContext(page)

    result = codex_auth._fetch_team_session_bundle_from_context(
        context,
        "tmp@example.com",
        "account-1",
        stage_label="unit",
        attempts=1,
    )

    assert result is None
    assert page.goto_urls == ["https://chatgpt.com/admin/workspace/account-1"]


def test_codex_session_fallback_runs_after_chatgpt_login(monkeypatch):
    monkeypatch.setattr(codex_auth.time, "sleep", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(codex_auth, "get_chatgpt_account_id", lambda: "account-1")
    monkeypatch.setattr(codex_auth, "get_playwright_launch_options", lambda **_kwargs: {})
    monkeypatch.setattr(codex_auth, "get_playwright_context_options", lambda: {})
    monkeypatch.setattr(codex_auth, "_screenshot", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(codex_auth, "_is_otp_input_visible", lambda *_args, **_kwargs: False)

    page = _FakeLoginPage()
    monkeypatch.setattr(codex_auth, "sync_playwright", lambda: _FakePlaywright(page))

    def fake_click(_page, field, labels):
        if field is page.password_input:
            page.url = "https://chatgpt.com/"
        return True

    token = _fake_jwt(email="tmp@example.com", account_id="account-1", plan_type="team")
    seen = {}

    monkeypatch.setattr(codex_auth, "_click_primary_auth_button", fake_click)
    monkeypatch.setattr(
        codex_auth,
        "_resolve_team_session_fallback_bundle",
        lambda _context, email, account_id, *, stage_label, attempts=3: seen.setdefault(
            "call",
            (email, account_id, stage_label, attempts),
        )
        and {
            "access_token": token,
            "email": email,
            "account_id": account_id,
            "plan_type": "team",
            "plan_supported": True,
        },
    )

    result = codex_auth.login_codex_via_browser(
        "tmp@example.com",
        "secret",
        mail_client=None,
        return_result=True,
    )

    assert result["ok"] is True
    assert result["bundle"]["access_token"] == token
    assert seen["call"] == ("tmp@example.com", "account-1", "post-chatgpt-login", 5)
    assert page.goto_urls == ["https://chatgpt.com/auth/login"]
