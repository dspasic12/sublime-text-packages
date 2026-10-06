# GitLab MR hover helpers (no Sublime commands).

from __future__ import annotations

import hashlib
import json
import re
import time
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request

from . import notes_common as _nc
from . import notes_yt as _yt

_settings = _nc._settings
log = _nc.log
_gitlab_base = _yt._gitlab_base
_gitlab_api_root = _yt._gitlab_api_root
_gitlab_token = _yt._gitlab_token
_api_timeout = _yt._api_timeout
_api_max_retries = _yt._api_max_retries
_api_rate_wait = _yt._api_rate_wait
_validate_gitlab_base = _yt._validate_gitlab_base
_is_gitlab_host = _yt._is_gitlab_host
_yt_urlopen = _yt._yt_urlopen
_ssl_context = _yt._ssl_context
_is_api_error = _yt._is_api_error
_make_api_error = _yt._make_api_error
_API_MAX_RESPONSE_BYTES_LIST = _yt._API_MAX_RESPONSE_BYTES_LIST
_NOT_FOUND = _yt._NOT_FOUND
_h = _nc._h
_hover_sep_style = _nc._hover_sep_style
_hover_body_style = _nc._hover_body_style
_hover_pre_style = _nc._hover_pre_style

def _gitlab_diff_file_hash(file_path: str) -> str:
    """GitLab MR diffs anchor id: #diff-content-{sha1(path)}."""
    return hashlib.sha1(file_path.encode("utf-8")).hexdigest()


def _mr_file_diffs_url(mr_url: str, file_path: str) -> str:
    """URL to MR Changes tab scrolled to ``file_path``."""
    base = (mr_url or "").split("#", 1)[0].rstrip("/")
    if base.endswith("/diffs"):
        base = base[: -len("/diffs")]
    h = _gitlab_diff_file_hash(file_path)
    return f"{base}/diffs#diff-content-{h}"


# ---------------------------------------------------------------------------

_GITLAB_MR_URL_RE = re.compile(
    r"^https://([^/]+)/(.+)/-/merge_requests/(\d+)(?:[/?#]|$)",
    re.IGNORECASE,
)


def _parse_gitlab_mr_url(url: str) -> tuple[str, str, str] | None:
    """
    Return (host, project_path, mr_iid) for a GitLab MR URL, or None.
    project_path is URL-decoded path without leading slash
    (e.g. group/subgroup/project).
    """
    m = _GITLAB_MR_URL_RE.match(url.strip())
    if not m:
        return None
    host = m.group(1).lower()
    project_path = m.group(2).strip("/")
    iid = m.group(3)
    if not project_path or not iid:
        return None
    return host, project_path, iid


def _gitlab_request(path: str, params: str = "") -> dict | list | None:
    api_root = _gitlab_api_root()
    token = _gitlab_token()
    if not api_root or not token:
        return None

    base_err = _validate_gitlab_base(_gitlab_base())
    if base_err:
        log.error("GitLab base URL rejected: %s", base_err)
        return None

    url = f"{api_root}{path}"
    if params:
        url += ("&" if "?" in url else "?") + params

    timeout = _api_timeout()
    retries = _api_max_retries()
    ctx = _ssl_context()

    for attempt in range(retries + 1):
        _api_rate_wait()
        req = Request(
            url,
            method="GET",
            headers={
                "PRIVATE-TOKEN": token,
                "Accept": "application/json",
                "Cache-Control": "no-cache",
            },
        )
        try:
            with _yt_urlopen(req, timeout=timeout, ctx=ctx) as resp:
                raw = resp.read(_API_MAX_RESPONSE_BYTES_LIST).decode("utf-8")
                return json.loads(raw) if raw.strip() else {}
        except HTTPError as exc:
            if exc.code == 404:
                return _NOT_FOUND
            if exc.code in (429, 502, 503) and attempt < retries:
                time.sleep(1 + attempt)
                continue
            log.warning("GitLab API HTTP %s for GET %s", exc.code, path)
            return None
        except Exception as exc:
            if attempt < retries:
                time.sleep(1 + attempt)
                continue
            _safe_log("warning", "GitLab API error GET %s: %s", path, exc)
            return None
    return None


def _pipeline_color(status: str) -> str:
    s = (status or "").lower()
    if s in ("success", "passed"):
        return "#98c379"
    if s in ("failed", "canceled", "cancelled"):
        return "#e06c75"
    if s in ("running", "pending", "created", "waiting_for_resource", "preparing"):
        return "#e5c07b"
    if s in ("skipped", "manual"):
        return "#5c6370"
    return "#abb2bf"


def _mr_state_color(state: str) -> str:
    s = (state or "").lower()
    if s == "merged":
        return "#c678dd"
    if s == "opened" or s == "open":
        return "#98c379"
    if s == "closed":
        return "#e06c75"
    return "#abb2bf"


def _fetch_gitlab_mr_info(project_path: str, iid: str) -> dict | None:
    proj = quote(project_path, safe="")
    mr = _gitlab_request(
        f"/projects/{proj}/merge_requests/{iid}",
        params=(
            "include_diverged_commits_count=false"
            "&include_rebase_in_progress=false"
        ),
    )
    if mr is _NOT_FOUND:
        return {"__not_found__": True}
    if not isinstance(mr, dict) or not mr:
        return None

    state = str(mr.get("state") or "")
    title = str(mr.get("title") or "")
    draft = bool(mr.get("draft") or mr.get("work_in_progress"))

    pipe = mr.get("head_pipeline") or {}
    if not isinstance(pipe, dict):
        pipe = {}
    pipe_status = str(pipe.get("status") or "")
    pipe_web = str(pipe.get("web_url") or "")

    # Detailed merge / approval status when present
    detailed = str(
        mr.get("detailed_merge_status")
        or mr.get("merge_status")
        or ""
    )

    changes = _gitlab_request(
        f"/projects/{proj}/merge_requests/{iid}/changes",
        params="access_raw_diffs=false",
    )
    # Each entry: {label, path} — path is used for GitLab diffs deep-link / local open
    files: list[dict] = []
    if isinstance(changes, dict):
        ch_list = changes.get("changes") or []
        if isinstance(ch_list, list):
            for ch in ch_list[:40]:
                if not isinstance(ch, dict):
                    continue
                new_p = str(ch.get("new_path") or ch.get("old_path") or "")
                old_p = str(ch.get("old_path") or "")
                # Prefer new_path for anchor hash (matches GitLab UI for non-deleted)
                link_path = new_p or old_p
                if ch.get("new_file"):
                    label = f"+ {new_p}"
                elif ch.get("deleted_file"):
                    link_path = old_p or new_p
                    label = f"- {link_path}"
                elif ch.get("renamed_file") and old_p and new_p and old_p != new_p:
                    label = f"~ {old_p} → {new_p}"
                    link_path = new_p
                else:
                    label = f"  {new_p}"
                if link_path:
                    files.append({"label": label, "path": link_path})
            total = len(ch_list)
            if total > len(files):
                files.append(
                    {
                        "label": f"… +{total - len(files)} more",
                        "path": "",
                    }
                )

    return {
        "title": title,
        "state": state,
        "draft": draft,
        "detailed_merge_status": detailed,
        "pipeline_status": pipe_status,
        "pipeline_url": pipe_web,
        "files": files,
        "author": ((mr.get("author") or {}) if isinstance(mr.get("author"), dict) else {}).get("username", ""),
        "source_branch": str(mr.get("source_branch") or ""),
        "target_branch": str(mr.get("target_branch") or ""),
    }


def _build_mr_hover_html(url: str, info: dict | None, not_found: bool = False) -> str:
    parsed = _parse_gitlab_mr_url(url)
    mr_label = f"!{parsed[2]}" if parsed else url
    link_html = (
        f"<a href='open:{_h(url)}' "
        f"style='color:#56b6c2;text-decoration:underline'>{_h(mr_label)}</a>"
    )
    sep = f"<div style='{_hover_sep_style()}'></div>"

    if not_found:
        body = (
            "<span style='color:#e06c75'>MR not found</span> "
            "<span style='color:#5c6370'>(404)</span>"
        )
        return (
            "<body id='stnotes-hover' "
            f"style='{_hover_body_style()}'>"
            + link_html + sep + body + "</body>"
        )

    if info is None:
        hint = ""
        if not _gitlab_token() or not _gitlab_base():
            hint = (
                "<div style='color:#5c6370;margin-top:6px'>"
                "Set gitlab_base + gitlab_token in ST4Notes settings for MR details."
                "</div>"
            )
        return (
            "<body id='stnotes-hover' "
            f"style='{_hover_body_style()}'>"
            + link_html + hint + "</body>"
        )

    state = info.get("state", "")
    state_label = state
    if info.get("draft"):
        state_label = f"{state} (draft)" if state else "draft"

    pipe = info.get("pipeline_status", "") or "(none)"
    rows: list[tuple[str, str, str]] = [
        ("title", info.get("title", ""), "#cdd9e5"),
        ("status", state_label, _mr_state_color(state)),
        ("pipeline", pipe, _pipeline_color(info.get("pipeline_status", ""))),
    ]
    if info.get("detailed_merge_status"):
        rows.append(
            ("merge", str(info.get("detailed_merge_status")), "#abb2bf")
        )
    if info.get("source_branch") and info.get("target_branch"):
        rows.append(
            (
                "branch",
                f"{info['source_branch']} → {info['target_branch']}",
                "#5c6370",
            )
        )
    if info.get("author"):
        rows.append(("author", str(info.get("author")), "#6699cc"))

    col_w = max(len(lbl) for lbl, _, _ in rows) + 2
    rows_html = "\n".join(
        f"<span style='color:#5c6370'>{_h((lbl + ':').ljust(col_w))}</span>"
        f"<span style='color:{col}'>{_h(val)}</span>"
        for lbl, val, col in rows
        if val
    )

    files = info.get("files") or []
    files_html = ""
    if files:
        # Count only real file entries (exclude the “… +N more” placeholder)
        real_count = sum(
            1
            for f in files
            if (isinstance(f, dict) and f.get("path"))
            or (isinstance(f, str) and not str(f).startswith("…"))
        )
        shown = files[:25]
        flines_parts: list[str] = []
        for f in shown:
            if isinstance(f, dict):
                label = str(f.get("label") or f.get("path") or "")
                path = str(f.get("path") or "")
            else:
                label = str(f)
                path = ""
            if not path:
                flines_parts.append(
                    f"<div style='color:#5c6370'>{_h(label)}</div>"
                )
                continue
            diffs_url = _mr_file_diffs_url(url, path)
            flines_parts.append(
                f"<div><a href='open:{_h(diffs_url)}' "
                f"style='color:#abb2bf;text-decoration:none'>"
                f"{_h(label)}</a></div>"
            )
        flines = "\n".join(flines_parts)
        more = ""
        if len(files) > 25:
            more = (
                f"<div style='color:#5c6370'>"
                f"… {len(files) - 25} more</div>"
            )
        files_html = (
            sep
            + f"<div style='color:#5c6370;margin-bottom:4px'>"
            f"changed files ({real_count or len(files)}) "
            f"<span style='color:#3e4451'>click → GitLab diffs</span></div>"
            + f"<div style='{_hover_pre_style()};max-height:280px;overflow:hidden'>"
            f"{flines}{more}</div>"
        )

    return (
        "<body id='stnotes-hover' "
        f"style='{_hover_body_style()}'>"
        + link_html
        + sep
        + f"<div style='{_hover_pre_style()}'>{rows_html}</div>"
        + files_html
        + "</body>"
    )


