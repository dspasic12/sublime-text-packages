# =============================================================================
# notes_plugin.py  —  Sublime Text 4 package `notes` (Command Palette: .notes)
# Settings/syntax resource basename remains ST4Notes.* for backward compatibility.
# Package: notes/  (Sublime Text Packages directory)
# Requires plugin host Python 3.14 (see .python-version)
# =============================================================================

from __future__ import annotations

import os
import re
import sys
import tempfile
import logging
import threading
import webbrowser
import subprocess
from datetime import datetime, date as datetime_date, timedelta
from urllib.parse import quote, urlparse

import sublime
import sublime_plugin

from . import notes_common as _nc
from . import notes_kb_pure as _pure
from . import notes_yt as _yt
from . import notes_gitlab as _gl


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


# Shared helpers (notes_common) — keep underscore names for call sites in this file
_SETTINGS_FILE = _nc._SETTINGS_FILE
_DEFAULT_NOTES_PATH = _nc._DEFAULT_NOTES_PATH
_NOTES_MAX_FILE_BYTES = _nc._NOTES_MAX_FILE_BYTES
_SEP = _nc._SEP
_SEP_RE = _nc._SEP_RE
log = _nc.log
_settings = _nc._settings
_notes_root_jail = _nc._notes_root_jail
_assert_notes_path_in_jail = _nc._assert_notes_path_in_jail
_scrub_error_text = _nc._scrub_error_text
_safe_log = _nc._safe_log
_notes_file = _nc._notes_file
_knowledge_base_file = _nc._knowledge_base_file
_error_message = _nc._error_message
_status_message = _nc._status_message
_message_dialog = _nc._message_dialog
_h = _nc._h
_is_sep = _nc._is_sep
_is_stnotes_view = _nc._is_stnotes_view
_is_notes_scratch_view = _nc._is_notes_scratch_view
_is_weekly_summary_view = _nc._is_weekly_summary_view
_assign_stnotes_syntax = _nc._assign_stnotes_syntax
_open_scratch_view = _nc._open_scratch_view
_hover_font_family = _nc._hover_font_family
_hover_font_size = _nc._hover_font_size
_hover_line_height = _nc._hover_line_height
_hover_body_style = _nc._hover_body_style
_hover_pre_style = _nc._hover_pre_style
_hover_sep_style = _nc._hover_sep_style
_hover_code_block_style = _nc._hover_code_block_style


_TICKET_RE               = re.compile(r"^[A-Z0-9][A-Z0-9_\-]{0,63}$")
_NEW_NOTE_LABEL          = "New note"
_NEW_TICKET_LABEL        = "Issue"
_IMPORT_FROM_YT_ME_LABEL  = "Import from YouTrack (assigned to me)"
_IMPORT_FROM_YT_ALL_LABEL = "Import from YouTrack (all)"
_OPEN_BY_ID_LABEL        = "Open by ticket ID..."
_TODO_ID           = "TODO"
_OPS_ID            = "OPS"
_TODO_SEARCH_LABEL = "TODO (all days)"
_URL_RE            = re.compile(
    r"https?://[A-Za-z0-9\-._~:/?#\[\]@!$&'()*+,;=%]+"
)
# Inline ticket pattern: e.g. PROJ-1234, ABC-99
_INLINE_TICKET_RE = re.compile(
    r"\b([A-Z][A-Z0-9_]{0,30}-\d+)\b",
    re.IGNORECASE,
)

# Maximum bytes read from a single API response
_API_MAX_RESPONSE_BYTES      = 512 * 1024      # 512 KB — single issue / small calls
_API_MAX_RESPONSE_BYTES_LIST = 8 * 1024 * 1024 # 8 MB  — list endpoints

log = logging.getLogger("ST4Notes")


# ---------------------------------------------------------------------------
# Browser helper
# ---------------------------------------------------------------------------

def _open_in_browser(url: str) -> None:
    """Open URL in the system browser. HTTPS only (rejects javascript:/file:/data:)."""
    url = (url or "").strip()
    if not url:
        return
    parsed = urlparse(url)
    if parsed.scheme.lower() != "https":
        log.warning("Refusing to open non-https URL: %s", parsed.scheme or "(none)")
        sublime.status_message("ST4Notes: only https:// links can be opened")
        return
    if not parsed.hostname:
        sublime.status_message("ST4Notes: URL has no hostname")
        return
    try:
        if sys.platform == "darwin":
            subprocess.Popen(["open", url])
        elif sys.platform == "win32":
            os.startfile(url)  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["xdg-open", url])
    except Exception:
        try:
            webbrowser.open(url)
        except Exception as exc:
            log.error("Cannot open browser: %s", exc)


# Settings, URL guards, and YouTrack HTTP live in notes_yt.py
_youtrack_base = _yt._youtrack_base
_youtrack_api_root = _yt._youtrack_api_root
_youtrack_token = _yt._youtrack_token
_default_project = _yt._default_project
_issue_stages = _yt._issue_stages
_api_timeout = _yt._api_timeout
_api_max_retries = _yt._api_max_retries
_post_comments_enabled = _yt._post_comments_enabled
_gitlab_base = _yt._gitlab_base
_gitlab_api_root = _yt._gitlab_api_root
_gitlab_token = _yt._gitlab_token
_note_max_lines = _yt._note_max_lines
_note_max_line_len = _yt._note_max_line_len
_is_blocked_ip_literal = _yt._is_blocked_ip_literal
_validate_youtrack_base = _yt._validate_youtrack_base
_youtrack_host = _yt._youtrack_host
_is_youtrack_host = _yt._is_youtrack_host
_validate_gitlab_base = _yt._validate_gitlab_base
_gitlab_host = _yt._gitlab_host
_is_gitlab_host = _yt._is_gitlab_host
_ssl_context = _yt._ssl_context
_api_rate_wait = _yt._api_rate_wait
_ApiError = _yt._ApiError
_RejectRedirectHandler = _yt._RejectRedirectHandler
_yt_urlopen = _yt._yt_urlopen
_yt_request = _yt._yt_request
_NOT_FOUND = _yt._NOT_FOUND
_is_api_error = _yt._is_api_error
_fetch_current_user = _yt._fetch_current_user
_fetch_current_user_login = _yt._fetch_current_user_login
_cached_assignee_display = _yt._cached_assignee_display
_default_assignee_display = _yt._default_assignee_display
_fetch_youtrack_issue = _yt._fetch_youtrack_issue
_fetch_my_open_issues = _yt._fetch_my_open_issues
_fetch_issues_list = _yt._fetch_issues_list
_fetch_project_issues_for_import = _yt._fetch_project_issues_for_import
_fetch_my_assigned_issues_for_import = _yt._fetch_my_assigned_issues_for_import
_issue_search_blob = _yt._issue_search_blob
_filter_issues_by_text = _yt._filter_issues_by_text
_build_unassigned_query = _yt._build_unassigned_query
_fetch_unassigned_issues = _yt._fetch_unassigned_issues
_fetch_all_project_issues = _yt._fetch_all_project_issues
_is_finished_state = _yt._is_finished_state
_issue_updated_ms = _yt._issue_updated_ms
_issue_updated_label = _yt._issue_updated_label
_fetch_my_recently_resolved = _yt._fetch_my_recently_resolved
_yt_query_token = _yt._yt_query_token
_fetch_issues_assigned_to = _yt._fetch_issues_assigned_to
_invalidate_users_cache = _yt._invalidate_users_cache
_assignees_from_issues = _yt._assignees_from_issues
_fetch_youtrack_users = _yt._fetch_youtrack_users
_fetch_parent_info = _yt._fetch_parent_info
_yt_add_comment = _yt._yt_add_comment
_yt_apply_command = _yt._yt_apply_command
_issue_severity_rank = _yt._issue_severity_rank
_sort_issues_by_severity = _yt._sort_issues_by_severity
IssueCreateError = _yt.IssueCreateError
_set_youtrack_done = _yt._set_youtrack_done
_set_youtrack_in_review = _yt._set_youtrack_in_review
_yt_get_project_id = _yt._yt_get_project_id
_yt_get_subtask_link_id = _yt._yt_get_subtask_link_id
_yt_issue_entity_id = _yt._yt_issue_entity_id
_yt_link_as_subtask = _yt._yt_link_as_subtask
_yt_create_issue = _yt._yt_create_issue
_parse_youtrack_issue = _yt._parse_youtrack_issue

_gitlab_diff_file_hash = _gl._gitlab_diff_file_hash
_mr_file_diffs_url = _gl._mr_file_diffs_url
_parse_gitlab_mr_url = _gl._parse_gitlab_mr_url
_gitlab_request = _gl._gitlab_request
_pipeline_color = _gl._pipeline_color
_mr_state_color = _gl._mr_state_color
_fetch_gitlab_mr_info = _gl._fetch_gitlab_mr_info
_build_mr_hover_html = _gl._build_mr_hover_html
# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def _today_header() -> str:
    now = datetime.now()
    return f"# DATE: {now.strftime('%Y-%m-%d')}  {now.strftime('%A')}"


# ---------------------------------------------------------------------------
# File I/O  (atomic write) + mtime-keyed read cache
# ---------------------------------------------------------------------------

# Serializes read-modify-write so concurrent Notes: Add cannot drop lines.
_notes_io_lock      = threading.Lock()
_notes_cache_lock   = threading.Lock()
_notes_cache_mtime: float | None = None
_notes_cache_lines: list[str]    = []


def _read_notes(force: bool = False) -> list[str]:
    global _notes_cache_mtime, _notes_cache_lines

    if os.path.isdir(_notes_file()):
        raise RuntimeError(f"Notes path '{_notes_file()}' is a directory, not a file.")

    if not os.path.exists(_notes_file()):
        return []

    try:
        size = os.path.getsize(_notes_file())
    except OSError:
        size = 0
    if size > _NOTES_MAX_FILE_BYTES:
        raise RuntimeError(
            f"Notes file is too large ({size} bytes; max {_NOTES_MAX_FILE_BYTES}). "
            "Archive old sections before continuing."
        )

    try:
        mtime = os.path.getmtime(_notes_file())
    except OSError:
        mtime = 0.0

    with _notes_cache_lock:
        if not force and _notes_cache_mtime == mtime and _notes_cache_lines is not None:
            return list(_notes_cache_lines)

        try:
            with open(_notes_file(), "r", encoding="utf-8") as fh:
                lines = fh.read().splitlines()
        except PermissionError:
            raise RuntimeError(f"Permission denied reading '{_notes_file()}'.")
        except OSError as exc:
            raise RuntimeError(f"Cannot read notes file: {exc}") from exc

        _notes_cache_mtime = mtime
        _notes_cache_lines = lines
        return list(lines)


def _invalidate_notes_cache() -> None:
    global _notes_cache_mtime
    with _notes_cache_lock:
        _notes_cache_mtime = None


def _write_notes(lines: list[str]) -> None:
    notes_dir = os.path.dirname(_notes_file()) or os.path.expanduser("~")
    try:
        os.makedirs(notes_dir, exist_ok=True)
    except OSError as exc:
        raise RuntimeError(
            f"Cannot create notes directory '{notes_dir}': {exc}"
        ) from exc

    lines = _pure.ensure_blank_between_journal_blocks(list(lines))
    content = "\n".join(lines) + ("\n" if lines else "")
    if len(content.encode("utf-8")) > _NOTES_MAX_FILE_BYTES:
        raise RuntimeError(
            f"Refusing to write notes file over {_NOTES_MAX_FILE_BYTES} bytes. "
            "Archive old sections first."
        )
    try:
        _nc.atomic_write_utf8(
            _notes_file(),
            content,
            mode=0o600,
            replace_command="notes_replace_text",
        )
    except PermissionError:
        raise RuntimeError(f"Permission denied writing to '{_notes_file()}'.")
    except OSError as exc:
        raise RuntimeError(f"Cannot write notes file: {exc}") from exc

    _invalidate_notes_cache()


# ---------------------------------------------------------------------------
# Ticket index cache (mtime-keyed)
# ---------------------------------------------------------------------------

_index_cache_lock   = threading.Lock()
_index_cache_mtime: float | None = None
_index_cache_data:  dict | None  = None


def _get_ticket_index() -> tuple[dict[str, list[str]], list[str]]:
    global _index_cache_mtime, _index_cache_data

    try:
        mtime = os.path.getmtime(_notes_file()) if os.path.exists(_notes_file()) else 0.0
    except OSError:
        mtime = 0.0

    with _index_cache_lock:
        if _index_cache_mtime == mtime and _index_cache_data is not None:
            return _index_cache_data["index"], _index_cache_data["tickets"]

    lines   = _read_notes()
    index   = _build_ticket_index(lines)
    tickets = _all_tickets_sorted(index)

    with _index_cache_lock:
        _index_cache_mtime = mtime
        _index_cache_data  = {"index": index, "tickets": tickets}

    return index, tickets


# ---------------------------------------------------------------------------
# Section / block location
# ---------------------------------------------------------------------------

def _find_today_section(lines: list[str]) -> tuple[int | None, ...]:
    today = datetime.now().date()
    for idx, line in enumerate(lines):
        if _parse_date_header(line) != today:
            continue
        open_sep  = idx - 1 if (idx > 0 and _is_sep(lines[idx - 1])) else None
        hdr_close = idx + 1
        if hdr_close < len(lines) and _is_sep(lines[hdr_close]):
            content_start = hdr_close + 1
        else:
            hdr_close     = None
            content_start = idx + 1
        next_sep = len(lines)
        for j in range(content_start, len(lines)):
            if _is_sep(lines[j]):
                next_sep = j
                break
        content_end = next_sep - 1
        while content_end >= content_start and not lines[content_end].strip():
            content_end -= 1
        if content_end < content_start:
            content_end = content_start - 1
        return open_sep, idx, hdr_close, content_start, content_end, next_sep
    return None, None, None, None, None, None


def _find_ticket_in_section(
    lines: list[str], content_start: int, content_end: int, ticket_id: str
) -> int | None:
    needle_name = (ticket_id or "").strip()
    found_header: int | None = None
    for i in range(content_start, content_end + 1):
        name = _pure.journal_ticket_header_name(lines[i])
        if name and _pure.journal_topics_equivalent(name, needle_name):
            found_header = i
    if found_header is None:
        return None
    last_entry = found_header
    for i in range(found_header + 1, content_end + 1):
        stripped = lines[i].strip()
        if stripped.startswith("- "):
            last_entry = i
        elif stripped.upper().startswith("# "):
            break
    return last_entry


def _get_today_tickets() -> list[str]:
    try:
        lines = _read_notes()
    except RuntimeError:
        return []
    (_, hdr_idx, _, content_start, content_end, _) = _find_today_section(lines)
    if hdr_idx is None or content_end < content_start:
        return []
    seen: dict[str, None] = {}
    for i in range(content_start, content_end + 1):
        name = _pure.journal_ticket_header_name(lines[i])
        if name:
            seen.setdefault(name, None)
    return list(seen.keys())


def _get_today_tickets_with_desc() -> list[tuple[str, str]]:
    try:
        lines = _read_notes()
    except RuntimeError:
        return []
    (_, hdr_idx, _, content_start, content_end, _) = _find_today_section(lines)
    if hdr_idx is None or content_end < content_start:
        return []

    bullet_re = re.compile(r"^-\s+(?:\[\d{2}:\d{2}\]\s+)?(.+)$")

    order:       list[str]      = []
    last_desc:   dict[str, str] = {}
    current_tid: str | None     = None
    current_key: str | None     = None

    for i in range(content_start, content_end + 1):
        stripped = lines[i].strip()
        name = _pure.journal_ticket_header_name(stripped)
        if name:
            current_tid = name
            current_key = name.casefold()
            if current_key not in last_desc:
                order.append(name)
                last_desc[current_key] = ""
        elif current_key and stripped.startswith("- "):
            bm = bullet_re.match(stripped)
            if bm:
                last_desc[current_key] = bm.group(1).strip()

    return [(tid, last_desc.get(tid.casefold(), "")) for tid in order]


# ---------------------------------------------------------------------------
# Full-file ticket index
# ---------------------------------------------------------------------------

_DATE_HDR_RE   = re.compile(r"^#\s+\d{4}\.\d{1,2}\.\d{1,2}\s*$")


def _build_ticket_index(lines: list[str]) -> dict[str, list[str]]:
    index:   dict[str, list[str]] = {}
    emitted: set[tuple[str, str]] = set()
    fold_to_key: dict[str, str] = {}
    current_date   = ""
    current_ticket = ""

    for line in lines:
        stripped = line.strip()
        if _is_sep(stripped):
            current_ticket = ""
            continue
        if _DATE_HDR_RE.match(stripped) or _pure.parse_journal_date_header(stripped):
            current_date   = stripped.lstrip("#").strip()
            current_ticket = ""
            continue
        name = _pure.journal_ticket_header_name(stripped)
        if name:
            fold = name.casefold()
            if fold not in fold_to_key:
                fold_to_key[fold] = name
                index[name] = []
            current_ticket = fold_to_key[fold]
            continue
        if stripped.startswith("- ") and current_ticket:
            key = (current_ticket, current_date)
            if key not in emitted:
                emitted.add(key)
                if index[current_ticket]:
                    index[current_ticket].append("")
                if current_date:
                    index[current_ticket].append(f"# {current_date}")
            index[current_ticket].append(stripped)
    return index


def _all_tickets_sorted(index: dict[str, list[str]]) -> list[str]:
    pinned = [t for t in (_TODO_ID, _OPS_ID) if t in index]
    done   = [t for t in index if t not in (_TODO_ID, _OPS_ID) and
              any("[DONE]<<-" in e for e in index[t])]
    active = [t for t in index if t not in (_TODO_ID, _OPS_ID) and t not in done]
    return pinned + active + done


def _first_bullet_from_index(entries: list[str]) -> str:
    bullet_re = re.compile(r"^-\s+(?:\[\d{2}:\d{2}\]\s+)?(.+)$")
    for entry in entries:
        m = bullet_re.match(entry.strip())
        if m:
            return m.group(1).strip()
    return ""



# ---------------------------------------------------------------------------
# Popup HTML helpers
# ---------------------------------------------------------------------------


def _state_color(state: str) -> str:
    s = state.lower()
    if any(x in s for x in ("fixed", "done", "closed", "resolved")):
        return "#98c379"
    if any(x in s for x in ("open", "to do", "new")):
        return "#e06c75"
    return "#e5c07b"


def _priority_color(priority: str) -> str:
    p = priority.lower()
    if p in ("critical", "show-stopper", "blocker", "showstopper"):
        return "#e06c75"
    if p in ("major",):
        return "#e5c07b"
    return "#abb2bf"


def _build_hover_html(
    ticket_id: str,
    url: str,
    info: dict[str, str] | None,
    not_found: bool = False,
) -> str:
    safe_url  = _h(url)
    label_txt = ticket_id if ticket_id else url

    link_html = (
        f"<div style='margin-bottom:5px'>"
        f"<a href='open:{safe_url}' "
        f"style='color:#56b6c2;text-decoration:none;font-weight:bold'>"
        f"&#128279; {_h(label_txt)}</a>"
        f"</div>"
    )
    sep = f"<div style='{_hover_sep_style()}'></div>"

    if not_found:
        body = (
            f"<div style='{_hover_pre_style()}'>"
            "<span style='color:#e06c75'>x  Not found</span>\n"
            f"<span style='color:#5c6370'>No issue matching </span>"
            f"<span style='color:#abb2bf'>{_h(ticket_id)}</span>"
            f"<span style='color:#5c6370'> exists on this YouTrack instance.</span>"
            "</div>"
        )
        return (
            "<body id='stnotes-hover' "
            f"style='{_hover_body_style()}'>"
            + link_html + sep + body
            + "</body>"
        )

    if info is None:
        return (
            "<body id='stnotes-hover' "
            f"style='{_hover_body_style()}'>"
            + link_html
            + "</body>"
        )

    sev = info.get("severity", "")
    pri = info.get("priority", "")
    if sev and pri:
        priority_row = ("sev/pri", f"{sev} / {pri}", _priority_color(pri))
        severity_row = None
    elif sev:
        severity_row = ("severity", sev, "#abb2bf")
        priority_row = None
    elif pri:
        severity_row = None
        priority_row = ("priority", pri, _priority_color(pri))
    else:
        severity_row = None
        priority_row = None

    FIELDS: list[tuple[str, str, str]] = []
    FIELDS.append(("summary",  info.get("summary",  ""), "#000000"))
    if priority_row:
        FIELDS.append(priority_row)
    if severity_row:
        FIELDS.append(severity_row)
    FIELDS.append(("state",    info.get("state",    ""), _state_color(info.get("state", ""))))
    FIELDS.append(("assignee", info.get("assignee", ""), "#6699cc"))
    FIELDS.append(("reporter", info.get("reporter", ""), "#abb2bf"))
    FIELDS.append(("created",  info.get("created",  ""), "#5c6370"))

    populated = [(lbl, val, col) for lbl, val, col in FIELDS if val]
    if not populated:
        rows_html = "<span style='color:#5c6370'>no data returned</span>"
    else:
        col_w = max(len(lbl) for lbl, _, _ in populated) + 2
        rows: list[str] = []
        for lbl, val, color in populated:
            label_padded = (lbl + ":").ljust(col_w)
            rows.append(
                f"<span style='color:#5c6370'>{_h(label_padded)}</span>"
                f"<span style='color:{color}'>{_h(val)}</span>"
            )
        rows_html = "\n".join(rows)

    return (
        "<body id='stnotes-hover' "
        f"style='{_hover_body_style()}'>"
        + link_html
        + sep
        + f"<div style='{_hover_pre_style()}'>{rows_html}</div>"
        + "</body>"
    )


# ---------------------------------------------------------------------------
# Core business logic
# ---------------------------------------------------------------------------

def _normalize_note_lines(description: str) -> list[str]:
    """
    Split description into note lines.

    Each non-empty line becomes one bullet. Leading "- " is stripped so users
    may paste either plain lines or already-bulleted text. Comment lines
    starting with "#" are ignored (scratch-buffer instructions).
    """
    max_lines = _note_max_lines()
    max_len = _note_max_line_len()
    out: list[str] = []
    for raw in (description or "").splitlines():
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        if s.startswith("- "):
            s = s[2:].strip()
        elif s == "-":
            continue
        if not s:
            continue
        if len(s) > max_len:
            s = s[: max_len - 1] + "…"
        out.append(s)
        if len(out) >= max_lines:
            break
    return out


def _format_bullet_body(description: str) -> str:
    tag = description.strip().upper()
    if tag == "DONE":
        return "[DONE]<<-"
    if tag == "CREATED":
        return "[CREATED]"
    if tag == "REVIEW":
        return "[IN REVIEW]"
    return description.strip()


def _build_entries(description: str) -> list[str]:
    """Build bullet lines without HH:MM timestamps."""
    lines = _normalize_note_lines(description)
    return [f"- {_format_bullet_body(line)}" for line in lines]


def _insert_entries_for_ticket(ticket_id: str, entries: list[str]) -> None:
    if not entries:
        raise RuntimeError("Note description produced no lines.")

    lines = _read_notes(force=True)

    (_open_sep, hdr_idx, _hdr_close, content_start,
     content_end, _next_sep) = _find_today_section(lines)

    if hdr_idx is None:
        new_section: list[str] = [
            _SEP,
            _today_header(),
            _SEP,
            "",
            f"# {ticket_id}:",
            *entries,
            "",
        ]
        if lines and lines[0].strip():
            new_section.append("")
        lines = new_section + lines
        _write_notes(lines)
        _reload_open_journal_views()
        return

    ticket_last = _find_ticket_in_section(
        lines, content_start, content_end, ticket_id
    )

    if ticket_last is None:
        insert_at = content_end + 1
        new_block: list[str] = []
        if content_end >= content_start:
            new_block.append("")
        new_block += [f"# {ticket_id}:", *entries]
        lines[insert_at:insert_at] = new_block
    else:
        # Newest first under the ticket header (same as previous single-line insert).
        for offset, entry in enumerate(entries):
            lines.insert(ticket_last + 1 + offset, entry)

    _write_notes(lines)
    _reload_open_journal_views()


def add_note(ticket_id: str, description: str) -> None:
    entries = _build_entries(description)
    with _notes_io_lock:
        _insert_entries_for_ticket(ticket_id, entries)


def add_note_raw(ticket_id: str, raw_entry: str) -> None:
    """Insert a pre-formatted bullet line (or multi-line block) as-is."""
    raw = (raw_entry or "").rstrip("\n")
    if not raw.strip():
        raise RuntimeError("Empty raw note entry.")
    # Normalize to bullet lines without inventing timestamps.
    chunk = []
    for line in raw.splitlines():
        s = line.rstrip()
        if not s.strip():
            continue
        if not s.lstrip().startswith("-"):
            s = f"- {s.strip()}"
        # Strip legacy HH:MM if somehow present in crafted raw entries.
        s2 = s.lstrip()
        if s2.startswith("- ["):
            m = re.match(r"^- \[(\d{2}:\d{2})\]\s+(.*)$", s2)
            if m:
                s = f"- {m.group(2)}"
        chunk.append(s)
    with _notes_io_lock:
        _insert_entries_for_ticket(ticket_id, chunk)


def _reload_open_journal_views() -> None:
    try:
        path = os.path.realpath(_notes_file())
    except OSError:
        return
    for window in sublime.windows():
        for view in window.views():
            fname = view.file_name() or ""
            if not fname:
                continue
            try:
                if os.path.realpath(fname) != path:
                    continue
            except OSError:
                continue
            was_ro = view.is_read_only()
            if was_ro:
                view.set_read_only(False)
            view.run_command("revert")
            _protect_journal_view(view)


def _replace_note_in_journal(
    orig_ticket: str,
    orig_date_iso: str,
    new_ticket: str,
    description: str,
) -> None:
    entries = _build_entries(description) if (description or "").strip() else []
    date = _pure.parse_journal_date_iso(orig_date_iso)
    if date is None:
        today = datetime.now().date()
        date = (today.year, today.month, today.day)
    with _notes_io_lock:
        lines = _read_notes(force=True)
        loc = _pure.find_journal_block_by_date_ticket(lines, date, orig_ticket)
        if loc is None:
            raise RuntimeError(
                f"Could not find # {orig_ticket}: on {orig_date_iso or 'that day'}."
            )
        start, end = loc
        lines = _pure.replace_journal_ticket_block(
            lines, start, end, new_ticket, entries
        )
        _write_notes(lines)
    _reload_open_journal_views()


# ---------------------------------------------------------------------------
# Notes: Add — multi-line description scratch + commit
# ---------------------------------------------------------------------------

_ADD_SKIP_TICKETS = frozenset({_TODO_ID, _OPS_ID, "EXAMPLE"})

_SLICE_COMMIT_HINT = "Cmd+Shift+Enter (Mac) / Ctrl+Shift+Enter (Win/Linux)"


def _build_note_slice_text(
    ticket_id: str,
    body_lines: list[str] | None,
    *,
    replace: bool,
) -> str:
    tid = (ticket_id or "").strip()
    body = "\n".join(body_lines or []).rstrip()
    if not body:
        body = "- "
    mode = (
        "Commit replaces this header's block on that calendar day."
        if replace
        else "Commit creates the header under today if needed, then appends bullets."
    )
    return (
        "# .notes note\n"
        f"# Commit: {_SLICE_COMMIT_HINT}   ·   Cancel: close tab\n"
        "# Alt+Up / Alt+Down cycles header name and bullets\n"
        f"# {mode}\n"
        "# topic: any journal header — PROJ-1234, TODO, standup, or a short phrase\n"
        "# Body: one bullet per line. Tags: DONE | REVIEW | CREATED\n"
        "#\n"
        f"topic: {tid}\n"
        "---\n"
        f"{body}\n"
    )


def _open_add_description_scratch(window: sublime.Window, ticket_id: str) -> None:
    _open_note_slice(
        window, ticket_id, [], replace=False, allow_yt_comment=True
    )


def _open_note_slice(
    window: sublime.Window,
    ticket_id: str,
    body_lines: list[str] | None,
    *,
    replace: bool,
    orig_ticket: str = "",
    orig_date: str = "",
    allow_yt_comment: bool = False,
) -> None:
    tid = (ticket_id or "").strip()
    content = _build_note_slice_text(tid, body_lines, replace=replace)
    name = f".notes note · {tid}" if tid else ".notes note"
    view = window.new_file()
    view.set_name(name)
    view.set_scratch(True)
    view.settings().set("stnotes_slice", True)
    view.settings().set("stnotes_slice_kind", "note")
    view.settings().set("stnotes_view_name", name)
    view.settings().set("stnotes_add_ticket_id", tid)
    if replace:
        view.settings().set("stnotes_note_replace", True)
        view.settings().set("stnotes_note_orig_ticket", orig_ticket or tid)
        view.settings().set("stnotes_note_orig_date", orig_date or "")
    else:
        view.settings().set("stnotes_add_description", True)
        view.settings().set("stnotes_allow_yt_comment", bool(allow_yt_comment))
    view.run_command("notes_insert_text", {"text": content})
    try:
        text = view.substr(sublime.Region(0, view.size()))
        regions = _pure.slice_field_regions(text, kind="note")
        pick = None
        if replace and len(regions) > 1:
            pick = regions[1]
        elif not tid and regions:
            pick = regions[0]
        elif regions:
            pick = regions[-1]
        if pick:
            a, b = pick
            view.sel().clear()
            view.sel().add(sublime.Region(a, b))
            view.show(a)
    except Exception:
        pass
    _assign_stnotes_syntax(view)
    sublime.status_message(
        f"Notes: fill note slice, then {_SLICE_COMMIT_HINT}"
    )


def _extract_description_from_add_view(view: sublime.View) -> tuple[str, str]:
    text = view.substr(sublime.Region(0, view.size()))
    ticket, desc = _pure.parse_note_slice(text)
    if not ticket:
        ticket = str(view.settings().get("stnotes_add_ticket_id") or "").strip()
    return ticket, desc


def _finish_add_description(
    window: sublime.Window,
    ticket_id: str,
    raw: str,
    *,
    allow_yt_comment: bool = False,
) -> None:
    desc = (raw or "").strip()
    if not desc:
        sublime.status_message(
            "Notes: description cannot be empty — entry skipped."
        )
        return

    try:
        add_note(ticket_id, desc)
        n = len(_normalize_note_lines(desc))
        sublime.status_message(
            f"Notes: [{ticket_id}] added {n} line(s)."
        )
    except RuntimeError as exc:
        _error_message(f"Notes - could not write entry:\n\n{exc}")
        return
    except Exception as exc:
        log.exception("Unexpected error in add_note")
        _error_message(
            f"Notes - unexpected error:\n\n{type(exc).__name__}: {exc}"
        )
        return

    if not allow_yt_comment:
        return
    if ticket_id.casefold() in {_TODO_ID.casefold(), _OPS_ID.casefold()} or not (
        _youtrack_token() and _youtrack_base()
    ):
        return
    if not _pure.is_youtrack_issue_id(ticket_id):
        return

    # Magic YouTrack transitions only for a single-line DONE/REVIEW.
    lines = _normalize_note_lines(desc)
    desc_upper = lines[0].upper() if len(lines) == 1 else ""

    def _maybe_post_comment() -> None:
        if _post_comments_enabled():
            comment_text = "\n".join(lines)
            sublime.set_timeout_async(
                lambda: _post_yt_comment_async(ticket_id, comment_text), 0
            )

    if desc_upper in ("DONE", "REVIEW"):
        action = "Done" if desc_upper == "DONE" else "In Review"
        items = [
            [f"Set YouTrack {ticket_id} → {action}", "Confirm state change"],
            ["Skip YouTrack state change", "Keep local note only"],
        ]

        def on_pick(index: int) -> None:
            if index == 0:
                if desc_upper == "DONE":
                    sublime.set_timeout_async(
                        lambda: _set_youtrack_done(ticket_id), 0
                    )
                else:
                    sublime.set_timeout_async(
                        lambda: _set_youtrack_in_review(ticket_id), 0
                    )
            _maybe_post_comment()

        window.show_quick_panel(items, on_pick)
    else:
        _maybe_post_comment()


def _post_yt_comment_async(ticket_id: str, text: str) -> None:
    ok = _yt_add_comment(ticket_id, text)
    if ok:
        sublime.set_timeout(
            lambda: sublime.status_message(
                f"Notes: comment posted to {ticket_id}"
            ),
            0,
        )
    else:
        sublime.set_timeout(
            lambda: sublime.status_message(
                f"Notes: WARNING - could not post comment to {ticket_id}"
            ),
            0,
        )



# ---------------------------------------------------------------------------
# Create Issue — edit-slice form (replaces sequential input panels)
# ---------------------------------------------------------------------------

def _expand_issue_template(template: str, summary: str, stage: str = "") -> str:
    return _pure.expand_issue_template(template, summary, stage=stage)


def _parse_create_issue_slice(text: str) -> dict:
    return _pure.parse_create_issue_slice(text)


def _default_create_issue_subtask_lines() -> list[str]:
    stages = _issue_stages() or ["Design", "Dev", "QA", "Deploy"]
    return [f"- {s} | $name - {s}" for s in stages]


def _issue_slice_oneline(text: str) -> str:
    return " ".join((text or "").replace("\r\n", "\n").split())


def _build_issue_slice_text(fields: dict | None = None) -> str:
    f = fields or {}
    project = (f.get("project") or _default_project() or "PROJECT").strip()
    subtasks = (f.get("subtasks_text") or "").rstrip()
    if not subtasks:
        subtasks = "\n".join(_default_create_issue_subtask_lines())
    ticket = (f.get("ticket") or "").strip()
    assignee = f.get("assignee")
    if assignee is None:
        assignee = _cached_assignee_display()
    return (
        "# .notes issue\n"
        f"# Commit: {_SLICE_COMMIT_HINT}   ·   Cancel: close tab\n"
        "# Alt+Up / Alt+Down cycles fields (same as other slices)\n"
        "#\n"
        "# ticket:      read-only (YouTrack assigns on create; import fills it)\n"
        "# project:     YouTrack shortName (required to create)\n"
        "# summary:     title; also fills $summary / $name in sub-task titles\n"
        "# description: multiline — extra lines indented two spaces\n"
        "# assignee:    email or login; empty = unassigned\n"
        "# reporter:    author (informational on import; not sent on create)\n"
        "# due:         YYYY-MM-DD (informational on import)\n"
        "# state / priority: informational on import\n"
        "# parent:      empty = new parent from summary; or PROJ-1234 for children only\n"
        "# stages:      true = create sub-tasks listed below; false = one issue\n"
        "#\n"
        "# Below ---: new issue sub-tasks when stages: true, or a journal note\n"
        "# for an existing ticket (import). One bullet per line.\n"
        f"ticket: {ticket}\n"
        f"project: {project}\n"
        f"summary: {_issue_slice_oneline(f.get('summary') or '')}\n"
        f"{_pure.format_create_issue_description(f.get('description') or '')}\n"
        f"assignee: {_issue_slice_oneline(str(assignee or ''))}\n"
        f"reporter: {_issue_slice_oneline(f.get('reporter') or '')}\n"
        f"due: {_issue_slice_oneline(f.get('due') or '')}\n"
        f"state: {_issue_slice_oneline(f.get('state') or '')}\n"
        f"priority: {_issue_slice_oneline(f.get('priority') or '')}\n"
        f"parent: {_issue_slice_oneline(f.get('parent') or '')}\n"
        f"stages: {f.get('stages') or 'false'}\n"
        "---\n"
        f"{subtasks}\n"
    )


def _open_issue_slice(
    window: sublime.Window,
    fields: dict | None = None,
    *,
    select: str = "summary",
    fill_assignee: bool = True,
) -> None:
    if not _youtrack_token() or not _youtrack_base():
        _error_message(
            "Notes - YouTrack not configured.\n\n"
            "Run 'Notes - Settings' and set:\n"
            '  "youtrack_base":  "https://youtrack.example.com/issue/"\n'
            '  "youtrack_token": "<your permanent token>"'
        )
        return
    base_err = _validate_youtrack_base(_youtrack_base())
    if base_err:
        _error_message(f"Notes - invalid youtrack_base:\n\n{base_err}")
        return
    content = _build_issue_slice_text(fields)
    ticket = ((fields or {}).get("ticket") or "").strip()
    name = f".notes issue · {ticket}" if ticket else ".notes issue"
    view = window.new_file()
    view.set_name(name)
    view.set_scratch(True)
    view.settings().set("stnotes_create_issue", True)
    view.settings().set("stnotes_slice", True)
    view.settings().set("stnotes_slice_kind", "create_issue")
    view.settings().set("stnotes_view_name", name)
    view.settings().set("stnotes_issue_ticket", ticket)
    if ticket:
        view.settings().set("stnotes_issue_existing", True)
    view.run_command("notes_insert_text", {"text": content})
    try:
        text = view.substr(sublime.Region(0, view.size()))
        m = re.search(rf"(?m)^{re.escape(select)}:\s*", text)
        if m:
            pt = m.end()
            view.sel().clear()
            view.sel().add(sublime.Region(pt, pt))
            view.show(pt)
    except Exception:
        pass
    _assign_stnotes_syntax(view)
    sublime.status_message(f"Notes: fill issue slice, then {_SLICE_COMMIT_HINT}")
    if fill_assignee and not ((fields or {}).get("assignee") or "").strip():
        sublime.set_timeout_async(lambda vid=view.id(): _fill_create_issue_assignee_async(vid), 0)


def _open_create_issue_slice(window: sublime.Window) -> None:
    _open_issue_slice(window, select="summary", fill_assignee=True)


def _slice_fields_from_youtrack(raw: dict | None, stub: dict | None, ticket_id: str) -> dict:
    parsed = _parse_youtrack_issue(raw) if raw else {}
    stub_p = stub or {}
    desc = parsed.get("description") or stub_p.get("description") or ""
    return {
        "ticket": ticket_id,
        "project": parsed.get("project") or stub_p.get("project") or _default_project(),
        "summary": parsed.get("summary") or stub_p.get("summary") or "",
        "description": desc,
        "assignee": parsed.get("assignee") or stub_p.get("assignee") or "",
        "reporter": parsed.get("reporter") or stub_p.get("reporter") or "",
        "due": parsed.get("due") or "",
        "state": parsed.get("state") or stub_p.get("state") or "",
        "priority": parsed.get("priority") or stub_p.get("priority") or "",
        "stages": "false",
        "subtasks_text": "- \n",
    }


def _open_imported_issue_slice(window: sublime.Window, ticket_id: str, stub: dict | None = None) -> None:
    sublime.status_message(f"Notes: loading {ticket_id} from YouTrack...")

    def work() -> None:
        raw = None
        try:
            raw = _fetch_youtrack_issue(ticket_id)
        except Exception:
            log.exception("YouTrack fetch for import slice failed")
        fields = _slice_fields_from_youtrack(raw, stub, ticket_id)
        sublime.set_timeout(
            lambda: _open_issue_slice(
                window, fields, select="summary", fill_assignee=False
            ),
            0,
        )

    sublime.set_timeout_async(work, 0)


def _fill_create_issue_assignee_async(view_id: int) -> None:
    if _cached_assignee_display():
        return
    email = _default_assignee_display()
    if not email:
        return

    def apply() -> None:
        for window in sublime.windows():
            for v in window.views():
                if v.id() == view_id and v.is_valid():
                    v.run_command(
                        "notes_fill_create_issue_assignee", {"value": email}
                    )
                    return

    sublime.set_timeout(apply, 0)


def _yt_lookup_user_login(raw: str) -> str:
    """Resolve email or login to a YouTrack user login (empty if unset)."""
    val = (raw or "").strip()
    if not val:
        return ""
    if val.lower() in ("me", "myself", "self"):
        user = _fetch_current_user()
        return (user.get("login") or "").strip()

    def _from_list(rows: object, needle: str) -> str:
        if not isinstance(rows, list):
            return ""
        want = needle.lower()
        exact_email = []
        exact_login = []
        for u in rows:
            if not isinstance(u, dict):
                continue
            login = (u.get("login") or "").strip()
            email = (u.get("email") or "").strip()
            if email.lower() == want and login:
                exact_email.append(login)
            if login.lower() == want:
                exact_login.append(login)
        if exact_email:
            return exact_email[0]
        if exact_login:
            return exact_login[0]
        if len(rows) == 1:
            only = rows[0]
            if isinstance(only, dict):
                return (only.get("login") or "").strip()
        return ""

    queries = [val]
    if "@" in val:
        queries.append(f"email: {val}")
    for q in queries:
        result = _yt_request(
            "GET",
            "/users",
            params=(
                f"fields=login,email,fullName,name"
                f"&query={quote(q)}"
                f"&$top=20"
            ),
        )
        hit = _from_list(result, val)
        if hit:
            return hit

    if "@" not in val:
        result = _yt_request(
            "GET",
            f"/users/{quote(val)}",
            params="fields=login,email,fullName",
        )
        if isinstance(result, dict) and not _is_api_error(result):
            return (result.get("login") or val).strip()
        return val

    raise IssueCreateError(
        f"No YouTrack user with email '{val}'.\n"
        "Enter the address from YouTrack (Profile), or a login, or leave assignee empty."
    )


def _resolve_assignee_value(raw: str) -> str:
    return _yt_lookup_user_login(raw)


def _create_issue_job_single(
    project: str, summary: str, description: str, assignee: str,
) -> None:
    try:
        ticket_id = _yt_create_issue(project, summary, description, assignee)
    except IssueCreateError as exc:
        err_msg = str(exc)
        sublime.set_timeout(
            lambda: _error_message(f"Notes - failed to create issue:\n\n{err_msg}"), 0
        )
        return
    base = _youtrack_base()
    url = f"{base}{ticket_id}"

    def _notify() -> None:
        try:
            add_note(ticket_id, "CREATED")
        except Exception as exc:
            log.warning("Could not write CREATED entry: %s", exc)
        sublime.set_clipboard(url)
        _message_dialog(
            f"Issue created: {ticket_id}\nURL: {url}\n\nURL copied to clipboard."
        )
        sublime.status_message(f"Notes: created {ticket_id}")

    sublime.set_timeout(_notify, 0)


def _create_issue_job_with_subtasks(
    *,
    project: str,
    parent_id: str | None,
    parent_summary: str,
    description: str,
    assignee: str,
    subtasks: list[tuple[str, str]],
    create_parent: bool,
) -> None:
    base = _youtrack_base()
    if create_parent:
        try:
            parent_id = _yt_create_issue(project, parent_summary, description, assignee)
        except IssueCreateError as exc:
            err_msg = str(exc)
            sublime.set_timeout(
                lambda: _error_message(
                    f"Notes - failed to create parent issue:\n\n{err_msg}"
                ),
                0,
            )
            return
    assert parent_id is not None
    child_results: list[tuple[str, str, bool]] = []
    for label, title_tmpl in subtasks:
        child_summary = (
            _expand_issue_template(title_tmpl, parent_summary, stage=label)
            or f"{parent_summary} - {label}"
        )
        try:
            child_id = _yt_create_issue(project, child_summary, "", assignee)
            linked = _yt_link_as_subtask(parent_id, child_id)
            child_results.append((label, child_id, linked))
        except IssueCreateError as exc:
            log.warning("YouTrack: failed to create sub-task '%s': %s", label, exc)
            child_results.append((label, f"FAILED: {exc}", False))

    _parent_id_snap = parent_id
    _parent_url_snap = f"{base}{parent_id}"

    def _notify() -> None:
        ok_children = [cid for _, cid, _ in child_results if not cid.startswith("FAILED:")]
        if ok_children:
            child_refs = ", ".join(f"#{cid}" for cid in ok_children)
            stage_entry = f"- [CREATED] for each stage: {child_refs}"
            try:
                add_note_raw(_parent_id_snap, stage_entry)
            except Exception as exc:
                log.warning("Could not write stage CREATED entry for %s: %s", _parent_id_snap, exc)
        else:
            try:
                add_note(_parent_id_snap, "CREATED")
            except Exception as exc:
                log.warning("Could not write CREATED for parent %s: %s", _parent_id_snap, exc)
        sublime.set_clipboard(_parent_url_snap)
        msg_lines = [
            f"Parent issue:   {_parent_id_snap}",
            f"URL:            {_parent_url_snap}",
            "Parent URL copied to clipboard.",
            "",
            "Stage sub-tasks:",
        ]
        for label, cid_or_err, linked in child_results:
            if cid_or_err.startswith("FAILED:"):
                msg_lines.append(f"  [{label}]  FAILED - {cid_or_err[7:]}")
            else:
                link_note = " (linked as subtask)" if linked else " (standalone)"
                msg_lines.append(f"  [{label}]  {cid_or_err}{link_note}")
        _message_dialog("\n".join(msg_lines))
        sublime.status_message(
            f"Notes: {_parent_id_snap} + {len(ok_children)}/{len(subtasks)} stage(s) created"
        )

    sublime.set_timeout(_notify, 0)


def _commit_create_issue_view(window: sublime.Window, view: sublime.View) -> None:
    data = _parse_create_issue_slice(view.substr(sublime.Region(0, view.size())))
    existing = bool(view.settings().get("stnotes_issue_existing"))
    locked = str(view.settings().get("stnotes_issue_ticket") or "").strip()
    ticket = (locked or data.get("ticket") or "") if existing else ""
    project = data["project"]
    summary = data["summary"]
    description = data["description"]
    parent = data["parent"]
    stages_yes = data["stages_yes"]
    subtasks = data["subtasks"]

    if ticket:
        note_lines = [title for _lab, title in subtasks if (title or "").strip()]
        note = "\n".join(note_lines).strip() or "IMPORTED"
        view.set_scratch(True)
        window.focus_view(view)
        window.run_command("close")
        _finish_add_description(window, ticket, note, allow_yt_comment=True)
        return

    if not project and not parent:
        raise RuntimeError("Set project: (or parent: for sub-tasks under an existing ticket)")
    if stages_yes and not subtasks:
        raise RuntimeError("stages: true but no sub-tasks listed below ---")
    if not stages_yes and not summary:
        raise RuntimeError("summary: is required")
    if stages_yes and not parent and not summary:
        raise RuntimeError("summary: is required when creating a new parent")

    # Persist labels from the slice so the next Create Issue reuses them
    if stages_yes and subtasks:
        labels = [lab for lab, _ in subtasks]
        if labels:
            s = _settings()
            s.set("issue_stages", labels)
            sublime.save_settings(_SETTINGS_FILE)

    view.set_scratch(True)
    window.focus_view(view)
    window.run_command("close")

    def _work() -> None:
        try:
            assignee = _resolve_assignee_value(data["assignee"])
        except IssueCreateError as exc:
            err_msg = str(exc)
            sublime.set_timeout(
                lambda m=err_msg: _error_message(f"Notes - assignee:\n\n{m}"),
                0,
            )
            return
        if stages_yes:
            parent_summary = summary
            create_parent = not bool(parent)
            parent_id = parent or None
            proj = project
            if parent_id:
                info = _fetch_parent_info(parent_id)
                if not info:
                    sublime.set_timeout(
                        lambda: _error_message(
                            f"Notes: parent {parent_id} not found in YouTrack.\n"
                            "Use idReadable (PROJ-1234) or the issue URL."
                        ),
                        0,
                    )
                    return
                parent_id = info.get("idReadable") or parent_id
                if not parent_summary:
                    parent_summary = (info.get("summary") or "").strip()
                if info.get("project"):
                    proj = info["project"]
                elif not proj and "-" in parent_id:
                    proj = parent_id.split("-")[0]
            if not parent_summary:
                sublime.set_timeout(
                    lambda: _error_message("Notes: missing summary for parent / $summary"),
                    0,
                )
                return
            if not proj:
                sublime.set_timeout(
                    lambda: _error_message("Notes: set project: for new parent"),
                    0,
                )
                return
            sublime.set_timeout(
                lambda: sublime.status_message(
                    f"Notes: creating {len(subtasks)} sub-task(s) under {parent_id or 'new parent'}..."
                ),
                0,
            )
            _create_issue_job_with_subtasks(
                project=proj,
                parent_id=parent_id,
                parent_summary=parent_summary,
                description=description,
                assignee=assignee,
                subtasks=subtasks,
                create_parent=create_parent,
            )
        else:
            sublime.set_timeout(
                lambda: sublime.status_message(
                    f"Notes: creating {project} issue '{summary}'..."
                ),
                0,
            )
            _create_issue_job_single(project, summary, description, assignee)

    sublime.set_timeout_async(_work, 0)


class NotesCommitDescriptionCommand(sublime_plugin.WindowCommand):
    """Commit multi-line description from the Add scratch buffer."""

    def is_enabled(self) -> bool:
        view = self.window.active_view()
        return bool(view and view.settings().get("stnotes_add_description"))

    def run(self) -> None:
        view = self.window.active_view()
        if not view or not view.settings().get("stnotes_add_description"):
            sublime.status_message(
                "Notes: open Notes: Add description buffer first"
            )
            return
        ticket_id, desc = _extract_description_from_add_view(view)
        if not ticket_id:
            _error_message("Notes: topic: is empty — set a header name then commit")
            return
        ok, err = _pure.normalize_journal_topic(ticket_id)
        if err or not ok:
            _error_message(f"Notes: {err or 'invalid topic'}")
            return
        ticket_id = ok
        allow_yt = bool(view.settings().get("stnotes_allow_yt_comment"))
        # Close scratch before write so user returns to previous view
        view.set_scratch(True)
        self.window.focus_view(view)
        self.window.run_command("close")
        _finish_add_description(
            self.window, ticket_id, desc, allow_yt_comment=allow_yt
        )


class NotesCommitCreateIssueCommand(sublime_plugin.WindowCommand):
    """Command: notes_commit_create_issue | commit create-issue slice"""

    def is_enabled(self) -> bool:
        view = self.window.active_view()
        return bool(view and view.settings().get("stnotes_create_issue"))

    def run(self) -> None:
        view = self.window.active_view()
        if not view or not view.settings().get("stnotes_create_issue"):
            sublime.status_message("Notes: open create-issue slice first")
            return
        try:
            _commit_create_issue_view(self.window, view)
        except RuntimeError as exc:
            _error_message(f"Notes - create issue:\n\n{exc}")
        except Exception as exc:
            _error_message(f"Notes - create issue failed:\n\n{exc}")


class NotesCommitSliceCommand(sublime_plugin.WindowCommand):
    """Unified commit for all .notes slices (add / KB / create-issue)."""

    def is_enabled(self) -> bool:
        view = self.window.active_view()
        return bool(view and view.settings().get("stnotes_slice"))

    def run(self) -> None:
        view = self.window.active_view()
        if not view or not view.settings().get("stnotes_slice"):
            sublime.status_message("Notes: no slice editor focused")
            return
        kind = view.settings().get("stnotes_slice_kind") or ""
        if (
            kind == "kb"
            or view.settings().get("stnotes_kb_subject_edit")
            or view.settings().get("kb_subject_edit")
        ):
            self.window.run_command("notes_kb_commit_subject")
        elif kind == "create_issue" or view.settings().get("stnotes_create_issue"):
            self.window.run_command("notes_commit_create_issue")
        elif view.settings().get("stnotes_note_replace"):
            self.window.run_command("notes_commit_note")
        elif kind in ("add", "note") or view.settings().get("stnotes_add_description"):
            self.window.run_command("notes_commit_description")
        else:
            sublime.status_message("Notes: unknown slice kind")


class NotesCommitNoteCommand(sublime_plugin.WindowCommand):
    """Commit a note slice that replaces an existing day's header block."""

    def is_enabled(self) -> bool:
        view = self.window.active_view()
        return bool(view and view.settings().get("stnotes_note_replace"))

    def run(self) -> None:
        view = self.window.active_view()
        if not view or not view.settings().get("stnotes_note_replace"):
            sublime.status_message("Notes: open a note slice from View first")
            return
        ticket_id, desc = _extract_description_from_add_view(view)
        orig = str(view.settings().get("stnotes_note_orig_ticket") or "").strip()
        orig_date = str(view.settings().get("stnotes_note_orig_date") or "").strip()
        if not ticket_id:
            ticket_id = orig
        ok, err = _pure.normalize_journal_topic(ticket_id)
        if err or not ok:
            _error_message(f"Notes: {err or 'invalid topic'}")
            return
        ticket_id = ok
        view.set_scratch(True)
        self.window.focus_view(view)
        self.window.run_command("close")
        try:
            _replace_note_in_journal(orig or ticket_id, orig_date, ticket_id, desc)
            sublime.status_message(f"Notes: updated [{ticket_id}]")
        except RuntimeError as exc:
            _error_message(f"Notes - could not update note:\n\n{exc}")
        except Exception as exc:
            log.exception("replace note failed")
            _error_message(f"Notes - could not update note:\n\n{exc}")


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------

def _validate_ticket_id(raw: str) -> tuple[str | None, str | None]:
    tid = raw.strip().upper()
    if not tid:
        return None, "Ticket ID cannot be empty."
    if not _TICKET_RE.match(tid):
        return None, (
            f"Invalid ticket ID: '{tid}'\n"
            "Allowed: letters, digits, hyphens, underscores. "
            "Must start with a letter or digit. Max 64 chars."
        )
    return tid, None


# ---------------------------------------------------------------------------
# Scratch view helpers
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# YT open issues — sort + render
# ---------------------------------------------------------------------------

def _build_yt_issues_section(project: str, issues: list[dict]) -> str:
    header = f"{_SEP}\n# YouTrack open issues [{project}] - assigned to me\n{_SEP}"

    if not issues:
        return (
            header
            + "\n\n- (no open issues found or YouTrack not reachable)\n"
        )

    sorted_issues = _sort_issues_by_severity(issues)
    lines: list[str] = [header, ""]
    for issue in sorted_issues:
        iid     = issue.get("idReadable") or ""
        summary = issue.get("summary") or "(no summary)"
        if not iid:
            continue
        lines.append(f"# {iid}:")
        lines.append(f"- {summary}")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Weekly summary helpers
# ---------------------------------------------------------------------------

_DATE_PARSE_RE = re.compile(
    r"^#\s+(?:DATE:\s+)?(\d{4})[.\-](\d{1,2})[.\-](\d{1,2})"
    r"(?:\s+[A-Za-z][A-Za-z ,-]*)?\s*$",
    re.IGNORECASE,
)


def _parse_date_header(line: str) -> datetime_date | None:
    m = _DATE_PARSE_RE.match(line.strip())
    if not m:
        return None
    try:
        return datetime_date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def _collect_weekly_sections(
    lines: list[str],
    start_date: datetime_date,
    end_date: datetime_date,
) -> list[tuple[datetime_date, list[str]]]:
    sections: list[tuple[datetime_date, list[str]]] = []
    i = 0
    n = len(lines)

    while i < n:
        stripped = lines[i].strip()

        if _is_sep(stripped):
            i += 1
            continue

        date = _parse_date_header(stripped)
        if date is None:
            i += 1
            continue

        content: list[str] = []
        j = i + 1

        if j < n and _is_sep(lines[j].strip()):
            j += 1

        while j < n:
            l = lines[j].strip()
            if _is_sep(l) or _parse_date_header(l) is not None:
                break
            content.append(lines[j])
            j += 1

        if start_date <= date <= end_date:
            sections.append((date, content))

        i = j

    sections.sort(key=lambda x: x[0])
    return sections


def _weekly_sort_key(ticket_id: str) -> tuple[int, str]:
    u = ticket_id.upper()
    if u == _TODO_ID:
        return (2, u)
    if u == _OPS_ID:
        return (1, u)
    return (0, u)


# ---------------------------------------------------------------------------
# Weekly summary — notes-file-compatible format
# ---------------------------------------------------------------------------

_BULLET_TEXT_RE = re.compile(r"^-\s+(?:\[\d{2}:\d{2}\]\s+)?(.+)$")


def _strip_bullet_text(raw: str) -> str:
    m = _BULLET_TEXT_RE.match(raw.strip())
    return m.group(1).strip() if m else raw.strip().lstrip("- ").strip()


def _render_weekly_summary(
    sections: list[tuple[datetime_date, list[str]]],
    start_date: datetime_date,
    end_date: datetime_date,
) -> str:
    ticket_re = re.compile(r"^#\s+([A-Z0-9][A-Z0-9_\-]*):\s*$", re.IGNORECASE)
    bullet_re = re.compile(r"^-\s+(?:\[\d{2}:\d{2}\]\s+)?(.+)$")

    ticket_order:       list[str]                                  = []
    ticket_first_date:  dict[str, datetime_date]                   = {}
    ticket_last_date:   dict[str, datetime_date]                   = {}
    ticket_bullets:     dict[str, list[tuple[datetime_date, str]]] = {}

    ops_entries:  list[tuple[datetime_date, str]] = []
    todo_entries: list[tuple[datetime_date, str]] = []

    for day, content_lines in sections:
        current_tid: str | None = None

        for raw in content_lines:
            stripped = raw.strip()
            if not stripped:
                continue

            m = ticket_re.match(stripped)
            if m:
                current_tid = m.group(1).upper()
                if current_tid not in ticket_first_date:
                    ticket_order.append(current_tid)
                    ticket_first_date[current_tid] = day
                    ticket_bullets[current_tid]    = []
                ticket_last_date[current_tid] = day
                continue

            if current_tid and stripped.startswith("- "):
                bm = bullet_re.match(stripped)
                text = bm.group(1).strip() if bm else stripped[2:].strip()

                if current_tid == _OPS_ID:
                    ops_entries.append((day, text))
                elif current_tid == _TODO_ID:
                    todo_entries.append((day, text))
                else:
                    ticket_bullets[current_tid].append((day, text))

    regular_tickets = [
        t for t in ticket_order
        if t not in (_OPS_ID, _TODO_ID)
    ]
    regular_tickets.sort(
        key=lambda t: ticket_last_date.get(t, ticket_first_date[t]),
        reverse=True,
    )

    ops_entries.sort(key=lambda x: x[0],  reverse=True)
    todo_entries.sort(key=lambda x: x[0], reverse=True)

    title_line = (
        f"# Weekly Summary  "
        f"{start_date.strftime('%Y.%m.%d')} - {end_date.strftime('%Y.%m.%d')}"
        f"  ({start_date.strftime('%A')} to {end_date.strftime('%A')})"
    )
    out: list[str] = [_SEP, title_line, _SEP, ""]

    out.append("# TICKETS:")
    out.append("")

    if regular_tickets:
        for tid in regular_tickets:
            out.append(f"# {tid}:")
            bullets = ticket_bullets.get(tid, [])
            bullets_sorted = sorted(bullets, key=lambda x: x[0], reverse=True)
            if bullets_sorted:
                for d, text in bullets_sorted:
                    out.append(f"- [{d.strftime('%Y.%m.%d')}] {text}")
            else:
                d = ticket_last_date.get(tid, ticket_first_date[tid])
                out.append(f"- [{d.strftime('%Y.%m.%d')}] (no entries)")
            out.append("")
    else:
        out.append("- (no tickets this week)")
        out.append("")

    out.append(_SEP)
    out.append("# OPS:")
    out.append("")
    if ops_entries:
        for d, text in ops_entries:
            out.append(f"- [{d.strftime('%Y.%m.%d')}] {text}")
    else:
        out.append("- (no OPS entries this week)")
    out.append("")

    out.append(_SEP)
    out.append("# TODO:")
    out.append("")
    if todo_entries:
        for d, text in todo_entries:
            out.append(f"- [{d.strftime('%Y.%m.%d')}] {text}")
    else:
        out.append("- (no TODO entries this week)")
    out.append("")

    out.append(_SEP)

    return "\n".join(out) + "\n"


# ---------------------------------------------------------------------------
# Week index helpers  (for Notes - Weekly Search)
# ---------------------------------------------------------------------------

def _iso_week_key(d: datetime_date) -> str:
    iso = d.isocalendar()
    return f"{iso[0]:04d}-W{iso[1]:02d}"


def _week_bounds(iso_year: int, iso_week: int) -> tuple[datetime_date, datetime_date]:
    jan4   = datetime_date(iso_year, 1, 4)
    monday = jan4 - timedelta(days=jan4.weekday()) + timedelta(weeks=iso_week - 1)
    sunday = monday + timedelta(days=6)
    return monday, sunday


def _collect_all_weeks(
    lines: list[str],
) -> list[tuple[str, datetime_date, datetime_date]]:
    seen_weeks: dict[str, tuple[datetime_date, datetime_date]] = {}
    for line in lines:
        d = _parse_date_header(line.strip())
        if d is None:
            continue
        key = _iso_week_key(d)
        if key not in seen_weeks:
            iso      = d.isocalendar()
            mon, sun = _week_bounds(iso[0], iso[1])
            seen_weeks[key] = (mon, sun)

    return sorted(
        [(k, v[0], v[1]) for k, v in seen_weeks.items()],
        key=lambda x: x[1],
        reverse=True,
    )


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

class NotesHubCommand(sublime_plugin.WindowCommand):
    """Command: notes_hub | Palette: .notes - Hub"""

    _HELP = "__help__"
    _WEEKLY_SUMMARY = "__weekly_summary__"
    _WEEKLY_SEARCH = "__weekly_search__"

    def run(self) -> None:
        items = [
            ["Getting started", "How journal slices, YouTrack, and kb: refs work"],
            ["Weekly summary", "Scratch view of recent activity (by date)"],
            ["Weekly search", "Search within a week window"],
        ]
        self._hub_keys = [self._HELP, self._WEEKLY_SUMMARY, self._WEEKLY_SEARCH]
        self.window.show_quick_panel(
            items,
            self._on_hub,
            flags=sublime.MONOSPACE_FONT,
            selected_index=0,
            placeholder=".notes hub — Enter selects",
        )

    def _on_hub(self, index: int) -> None:
        if index < 0:
            return
        key = self._hub_keys[index]
        if key == self._HELP:
            self.window.run_command("notes_getting_started")
        elif key == self._WEEKLY_SUMMARY:
            self.window.run_command("notes_weekly_summary")
        elif key == self._WEEKLY_SEARCH:
            self.window.run_command("notes_weekly_search")


class NotesAddCommand(sublime_plugin.WindowCommand):
    """
    Command: notes_add  |  Palette: .notes - Add

    Flow:
      Quick panel -> today's headers, New note, YouTrack import, Issue.

    New note: empty journal slice (header + bullets), appends under today.
    Issue: YouTrack create slice. Import: same issue slice, fields filled.

    YouTrack comments: only if post_comments is on, the slice came from Add
    (existing heading / Import), and the heading looks like PROJ-1234.
    New note never comments.
    """

    _active: bool = False

    _panel_items:    list[list[str] | str]
    _panel_item_ids: list[str]

    _ID_CREATE_ISSUE       = "__create_issue__"
    _ID_NEW_NOTE           = "__new_note__"
    _ID_IMPORT_FROM_YT_ME  = "__import_from_yt_me__"
    _ID_IMPORT_FROM_YT_ALL = "__import_from_yt_all__"

    def run(self) -> None:
        if self._active:
            sublime.status_message("Notes: already waiting for input.")
            return
        self._active = True

        today_with_desc = _get_today_tickets_with_desc()

        def _make_item(tid: str, desc: str = "") -> list[str]:
            return [tid, desc] if desc else [tid]

        regular_items = [
            _make_item(tid, desc)
            for tid, desc in today_with_desc
            if tid not in _ADD_SKIP_TICKETS
        ]
        regular_ids = [
            tid for tid, _ in today_with_desc
            if tid not in _ADD_SKIP_TICKETS
        ]

        yt_configured = bool(_youtrack_token() and _youtrack_base() and _default_project())

        action_items: list[list[str]] = []
        action_ids:   list[str]       = []

        action_items.append([_NEW_NOTE_LABEL, "empty journal note (header + bullets)"])
        action_ids.append(self._ID_NEW_NOTE)

        if yt_configured:
            project = _default_project()
            action_items.append([
                _IMPORT_FROM_YT_ME_LABEL,
                f"project: {project}  |  assigned to me",
            ])
            action_ids.append(self._ID_IMPORT_FROM_YT_ME)

            action_items.append([
                _IMPORT_FROM_YT_ALL_LABEL,
                f"project: {project}  |  all unresolved",
            ])
            action_ids.append(self._ID_IMPORT_FROM_YT_ALL)

        action_items.append([_NEW_TICKET_LABEL, "empty YouTrack issue slice (create on commit)"])
        action_ids.append(self._ID_CREATE_ISSUE)

        self._panel_items    = regular_items + action_items
        self._panel_item_ids = regular_ids   + action_ids

        default_idx = 0

        self.window.show_quick_panel(
            self._panel_items,
            self._on_quick_panel_done,
            flags=sublime.MONOSPACE_FONT,
            selected_index=default_idx,
            placeholder="Select a ticket, import from YouTrack, or create new...",
        )

    def _on_quick_panel_done(self, index: int) -> None:
        if index == -1:
            self._active = False
            sublime.status_message("Notes: cancelled.")
            return
        selected = self._panel_item_ids[index]
        if selected == self._ID_NEW_NOTE:
            self._start_new_note()
        elif selected == self._ID_CREATE_ISSUE:
            self._start_create_issue()
        elif selected == self._ID_IMPORT_FROM_YT_ME:
            self._start_import_from_yt(mode="me")
        elif selected == self._ID_IMPORT_FROM_YT_ALL:
            self._start_import_from_yt(mode="all")
        else:
            self._ticket_id = selected
            self._prompt_description()

    # ------------------------------------------------------------------
    # Create new issue (full flow, embedded)
    # ------------------------------------------------------------------

    def _start_new_note(self) -> None:
        self._active = False
        _open_note_slice(self.window, "", [], replace=False)

    def _start_create_issue(self) -> None:
        self._active = False
        _open_create_issue_slice(self.window)

    # ------------------------------------------------------------------
    # Import from YouTrack  (mode="me" | mode="all")
    # ------------------------------------------------------------------

    def _start_import_from_yt(self, mode: str) -> None:
        """
        Kick off the import flow.

        mode="me"  -> fetch issues assigned to the current user
                      uses query: for: me #Unresolved project: {PROJECT}

        mode="all" -> fetch all unresolved issues in the project
                      uses query: #Unresolved project: {PROJECT}
        """
        project = _default_project()
        if not project:
            self._active = False
            _error_message(
                "Notes - default_project is not set.\n\n"
                "Run 'Notes - Settings' and set:\n"
                '  "default_project": "MYPROJECT"'
            )
            return

        label = "assigned to me" if mode == "me" else "all unresolved"
        sublime.status_message(
            f"Notes: fetching YouTrack issues for {project} ({label})..."
        )
        sublime.set_timeout_async(
            lambda: self._fetch_yt_for_import(project, mode), 0
        )

    def _fetch_yt_for_import(self, project: str, mode: str) -> None:
        err_msg: str | None = None
        issues: list[dict] = []
        try:
            if mode == "me":
                issues, err_msg = _fetch_my_assigned_issues_for_import(project)
            else:
                issues, err_msg = _fetch_project_issues_for_import(project)
        except Exception as exc:
            log.exception("YouTrack import fetch failed")
            err_msg = f"{type(exc).__name__}: {exc}"
            issues = []
        sublime.set_timeout(
            lambda: self._show_yt_import_panel(issues, project, mode, err_msg), 0
        )

    def _show_yt_import_panel(
        self,
        issues:   list[dict],
        project:  str,
        mode:     str,
        err_msg:  str | None,
    ) -> None:
        if not issues:
            self._active = False
            if err_msg:
                low = err_msg.lower()
                if "timed out" in low or "timeout" in low:
                    sublime.status_message(
                        f"Notes: YouTrack timed out fetching {project} issues "
                        f"— check network or increase api_timeout_sec. ({err_msg})"
                    )
                elif "cannot reach" in low or "network" in low or "urlopen" in low:
                    sublime.status_message(
                        f"Notes: Cannot reach YouTrack ({err_msg}) "
                        f"— check youtrack_base URL and connectivity."
                    )
                elif "401" in err_msg or "authentication" in low:
                    sublime.status_message(
                        "Notes: YouTrack authentication failed — check youtrack_token."
                    )
                elif "403" in err_msg or "permission" in low:
                    sublime.status_message(
                        "Notes: YouTrack permission denied — token needs Read Issue permission."
                    )
                elif "404" in err_msg or "not found" in low:
                    sublime.status_message(
                        f"Notes: YouTrack project '{project}' not found — check default_project."
                    )
                else:
                    sublime.status_message(
                        f"Notes: Could not fetch issues from YouTrack — {err_msg}"
                    )
            else:
                label = "assigned to you" if mode == "me" else "unresolved"
                sublime.status_message(
                    f"Notes: no {label} issues found in project {project}."
                )
            return

        self._yt_import_issues = issues
        panel_items: list[list[str]] = []
        for issue in issues:
            try:
                iid     = issue.get("idReadable") or ""
                summary = issue.get("summary") or "(no summary)"
                parsed  = _parse_youtrack_issue(issue)
                state   = parsed.get("state") or ""
                assign  = parsed.get("assignee") or ""
                sub     = "  ".join(x for x in [state, assign] if x)
                panel_items.append(
                    [f"{iid}  {summary}", sub] if sub else [f"{iid}  {summary}"]
                )
            except Exception:
                iid = (issue.get("idReadable") or "?") if isinstance(issue, dict) else "?"
                panel_items.append([str(iid)])

        mode_label = "assigned to me" if mode == "me" else "all unresolved"
        self.window.show_quick_panel(
            panel_items,
            self._on_yt_issue_selected,
            flags=sublime.MONOSPACE_FONT,
            selected_index=0,
            placeholder=f"Import from {project} ({mode_label})...",
        )

    def _on_yt_issue_selected(self, index: int) -> None:
        if index == -1:
            self._active = False
            sublime.status_message("Notes: import cancelled.")
            return
        issue = self._yt_import_issues[index]
        ticket_id = (issue.get("idReadable") or "").strip()
        if not ticket_id:
            self._active = False
            sublime.status_message("Notes: could not determine ticket ID.")
            return
        parsed = {}
        try:
            parsed = _parse_youtrack_issue(issue)
        except Exception:
            pass
        stub = {
            "summary": issue.get("summary") or parsed.get("summary") or "",
            "assignee": parsed.get("assignee") or "",
            "reporter": parsed.get("reporter") or "",
            "state": parsed.get("state") or "",
            "priority": parsed.get("priority") or "",
            "project": parsed.get("project") or _default_project(),
        }
        self._active = False
        _open_imported_issue_slice(self.window, ticket_id, stub)

    # ------------------------------------------------------------------
    # Description + write (multi-line via scratch buffer)
    # ------------------------------------------------------------------

    def _prompt_description(self) -> None:
        _open_add_description_scratch(self.window, self._ticket_id)
        # Command instance can finish; commit command owns the rest.
        self._active = False

    def _on_description(self, raw: str) -> None:
        """Legacy single-line path (kept for any callers)."""
        self._active = False
        _finish_add_description(
            self.window, self._ticket_id, raw, allow_yt_comment=True
        )

    def _on_cancel(self) -> None:
        self._active = False
        sublime.status_message("Notes: cancelled.")


class NotesSearchCommand(sublime_plugin.WindowCommand):
    """Command: notes_search  |  Palette: .notes - Search"""

    def run(self) -> None:
        try:
            index, all_tickets = _get_ticket_index()
        except RuntimeError as exc:
            _error_message(f"Notes - cannot read file:\n\n{exc}")
            return

        if not index:
            sublime.status_message(
                "Notes: no entries found — notes file is empty or has no tickets yet. "
                "Use 'Notes - Add' to create your first entry."
            )
            return

        self._index = index
        self._tickets = [t for t in all_tickets if t != _OPS_ID]
        if not self._tickets:
            sublime.status_message(
                "Notes: no searchable tickets found "
                "(only OPS entries present, or file is empty)."
            )
            return

        panel_items: list[list[str]] = []
        for tid in self._tickets:
            entries = self._index[tid]
            desc    = _first_bullet_from_index(entries)
            if tid == _TODO_ID:
                label = _TODO_SEARCH_LABEL
            else:
                done_mark = (
                    "  [DONE]"
                    if any("[DONE]<<-" in e for e in entries)
                    else ""
                )
                label = f"{tid}{done_mark}"
            panel_items.append([label, desc] if desc else [label])

        self.window.show_quick_panel(
            panel_items,
            self._on_select,
            flags=sublime.MONOSPACE_FONT,
            selected_index=0,
            placeholder="Search ticket ID or TODO...",
        )

    def _on_select(self, index: int) -> None:
        if index == -1:
            return
        tid = self._tickets[index]

        if tid == _TODO_ID:
            self._show_todo(self._index[tid])
            return

        entries    = self._index[tid]
        name       = f"Notes: {tid}"
        lines_out: list[str] = [
            _SEP,
            f"# {tid}:",
            _SEP,
            "",
        ]
        lines_out.extend(entries)
        content = "\n".join(lines_out) + "\n"
        _open_scratch_view(self.window, name, content)

    def _show_todo(self, local_entries: list[str]) -> None:
        local_lines: list[str] = [
            _SEP,
            "# TODO - local notes (all dates)",
            _SEP,
            "",
        ]
        local_lines.extend(local_entries)
        local_body = "\n".join(local_lines)

        project = _default_project()
        if project and _youtrack_token() and _youtrack_base():
            view = self.window.new_file()
            view.set_name("Notes: TODO")
            view.set_scratch(True)
            view.set_read_only(False)
            view.settings().set("stnotes_view_name", "Notes: TODO")
            loading_text = (
                local_body
                + f"\n\n{_SEP}\n# YouTrack open issues [{project}] - loading...\n{_SEP}\n"
            )
            view.run_command("notes_insert_text", {"text": loading_text})
            view.set_read_only(True)
            _assign_stnotes_syntax(view)

            def _fetch_and_fill() -> None:
                yt_issues = _fetch_my_open_issues(project)

                def _render() -> None:
                    yt_section = _build_yt_issues_section(project, yt_issues)
                    full       = local_body + "\n\n" + yt_section
                    view.set_read_only(False)
                    view.run_command("notes_replace_text", {"text": full})
                    view.set_read_only(True)

                sublime.set_timeout(_render, 0)

            sublime.set_timeout_async(_fetch_and_fill, 0)
        else:
            view = self.window.new_file()
            view.set_name("Notes: TODO")
            view.set_scratch(True)
            view.set_read_only(False)
            view.settings().set("stnotes_view_name", "Notes: TODO")
            view.run_command("notes_insert_text", {"text": local_body})
            view.set_read_only(True)
            _assign_stnotes_syntax(view)


class NotesSearchResolvedCommand(sublime_plugin.WindowCommand):
    """
    Command: notes_search_resolved
    Palette: .notes - Search Recently Resolved
    Menu: Notes → Search → Recently Resolved / Closed (mine)

    Lists finished (#Resolved / Done / Closed / …) issues assigned to me in
    default_project, newest-updated first. Enter opens in the browser.
    """

    def run(self) -> None:
        base = _youtrack_base()
        if not base:
            _error_message(
                "Notes - YouTrack base URL not configured.\n\n"
                "Run 'Notes - Settings' and set:\n"
                '  "youtrack_base": "https://youtrack.example.com/issue/"'
            )
            return
        base_err = _validate_youtrack_base(base)
        if base_err:
            _error_message(f"Notes - invalid youtrack_base:\n\n{base_err}")
            return
        if not _youtrack_token():
            _error_message(
                "Notes - youtrack_token is not set.\n\n"
                "Add it in Packages/User/ST4Notes.sublime-settings."
            )
            return

        project = _default_project()
        if not project:
            _error_message(
                "Notes - default_project is not set.\n\n"
                'Add e.g. "default_project": "MYPROJECT" in ST4Notes settings.'
            )
            return

        self._base = base
        self._project = project
        sublime.status_message(
            f"Notes: loading recently resolved {project} issues for me..."
        )
        sublime.set_timeout_async(self._fetch_async, 0)

    def _fetch_async(self) -> None:
        issues, err = _fetch_my_recently_resolved(self._project)
        sublime.set_timeout(lambda: self._show_panel(issues, err), 0)

    def _show_panel(self, issues: list[dict], err: str | None) -> None:
        if err and not issues:
            _error_message(
                f"Notes: could not fetch resolved issues for {self._project}\n\n"
                f"{err}"
            )
            return
        if not issues:
            sublime.status_message(
                f"Notes: no recently resolved/closed issues for you in "
                f"{self._project}."
            )
            return

        self._panel_issues = issues
        panel_items: list = []
        for issue in issues:
            iid = issue.get("idReadable") or ""
            summary = issue.get("summary") or "(no summary)"
            parsed = _parse_youtrack_issue(issue)
            state = parsed.get("state") or "Resolved"
            when = _issue_updated_label(issue)
            meta = f"{state}  ·  {when}" if when else state
            try:
                panel_items.append(
                    sublime.QuickPanelItem(
                        trigger=f"{iid}  {summary}",
                        annotation=meta,
                    )
                )
            except Exception:
                panel_items.append([f"{iid}  {summary}", meta])

        sublime.status_message(
            f"Notes: {len(issues)} resolved/closed issue(s) (newest first)"
        )
        self.window.show_quick_panel(
            panel_items,
            self._on_select,
            flags=sublime.MONOSPACE_FONT,
            selected_index=0,
            placeholder=(
                f"{self._project} · my resolved/closed · newest first — "
                "type to filter, Enter opens"
            ),
        )

    def _on_select(self, index: int) -> None:
        if index == -1:
            return
        issues = getattr(self, "_panel_issues", None) or []
        if index < 0 or index >= len(issues):
            return
        iid = issues[index].get("idReadable") or ""
        if not iid:
            return
        url = f"{self._base}{iid}"
        _open_in_browser(url)
        sublime.status_message(f"Notes: opened {url}")


class NotesOpenIssueCommand(sublime_plugin.WindowCommand):
    """Command: notes_open_issue  |  Palette: .notes - Open Issue"""

    _OPEN_BY_ID        = "__open_by_id__"
    _UNASSIGNED_ISSUES = "__unassigned_issues__"
    _ALL_ISSUES        = "__all_issues__"

    def run(self) -> None:
        base = _youtrack_base()
        if not base:
            _error_message(
                "Notes - YouTrack base URL not configured.\n\n"
                "Run 'Notes - Settings' and set:\n"
                '  "youtrack_base": "https://youtrack.example.com/issue/"'
            )
            return

        base_err = _validate_youtrack_base(base)
        if base_err:
            _error_message(f"Notes - invalid youtrack_base:\n\n{base_err}")
            return

        self._base = base

        try:
            index, all_tickets = _get_ticket_index()
        except RuntimeError as exc:
            _error_message(f"Notes - cannot read file:\n\n{exc}")
            return

        tickets = [
            t for t in all_tickets
            if t not in (_TODO_ID, _OPS_ID)
        ]

        project       = _default_project()
        yt_configured = bool(_youtrack_token() and project)

        panel_items: list[list[str]] = [[_OPEN_BY_ID_LABEL, "type any ticket ID"]]
        self._tickets: list[str]     = [self._OPEN_BY_ID]

        if yt_configured:
            panel_items.append([
                f"All issues ({project})",
                "YouTrack: pick assignee, then optional text filter",
            ])
            self._tickets.append(self._ALL_ISSUES)

            panel_items.append([
                f"Unassigned issues ({project})",
                "YouTrack: unassigned open issues",
            ])
            self._tickets.append(self._UNASSIGNED_ISSUES)

        for tid in tickets:
            entries   = index[tid]
            desc      = _first_bullet_from_index(entries)
            done_mark = (
                "  [DONE]"
                if any("[DONE]<<-" in e for e in entries)
                else ""
            )
            label = f"{tid}{done_mark}"
            panel_items.append([label, desc] if desc else [label])
            self._tickets.append(tid)

        self.window.show_quick_panel(
            panel_items,
            self._on_select,
            flags=sublime.MONOSPACE_FONT,
            selected_index=0,
            placeholder="Select ticket, open by ID, or view YouTrack issues...",
        )

    def _on_select(self, index: int) -> None:
        if index == -1:
            return
        tid = self._tickets[index]

        if tid == self._OPEN_BY_ID:
            self.window.show_input_panel(
                "Ticket ID to open (e.g. PROJ-1234):",
                "",
                self._on_manual_id,
                None,
                None,
            )
            return

        if tid == self._ALL_ISSUES:
            self._show_all_issues()
            return

        if tid == self._UNASSIGNED_ISSUES:
            self._show_unassigned_issues()
            return

        url = f"{self._base}{tid}"
        _open_in_browser(url)
        sublime.status_message(f"Notes: opened {url}")

    def _on_manual_id(self, raw: str) -> None:
        tid = raw.strip().upper()
        if not tid:
            sublime.status_message("Notes: no ticket ID entered - cancelled.")
            return
        if not _ISSUE_ID_RE.match(tid):
            _error_message(
                "Notes - invalid ticket ID.\n\n"
                "Expected form: PROJ-1234"
            )
            return
        url = f"{self._base}{tid}"
        _open_in_browser(url)
        sublime.status_message(f"Notes: opened {url}")

    # ------------------------------------------------------------------
    # All issues — assignee filter, optional text narrow, then panel
    # ------------------------------------------------------------------

    _ALL_ASSIGNEE_ANY = "__all_assignee_any__"
    _ALL_ASSIGNEE_TYPE = "__all_assignee_type__"
    _ALL_ASSIGNEE_ME = "__all_assignee_me__"
    _REFINE_FILTER = "__refine_filter__"

    def _show_all_issues(self) -> None:
        project = _default_project()
        if not project:
            sublime.status_message("Notes: default_project is not set.")
            return
        sublime.status_message(
            f"Notes: loading {project} issues for assignee filter..."
        )
        sublime.set_timeout_async(lambda: self._load_assignee_filter(project), 0)

    def _load_assignee_filter(self, project: str) -> None:
        # Load project issues once — assignees come from those issues only
        # (not the whole YouTrack user directory).
        issues, err = _fetch_all_project_issues(project)
        users = _assignees_from_issues(issues)
        me_login, me_name = _fetch_current_user_login()
        sublime.set_timeout(
            lambda: self._show_assignee_filter_panel(
                project, users, err, me_login, me_name, issues=issues
            ),
            0,
        )

    def _show_assignee_filter_panel(
        self,
        project: str,
        users: list[dict],
        err: str | None,
        me_login: str | None,
        me_name: str | None,
        issues: list[dict] | None = None,
    ) -> None:
        if err and not issues:
            _error_message(
                f"Notes: could not load {project} issues for assignee filter\n\n"
                f"{err}"
            )
            return

        self._assignee_filter_project = project
        self._assignee_filter_issues = list(issues or [])
        panel_items: list[list[str]] = []
        self._assignee_filter_keys: list[str] = []

        panel_items.append([
            "All unresolved",
            f"Any assignee in {project} ({len(self._assignee_filter_issues)})",
        ])
        self._assignee_filter_keys.append(self._ALL_ASSIGNEE_ANY)

        me_on_project = False
        if me_login:
            me_lower = me_login.lower()
            me_on_project = any(
                (u.get("login") or "").lower() == me_lower for u in users
            )
            label = f"me  ({me_login})"
            if me_name:
                label = f"me  {me_name} ({me_login})"
            hint = (
                "Issues assigned to you"
                if me_on_project
                else "No open issues assigned to you in this project"
            )
            panel_items.append([label, hint])
            self._assignee_filter_keys.append(self._ALL_ASSIGNEE_ME)

        panel_items.append([
            "Type login…",
            "Enter a login not listed (queries YouTrack)",
        ])
        self._assignee_filter_keys.append(self._ALL_ASSIGNEE_TYPE)

        for u in users:
            login = u.get("login") or ""
            full = u.get("fullName") or ""
            # Login first so typing "jdoe" matches explicitly in the quick panel
            if full:
                panel_items.append([login, full])
            else:
                panel_items.append([login])
            self._assignee_filter_keys.append(login)

        n = len(users)
        sublime.status_message(
            f"Notes: {n} assignee(s) on open {project} issues"
        )
        self.window.show_quick_panel(
            panel_items,
            self._on_assignee_filter_select,
            flags=sublime.MONOSPACE_FONT,
            selected_index=0,
            placeholder=(
                f"{project}: assignees on open issues — type to filter"
            ),
        )

    def _on_assignee_filter_select(self, index: int) -> None:
        if index == -1:
            return
        key = self._assignee_filter_keys[index]
        project = getattr(self, "_assignee_filter_project", "") or _default_project()

        if key == self._ALL_ASSIGNEE_ANY:
            self._start_fetch_all(project, assignee_login=None, use_cache=True)
            return

        if key == self._ALL_ASSIGNEE_ME:
            sublime.status_message("Notes: resolving current user...")
            sublime.set_timeout_async(
                lambda: self._resolve_me_and_fetch(project), 0
            )
            return

        if key == self._ALL_ASSIGNEE_TYPE:
            self.window.show_input_panel(
                "Assignee login (e.g. jdoe):",
                "",
                lambda raw: self._on_typed_assignee(project, raw),
                None,
                None,
            )
            return

        self._start_fetch_all(project, assignee_login=key, use_cache=True)

    def _resolve_me_and_fetch(self, project: str) -> None:
        login, _name = _fetch_current_user_login()
        if not login:
            sublime.set_timeout(
                lambda: _error_message(
                    "Notes: could not resolve current YouTrack user "
                    "(check youtrack_token)."
                ),
                0,
            )
            return
        sublime.set_timeout(
            lambda: self._start_fetch_all(
                project, assignee_login=login, use_cache=True
            ),
            0,
        )

    def _on_typed_assignee(self, project: str, raw: str) -> None:
        login = (raw or "").strip().lstrip("@")
        if not login:
            sublime.status_message("Notes: no assignee entered — cancelled.")
            return
        sublime.status_message(f"Notes: resolving {login}...")
        sublime.set_timeout_async(
            lambda: self._resolve_typed_assignee_async(project, login), 0
        )

    def _resolve_typed_assignee_async(self, project: str, login: str) -> None:
        # Prefer matches among project assignees already loaded.
        project_users = _assignees_from_issues(
            getattr(self, "_assignee_filter_issues", None)
        )
        q = login.lower()
        proj_exact = [
            u for u in project_users
            if (u.get("login") or "").lower() == q
        ]
        proj_fuzzy = [
            u for u in project_users
            if q in (u.get("login") or "").lower()
            or q in (u.get("fullName") or "").lower()
        ]

        def done_project(users: list[dict], use_cache: bool) -> None:
            exact = [
                u for u in users
                if (u.get("login") or "").lower() == q
            ]
            if exact:
                self._start_fetch_all(
                    project,
                    assignee_login=exact[0]["login"],
                    use_cache=use_cache,
                )
                return
            if len(users) == 1:
                self._start_fetch_all(
                    project,
                    assignee_login=users[0]["login"],
                    use_cache=use_cache,
                )
                return
            if users:
                self._assignee_filter_project = project
                panel_items: list[list[str]] = []
                self._assignee_filter_keys = []
                for u in users:
                    lg = u.get("login") or ""
                    full = u.get("fullName") or ""
                    panel_items.append([lg, full] if full else [lg])
                    self._assignee_filter_keys.append(lg)
                self.window.show_quick_panel(
                    panel_items,
                    self._on_assignee_filter_select,
                    flags=sublime.MONOSPACE_FONT,
                    selected_index=0,
                    placeholder=f"Matches for {login} — pick one",
                )
                return
            # No directory match — query YouTrack directly with typed login.
            self._start_fetch_all(
                project, assignee_login=login, use_cache=False
            )

        if proj_exact or proj_fuzzy:
            sublime.set_timeout(
                lambda: done_project(proj_exact or proj_fuzzy, True), 0
            )
            return

        # Fallback: instance user directory (escape hatch for users with
        # no open issues on this project right now).
        users, _err = _fetch_youtrack_users(login)
        sublime.set_timeout(lambda: done_project(users, False), 0)

    def _filter_cached_issues_by_assignee(
        self, issues: list[dict], assignee_login: str
    ) -> list[dict]:
        want = (assignee_login or "").strip().lower()
        if not want:
            return list(issues)
        out: list[dict] = []
        for issue in issues:
            parsed = _parse_youtrack_issue(issue)
            if (parsed.get("assignee_login") or "").strip().lower() == want:
                out.append(issue)
        return out

    def _start_fetch_all(
        self,
        project: str,
        assignee_login: str | None,
        use_cache: bool = True,
    ) -> None:
        cached = getattr(self, "_assignee_filter_issues", None)
        cache_proj = getattr(self, "_assignee_filter_project", None)
        if (
            use_cache
            and isinstance(cached, list)
            and cache_proj == project
        ):
            if assignee_login:
                issues = self._filter_cached_issues_by_assignee(
                    cached, assignee_login
                )
                label = assignee_login
            else:
                issues = list(cached)
                label = "all"
            self._after_fetch_all(
                issues, project, None, assignee_label=label
            )
            return

        if assignee_login:
            sublime.status_message(
                f"Notes: fetching {project} issues for {assignee_login}..."
            )
        else:
            sublime.status_message(f"Notes: fetching all issues for {project}...")
        sublime.set_timeout_async(
            lambda: self._fetch_all(project, assignee_login), 0
        )

    def _fetch_all(
        self, project: str, assignee_login: str | None = None
    ) -> None:
        if assignee_login:
            issues, err_msg = _fetch_issues_assigned_to(project, assignee_login)
        else:
            issues, err_msg = _fetch_all_project_issues(project)
        label = assignee_login or "all"
        sublime.set_timeout(
            lambda: self._after_fetch_all(
                issues, project, err_msg, assignee_label=label
            ),
            0,
        )

    def _after_fetch_all(
        self,
        issues: list[dict],
        project: str,
        err_msg: str | None,
        assignee_label: str = "all",
    ) -> None:
        if err_msg and not issues:
            _error_message(
                f"Notes: could not fetch issues for {project}\n\n{err_msg}"
            )
            return
        if not issues:
            sublime.status_message(
                f"Notes: no unresolved issues in {project} ({assignee_label})."
            )
            return

        self._show_panel_all(issues, project, None, assignee_label=assignee_label)

    def _show_panel_all(
        self,
        issues: list[dict],
        project: str,
        err_msg: str | None,
        assignee_label: str = "all",
        text_filter: str = "",  # kept for API compat; unused (palette filters live)
    ) -> None:
        if err_msg and not issues:
            _error_message(
                f"Notes: could not fetch issues for {project}\n\n{err_msg}"
            )
            return
        if not issues:
            sublime.status_message(
                f"Notes: no unresolved issues in {project} ({assignee_label})."
            )
            return

        self._panel_all_issues = issues
        panel_items: list = []

        for issue in issues:
            iid     = issue.get("idReadable") or ""
            summary = issue.get("summary") or "(no summary)"
            parsed  = _parse_youtrack_issue(issue)
            state   = parsed.get("state") or ""
            assign  = parsed.get("assignee") or "Unassigned"
            meta    = f"{state}  |  {assign}" if state else assign
            # trigger = only text matched while typing (cmd-palette style).
            # annotation is display-only — avoids vague fuzzy hits on assignee names.
            try:
                panel_items.append(
                    sublime.QuickPanelItem(
                        trigger=f"{iid}  {summary}",
                        annotation=meta,
                    )
                )
            except Exception:
                panel_items.append([f"{iid}  {summary}", meta])

        sublime.status_message("")
        self.window.show_quick_panel(
            panel_items,
            self._on_all_issues_select,
            flags=sublime.MONOSPACE_FONT,
            selected_index=0,
            placeholder=(
                f"{project} · {assignee_label} · {len(issues)} issue(s) — "
                "type to filter, Enter opens"
            ),
        )

    def _on_all_issues_select(self, index: int) -> None:
        if index == -1:
            return
        if index < 0 or index >= len(self._panel_all_issues):
            return
        issue = self._panel_all_issues[index]
        iid   = issue.get("idReadable") or ""
        if not iid:
            return
        url = f"{self._base}{iid}"
        _open_in_browser(url)
        sublime.status_message(f"Notes: opened {url}")

    # ------------------------------------------------------------------
    # Unassigned issues — quick panel
    # ------------------------------------------------------------------

    def _show_unassigned_issues(self) -> None:
        project = _default_project()
        if not project:
            sublime.status_message("Notes: default_project is not set.")
            return
        sublime.status_message(
            f"Notes: fetching unassigned issues for {project}..."
        )
        sublime.set_timeout_async(lambda: self._fetch_unassigned(project), 0)

    def _fetch_unassigned(self, project: str) -> None:
        issues, err_msg = _fetch_unassigned_issues(project)
        sublime.set_timeout(
            lambda: self._show_panel_unassigned(issues, project, err_msg), 0
        )

    def _show_panel_unassigned(
        self, issues: list[dict], project: str, err_msg: str | None
    ) -> None:
        if err_msg and not issues:
            _error_message(
                f"Notes: could not fetch unassigned issues for {project}\n\n{err_msg}"
            )
            return
        if not issues:
            sublime.status_message(
                f"Notes: no unassigned open issues found in {project}."
            )
            return

        self._panel_unassigned = issues
        panel_items: list = []
        for issue in issues:
            iid     = issue.get("idReadable") or ""
            summary = issue.get("summary") or "(no summary)"
            parsed  = _parse_youtrack_issue(issue)
            state   = parsed.get("state") or ""
            try:
                panel_items.append(
                    sublime.QuickPanelItem(
                        trigger=f"{iid}  {summary}",
                        annotation=state,
                    )
                )
            except Exception:
                panel_items.append([f"{iid}  {summary}", state])

        sublime.status_message("")
        self.window.show_quick_panel(
            panel_items,
            self._on_unassigned_select,
            flags=sublime.MONOSPACE_FONT,
            selected_index=0,
            placeholder=f"Unassigned open issues in {project}...",
        )

    def _on_unassigned_select(self, index: int) -> None:
        if index == -1:
            return
        issue = self._panel_unassigned[index]
        iid   = issue.get("idReadable") or ""
        if not iid:
            return
        url = f"{self._base}{iid}"
        _open_in_browser(url)
        sublime.status_message(f"Notes: opened {url}")


class NotesWeeklySearchCommand(sublime_plugin.WindowCommand):
    """Command: notes_weekly_search  |  Palette: .notes - Weekly Search"""

    def run(self) -> None:
        try:
            lines = _read_notes()
        except RuntimeError as exc:
            _error_message(f"Notes - cannot read file:\n\n{exc}")
            return
        if not lines:
            sublime.status_message("Notes: file is empty.")
            return

        self._lines = lines
        weeks = _collect_all_weeks(lines)

        if not weeks:
            sublime.status_message("Notes: no dated entries found.")
            return

        panel_items: list[list[str]] = []
        for week_key, monday, sunday in weeks:
            label = (
                f"{week_key}  "
                f"({monday.strftime('%b %d')} - {sunday.strftime('%b %d, %Y')})"
            )
            day_count = sum(
                1 for line in lines
                if (d := _parse_date_header(line.strip())) is not None
                and monday <= d <= sunday
            )
            day_hint = f"{day_count} day{'s' if day_count != 1 else ''} with entries"
            panel_items.append([label, day_hint])

        self._weeks = weeks

        self.window.show_quick_panel(
            panel_items,
            self._on_select,
            flags=sublime.MONOSPACE_FONT,
            selected_index=0,
            placeholder="Select week...",
        )

    def _on_select(self, index: int) -> None:
        if index == -1:
            return
        week_key, monday, sunday = self._weeks[index]
        sections = _collect_weekly_sections(self._lines, monday, sunday)

        if not sections:
            sublime.status_message(f"Notes: no entries found for {week_key}.")
            return

        content = _render_weekly_summary(sections, monday, sunday)
        title   = (
            f"Notes: Weekly {week_key}  "
            f"[{monday.strftime('%Y.%m.%d')} - {sunday.strftime('%Y.%m.%d')}]"
        )
        _open_scratch_view(self.window, title, content)


class NotesWeeklySummaryCommand(sublime_plugin.WindowCommand):
    """Command: notes_weekly_summary  |  Palette: .notes - Weekly Summary"""

    def run(self) -> None:
        try:
            lines = _read_notes()
        except RuntimeError as exc:
            _error_message(f"Notes - cannot read file:\n\n{exc}")
            return
        if not lines:
            sublime.status_message("Notes: file is empty.")
            return

        today      = datetime.now().date()
        start_date = today - timedelta(days=7)

        sections = _collect_weekly_sections(lines, start_date, today)

        if not sections:
            sublime.status_message(
                f"Notes: no entries found between {start_date} and {today}."
            )
            return

        content = _render_weekly_summary(sections, start_date, today)
        title = (
            f"Notes: Weekly Summary  "
            f"[{start_date.strftime('%Y.%m.%d')} - {today.strftime('%Y.%m.%d')}]"
            f"  ({start_date.strftime('%A')} to {today.strftime('%A')})"
        )
        _open_scratch_view(self.window, title, content)


# ---------------------------------------------------------------------------
# Notes - Create Issue  (standalone — kept for direct palette use)
# ---------------------------------------------------------------------------

class NotesCreateIssueCommand(sublime_plugin.WindowCommand):
    """Command: notes_create_issue | same empty issue slice as Add → Issue."""

    def run(self) -> None:
        _open_create_issue_slice(self.window)


def _is_journal_file_view(view: sublime.View) -> bool:
    fname = view.file_name() or ""
    if not fname:
        return False
    try:
        return os.path.realpath(fname) == _notes_file()
    except OSError:
        return False


def _protect_journal_view(view: sublime.View) -> None:
    if view is None or not view.is_valid():
        return
    try:
        view.settings().set("stnotes_journal_file", True)
        if not view.is_read_only():
            view.set_read_only(True)
        _assign_stnotes_syntax(view)
    except Exception:
        pass


class NotesViewCommand(sublime_plugin.WindowCommand):
    """Command: notes_view | Palette: .notes - View (read-only journal)."""

    def run(self) -> None:
        try:
            if os.path.isdir(_notes_file()):
                _error_message(
                    f"Notes - path is a directory, not a file:\n{_notes_file()}"
                )
                return
            if not os.path.exists(_notes_file()):
                if not seed_notes_demo_if_needed():
                    _write_notes([])
        except RuntimeError as exc:
            _error_message(f"Notes - cannot create file:\n\n{exc}")
            return

        view = self.window.open_file(_notes_file())
        view.settings().set("stnotes_journal_file", True)

        def _after_load() -> None:
            if view.is_loading():
                sublime.set_timeout(_after_load, 50)
                return
            _protect_journal_view(view)

        _after_load()


class NotesEditCommand(NotesViewCommand):
    """Back-compat: notes_edit → same as notes_view."""


class NotesJournalDetectListener(sublime_plugin.EventListener):
    """Mark the on-disk journal read-only and enable Alt+Up/Down / Enter."""

    def on_load_async(self, view: sublime.View) -> None:
        self._mark_and_protect(view)

    def on_activated_async(self, view: sublime.View) -> None:
        self._mark_and_protect(view)

    def _mark_and_protect(self, view: sublime.View) -> None:
        try:
            if view.settings().get("stnotes_slice"):
                return
            if _is_journal_file_view(view) or view.settings().get("stnotes_journal_file"):
                sublime.set_timeout(lambda v=view: _protect_journal_view(v), 0)
        except Exception:
            pass


class NotesSettingsCommand(sublime_plugin.WindowCommand):
    """Command: notes_settings  |  Palette: .notes - Settings"""

    def run(self) -> None:
        self.window.run_command(
            "edit_settings",
            {
                "base_file": "${packages}/notes/ST4Notes.sublime-settings",
                "default": (
                    "// notes — User overlay (tokens stay here, not in the package repo)\n"
                    "// Settings file: ST4Notes.sublime-settings (legacy basename)\n"
                    "// Optional: override notes_file if Documents is OneDrive or localized\n"
                    "//   (e.g. ~/OneDrive/Documents/ST4Notes or ~/Dokumente/ST4Notes).\n"
                    "{\n"
                    '    "youtrack_base": "https://youtrack.example.com/issue/",\n'
                    '    "youtrack_token": "",\n'
                    '    "gitlab_base": "https://gitlab.example.com/",\n'
                    '    "gitlab_token": "",\n'
                    '    "default_project": "",\n'
                    '    "issue_stages": [],\n'
                    '    "post_comments": false\n'
                    "}\n"
                ),
            },
        )


# ---------------------------------------------------------------------------
# Issue ID pattern
# ---------------------------------------------------------------------------

_ISSUE_ID_RE = re.compile(
    r"^[A-Z][A-Z0-9_]{0,30}-\d+$",
    re.IGNORECASE,
)



def _invalidate_kb_cache() -> None:
    try:
        import kb.kb_plugin as _kbp
        _kbp._invalidate_kb_cache()
    except Exception:
        pass


def _is_knowledge_base_view(view: sublime.View) -> bool:
    if view.settings().get("kb_file") or view.settings().get("stnotes_kb_file"):
        return True
    fname = view.file_name() or ""
    if not fname:
        return False
    try:
        return os.path.realpath(fname) == _knowledge_base_file()
    except OSError:
        return False


# ---------------------------------------------------------------------------
# URL + ticket hover
# ---------------------------------------------------------------------------

class NotesUrlHoverListener(sublime_plugin.EventListener):

    def on_hover(
        self,
        view: sublime.View,
        point: int,
        hover_zone: int,
    ) -> None:
        if hover_zone != sublime.HOVER_TEXT:
            return

        if not _is_stnotes_view(view) and not _is_notes_scratch_view(view):
            return
        if _is_knowledge_base_view(view):
            return

        line_region = view.line(point)
        line_text   = view.substr(line_region)
        col         = point - line_region.begin()

        # 1. Check for a URL under the cursor
        url: str | None = None
        for m in _URL_RE.finditer(line_text):
            if m.start() <= col <= m.end():
                url = m.group(0)
                break

        if url:
            if _is_youtrack_host(url):
                base      = _youtrack_base()
                ticket_id = self._ticket_id_from_url(url, base)
                if ticket_id:
                    self._show_issue_popup(view, point, url, ticket_id)
                else:
                    self._show_plain_popup(view, point, url)
            elif _parse_gitlab_mr_url(url):
                if _gitlab_token() and _gitlab_base() and _is_gitlab_host(url):
                    self._show_mr_popup(view, point, url)
                else:
                    view.show_popup(
                        _build_mr_hover_html(url, None),
                        flags=sublime.HIDE_ON_MOUSE_MOVE_AWAY,
                        location=point,
                        max_width=740,
                        on_navigate=self._on_navigate,
                    )
            else:
                self._show_plain_popup(view, point, url)
            return

        # 1b. Inside a kb:… span → leave hover to NotesKbEventListener (not YouTrack)
        try:
            if _pure.kb_ref_at_col(line_text, col):
                return
        except Exception:
            pass

        # 2. Check for a ticket-ID header:  # PROJ-1234:
        header_id = self._ticket_id_from_header(line_text)
        if header_id:
            base = _youtrack_base()
            if base:
                constructed_url = f"{base}{header_id}"
                self._show_issue_popup(view, point, constructed_url, header_id)
            return

        # 3. Check for an inline ticket ID in bullet text or anywhere in the line
        inline_id = self._ticket_id_from_inline(line_text, col)
        if inline_id:
            base = _youtrack_base()
            if base:
                constructed_url = f"{base}{inline_id}"
                self._show_issue_popup(view, point, constructed_url, inline_id)
            return

        # 4. Fallback: word under cursor (original behaviour)
        word_id = self._ticket_id_from_word(view, point)
        if word_id:
            base = _youtrack_base()
            if base:
                constructed_url = f"{base}{word_id}"
                self._show_issue_popup(view, point, constructed_url, word_id)

    # ------------------------------------------------------------------
    # Inline scan: find any PROJ-NNN overlapping the cursor column
    # ------------------------------------------------------------------

    def _ticket_id_from_inline(self, line_text: str, col: int) -> str | None:
        """
        Scan the entire line for ticket IDs (PROJ-NNN pattern).
        Return the one whose span contains the cursor column.
        Skip IDs that sit inside a kb:… ref (e.g. kb:other/proj-5124).
        """
        for m in _INLINE_TICKET_RE.finditer(line_text):
            if m.start() <= col <= m.end():
                try:
                    if _pure.kb_ref_at_col(line_text, col):
                        continue
                except Exception:
                    pass
                candidate = m.group(1).upper()
                if candidate not in (_TODO_ID, _OPS_ID):
                    return candidate
        return None

    # ------------------------------------------------------------------

    def _show_issue_popup(self, view, point, url, ticket_id):
        if _youtrack_token():
            view.show_popup(
                self._loading_html(ticket_id, url),
                flags=sublime.HIDE_ON_MOUSE_MOVE_AWAY,
                location=point,
                max_width=740,
                on_navigate=self._on_navigate,
            )
            sublime.set_timeout_async(
                lambda: self._fetch_and_update(view, point, ticket_id, url), 0
            )
        else:
            view.show_popup(
                _build_hover_html(ticket_id, url, None),
                flags=sublime.HIDE_ON_MOUSE_MOVE_AWAY,
                location=point,
                max_width=740,
                on_navigate=self._on_navigate,
            )

    def _show_plain_popup(self, view, point, url):
        view.show_popup(
            _build_hover_html("", url, None),
            flags=sublime.HIDE_ON_MOUSE_MOVE_AWAY,
            location=point,
            max_width=520,
            on_navigate=self._on_navigate,
        )

    def _show_mr_popup(self, view, point, url):
        view.show_popup(
            (
                "<body id='stnotes-hover' "
            f"style='{_hover_body_style()}'>"
                f"<a href='open:{_h(url)}' style='color:#56b6c2'>{_h(url)}</a>"
                "<div style='color:#5c6370;margin-top:6px'>Loading MR…</div>"
                "</body>"
            ),
            flags=sublime.HIDE_ON_MOUSE_MOVE_AWAY,
            location=point,
            max_width=780,
            on_navigate=self._on_navigate,
        )
        sublime.set_timeout_async(
            lambda: self._fetch_mr_and_update(view, point, url), 0
        )

    def _fetch_mr_and_update(self, view, point, url):
        parsed = _parse_gitlab_mr_url(url)
        if not parsed:
            html = _build_mr_hover_html(url, None)
            sublime.set_timeout(lambda: self._safe_update_popup(view, html), 0)
            return
        _host, project_path, iid = parsed
        info = _fetch_gitlab_mr_info(project_path, iid)
        if info and info.get("__not_found__"):
            html = _build_mr_hover_html(url, None, not_found=True)
        else:
            html = _build_mr_hover_html(url, info)
        sublime.set_timeout(lambda: self._safe_update_popup(view, html), 0)

    def _fetch_and_update(self, view, point, ticket_id, url):
        raw = _fetch_youtrack_issue(ticket_id)
        if raw is _NOT_FOUND:
            html = _build_hover_html(ticket_id, url, None, not_found=True)
        elif raw is None:
            html = _build_hover_html(ticket_id, url, None)
        else:
            info = _parse_youtrack_issue(raw)
            html = _build_hover_html(ticket_id, url, info)
        sublime.set_timeout(lambda: self._safe_update_popup(view, html), 0)

    @staticmethod
    def _safe_update_popup(view, html):
        try:
            if view is None or not view.is_valid():
                return
            view.update_popup(html)
        except Exception:
            pass

    def _ticket_id_from_url(self, url, base):
        if not base:
            return None
        if not url.lower().startswith(base.lower()):
            return None
        suffix = url[len(base):].split("?")[0].split("#")[0].strip("/")
        if suffix and _ISSUE_ID_RE.match(suffix.upper()):
            return suffix.upper()
        return None

    def _ticket_id_from_header(self, line_text: str) -> str | None:
        """
        Match lines like:  # PROJ-1234:
        Also handles the weekly summary format:  # PROJ-1234 - full history
        """
        # Standard header with colon
        m = re.match(
            r"^\s*#\s+([A-Z][A-Z0-9_\-]{1,63}):\s*$",
            line_text,
            re.IGNORECASE,
        )
        if m:
            candidate = m.group(1).upper()
            if _ISSUE_ID_RE.match(candidate):
                return candidate

        # Weekly/search scratch header: "# PROJ-1234 - full history" or "# PROJ-1234:"
        m2 = re.match(
            r"^\s*#\s+([A-Z][A-Z0-9_\-]{1,63})(?::|[\s\-])",
            line_text,
            re.IGNORECASE,
        )
        if m2:
            candidate = m2.group(1).upper()
            if _ISSUE_ID_RE.match(candidate) and candidate not in (_TODO_ID, _OPS_ID):
                return candidate

        return None

    def _ticket_id_from_word(self, view, point):
        word_region = view.word(point)
        if word_region.empty():
            return None
        raw = view.substr(word_region).strip(".,;:()[]{}\"'`")
        if not raw:
            return None
        if not _ISSUE_ID_RE.match(raw.upper()):
            return None
        candidate = raw.upper()
        if candidate in (_TODO_ID, _OPS_ID):
            return None
        return candidate

    def _loading_html(self, ticket_id, url):
        safe_url = _h(url)
        return (
            "<body id='stnotes-hover' "
            f"style='{_hover_body_style()}'>"
            f"<div><a href='open:{safe_url}' "
            f"style='color:#56b6c2;text-decoration:none;font-weight:bold'>"
            f"&#128279; {_h(ticket_id)}</a>"
            f"&nbsp;<span style='color:#5c6370'>loading...</span></div>"
            "</body>"
        )


    def _on_navigate(self, href):
        # Decode HTML entities that _h() may have introduced in hrefs
        href = (
            (href or "")
            .replace("&amp;", "&")
            .replace("&lt;", "<")
            .replace("&gt;", ">")
            .replace("&quot;", '"')
        )
        if href.startswith("open:"):
            target = href[len("open:"):]
            _open_in_browser(target)
            return


class NotesJumpTicketCommand(sublime_plugin.TextCommand):
    """Command: notes_jump_ticket | Alt+Up/Down between # TICKET: headers."""

    def is_enabled(self) -> bool:
        return bool(self.view.settings().get("stnotes_journal_file"))

    def run(self, edit: sublime.Edit, forward: bool = True) -> None:
        text = self.view.substr(sublime.Region(0, self.view.size()))
        regions = _pure.notes_ticket_header_regions(text)
        if not regions:
            sublime.status_message("Notes: no ticket headers in journal")
            return
        caret = self.view.sel()[0].begin() if self.view.sel() else 0
        if self.view.sel():
            sel = self.view.sel()[0]
            for a, b in regions:
                if sel.begin() == a and sel.end() == b:
                    caret = a
                    break
        idx = _pure.next_slice_field_index(regions, caret, forward=bool(forward))
        if idx is None:
            return
        a, b = regions[idx]
        self.view.sel().clear()
        self.view.sel().add(sublime.Region(a, b))
        self.view.show_at_center(a)


class NotesEditJournalNoteCommand(sublime_plugin.TextCommand):
    """Enter in View: open a note slice for the selected # HEADER: block."""

    def is_enabled(self) -> bool:
        return bool(self.view.settings().get("stnotes_journal_file"))

    def run(self, edit: sublime.Edit) -> None:
        view = self.view
        window = view.window()
        if window is None:
            return
        text = view.substr(sublime.Region(0, view.size()))
        caret = view.sel()[0].begin() if view.sel() else 0
        if view.sel():
            sel = view.sel()[0]
            for a, b in _pure.notes_ticket_header_regions(text):
                if sel.begin() == a and sel.end() == b:
                    caret = a
                    break
        block = _pure.find_journal_block_at_offset(text, caret)
        if not block:
            _open_note_slice(window, "", [], replace=False)
            return
        orig_date = _pure.journal_date_iso(block.get("date"))
        _open_note_slice(
            window,
            str(block.get("ticket") or ""),
            list(block.get("body") or []),
            replace=True,
            orig_ticket=str(block.get("ticket") or ""),
            orig_date=orig_date,
        )


class NotesInsertTextCommand(sublime_plugin.TextCommand):
    """Internal: insert text at position 0."""

    def run(self, edit: sublime.Edit, text: str = "") -> None:
        self.view.insert(edit, 0, text)


class NotesReplaceTextCommand(sublime_plugin.TextCommand):
    """Internal: replace entire view (Windows-safe write when the file is open)."""

    def run(self, edit: sublime.Edit, text: str = "") -> None:
        self.view.replace(edit, sublime.Region(0, self.view.size()), text)


class NotesFillCreateIssueAssigneeCommand(sublime_plugin.TextCommand):
    """Fill empty assignee: after async /users/me (does not steal the caret)."""

    def run(self, edit: sublime.Edit, value: str = "") -> None:
        if not self.view.settings().get("stnotes_create_issue"):
            return
        email = (value or "").strip()
        if not email:
            return
        text = self.view.substr(sublime.Region(0, self.view.size()))
        m = re.search(r"(?m)^assignee:[ \t]*$", text)
        if not m:
            return
        self.view.replace(
            edit, sublime.Region(m.start(), m.end()), f"assignee: {email}"
        )


class NotesLockIssueTicketLineCommand(sublime_plugin.TextCommand):
    """Restore the read-only ticket: line on an issue slice."""

    def run(self, edit: sublime.Edit, value: str = "") -> None:
        if not self.view.settings().get("stnotes_create_issue"):
            return
        want = (value or "").strip()
        text = self.view.substr(sublime.Region(0, self.view.size()))
        m = re.search(r"(?m)^ticket:[ \t]*.*$", text)
        line = f"ticket: {want}".rstrip()
        if m:
            if text[m.start() : m.end()] == line:
                return
            self.view.replace(edit, sublime.Region(m.start(), m.end()), line)
            return
        ins = re.search(r"(?m)^project:", text)
        pt = ins.start() if ins else 0
        self.view.insert(edit, pt, line + "\n")


class NotesIssueTicketLockListener(sublime_plugin.ViewEventListener):
    """ticket: is display-only — YouTrack assigns it, or import already filled it."""

    @classmethod
    def is_applicable(cls, settings: sublime.Settings) -> bool:
        return bool(settings.get("stnotes_create_issue"))

    @classmethod
    def applies_to_primary_view_only(cls) -> bool:
        return True

    def on_modified_async(self) -> None:
        view = self.view
        if not view.is_valid() or view.settings().get("stnotes_issue_ticket_rewriting"):
            return
        locked = str(view.settings().get("stnotes_issue_ticket") or "").strip()
        text = view.substr(sublime.Region(0, view.size()))
        m = re.search(r"(?m)^ticket:[ \t]*(.*)$", text)
        current = (m.group(1).strip() if m else "")
        if current == locked and m:
            return
        if not m and not locked:
            return
        view.settings().set("stnotes_issue_ticket_rewriting", True)
        view.run_command("notes_lock_issue_ticket_line", {"value": locked})

        def _clear() -> None:
            if view.is_valid():
                view.settings().erase("stnotes_issue_ticket_rewriting")

        sublime.set_timeout(_clear, 0)


class NotesReplaceTextCommand(sublime_plugin.TextCommand):
    """Internal: replace entire view content."""

    def run(self, edit: sublime.Edit, text: str = "") -> None:
        self.view.replace(edit, sublime.Region(0, self.view.size()), text)


class NotesSliceNextFieldCommand(sublime_plugin.TextCommand):
    """Command: notes_slice_next_field | Alt+Up/Down select next/prev field value."""

    def is_enabled(self) -> bool:
        return bool(self.view.settings().get("stnotes_slice"))

    def run(self, edit: sublime.Edit, forward: bool = True) -> None:
        view = self.view
        text = view.substr(sublime.Region(0, view.size()))
        fields = _pure.slice_field_regions(
            text, kind=str(view.settings().get("stnotes_slice_kind") or "")
        )
        if not fields:
            return
        caret = view.sel()[0].begin() if view.sel() else 0
        if view.sel():
            sel = view.sel()[0]
            for a, b in fields:
                if sel.begin() == a and sel.end() == b:
                    caret = a
                    break
        idx = _pure.next_slice_field_index(fields, caret, forward=bool(forward))
        if idx is None:
            return
        a, b = fields[idx]
        view.sel().clear()
        view.sel().add(sublime.Region(a, b))
        view.show(a)


# ---------------------------------------------------------------------------
# Package lifecycle (ST recommendation)
# ---------------------------------------------------------------------------

_SETTINGS_LISTENER_KEY = "st4notes-settings-listener"


def _on_settings_change():
    """Invalidate caches when User settings change."""
    try:
        _invalidate_notes_cache()
    except Exception:
        pass
    try:
        global _index_cache_mtime, _index_cache_data
        with _index_cache_lock:
            _index_cache_mtime = None
            _index_cache_data = None
    except Exception:
        pass
    try:
        _invalidate_users_cache()
    except Exception:
        pass
    try:
        _invalidate_kb_cache()
    except Exception:
        pass


_GETTING_STARTED_NAME = ".notes getting started"


def _notes_seed_demo_enabled() -> bool:
    try:
        return bool(_settings().get("notes_seed_demo", True))
    except Exception:
        return True


def _notes_example_text() -> str:
    fallback = (
        "# =============================================================================\n"
        "# 2026.10.6\n"
        "# =============================================================================\n\n"
        "# TODO:\n"
        "- Command Palette → type .notes\n"
        "- Hover kb:registry/cleanup-images\n"
    )
    return _nc.load_package_resource("examples/ST4Notes.example", fallback)


def seed_notes_demo_if_needed() -> bool:
    """Fill missing/empty journal with examples. Never overwrites non-empty files."""
    if not _notes_seed_demo_enabled():
        return False
    try:
        path = _notes_file()
    except Exception:
        return False
    if os.path.exists(path):
        try:
            if os.path.getsize(path) > 0:
                return False
        except OSError:
            return False
    try:
        _write_notes(_notes_example_text().splitlines())
        return True
    except Exception:
        return False


def _getting_started_text() -> str:
    return _nc.load_package_resource(
        "messages/install.txt",
        ".notes — getting started\n\nCommand Palette → type .notes\n",
    )


def show_getting_started(window: sublime.Window | None = None) -> None:
    window = window or sublime.active_window()
    if window is None:
        return
    for v in window.views():
        if v.name() == _GETTING_STARTED_NAME:
            window.focus_view(v)
            return
    view = window.new_file()
    view.set_name(_GETTING_STARTED_NAME)
    view.set_scratch(True)
    view.settings().set("stnotes_getting_started", True)
    view.run_command("notes_insert_text", {"text": _getting_started_text()})
    view.set_read_only(True)


def _getting_started_flag_path() -> str:
    return os.path.join(sublime.cache_path(), "notes", "getting_started_shown")


def _maybe_show_getting_started() -> None:
    try:
        if not bool(_settings().get("show_getting_started", True)):
            return
    except Exception:
        return
    flag = _getting_started_flag_path()
    try:
        if os.path.exists(flag):
            return
    except OSError:
        pass
    window = sublime.active_window()
    if window is None:
        sublime.set_timeout(_maybe_show_getting_started, 400)
        return
    show_getting_started(window)
    try:
        os.makedirs(os.path.dirname(flag), exist_ok=True)
        with open(flag, "w", encoding="utf-8") as fh:
            fh.write("1\n")
    except OSError:
        pass


def _after_plugin_loaded() -> None:
    try:
        if seed_notes_demo_if_needed():
            sublime.status_message("Notes: seeded example journal")
    except Exception:
        pass
    _maybe_show_getting_started()


class NotesGettingStartedCommand(sublime_plugin.WindowCommand):
    """Command: notes_getting_started | Hub: Getting started"""

    def run(self) -> None:
        show_getting_started(self.window)


def plugin_loaded():
    try:
        s = sublime.load_settings(_SETTINGS_FILE)
        s.clear_on_change(_SETTINGS_LISTENER_KEY)
        s.add_on_change(_SETTINGS_LISTENER_KEY, _on_settings_change)
    except Exception:
        pass
    # Defer so windows exist and load_resource works (Package Control / symlink).
    sublime.set_timeout(_after_plugin_loaded, 400)


def plugin_unloaded():
    try:
        s = sublime.load_settings(_SETTINGS_FILE)
        s.clear_on_change(_SETTINGS_LISTENER_KEY)
    except Exception:
        pass
    # Drop in-memory caches (may hold issue summaries / assignee names)
    try:
        _invalidate_notes_cache()
    except Exception:
        pass
    try:
        global _index_cache_mtime, _index_cache_data
        with _index_cache_lock:
            _index_cache_mtime = None
            _index_cache_data = None
    except Exception:
        pass
    try:
        _invalidate_users_cache()
    except Exception:
        pass
    try:
        _invalidate_kb_cache()
    except Exception:
        pass
    try:
        with _current_user_lock:
            _CURRENT_USER_CACHE.clear()
    except Exception:
        pass
