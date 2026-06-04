"""账号资源清理与远端对账操作。"""

import json
import logging
from pathlib import Path

from autoteam.accounts import STATUS_AUTH_INVALID, STATUS_PERSONAL, find_account, load_accounts, save_accounts
from autoteam.admin_state import get_admin_email, get_chatgpt_account_id
from autoteam.sync_targets import delete_account_from_configured_targets
from autoteam.sync_targets import sync_to_configured_targets as sync_to_cpa

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).parent.parent.parent
AUTH_DIR = PROJECT_ROOT / "auths"


def _normalized_email(value: str | None) -> str:
    return (value or "").strip().lower()


def _is_main_account_email(email: str | None) -> bool:
    normalized = _normalized_email(email)
    return bool(normalized) and normalized == _normalized_email(get_admin_email())


def _auth_file_candidates(auth_file: str) -> list[Path]:
    path = Path(auth_file)
    candidates = [path]
    raw_path = auth_file.replace("\\", "/")
    marker = "/app/"
    if marker in raw_path:
        relative = raw_path.split(marker, 1)[1].lstrip("/")
        if relative:
            candidates.append(PROJECT_ROOT / relative)
    if path.name:
        candidates.append(PROJECT_ROOT / "data" / "auths" / path.name)
        candidates.append(AUTH_DIR / path.name)

    seen = set()
    unique = []
    for candidate in candidates:
        key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        unique.append(candidate)
    return unique


def _resolve_auth_file_path(auth_file: str | None) -> Path | None:
    auth_file = (auth_file or "").strip()
    if not auth_file:
        return None
    for path in _auth_file_candidates(auth_file):
        if path.exists():
            return path
    return None


def _find_auth_file_for_email(email: str | None) -> Path | None:
    email_l = _normalized_email(email)
    if not email_l:
        return None
    for auth_dir in (AUTH_DIR, PROJECT_ROOT / "data" / "auths"):
        if not auth_dir.exists():
            continue
        for candidate in sorted(auth_dir.glob(f"codex-{email_l}-*.json")):
            if candidate.exists():
                return candidate
    return None


def _account_mail_account_id(acc: dict | None):
    acc = acc or {}
    if acc.get("mail_account_id") is not None:
        return acc.get("mail_account_id")
    return acc.get("cloudmail_account_id")


def _has_account_mail_binding(acc: dict | None) -> bool:
    return _account_mail_account_id(acc) is not None


def _is_protected_local_credential_account(acc: dict | None) -> bool:
    if not acc or _is_main_account_email(acc.get("email")):
        return False
    has_auth = (
        _resolve_auth_file_path(acc.get("auth_file")) is not None
        or _find_auth_file_for_email(acc.get("email")) is not None
    )
    if acc.get("protect_team_seat") is True:
        return has_auth
    return has_auth and not _has_account_mail_binding(acc)


def _response_excerpt(body, limit=240):
    text = str(body or "").strip().replace("\n", " ")
    if len(text) > limit:
        text = text[:limit] + "..."
    return text


def _parse_team_api_json(response, label):
    status = int(response.get("status") or 0)
    body = response.get("body", "")

    if status in (401, 403):
        raise RuntimeError(f"{label}接口鉴权失败 (HTTP {status})，请重新完成管理员登录")
    if status != 200:
        raise RuntimeError(f"{label}接口请求失败 (HTTP {status}): {_response_excerpt(body)}")

    try:
        return json.loads(body)
    except Exception as exc:
        lower_body = str(body or "").lower()
        if "<html" in lower_body or "<!doctype" in lower_body:
            raise RuntimeError(f"{label}接口返回了非 JSON 内容（疑似登录页或错误页），请重新完成管理员登录") from exc
        raise RuntimeError(f"{label}接口返回了非 JSON 内容: {_response_excerpt(body)}") from exc


def extract_team_members(payload):
    """Return Team member records from known ChatGPT API response shapes."""
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    for key in ("items", "users", "members", "account_users"):
        value = payload.get(key)
        if isinstance(value, list):
            return value
    return []


def extract_team_invites(payload):
    """Return Team invite records from known ChatGPT API response shapes."""
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    for key in ("items", "invites", "account_invites"):
        value = payload.get(key)
        if isinstance(value, list):
            return value
    return []


def team_member_email(member) -> str:
    for source in (member, (member or {}).get("user") or {}, (member or {}).get("account_user") or {}):
        if not isinstance(source, dict):
            continue
        for key in ("email", "email_address"):
            value = _normalized_email(source.get(key))
            if value:
                return value
    return ""


def team_invite_email(invite) -> str:
    for source in (invite, (invite or {}).get("user") or {}, (invite or {}).get("account_user") or {}):
        if not isinstance(source, dict):
            continue
        for key in ("email_address", "email"):
            value = _normalized_email(source.get(key))
            if value:
                return value
    return ""


def team_member_user_id(member):
    for source in (member, (member or {}).get("user") or {}, (member or {}).get("account_user") or {}):
        if not isinstance(source, dict):
            continue
        for key in ("user_id", "id", "account_user_id", "accountUserId"):
            value = source.get(key)
            if value:
                return value
    return None


def team_member_role(member):
    for source in (member, (member or {}).get("user") or {}, (member or {}).get("account_user") or {}):
        if not isinstance(source, dict):
            continue
        for key in ("role", "account_role"):
            value = source.get(key)
            if value:
                return value
    return None


def team_member_account_user_id(member):
    for source in (member, (member or {}).get("account_user") or {}, (member or {}).get("user") or {}):
        if not isinstance(source, dict):
            continue
        for key in ("account_user_id", "accountUserId"):
            value = source.get(key)
            if value:
                return value
    return None


def team_member_seat_type(member) -> str:
    for source in (member, (member or {}).get("account_user") or {}, (member or {}).get("user") or {}):
        if not isinstance(source, dict):
            continue
        value = source.get("seat_type")
        if value:
            return str(value).strip().lower()
    return ""


def delete_team_invite(chatgpt_api, account_id: str, invite: dict | None = None, *, invite_id=None, email: str | None = None):
    """Cancel a Team invite across known ChatGPT API variants."""
    invite = invite or {}
    invite_id = invite_id or invite.get("id")
    email_l = _normalized_email(email) or team_invite_email(invite)
    last_result = None

    if invite_id:
        last_result = chatgpt_api._api_fetch(
            "DELETE",
            f"/backend-api/accounts/{account_id}/invites/{invite_id}",
        )
        if last_result["status"] in (200, 204):
            return last_result

    if email_l:
        last_result = chatgpt_api._api_fetch(
            "DELETE",
            f"/backend-api/accounts/{account_id}/invites",
            {"email_address": email_l},
        )
        return last_result

    return last_result or {"status": 0, "body": "missing invite id/email"}


def _get_mail_client_for_account(acc: dict | None):
    from autoteam.mail import get_mail_client

    provider_name = ((acc or {}).get("mail_provider") or "").strip().lower()
    client = None
    if provider_name:
        try:
            from autoteam import mail as _mail_pkg

            resolver = getattr(_mail_pkg, "_resolve_provider_factory", None)
            if callable(resolver):
                factory = resolver(provider_name)
                if callable(factory):
                    client = factory()
        except Exception as exc:
            logger.warning(
                "[账号] mail_provider=%s 路由失败，回退默认 provider: %s",
                provider_name,
                exc,
            )

    if client is None:
        client = get_mail_client()
    login = getattr(client, "login", None)
    if callable(login):
        login()
    return client


def fetch_team_state(chatgpt_api):
    """读取 Team 成员和邀请状态。"""
    account_id = get_chatgpt_account_id()

    users_resp = chatgpt_api._api_fetch("GET", f"/backend-api/accounts/{account_id}/users")
    members = extract_team_members(_parse_team_api_json(users_resp, "Team 成员"))

    invites_resp = chatgpt_api._api_fetch("GET", f"/backend-api/accounts/{account_id}/invites")
    invites = extract_team_invites(_parse_team_api_json(invites_resp, "Team 邀请"))

    return members, invites


def delete_managed_account(
    email,
    *,
    remove_remote=True,
    remove_cloudmail=True,
    sync_cpa_after=True,
    chatgpt_api=None,
    mail_client=None,
    remote_state=None,
):
    """
    删除本地管理账号及其衍生资源。
    返回 cleanup 摘要，设计为幂等操作。
    """
    email_l = email.lower()
    accounts = load_accounts()
    acc = find_account(accounts, email)

    cleanup = {
        "local_record": False,
        "local_auth_files": [],
        "cpa_files": [],
        "sub2api_accounts": [],
        "team_member_removed": False,
        "invite_removed": False,
        "cloudmail_deleted": False,
        "protected_local_credential": False,
    }

    members = []
    invites = []
    own_chatgpt = None
    own_mail_client = None

    try:
        if _is_protected_local_credential_account(acc):
            cleanup["protected_local_credential"] = True
            logger.warning("[账号] 保护本地凭据席位，跳过删除: %s", email)
            return cleanup

        account_id = get_chatgpt_account_id()
        # SPEC-2 FR-H1 (issue #2 独属) + Round 6 PRD-5 FR-P1.2:personal / auth_invalid 账号
        # 删除时不需要拉 remote_state(members/invites)。auth_invalid 的 token 已失效,继续走
        # ChatGPTTeamAPI 会在 wham/usage 401 时拖累整个删除链路;主号 session 失效场景下,
        # 启动 ChatGPTTeamAPI 还会卡死 30s。这条短路同时规避两类资源浪费,纯本地操作。
        # 注意:变量名仍保留 is_personal 以最小化下游契约变化,但语义已扩到"本地清理即可"。
        is_personal = bool(acc and acc.get("status") in (STATUS_PERSONAL, STATUS_AUTH_INVALID))
        skip_remote = is_personal
        if remove_remote and not skip_remote:
            if remote_state is not None:
                members, invites = remote_state
            else:
                if chatgpt_api is None:
                    from autoteam.chatgpt_api import ChatGPTTeamAPI

                    own_chatgpt = ChatGPTTeamAPI()
                    own_chatgpt.start()
                    chatgpt_api = own_chatgpt
                members, invites = fetch_team_state(chatgpt_api)

            member_matches = [m for m in members if team_member_email(m) == email_l]
            for member in member_matches:
                user_id = team_member_user_id(member)
                if not user_id:
                    continue
                result = chatgpt_api._api_fetch(
                    "DELETE",
                    f"/backend-api/accounts/{account_id}/users/{user_id}",
                )
                if result["status"] not in (200, 204):
                    raise RuntimeError(f"移除 Team 成员失败: {email}")
                cleanup["team_member_removed"] = True

            invite_matches = []
            for inv in invites:
                inv_email = team_invite_email(inv)
                if inv_email == email_l:
                    invite_matches.append(inv)

            for inv in invite_matches:
                result = delete_team_invite(chatgpt_api, account_id, inv)
                if result["status"] not in (200, 204):
                    raise RuntimeError(f"取消 Team 邀请失败: {email}")
                cleanup["invite_removed"] = True

        auth_candidates = set()
        if acc and acc.get("auth_file"):
            auth_candidates.update(_auth_file_candidates(acc["auth_file"]))
        auth_candidates.update(AUTH_DIR.glob(f"codex-{email_l}-*.json"))

        for path in sorted(auth_candidates):
            if path.exists():
                path.unlink()
                cleanup["local_auth_files"].append(path.name)
                logger.info("[账号] 已删除本地 auth: %s", path.name)

        remote_cleanup = delete_account_from_configured_targets(
            email,
            auth_names=list(cleanup["local_auth_files"]),
            include_disabled=True,
        )
        cleanup["cpa_files"] = list((remote_cleanup.get("cpa") or {}).get("deleted", []))
        cleanup["sub2api_accounts"] = list((remote_cleanup.get("sub2api") or {}).get("deleted", []))

        if acc:
            accounts = [item for item in accounts if item["email"].lower() != email_l]
            save_accounts(accounts)
            cleanup["local_record"] = True
            logger.info("[账号] 已删除本地记录: %s", email)

            mail_account_id = _account_mail_account_id(acc)
            if remove_cloudmail and mail_account_id is not None:
                try:
                    if mail_client is None:
                        own_mail_client = _get_mail_client_for_account(acc)
                        mail_client = own_mail_client
                    resp = mail_client.delete_account(mail_account_id)
                    if resp.get("code") == 200:
                        cleanup["cloudmail_deleted"] = True
                except Exception as exc:
                    logger.warning("[账号] 删除 CloudMail 账户失败: %s", exc)

        if sync_cpa_after:
            sync_to_cpa()

        return cleanup
    finally:
        if own_chatgpt:
            own_chatgpt.stop()
