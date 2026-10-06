# YouTrack HTTP + URL guards (no Sublime commands).
# Imported by notes_plugin. Sublime may also load this file as a plugin.

from __future__ import annotations

import ipaddress
import json
import re
import ssl
import threading
import time
from datetime import datetime
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse
from urllib.request import (
    HTTPRedirectHandler,
    HTTPSHandler,
    Request,
    build_opener,
    urlopen,
)

import sublime

from . import notes_common as _nc

_settings = _nc._settings
_safe_log = _nc._safe_log
_status_message = _nc._status_message
_scrub_error_text = _nc._scrub_error_text
log = _nc.log

_API_MAX_RESPONSE_BYTES = 512 * 1024
_API_MAX_RESPONSE_BYTES_LIST = 8 * 1024 * 1024


def _youtrack_base() -> str:
    base = _settings().get("youtrack_base", "").strip()
    if base and not base.endswith("/"):
        base += "/"
    return base


def _youtrack_api_root() -> str:
    base = _youtrack_base()
    if not base:
        return ""
    api_root = re.sub(r"/issues?/?$", "", base.rstrip("/"))
    return api_root + "/api"


def _youtrack_token() -> str:
    return _settings().get("youtrack_token", "").strip()


def _default_project() -> str:
    return _settings().get("default_project", "").strip().upper()


def _issue_stages() -> list[str]:
    stages = _settings().get("issue_stages", [])
    if not isinstance(stages, list):
        return []
    return [str(s).strip() for s in stages if str(s).strip()]


def _api_timeout() -> int:
    val = _settings().get("api_timeout_sec", 10)
    try:
        return max(3, min(60, int(val)))
    except (TypeError, ValueError):
        return 10


def _api_max_retries() -> int:
    val = _settings().get("api_max_retries", 2)
    try:
        return max(0, min(5, int(val)))
    except (TypeError, ValueError):
        return 2


def _post_comments_enabled() -> bool:
    """Global toggle: whether to post YouTrack comments when adding notes."""
    val = _settings().get("post_comments", False)
    if isinstance(val, bool):
        return val
    return False


def _gitlab_base() -> str:
    base = _settings().get("gitlab_base", "").strip()
    if base and not base.endswith("/"):
        base += "/"
    return base


def _gitlab_api_root() -> str:
    base = _gitlab_base()
    if not base:
        return ""
    return base.rstrip("/") + "/api/v4"


def _gitlab_token() -> str:
    return _settings().get("gitlab_token", "").strip()


def _note_max_lines() -> int:
    try:
        return max(1, min(200, int(_settings().get("note_max_lines", 50))))
    except (TypeError, ValueError):
        return 50


def _note_max_line_len() -> int:
    try:
        return max(40, min(2000, int(_settings().get("note_max_line_len", 500))))
    except (TypeError, ValueError):
        return 500


def _is_blocked_ip_literal(hostname: str) -> bool:
    try:
        ip = ipaddress.ip_address(hostname)
    except ValueError:
        return False
    if (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    ):
        return True
    # CGNAT 100.64.0.0/10 (is_private covers this on modern Python; keep explicit)
    if isinstance(ip, ipaddress.IPv4Address):
        return ipaddress.IPv4Address("100.64.0.0") <= ip <= ipaddress.IPv4Address("100.127.255.255")
    return False


def _validate_youtrack_base(base: str) -> str | None:
    if not base:
        return None

    parsed = urlparse(base)

    if parsed.scheme != "https":
        return (
            "youtrack_base must start with https://\n"
            "Plain http is rejected because the Bearer token would be "
            "transmitted in cleartext."
        )

    hostname = (parsed.hostname or "").lower()
    if not hostname:
        return "youtrack_base has no hostname."

    if _is_blocked_ip_literal(hostname):
        return (
            f"youtrack_base hostname '{hostname}' is a loopback/private/"
            "link-local address.\n"
            "Point youtrack_base at your public YouTrack instance."
        )

    _BLOCKED_EXACT = {
        "localhost",
        "metadata.google.internal",
        "kubernetes.default",
        "kubernetes.default.svc",
    }
    if hostname in _BLOCKED_EXACT or hostname.endswith(".local"):
        return (
            f"youtrack_base hostname '{hostname}' is blocked.\n"
            "Point youtrack_base at your public YouTrack instance."
        )

    _BLOCKED_PREFIXES = (
        "localhost.", "127.", "0.", "10.", "192.168.", "169.254.",
    )
    _BLOCKED_RANGES_172 = range(16, 32)

    if any(hostname == p.rstrip(".") or hostname.startswith(p) for p in _BLOCKED_PREFIXES):
        return (
            f"youtrack_base hostname '{hostname}' is a loopback or private address.\n"
            "Point youtrack_base at your public YouTrack instance."
        )

    # Dotted hostname that is actually an IPv4 string already handled; also
    # reject 172.16–31.* and 100.64–127.* when written as DNS-looking labels.
    parts = hostname.split(".")
    if len(parts) == 4 and all(p.isdigit() for p in parts):
        if _is_blocked_ip_literal(hostname):
            return (
                f"youtrack_base hostname '{hostname}' is a private address.\n"
                "Point youtrack_base at your public YouTrack instance."
            )
    if (
        len(parts) >= 2
        and parts[0] == "172"
        and parts[1].isdigit()
        and int(parts[1]) in _BLOCKED_RANGES_172
    ):
        return (
            f"youtrack_base hostname '{hostname}' is in a private IP range.\n"
            "Point youtrack_base at your public YouTrack instance."
        )
    if (
        len(parts) >= 2
        and parts[0] == "100"
        and parts[1].isdigit()
        and 64 <= int(parts[1]) <= 127
    ):
        return (
            f"youtrack_base hostname '{hostname}' is in the CGNAT range.\n"
            "Point youtrack_base at your public YouTrack instance."
        )

    return None


def _youtrack_host() -> str:
    base = _youtrack_base()
    if not base:
        return ""
    try:
        return urlparse(base).hostname or ""
    except Exception:
        return ""


def _is_youtrack_host(url: str) -> bool:
    yt_host = _youtrack_host()
    if not yt_host:
        return False
    try:
        url_host = urlparse(url).hostname or ""
    except Exception:
        return False
    return url_host.lower() == yt_host.lower()


def _validate_gitlab_base(base: str) -> str | None:
    """Reuse YouTrack HTTPS / private-host rules for gitlab_base."""
    if not base:
        return None
    as_yt_shape = base if "/issue" in base else base.rstrip("/") + "/issue/"
    return _validate_youtrack_base(as_yt_shape)


def _gitlab_host() -> str:
    base = _gitlab_base()
    if not base:
        return ""
    try:
        return urlparse(base).hostname or ""
    except Exception:
        return ""


def _is_gitlab_host(url: str) -> bool:
    gl_host = _gitlab_host()
    if not gl_host:
        return False
    try:
        url_host = urlparse(url).hostname or ""
    except Exception:
        return False
    return url_host.lower() == gl_host.lower()



def _ssl_context() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.check_hostname = True
    ctx.verify_mode    = ssl.CERT_REQUIRED
    return ctx


# ---------------------------------------------------------------------------
# Rate-limit guard
# ---------------------------------------------------------------------------

_api_rate_lock      = threading.Lock()
_api_last_call_time: float = 0.0
_API_MIN_INTERVAL   = 0.1


def _api_rate_wait() -> None:
    global _api_last_call_time
    with _api_rate_lock:
        now     = time.monotonic()
        elapsed = now - _api_last_call_time
        if elapsed < _API_MIN_INTERVAL:
            time.sleep(_API_MIN_INTERVAL - elapsed)
        _api_last_call_time = time.monotonic()


# ---------------------------------------------------------------------------
# YouTrack REST API — shared HTTP helper
# ---------------------------------------------------------------------------

_NOT_FOUND: dict = {"__not_found__": True}


class _ApiError(dict):
    pass


def _make_api_error(status: int, body: str) -> _ApiError:
    try:
        data = json.loads(body)
        desc = data.get("error_description") or data.get("error") or body[:300]
    except Exception:
        desc = body[:300] if body else f"HTTP {status}"
    return _ApiError({"__api_error__": True, "status": status, "description": desc})


def _is_api_error(obj: object) -> bool:
    return isinstance(obj, _ApiError)



class _RejectRedirectHandler(HTTPRedirectHandler):
    """Refuse redirects so Bearer tokens are never forwarded to another origin."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise URLError(
            f"Refusing HTTP {code} redirect from YouTrack API to {newurl}"
        )


def _yt_urlopen(req: Request, timeout: int, ctx: ssl.SSLContext):
    opener = build_opener(HTTPSHandler(context=ctx), _RejectRedirectHandler())
    return opener.open(req, timeout=timeout)


def _yt_request(
    method: str,
    path: str,
    body: dict | None = None,
    params: str = "",
) -> dict | list | None:
    api_root = _youtrack_api_root()
    token    = _youtrack_token()
    if not api_root or not token:
        return None

    base_err = _validate_youtrack_base(_youtrack_base())
    if base_err:
        log.error("YouTrack base URL rejected: %s", base_err)
        return None

    url = f"{api_root}{path}"
    if params:
        url += ("&" if "?" in url else "?") + params

    data    = json.dumps(body).encode("utf-8") if body is not None else None
    timeout = _api_timeout()
    retries = _api_max_retries()
    ctx     = _ssl_context()

    for attempt in range(retries + 1):
        _api_rate_wait()

        req = Request(
            url,
            data=data,
            method=method,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept":        "application/json",
                "Content-Type":  "application/json",
                "Cache-Control": "no-cache",
            },
        )
        try:
            with _yt_urlopen(req, timeout=timeout, ctx=ctx) as resp:
                raw = resp.read(_API_MAX_RESPONSE_BYTES).decode("utf-8")
                return json.loads(raw) if raw.strip() else {}

        except HTTPError as exc:
            if exc.code == 404:
                return _NOT_FOUND

            if exc.code in (429, 502, 503) and attempt < retries:
                wait = 2 ** attempt
                log.warning(
                    "YouTrack: HTTP %s (attempt %d/%d), retrying in %ds",
                    exc.code, attempt + 1, retries + 1, wait,
                )
                time.sleep(wait)
                continue

            try:
                err_body = exc.read().decode("utf-8", errors="replace")
            except Exception:
                err_body = ""
            _safe_log(
                "warning",
                "YouTrack API HTTP %s for %s %s  body: %s",
                exc.code, method, path, err_body[:200],
            )
            return _make_api_error(exc.code, err_body)

        except (URLError, OSError, json.JSONDecodeError) as exc:
            if attempt < retries:
                wait = 2 ** attempt
                _safe_log(
                    "warning",
                    "YouTrack API %s %s transient error (attempt %d/%d): %s",
                    method, path, attempt + 1, retries + 1, exc,
                )
                time.sleep(wait)
                continue
            _safe_log("warning", "YouTrack API %s %s error: %s", method, path, exc)
            return None

    return None


# ---------------------------------------------------------------------------
# YouTrack — current user cache
# ---------------------------------------------------------------------------

_current_user_lock  = threading.Lock()
_CURRENT_USER_CACHE: dict[str, str] = {}


def _fetch_current_user() -> dict[str, str]:
    """Cached YouTrack ``/users/me``: login, fullName, email."""
    with _current_user_lock:
        if _CURRENT_USER_CACHE.get("login") and "email" in _CURRENT_USER_CACHE:
            return dict(_CURRENT_USER_CACHE)

    result = _yt_request("GET", "/users/me", params="fields=login,fullName,email")
    if not result or not isinstance(result, dict) or _is_api_error(result):
        return {}

    login = (result.get("login") or "").strip()
    fullname = (result.get("fullName") or "").strip()
    email = (result.get("email") or "").strip()
    if not login:
        return {}
    with _current_user_lock:
        _CURRENT_USER_CACHE["login"] = login
        _CURRENT_USER_CACHE["fullName"] = fullname
        _CURRENT_USER_CACHE["email"] = email
        return dict(_CURRENT_USER_CACHE)


def _fetch_current_user_login() -> tuple[str | None, str | None]:
    user = _fetch_current_user()
    login = (user.get("login") or "").strip() or None
    fullname = (user.get("fullName") or "").strip() or None
    return login, fullname


def _cached_assignee_display() -> str:
    """UI-thread safe: never hits the network."""
    with _current_user_lock:
        return (
            _CURRENT_USER_CACHE.get("email")
            or _CURRENT_USER_CACHE.get("login")
            or ""
        ).strip()


def _default_assignee_display() -> str:
    """Email for the create-issue slice; login if YouTrack has no email.

    Call only from a worker (`set_timeout_async`).
    """
    user = _fetch_current_user()
    return (user.get("email") or user.get("login") or "").strip()


# ---------------------------------------------------------------------------
# YouTrack — fetch ticket info
# ---------------------------------------------------------------------------

_YT_FIELDS = (
    "summary,"
    "description,"
    "idReadable,"
    "created,"
    "updated,"
    "project(shortName),"
    "reporter(fullName,login,email),"
    "customFields("
      "name,"
      "value(name,fullName,presentation,login)"
    ")"
)

_YT_LIST_FIELDS = (
    "idReadable,"
    "summary,"
    "created,"
    "updated,"
    "reporter(fullName,login),"
    "customFields("
      "name,"
      "value(name,fullName,presentation,login)"
    ")"
)


def _fetch_youtrack_issue(ticket_id: str) -> dict | None:
    result = _yt_request(
        "GET",
        f"/issues/{quote(ticket_id)}",
        params=f"fields={quote(_YT_FIELDS)}",
    )
    if _is_api_error(result):
        return None
    return result  # type: ignore[return-value]


def _fetch_my_open_issues(project: str) -> list[dict]:
    """Fetch issues assigned to me (used by TODO scratch view)."""
    if not project:
        return []
    query = f"for: me #Unresolved project: {{{project}}}"
    result = _yt_request(
        "GET",
        "/issues",
        params=(
            f"query={quote(query)}"
            f"&fields={quote(_YT_LIST_FIELDS)}"
            f"&$top=200"
        ),
    )
    if result is None or result is _NOT_FOUND or _is_api_error(result):
        return []
    if not isinstance(result, list):
        return []
    return result


# ---------------------------------------------------------------------------
# YouTrack — shared list fetch helper (large buffer, safe encoding)
# ---------------------------------------------------------------------------

def _fetch_issues_list(
    query: str,
    top: int = 300,
) -> tuple[list[dict], str | None]:
    """
    Generic helper: fetch a list of YouTrack issues using the given query.
    Uses _API_MAX_RESPONSE_BYTES_LIST (8 MB) to prevent truncation on large
    projects.  Returns (issues, error_message).
    Sorted newest-updated first (client-side).
    """
    api_root = _youtrack_api_root()
    token    = _youtrack_token()
    if not api_root or not token:
        return [], "YouTrack is not configured (missing token or base URL)"

    base_err = _validate_youtrack_base(_youtrack_base())
    if base_err:
        return [], base_err

    # safe='' encodes ALL special chars: spaces, {, }, :, #
    url = (
        f"{api_root}/issues"
        f"?query={quote(query, safe='')}"
        f"&fields={quote(_YT_LIST_FIELDS, safe='')}"
        f"&$top={top}"
    )

    timeout = _api_timeout()
    retries = _api_max_retries()
    ctx     = _ssl_context()
    last_error_msg: str = "Unknown error"
    raw: str = ""

    for attempt in range(retries + 1):
        _api_rate_wait()
        req = Request(
            url,
            method="GET",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept":        "application/json",
                "Content-Type":  "application/json",
                "Cache-Control": "no-cache",
            },
        )
        try:
            with _yt_urlopen(req, timeout=timeout, ctx=ctx) as resp:
                # Use large buffer — list responses for big projects can be
                # several MB; truncation causes JSONDecodeError mid-object.
                raw  = resp.read(_API_MAX_RESPONSE_BYTES_LIST).decode("utf-8")
                data = json.loads(raw) if raw.strip() else []
                if not isinstance(data, list):
                    return [], "Unexpected response format from YouTrack"
                data.sort(
                    key=lambda i: i.get("updated") or i.get("created") or 0,
                    reverse=True,
                )
                return data, None

        except HTTPError as exc:
            try:
                err_body = exc.read().decode("utf-8", errors="replace")
            except Exception:
                err_body = ""
            if exc.code == 400:
                _safe_log(
                    "warning",
                    "ST4Notes fetch_issues_list HTTP 400 query=%r body=%s",
                    query, err_body[:400],
                )
                # Do not put full API URLs / raw bodies in UI dialogs
                return [], (
                    f"HTTP 400 — query rejected by YouTrack.\n\n"
                    f"Query: {query}\n\n"
                    f"Response: {_scrub_error_text(err_body[:300])}"
                )
            if exc.code == 404:
                return [], "Project not found (HTTP 404)"
            if exc.code == 401:
                return [], "Authentication failed (HTTP 401) — check youtrack_token"
            if exc.code == 403:
                return [], "Permission denied (HTTP 403) — token lacks Read Issue"
            if exc.code in (429, 502, 503) and attempt < retries:
                time.sleep(2 ** attempt)
                last_error_msg = f"HTTP {exc.code}"
                continue
            last_error_msg = (
                f"HTTP {exc.code}: {_scrub_error_text(err_body[:200])}"
            )

        except (TimeoutError, URLError, OSError) as exc:
            last_error_msg = _scrub_error_text(f"Cannot reach YouTrack: {exc}")
            if attempt < retries:
                time.sleep(2 ** attempt)
                continue

        except json.JSONDecodeError as exc:
            _safe_log(
                "warning",
                "ST4Notes fetch_issues_list: JSON decode failed. Error: %s Raw: %.200s",
                exc, raw,
            )
            return [], (
                "JSON decode error from YouTrack (response truncated or corrupt).\n"
                "Check View → Show Console for scrubbed details."
            )

    return [], last_error_msg


# ---------------------------------------------------------------------------
# YouTrack — concrete list fetch functions
# ---------------------------------------------------------------------------

def _fetch_issues_list_try(queries: list[str], top: int) -> tuple[list[dict], str | None]:
    last_err: str | None = "YouTrack returned no issues"
    for query in queries:
        issues, err = _fetch_issues_list(query, top=top)
        if issues:
            return issues, None
        if err is None:
            return [], None
        last_err = err
        low = err.lower()
        if "query rejected" in low or "http 400" in low:
            continue
        return [], err
    return [], last_err


def _fetch_project_issues_for_import(project: str) -> tuple[list[dict], str | None]:
    """
    Fetch all unresolved issues in the project (not just assigned to me).
    Used by: Import from YouTrack (all).
    Returns (issues, error_message).  Sorted newest-updated first.
    """
    if not project:
        return [], "default_project is not configured"
    return _fetch_issues_list_try(
        [
            f"#Unresolved project: {{{project}}}",
            f"#Unresolved project: {project}",
            f"State: Unresolved project: {{{project}}}",
        ],
        top=300,
    )


def _fetch_my_assigned_issues_for_import(project: str) -> tuple[list[dict], str | None]:
    """
    Fetch unresolved issues assigned to the current user.
    Used by: Import from YouTrack (assigned to me).
    Returns (issues, error_message).  Sorted newest-updated first.
    """
    if not project:
        return [], "default_project is not configured"
    return _fetch_issues_list_try(
        [
            f"for: me #Unresolved project: {{{project}}}",
            f"Assignee: me #Unresolved project: {{{project}}}",
            f"for: me #Unresolved project: {project}",
            f"Assignee: me State: Unresolved project: {{{project}}}",
        ],
        top=200,
    )


def _issue_search_blob(issue: dict) -> str:
    """Plain text used for explicit substring filtering (not ST fuzzy)."""
    parsed = _parse_youtrack_issue(issue) if issue else {}
    parts = [
        issue.get("idReadable") or "",
        issue.get("summary") or "",
        parsed.get("state") or "",
        parsed.get("assignee") or "",
        parsed.get("assignee_login") or "",
    ]
    return " ".join(parts).casefold()


def _filter_issues_by_text(issues: list[dict], query: str) -> list[dict]:
    """
    Narrow issues with explicit substring match.
    Every whitespace-separated token must appear somewhere in id/summary/state/assignee.
    Empty query → unchanged list.
    """
    q = (query or "").strip().casefold()
    if not q:
        return list(issues)
    tokens = [t for t in q.split() if t]
    if not tokens:
        return list(issues)
    out: list[dict] = []
    for issue in issues:
        blob = _issue_search_blob(issue)
        if all(tok in blob for tok in tokens):
            out.append(issue)
    return out


# ---------------------------------------------------------------------------
# YouTrack — fetch unassigned issues
# ---------------------------------------------------------------------------

# States excluded from the unassigned view — mirrors your UI query exactly.
_UNASSIGNED_EXCLUDE_STATES: list[str] = [
    "Done",
    "In Progress",
    "In review",
    "On hold",
    "In PM review",
    "Code Review",
    "Ready For Review",
    "Resolved",
    "Closed",
    "Cannot Reproduce",
    "Fixed",
]


def _build_unassigned_query(project: str) -> str:
    """
    Build the YouTrack search query for unassigned open issues.

    Equivalent UI query:
        project: MyProject Assignee: Unassigned
        State: -Done, -{In Progress}, -{In review}, ...

    Each multi-word state must be wrapped in braces: -{In Progress}
    Single-word states are also wrapped for consistency.
    """
    exclude_clauses = " ".join(
        f"-{{{s}}}" for s in _UNASSIGNED_EXCLUDE_STATES
    )
    return (
        f"project: {{{project}}} "
        f"Assignee: Unassigned "
        f"State: {exclude_clauses}"
    )


def _fetch_unassigned_issues(project: str) -> tuple[list[dict], str | None]:
    """
    Fetch unassigned open issues, excluding resolved/done/in-progress states.
    Returns (issues, error_message).  Sorted newest-updated first.
    """
    if not project:
        return [], "default_project is not configured"
    query = _build_unassigned_query(project)
    return _fetch_issues_list(query, top=200)


# ---------------------------------------------------------------------------
# YouTrack — fetch ALL issues for a project
# ---------------------------------------------------------------------------

def _fetch_all_project_issues(project: str) -> tuple[list[dict], str | None]:
    """
    Fetch ALL unresolved issues for the given project (any assignee).
    Returns (issues, error_message).  Sorted newest-updated first.

    NOTE: Does NOT wrap single-word project names in braces.
    {PROJ} causes HTTP 400 on standard YouTrack REST endpoints.
    Only multi-word project names need braces, e.g. {My Project}.
    """
    if not project:
        return [], "default_project is not configured"
    proj_token = f"{{{project}}}" if " " in project else project
    query = f"project: {proj_token} #Unresolved"
    return _fetch_issues_list(query, top=500)


# ---------------------------------------------------------------------------
# YouTrack — my recently resolved / closed issues
# ---------------------------------------------------------------------------

# Finished-state keywords (case-insensitive substring match on State name).
# Used as a client-side safety net when #Resolved is unavailable / too broad.
_FINISHED_STATE_HINTS: tuple[str, ...] = (
    "done",
    "closed",
    "resolved",
    "fixed",
    "won't fix",
    "wont fix",
    "duplicate",
    "obsolete",
    "cancelled",
    "canceled",
    "rejected",
    "cannot reproduce",
    "complete",
    "finished",
    "declined",
)


def _is_finished_state(state: str) -> bool:
    s = (state or "").strip().lower()
    if not s:
        return False
    return any(h in s for h in _FINISHED_STATE_HINTS)


def _issue_updated_ms(issue: dict) -> int:
    try:
        return int(issue.get("updated") or issue.get("created") or 0)
    except (TypeError, ValueError):
        return 0


def _issue_updated_label(issue: dict) -> str:
    ms = _issue_updated_ms(issue)
    if not ms:
        return ""
    try:
        return datetime.fromtimestamp(ms / 1000).strftime("%Y-%m-%d %H:%M")
    except (ValueError, OSError, OverflowError):
        return ""


def _fetch_my_recently_resolved(
    project: str,
) -> tuple[list[dict], str | None]:
    """
    Resolved / closed issues assigned to me in ``project``, newest-updated first.

    Prefer YouTrack ``#Resolved`` (all workflow states marked resolved). Fall back
    to an explicit State list if that query is rejected.
    """
    if not project:
        return [], "default_project is not configured"
    proj_token = _yt_query_token(project)

    # #Resolved = all states in the "resolved" category for the project.
    # for: me   = currently assigned to the authenticated user.
    primary = f"project: {proj_token} #Resolved for: me"
    issues, err = _fetch_issues_list(primary, top=200)
    used_resolved_tag = not (err and not issues)
    if err and "400" in err:
        # Older / custom workflows: list common finished state names.
        state_clause = " ".join(
            f"{{{s}}}"
            for s in (
                "Done",
                "Resolved",
                "Closed",
                "Fixed",
                "Cannot Reproduce",
                "Won't fix",
                "Duplicate",
                "Obsolete",
                "Cancelled",
                "Canceled",
            )
        )
        fallback = (
            f"project: {proj_token} for: me State: {state_clause}"
        )
        issues, err = _fetch_issues_list(fallback, top=200)
        used_resolved_tag = False

    if err and not issues:
        return [], err

    # When falling back to an explicit State list, drop anything that still
    # looks open. With #Resolved, trust YouTrack's resolved category.
    if not used_resolved_tag:
        filtered: list[dict] = []
        for issue in issues or []:
            if not isinstance(issue, dict):
                continue
            parsed = _parse_youtrack_issue(issue)
            state = parsed.get("state") or ""
            if state and not _is_finished_state(state):
                continue
            filtered.append(issue)
        issues = filtered

    issues = [i for i in (issues or []) if isinstance(i, dict)]
    issues.sort(key=_issue_updated_ms, reverse=True)
    return issues, None


def _yt_query_token(value: str) -> str:
    """Brace-wrap a YouTrack query token when it contains spaces / special chars."""
    v = (value or "").strip()
    if not v:
        return v
    if any(ch in v for ch in " \t:{}()"):
        return "{" + v.replace("}", "") + "}"
    return v


def _fetch_issues_assigned_to(
    project: str, login: str
) -> tuple[list[dict], str | None]:
    """
    Unresolved issues in project assigned to ``login`` (YouTrack ``for:``).
    """
    if not project:
        return [], "default_project is not configured"
    login = (login or "").strip().lstrip("@")
    if not login:
        return _fetch_all_project_issues(project)
    proj_token = _yt_query_token(project)
    login_token = _yt_query_token(login)
    query = f"project: {proj_token} #Unresolved for: {login_token}"
    return _fetch_issues_list(query, top=500)


# ---------------------------------------------------------------------------
# YouTrack — users (for @assignee filter)
# ---------------------------------------------------------------------------

_users_cache_lock = threading.Lock()
_USERS_CACHE: list[dict] | None = None


def _invalidate_users_cache() -> None:
    global _USERS_CACHE
    with _users_cache_lock:
        _USERS_CACHE = None


def _assignees_from_issues(issues: list[dict] | None) -> list[dict]:
    """
    Unique assignees found on the given issues (project-scoped).
    Returns list of {login, fullName} sorted by login.
    """
    by_key: dict[str, dict] = {}
    for issue in issues or []:
        if not isinstance(issue, dict):
            continue
        parsed = _parse_youtrack_issue(issue)
        login = (parsed.get("assignee_login") or "").strip()
        if not login:
            continue
        full = (parsed.get("assignee") or "").strip()
        if full.lower() == login.lower():
            full = ""
        key = login.lower()
        prev = by_key.get(key)
        if prev is None:
            by_key[key] = {"login": login, "fullName": full}
        elif full and not prev.get("fullName"):
            prev["fullName"] = full
    users = list(by_key.values())
    users.sort(key=lambda x: (x["login"] or "").lower())
    return users


def _fetch_youtrack_users(
    query: str = "",
) -> tuple[list[dict], str | None]:
    """
    List YouTrack users (instance-wide) for typed-login resolve fallback.
    Prefer ``_assignees_from_issues`` for project-scoped assignee panels.
    Returns list of {login, fullName}, error.
    """
    global _USERS_CACHE
    q = (query or "").strip().lstrip("@").lower()

    with _users_cache_lock:
        cached = list(_USERS_CACHE) if _USERS_CACHE is not None else None

    if cached is None:
        # Prefer active users; fall back to bare /users if banned filter fails.
        result = _yt_request(
            "GET",
            "/users",
            params="fields=login,fullName,banned&$top=200",
        )
        if result is None or _is_api_error(result):
            result = _yt_request(
                "GET",
                "/users",
                params="fields=login,fullName&$top=200",
            )
        if result is None:
            return [], "Could not load YouTrack users (check token / network)"
        if _is_api_error(result):
            desc = ""
            if isinstance(result, dict):
                desc = str(result.get("description") or result.get("status") or "")
            return [], desc or "YouTrack users request failed"
        if not isinstance(result, list):
            return [], "Unexpected users response from YouTrack"

        users: list[dict] = []
        for u in result:
            if not isinstance(u, dict):
                continue
            if u.get("banned") is True:
                continue
            login = (u.get("login") or "").strip()
            if not login or login.startswith("guest"):
                continue
            users.append(
                {
                    "login": login,
                    "fullName": (u.get("fullName") or "").strip(),
                }
            )
        users.sort(key=lambda x: (x["login"] or "").lower())
        with _users_cache_lock:
            _USERS_CACHE = users
        cached = users

    if not q:
        return list(cached), None

    filtered = [
        u
        for u in cached
        if q in (u.get("login") or "").lower()
        or q in (u.get("fullName") or "").lower()
    ]
    return filtered, None


# ---------------------------------------------------------------------------
# Fetch parent issue info
# ---------------------------------------------------------------------------

_PARENT_FIELDS = (
    "idReadable,"
    "summary,"
    "project(id,shortName),"
    "customFields(name,value(login,fullName,name,email))"
)


def _fetch_parent_info(ticket_id: str) -> dict | None:
    """Return parent summary / project / assignee, or None if missing."""
    tid = _pure.normalize_ticket_id(ticket_id)
    if not tid:
        return None
    result = _yt_request(
        "GET",
        f"/issues/{quote(tid)}",
        params=f"fields={quote(_PARENT_FIELDS)}",
    )
    if not result or result is _NOT_FOUND or _is_api_error(result):
        return None
    if not isinstance(result, dict):
        return None
    parsed = _parse_youtrack_issue(result)
    proj = ""
    project = result.get("project") or {}
    if isinstance(project, dict):
        proj = (project.get("shortName") or "").strip()
    return {
        "idReadable": (result.get("idReadable") or tid).strip(),
        "summary": (result.get("summary") or parsed.get("summary") or "").strip(),
        "project": proj.upper(),
        "assignee_login": (parsed.get("assignee_login") or "").strip(),
    }


# ---------------------------------------------------------------------------
# YouTrack — add comment to issue
# ---------------------------------------------------------------------------

def _yt_add_comment(ticket_id: str, text: str) -> bool:
    """
    POST a plain-text comment to a YouTrack issue.
    Required permission: Create Comment.
    Returns True on success, False on any error.
    """
    if not text.strip():
        return False
    result = _yt_request(
        "POST",
        f"/issues/{quote(ticket_id)}/comments",
        body={"text": text},
        params="fields=id,text",
    )
    ok = (
        result is not None
        and result is not _NOT_FOUND
        and not _is_api_error(result)
    )
    if ok:
        log.info("YouTrack: comment added to %s", ticket_id)
    else:
        log.warning("YouTrack: failed to add comment to %s", ticket_id)
    return ok


# ---------------------------------------------------------------------------
# YouTrack — apply command (state transitions)
# ---------------------------------------------------------------------------

def _yt_apply_command(ticket_id: str, command: str) -> bool:
    """
    Apply a YouTrack command string to an issue (e.g. "State In Review", "Done").
    Returns True on success.
    """
    result = _yt_request(
        "POST",
        "/commands",
        body={
            "query":  command,
            "issues": [{"idReadable": ticket_id}],
            "silent": False,
        },
    )
    ok = (
        result is not None
        and result is not _NOT_FOUND
        and not _is_api_error(result)
    )
    if ok:
        log.info("YouTrack: applied command '%s' to %s", command, ticket_id)
    else:
        log.warning("YouTrack: failed to apply command '%s' to %s", command, ticket_id)
    return ok


# ---------------------------------------------------------------------------
# Severity sort
# ---------------------------------------------------------------------------

_SEVERITY_RANK: dict[str, int] = {
    "blocker":      0,
    "critical":     0,
    "show-stopper": 0,
    "showstopper":  0,
    "major":        1,
    "normal":       2,
    "minor":        3,
    "cosmetic":     4,
    "trivial":      4,
}


def _issue_severity_rank(issue: dict) -> int:
    for cf in issue.get("customFields") or []:
        name  = (cf.get("name") or "").lower()
        value = cf.get("value")
        if value is None:
            continue
        if name in ("priority", "severity"):
            val_name = ""
            if isinstance(value, dict):
                val_name = (
                    value.get("name") or value.get("presentation") or ""
                ).lower()
            elif isinstance(value, str):
                val_name = value.lower()
            rank = _SEVERITY_RANK.get(val_name)
            if rank is not None:
                return rank
    return 2


def _sort_issues_by_severity(issues: list[dict]) -> list[dict]:
    return sorted(issues, key=_issue_severity_rank)


# ---------------------------------------------------------------------------
# YouTrack — set issue Done
# ---------------------------------------------------------------------------

def _set_youtrack_done(ticket_id: str) -> None:
    ok = _yt_apply_command(ticket_id, "Done")
    if ok:
        sublime.set_timeout(
            lambda: sublime.status_message(
                f"Notes: YouTrack {ticket_id} -> Done"
            ),
            0,
        )
    else:
        sublime.set_timeout(
            lambda: sublime.status_message(
                f"Notes: WARNING - could not set {ticket_id} Done in YouTrack"
            ),
            0,
        )


# ---------------------------------------------------------------------------
# YouTrack — set issue In Review
# ---------------------------------------------------------------------------

def _set_youtrack_in_review(ticket_id: str) -> None:
    ok = _yt_apply_command(ticket_id, "State In Review")
    if ok:
        sublime.set_timeout(
            lambda: sublime.status_message(
                f"Notes: YouTrack {ticket_id} -> In Review"
            ),
            0,
        )
    else:
        sublime.set_timeout(
            lambda: sublime.status_message(
                f"Notes: WARNING - could not set {ticket_id} In Review in YouTrack"
            ),
            0,
        )


# ---------------------------------------------------------------------------
# YouTrack — project lookup
# ---------------------------------------------------------------------------

def _yt_get_project_id(short_name: str) -> str | None:
    result = _yt_request(
        "GET",
        "/admin/projects",
        params=f"fields=id,shortName,name&query={quote(short_name)}",
    )
    if not result or not isinstance(result, list):
        return None
    for proj in result:
        if (proj.get("shortName") or "").upper() == short_name.upper():
            return proj.get("id")
    if result:
        return result[0].get("id")
    return None


# ---------------------------------------------------------------------------
# YouTrack — subtask linking
# ---------------------------------------------------------------------------

def _yt_get_subtask_link_id(parent_id: str) -> str | None:
    result = _yt_request(
        "GET",
        f"/issues/{quote(parent_id)}/links",
        params="fields=id,direction,linkType(name,localizedName,sourceToTarget,targetToSource)",
    )
    if not result or not isinstance(result, list):
        return None
    scored: list[tuple[int, str]] = []
    for link in result:
        lid = link.get("id")
        if not lid:
            continue
        link_type = link.get("linkType") or {}
        type_name = (
            link_type.get("name") or link_type.get("localizedName") or ""
        ).lower()
        src = (link_type.get("sourceToTarget") or "").lower()
        tgt = (link_type.get("targetToSource") or "").lower()
        direction = (link.get("direction") or "").upper()
        blob = f"{type_name} {src} {tgt}"
        if not any(kw in blob for kw in ("parent", "subtask", "child")):
            continue
        score = 0
        if direction == "OUTWARD":
            score += 4
        if "parent for" in src:
            score += 3
        if "subtask" in type_name:
            score += 2
        if direction == "INWARD":
            score -= 3
        scored.append((score, lid))
    if not scored:
        return None
    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[0][1]


def _yt_issue_entity_id(ticket_id: str) -> str | None:
    result = _yt_request(
        "GET",
        f"/issues/{quote(ticket_id)}",
        params="fields=id,idReadable",
    )
    if not result or not isinstance(result, dict) or _is_api_error(result):
        return None
    return (result.get("id") or "").strip() or None


def _yt_link_as_subtask(parent_id: str, child_id: str) -> bool:
    """Attach child as a YouTrack subtask of parent. Tries REST link, then command."""
    link_id = _yt_get_subtask_link_id(parent_id)
    if link_id:
        payloads: list[dict] = [{"idReadable": child_id}]
        internal = _yt_issue_entity_id(child_id)
        if internal:
            payloads.append({"id": internal})
        for body in payloads:
            result = _yt_request(
                "POST",
                f"/issues/{quote(parent_id)}/links/{quote(link_id)}/issues",
                body=body,
                params="fields=id,idReadable",
            )
            ok = (
                result is not None
                and result is not _NOT_FOUND
                and not _is_api_error(result)
            )
            if ok:
                log.info("YouTrack: linked %s as subtask of %s", child_id, parent_id)
                return True
            log.warning(
                "YouTrack: link POST failed for %s under %s body=%s",
                child_id, parent_id, body,
            )

    if _yt_apply_command(child_id, f"subtask of {parent_id}"):
        log.info("YouTrack: command-linked %s as subtask of %s", child_id, parent_id)
        return True
    log.warning(
        "YouTrack: could not link %s as subtask of %s (REST + command failed)",
        child_id, parent_id,
    )
    return False


# ---------------------------------------------------------------------------
# YouTrack — issue creation
# ---------------------------------------------------------------------------

class IssueCreateError(Exception):
    def __init__(self, message: str, assignee_error: bool = False) -> None:
        super().__init__(message)
        self.assignee_error = assignee_error


def _yt_create_issue(
    project_short: str,
    summary: str,
    description: str,
    assignee_login: str,
) -> str:
    project_id = _yt_get_project_id(project_short)
    if not project_id:
        raise IssueCreateError(
            f"Project '{project_short}' not found.\n"
            "Check the project shortName and token Read Project permission."
        )

    custom_fields = []
    if assignee_login:
        custom_fields.append({
            "name":  "Assignee",
            "$type": "SingleUserIssueCustomField",
            "value": {"login": assignee_login},
        })

    body: dict = {
        "project": {"id": project_id},
        "summary": summary,
    }
    if description:
        body["description"] = description
    if custom_fields:
        body["customFields"] = custom_fields

    result = _yt_request(
        "POST",
        "/issues",
        body=body,
        params="fields=id,idReadable,summary",
    )

    if (
        result
        and isinstance(result, dict)
        and not _is_api_error(result)
        and result is not _NOT_FOUND
    ):
        ticket_id = result.get("idReadable")
        if ticket_id:
            return ticket_id
        raise IssueCreateError("Issue created but no idReadable returned.")

    if _is_api_error(result):
        api_err = result  # type: ignore[assignment]
        status  = api_err.get("status", 0)
        desc    = api_err.get("description", "")

        if assignee_login and status in (400, 404):
            body_no_assignee: dict = {
                "project": {"id": project_id},
                "summary": summary,
            }
            if description:
                body_no_assignee["description"] = description
            retry = _yt_request(
                "POST",
                "/issues",
                body=body_no_assignee,
                params="fields=id,idReadable,summary",
            )
            if (
                retry
                and isinstance(retry, dict)
                and not _is_api_error(retry)
                and retry is not _NOT_FOUND
                and retry.get("idReadable")
            ):
                orphan_id = retry.get("idReadable")
                raise IssueCreateError(
                    f"Assignee login '{assignee_login}' does not exist in YouTrack.\n\n"
                    f"The issue was created as {orphan_id} without an assignee.\n"
                    f"Please set the assignee manually in YouTrack.",
                    assignee_error=True,
                )
            raise IssueCreateError(
                f"HTTP {status} from YouTrack.\n\nDetails: {desc}\n\n"
                "Check: token permissions, project access, custom field values."
            )

        if status == 403:
            raise IssueCreateError(
                "Permission denied (HTTP 403).\n\n"
                "The API token does not have 'Create Issue' permission.\n"
                "Go to YouTrack -> Profile -> Authentication -> Permanent Tokens\n"
                "and ensure the token scope includes 'YouTrack' or 'Create Issue'."
            )

        raise IssueCreateError(
            f"YouTrack API error HTTP {status}.\n\nDetails: {desc}"
        )

    raise IssueCreateError(
        "Could not create issue: no response from YouTrack.\n"
        "Check your network connection and youtrack_base URL."
    )


# ---------------------------------------------------------------------------

def _parse_youtrack_issue(issue: dict) -> dict[str, str]:
    """
    Extract a flat dict of display fields from a raw YouTrack issue dict.
    Works with both _YT_FIELDS and _YT_LIST_FIELDS response shapes.
    """
    result: dict[str, str] = {}

    result["summary"] = (issue.get("summary") or "").strip()
    result["ticket"] = (issue.get("idReadable") or "").strip()
    desc = issue.get("description")
    if isinstance(desc, str):
        result["description"] = desc.strip()
    proj = issue.get("project")
    if isinstance(proj, dict):
        result["project"] = (proj.get("shortName") or "").strip()

    # created timestamp (only present in full _YT_FIELDS responses)
    created_ms = issue.get("created")
    if created_ms:
        try:
            dt = datetime.fromtimestamp(int(created_ms) / 1000)
            result["created"] = dt.strftime("%Y-%m-%d %H:%M")
        except (ValueError, OSError, OverflowError):
            pass

    # reporter (only present in full _YT_FIELDS responses)
    reporter = issue.get("reporter")
    if isinstance(reporter, dict):
        full  = (reporter.get("fullName") or "").strip()
        login = (reporter.get("login") or "").strip()
        result["reporter"] = full or login

    # custom fields
    for cf in issue.get("customFields") or []:
        name  = (cf.get("name") or "").strip() if isinstance(cf, dict) else ""
        value = cf.get("value") if isinstance(cf, dict) else None
        if value is None or not name:
            continue

        name_lower = name.lower()

        if name_lower == "assignee":
            if isinstance(value, dict):
                full  = (value.get("fullName") or "").strip()
                login = (value.get("login") or "").strip()
                result["assignee"]       = full or login
                result["assignee_login"] = login
            elif isinstance(value, str):
                result["assignee"]       = value.strip()
                result["assignee_login"] = value.strip()

        elif name_lower == "state":
            if isinstance(value, dict):
                result["state"] = (
                    value.get("name") or value.get("presentation") or ""
                ).strip()
            elif isinstance(value, str):
                result["state"] = value.strip()

        elif name_lower == "priority":
            if isinstance(value, dict):
                result["priority"] = (value.get("name") or "").strip()
            elif isinstance(value, str):
                result["priority"] = value.strip()

        elif name_lower in ("severity", "type"):
            if isinstance(value, dict):
                result["severity"] = (value.get("name") or "").strip()
            elif isinstance(value, str):
                result["severity"] = value.strip()

        elif "due" in name_lower or name_lower in ("deadline",):
            parsed_due = _cf_date_value(value)
            if parsed_due:
                result["due"] = parsed_due

    return result


def _cf_date_value(value) -> str:
    if isinstance(value, (int, float)) and float(value) > 10_000:
        try:
            return datetime.fromtimestamp(int(value) / 1000).strftime("%Y-%m-%d")
        except (ValueError, OSError, OverflowError):
            return ""
    if isinstance(value, dict):
        for k in ("presentation", "name"):
            raw = (value.get(k) or "").strip()
            if raw:
                return raw[:40]
        inner = value.get("value")
        if inner is not None and inner is not value:
            return _cf_date_value(inner)
    if isinstance(value, str):
        return value.strip()[:40]
    return ""

