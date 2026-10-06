"""Knowledge base package `kb` (Command Palette: .kb)."""
from __future__ import annotations

import base64
import os
import re
import tempfile
import threading

import sublime
import sublime_plugin

from . import kb_common as _nc
from . import kb_pure as _pure

_h = _nc._h
_error_message = _nc._error_message
_knowledge_base_file = _nc._knowledge_base_file
_assign_stnotes_syntax = _nc._assign_stnotes_syntax
_is_stnotes_view = _nc._is_stnotes_view
_is_notes_scratch_view = _nc._is_notes_scratch_view

class _MergedSettings:
    def get(self, key, default=None):
        return _nc.setting(key, default)

def _settings():
    return _MergedSettings()
_SEP = _nc._SEP
_NOTES_MAX_FILE_BYTES = _nc._NOTES_MAX_FILE_BYTES
_hover_font_family = _nc._hover_font_family
_hover_font_size = _nc._hover_font_size
_hover_line_height = _nc._hover_line_height
_hover_body_style = _nc._hover_body_style
_hover_pre_style = _nc._hover_pre_style
_hover_sep_style = _nc._hover_sep_style
_hover_code_block_style = _nc._hover_code_block_style



class KbInsertTextCommand(sublime_plugin.TextCommand):
    """Internal: insert text at position 0."""

    def run(self, edit: sublime.Edit, text: str = "") -> None:
        self.view.insert(edit, 0, text)


class KbReplaceTextCommand(sublime_plugin.TextCommand):
    """Internal: replace entire view content."""

    def run(self, edit: sublime.Edit, text: str = "") -> None:
        self.view.replace(edit, sublime.Region(0, self.view.size()), text)


class KbInsertAtCaretCommand(sublime_plugin.TextCommand):
    """Internal: insert text at primary caret."""

    def run(self, edit: sublime.Edit, text: str = "") -> None:
        if not text:
            return
        for region in self.view.sel():
            self.view.replace(edit, region, text)
            break


def _sep() -> str:
    return _SEP


def _notes_max_file_bytes() -> int:
    return _NOTES_MAX_FILE_BYTES


# ---------------------------------------------------------------------------
# Settings helpers
# ---------------------------------------------------------------------------

def kb_hover_enabled() -> bool:
    try:
        return bool(_settings().get("kb_hover_enabled", True))
    except Exception:
        return True


def kb_popup_max_width() -> int:
    try:
        return max(320, min(1600, int(_settings().get("kb_popup_max_width", 780))))
    except Exception:
        return 780


def kb_popup_max_height() -> int:
    try:
        return max(200, min(2000, int(_settings().get("kb_popup_max_height", 900))))
    except Exception:
        return 900


def kb_preview_on_highlight() -> bool:
    try:
        return bool(_settings().get("kb_preview_on_highlight", True))
    except Exception:
        return True



# ---------------------------------------------------------------------------
# Knowledge base (single file: topics → subjects)
# ---------------------------------------------------------------------------
#
# File format (knowledge_base_file):
#
#   # TOPIC: Registry
#   ## SUBJECT: cleanup images
#   commands / notes here...
#
#   ## SUBJECT: retention
#   ...
#
# Inline ref in daily notes:  kb:registry/cleanup-images
# Command Palette: .kb - Hub | Search | Create Subject | Insert Ref
#

_KB_TOPIC_RE = _pure._KB_TOPIC_RE
_KB_SUBJECT_RE = _pure._KB_SUBJECT_RE
_KB_REF_RE = _pure._KB_REF_RE
_KB_META_CREATED_RE = _pure._KB_META_CREATED_RE
_KB_META_TAGS_RE = _pure._KB_META_TAGS_RE

_KB_FENCE_OPEN_RE = re.compile(r"^```([\w.+-]*)\s*$")
_KB_FENCE_CLOSE_RE = re.compile(r"^```\s*$")
_SECRET_TOKEN_RE = re.compile(r"⟦secret:([A-Za-z0-9+/=]+)⟧")
_SECRET_FENCE_LANGS = frozenset({"secret", "secrets", "credential", "credentials", "password", "passwords"})
_kb_copy_lock = threading.Lock()
_kb_copy_payloads: dict[str, str] = {}
_kb_copy_seq = 0
_KB_COPY_MAX = 64


def _kb_register_copy(payload: str) -> str:
    global _kb_copy_seq
    with _kb_copy_lock:
        _kb_copy_seq += 1
        key = str(_kb_copy_seq)
        _kb_copy_payloads[key] = payload
        if len(_kb_copy_payloads) > _KB_COPY_MAX:
            keep = sorted(_kb_copy_payloads, key=lambda k: int(k))[-(_KB_COPY_MAX // 2):]
            for k in list(_kb_copy_payloads):
                if k not in keep:
                    _kb_copy_payloads.pop(k, None)
        return key


def _kb_take_copy(key: str):
    with _kb_copy_lock:
        return _kb_copy_payloads.get(key)


def _kb_encode_secret(plain: str) -> str:
    return "⟦secret:" + base64.b64encode(plain.encode("utf-8")).decode("ascii") + "⟧"


def _kb_decode_secret_b64(blob: str):
    try:
        return base64.b64decode(blob.encode("ascii")).decode("utf-8")
    except Exception:
        return None


def _kb_has_selection(view: sublime.View) -> bool:
    return any(not r.empty() for r in view.sel())


class KbObfuscateSelectionCommand(sublime_plugin.TextCommand):
    """Command: kb_obfuscate_selection | Context: .kb - Obfuscate (selection only)."""

    def is_visible(self) -> bool:
        return _kb_has_selection(self.view) and not self.view.is_read_only()

    def is_enabled(self) -> bool:
        return self.is_visible()

    def run(self, edit: sublime.Edit) -> None:
        if self.view.is_read_only():
            sublime.status_message("KB: view is read-only")
            return
        regions = [r for r in self.view.sel() if not r.empty()]
        if not regions:
            return
        for region in sorted(regions, key=lambda r: r.begin(), reverse=True):
            plain = self.view.substr(region)
            if plain.strip():
                self.view.replace(edit, region, _kb_encode_secret(plain))
        sublime.status_message("KB: selection obfuscated (⟦secret:…⟧)")


class KbDeobfuscateSelectionCommand(sublime_plugin.TextCommand):
    """Command: kb_deobfuscate_selection | Context: .kb - Deobfuscate (selection only)."""

    def is_visible(self) -> bool:
        return _kb_has_selection(self.view) and not self.view.is_read_only()

    def is_enabled(self) -> bool:
        return self.is_visible()

    def run(self, edit: sublime.Edit) -> None:
        if self.view.is_read_only():
            sublime.status_message("KB: view is read-only")
            return
        targets = [r for r in self.view.sel() if not r.empty()]
        n = 0
        for region in sorted(targets, key=lambda r: r.begin(), reverse=True):
            raw = self.view.substr(region)
            m = _SECRET_TOKEN_RE.search(raw) or _SECRET_TOKEN_RE.fullmatch(raw.strip())
            if not m:
                continue
            plain = _kb_decode_secret_b64(m.group(1))
            if plain is None:
                continue
            if region.size() != len(m.group(0)):
                mm = _SECRET_TOKEN_RE.search(raw)
                if not mm:
                    continue
                region = sublime.Region(region.begin() + mm.start(), region.begin() + mm.end())
                plain = _kb_decode_secret_b64(mm.group(1))
            if plain is None:
                continue
            self.view.replace(edit, region, plain)
            n += 1
        sublime.status_message(
            f"KB: deobfuscated {n} secret(s)" if n else "KB: no secret token in selection"
        )


# Back-compat command names (old keymaps)
class NotesObfuscateSelectionCommand(KbObfuscateSelectionCommand):
    pass


class NotesDeobfuscateSelectionCommand(KbDeobfuscateSelectionCommand):
    pass


def _kb_ui_font_family(view=None) -> str:
    """Alias — all minihtml uses Berkeley Mono via notes_common."""
    return _hover_font_family()


def _kb_format_prose_line(line: str) -> str:
    m = re.match(r"^#{1,6}\s+(\d+)[.)]?\s+(.*)$", line)
    if m:
        return f"{m.group(1)}. {m.group(2)}".rstrip()
    m = re.match(r"^#{1,6}\s+(.*)$", line)
    if m:
        return m.group(1).rstrip()
    return line


def _kb_parse_body_segments(body: str) -> list:
    lines = (body or "").splitlines()
    segments, buf, in_fence, lang = [], [], False, ""
    def flush_text():
        nonlocal buf
        if buf:
            segments.append({"type": "text", "text": "\n".join(buf)})
            buf = []
    def flush_code():
        nonlocal buf, in_fence, lang
        segments.append({"type": "code", "lang": lang or "", "text": "\n".join(buf)})
        buf, in_fence, lang = [], False, ""
    for line in lines:
        if not in_fence:
            m = _KB_FENCE_OPEN_RE.match(line)
            if m:
                flush_text()
                in_fence, lang, buf = True, m.group(1) or "", []
                continue
            buf.append(line)
            continue
        if _KB_FENCE_CLOSE_RE.match(line):
            flush_code()
            continue
        buf.append(line)
    if in_fence:
        flush_code()
    else:
        flush_text()
    return segments


def _kb_prose_to_html(text: str) -> str:
    lines = [_kb_format_prose_line(ln) for ln in (text or "").splitlines()]
    lines = [_SECRET_TOKEN_RE.sub("⟦secret:••••⟧", ln) for ln in lines]
    return _h("\n".join(lines)).replace("\n", "<br>")


def _kb_code_block_html(code: str, *, lang: str = "", copy_id: str | None = None) -> str:
    normalized = (code or "").replace("\t", "    ")
    label = (lang or "").strip() or "text"
    is_secret = label.lower() in _SECRET_FENCE_LANGS
    if is_secret:
        label = "secret"
        nlines = max(1, normalized.count("\n") + (1 if normalized else 0))
        body_html = _h("•" * 12 + f"  ({nlines} line{'s' if nlines != 1 else ''} hidden)")
    else:
        body_html = _h(normalized)
    header = f"<div style='margin:0 0 6px;white-space:normal;color:#9da5b4'>{_h(label)}"
    if copy_id:
        header += (
            f"&nbsp;&nbsp;<a href='kb-copy:{_h(copy_id)}' "
            f"style='color:#98c379;text-decoration:none' title='Copy'>⧉</a>"
        )
    header += "</div>"
    return (
        f"<div style='{_hover_code_block_style()}'>"
        f"{header}<div style='{_hover_pre_style()}'>{body_html}</div></div>"
    )


def _kb_render_segments_html(segments, *, max_chars: int = 12000) -> str:
    parts, used = [], 0
    for seg in segments:
        if used >= max_chars:
            parts.append("<div style='color:#5c6370'>…truncated</div>")
            break
        if seg.get("type") == "code":
            code = seg.get("text") or ""
            cid = _kb_register_copy(code)
            show = code if len(code) <= 6000 else code[:6000] + "\n…"
            used += len(show)
            parts.append(_kb_code_block_html(show, lang=seg.get("lang") or "", copy_id=cid))
        else:
            t = seg.get("text") or ""
            if not t.strip():
                continue
            chunk = t if used + len(t) <= max_chars else t[: max(0, max_chars - used)] + "\n…"
            used += len(chunk)
            parts.append(f"<div style='color:#abb2bf;margin:4px 0'>{_kb_prose_to_html(chunk)}</div>")
    return "".join(parts) if parts else "<div style='color:#5c6370'>(empty)</div>"


_KB_DEFAULT_TOPIC = _pure._KB_DEFAULT_TOPIC
_KB_DEFAULT_TOPIC_SLUG = _pure._KB_DEFAULT_TOPIC_SLUG

# Marker: file is still the package-shipped demo (safe to replace on upgrade only if unchanged)
_KB_DEMO_MARKER = "# .notes KB demo — replace or extend; safe to commit to git"

def _kb_example_text() -> str:
    """Starter KB shipped in examples/; fallback if the resource is missing."""
    fallback = (
        "# .notes KB demo — replace or extend; safe to commit to git\n"
        "# Format: # TOPIC: … / ## SUBJECT: …   refs: kb:topic/subject\n\n"
        "# TOPIC: Registry\n\n"
        "## SUBJECT: cleanup images\n"
        "- created: 2026-10-06\n"
        "- modified: 2026-10-06\n"
        "Retention then garbage-collect. Dry-run first.\n\n"
        "```bash\n"
        "registryctl gc now --dry-run\n"
        "```\n\n"
        "# TOPIC: other\n\n"
        "## SUBJECT: EXAMPLE-TICKET\n"
        "- created: 2026-10-06\n"
        "- modified: 2026-10-06\n"
        "Ticket-shaped subject. Short ref: kb:example-ticket\n"
    )
    return _nc.load_package_resource("examples/ST4Notes-kb.example", fallback)



_kb_index_lock = threading.Lock()
_kb_index_mtime: float | None = None
_kb_index_data: dict | None = None


def _kb_slug(name: str) -> str:
    return _pure.kb_slug(name)

def _kb_ref(topic_slug: str, subject_slug: str) -> str:
    return _pure.kb_ref(topic_slug, subject_slug)

def _kb_parse_ref(ref: str) -> tuple[str, str] | None:
    return _pure.kb_parse_ref(ref)

def kb_ref_at_col(line_text: str, col: int) -> str | None:
    return _pure.kb_ref_at_col(line_text, col)

def _invalidate_kb_cache() -> None:
    global _kb_index_mtime, _kb_index_data
    with _kb_index_lock:
        _kb_index_mtime = None
        _kb_index_data = None


def _kb_file_mode() -> int:
    """0600 private by default; 0644 when kb_git_friendly (shareable in a repo)."""
    try:
        if bool(_settings().get("kb_git_friendly", True)):
            return 0o644
    except Exception:
        pass
    return 0o600


def _kb_seed_demo_enabled() -> bool:
    try:
        return bool(_settings().get("kb_seed_demo", True))
    except Exception:
        return True


def _kb_write_new_file(path: str, content: str) -> None:
    payload = "\n".join(_pure.format_kb_lines((content or "").splitlines()))
    if payload:
        payload += "\n"
    _nc.atomic_write_utf8(
        path, payload, mode=_kb_file_mode(), replace_command="kb_replace_text"
    )


def _ensure_kb_file() -> str:
    """Create knowledge-base file with demo content if missing/empty. Return path."""
    path = _knowledge_base_file()
    parent = os.path.dirname(path) or os.path.expanduser("~")
    try:
        os.makedirs(parent, exist_ok=True)
    except OSError as exc:
        raise RuntimeError(f"Cannot create KB directory '{parent}': {exc}") from exc

    need_seed = False
    if not os.path.exists(path):
        need_seed = _kb_seed_demo_enabled()
    else:
        try:
            if os.path.getsize(path) == 0 and _kb_seed_demo_enabled():
                need_seed = True
        except OSError:
            pass

    if need_seed:
        try:
            _kb_write_new_file(path, _kb_example_text())
        except OSError as exc:
            raise RuntimeError(f"Cannot create knowledge base file: {exc}") from exc
        _invalidate_kb_cache()
    elif not os.path.exists(path):
        # seed disabled — still create an empty file so path is valid
        try:
            _kb_write_new_file(path, "")
        except OSError as exc:
            raise RuntimeError(f"Cannot create knowledge base file: {exc}") from exc
        _invalidate_kb_cache()
    return path


def seed_kb_demo_if_needed() -> bool:
    """Called from plugin_loaded. Returns True if demo was written."""
    if not _kb_seed_demo_enabled():
        return False
    try:
        path = _knowledge_base_file()
    except Exception:
        return False
    exists = os.path.exists(path)
    empty = False
    if exists:
        try:
            empty = os.path.getsize(path) == 0
        except OSError:
            empty = False
    if exists and not empty:
        return False
    try:
        _ensure_kb_file()
        return True
    except Exception:
        return False


def _read_kb_lines(force: bool = False) -> list[str]:
    path = _ensure_kb_file()
    if os.path.isdir(path):
        raise RuntimeError(f"Knowledge base path '{path}' is a directory.")
    size = os.path.getsize(path)
    if size > _notes_max_file_bytes():
        raise RuntimeError(
            f"Knowledge base file exceeds {_notes_max_file_bytes()} bytes."
        )
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read().splitlines()


def _parse_kb_index(lines: list[str]) -> dict:
    return _pure.parse_kb_index(lines)

def _get_kb_index(force: bool = False) -> dict:
    global _kb_index_mtime, _kb_index_data
    path = _ensure_kb_file()
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        mtime = 0.0
    with _kb_index_lock:
        if (
            not force
            and _kb_index_mtime == mtime
            and _kb_index_data is not None
        ):
            return _kb_index_data
    lines = _read_kb_lines()
    data = _parse_kb_index(lines)
    with _kb_index_lock:
        _kb_index_mtime = mtime
        _kb_index_data = data
    return data


def _kb_find_subject(topic_slug: str, subject_slug: str) -> dict | None:
    return _pure.find_subject(_get_kb_index(), topic_slug, subject_slug)

def _kb_ensure_two_groups(window: sublime.Window) -> None:
    """Ensure a two-column layout exists."""
    if window.num_groups() < 2:
        window.set_layout({
            "cols": [0.0, 0.5, 1.0],
            "rows": [0.0, 1.0],
            "cells": [[0, 0, 1, 1], [1, 0, 2, 1]],
        })


def _kb_opposite_group(window: sublime.Window, origin: int) -> int:
    """The other column from ``origin`` (creates a second column if needed)."""
    _kb_ensure_two_groups(window)
    n = window.num_groups()
    origin = max(0, min(int(origin), n - 1))
    for g in range(n):
        if g != origin:
            return g
    return min(1, n - 1)


def _kb_capture_target_group(window: sublime.Window) -> int:
    """Remember destination pane = opposite of where the user invoked the command."""
    origin = window.active_group()
    target = _kb_opposite_group(window, origin)
    window.settings().set("stnotes_kb_origin_group", origin)
    window.settings().set("stnotes_kb_target_group", target)
    try:
        av = window.active_view()
        window.settings().set(
            "stnotes_kb_origin_view_id", av.id() if av is not None else None
        )
    except Exception:
        window.settings().erase("stnotes_kb_origin_view_id")
    return target


def _kb_target_group(window: sublime.Window) -> int:
    """Pane for KB preview + edit-slice (opposite of invoke pane)."""
    _kb_ensure_two_groups(window)
    g = window.settings().get("stnotes_kb_target_group")
    try:
        gi = int(g)
        if 0 <= gi < window.num_groups():
            return gi
    except (TypeError, ValueError):
        pass
    return _kb_opposite_group(window, window.active_group())


def _kb_ensure_side_group(window: sublime.Window) -> int:
    """Backward-compatible: ensure two columns; return target pane for KB UI."""
    return _kb_target_group(window)


def _kb_subject_scratch_content(subject: dict) -> tuple[str, str]:
    topic = subject.get("topic") or subject.get("topic_slug") or ""
    title = subject.get("title") or subject.get("slug") or ""
    ref = subject.get("ref") or ""
    body = subject.get("body") or "(empty)"
    content = (
        f"{_sep()}\n"
        f"# TOPIC: {topic}\n"
        f"## SUBJECT: {title}\n"
        f"{_sep()}\n"
        f"- ref: {ref}\n"
        f"\n"
        f"{body}\n"
    )
    name = f".notes KB · {ref}" if ref else f".notes KB · {title}"
    return name, content


def _kb_find_preview_view(window: sublime.Window):
    for view in window.views():
        try:
            if view.settings().get("stnotes_kb_preview"):
                return view
        except Exception:
            continue
    return None


def _kb_open_subject_side(window: sublime.Window, subject: dict) -> None:
    """Open subject in the edit-slice tab (Cmd/Ctrl+Shift+Enter commits to the KB file)."""
    _kb_open_subject_editor(window, subject)


_kb_preview_seq = 0
_KB_PREVIEW_REGION = "kb_preview_hl"
# Do not use KEEP_OPEN_ON_FOCUS_LOST: focusing another pane then leaves the
# list visible but unfocused. Preview must not steal keyboard from the panel.
_KB_PANEL_FLAGS = sublime.MONOSPACE_FONT


def _kb_find_kb_file_view(window: sublime.Window):
    path = _ensure_kb_file()
    for v in window.views():
        fname = v.file_name() or ""
        if not fname:
            continue
        try:
            if os.path.realpath(fname) == path:
                return v
        except OSError:
            continue
    return None


def _kb_restore_origin(window: sublime.Window) -> None:
    """Put keyboard back on the pane that invoked the command (for the overlay)."""
    try:
        vid = window.settings().get("stnotes_kb_origin_view_id")
        if vid is not None:
            for v in window.views():
                if v.id() == vid:
                    window.focus_view(v)
                    return
        origin = window.settings().get("stnotes_kb_origin_group")
        if isinstance(origin, int) and 0 <= int(origin) < window.num_groups():
            window.focus_group(int(origin))
    except Exception:
        pass


def _kb_prepare_preview_then(window: sublime.Window, then) -> None:
    """Open/move the KB file into the preview pane, restore origin, then run ``then``.

    Highlight preview can only *scroll* afterward — opening/moving a view
    steals focus and leaves the quick panel unfocused on the other column.
    """
    try:
        path = _ensure_kb_file()
    except RuntimeError as exc:
        _error_message(f"Notes - knowledge base:\n\n{exc}")
        return
    preview_group = _kb_target_group(window)
    view = _kb_find_kb_file_view(window) or _kb_find_preview_view(window)
    if view is not None:
        try:
            view.settings().set("stnotes_kb_preview", True)
            gi, _vi = window.get_view_index(view)
            if gi == preview_group and not view.is_loading():
                _assign_stnotes_syntax(view)
                _kb_protect_file_view(view)
                _kb_restore_origin(window)
                then()
                return
            if gi != preview_group:
                window.set_view_index(view, preview_group, 0)
        except Exception:
            pass
    if view is None:
        try:
            view = window.open_file(path, sublime.FORCE_GROUP, preview_group)
        except TypeError:
            window.focus_group(preview_group)
            view = window.open_file(path)
    try:
        view.settings().set("stnotes_kb_preview", True)
    except Exception:
        pass
    _assign_stnotes_syntax(view)

    def _ready(retries: int = 40) -> None:
        if view.is_loading() and retries > 0:
            sublime.set_timeout(lambda: _ready(retries - 1), 30)
            return
        _assign_stnotes_syntax(view)
        _kb_protect_file_view(view)
        _kb_restore_origin(window)
        sublime.set_timeout(then, 0)

    sublime.set_timeout(lambda: _ready(), 0)


def _kb_preview_file_views(window: sublime.Window) -> list:
    """KB file view(s) already open for highlight-preview (no focus steal)."""
    found: list = []
    seen: set[int] = set()
    for view in (_kb_find_kb_file_view(window), _kb_find_preview_view(window)):
        if view is None or not view.is_valid():
            continue
        vid = view.id()
        if vid in seen:
            continue
        seen.add(vid)
        found.append(view)
    return found


def _kb_scroll_preview_line(window: sublime.Window, goto_line: int) -> None:
    """Select + center ``goto_line`` (0-based) in the preview pane; keep panel focus."""
    if goto_line < 0:
        return
    for view in _kb_preview_file_views(window):
        if view.is_loading():
            continue
        try:
            pt = view.text_point(int(goto_line), 0)
            line_reg = view.line(pt)
            view.sel().clear()
            view.sel().add(sublime.Region(pt, pt))
            view.add_regions(
                _KB_PREVIEW_REGION,
                [line_reg],
                scope="markup.inserted",
                flags=sublime.DRAW_NO_FILL | sublime.PERSISTENT,
            )
            view.show_at_center(pt)
        except Exception:
            continue


def _kb_goto_subject_line(
    window: sublime.Window,
    subject: dict,
    *,
    focus: bool = True,
) -> None:
    """Reveal subject in the KB file (arrow-preview / browse).

    ``focus=False`` (quick-panel highlight): only scroll an already-visible
    preview view — never open/move/focus, or the dropdown loses the keyboard.
    """
    global _kb_preview_seq
    _kb_preview_seq += 1
    seq = _kb_preview_seq

    goto_line = subject.get("start")
    if goto_line is None:
        goto_line = subject.get("topic_start")

    if not focus:
        if goto_line is not None:
            _kb_scroll_preview_line(window, int(goto_line))
        return

    path = _ensure_kb_file()
    preview_group = _kb_target_group(window)
    view = _kb_find_kb_file_view(window)
    if view is None:
        flags = sublime.FORCE_GROUP | sublime.ENCODED_POSITION
        loc = path
        if goto_line is not None and int(goto_line) >= 0:
            loc = f"{path}:{int(goto_line) + 1}:1"
        try:
            view = window.open_file(loc, flags, preview_group)
        except TypeError:
            window.focus_group(preview_group)
            view = window.open_file(loc, flags)
    else:
        try:
            gi, _vi = window.get_view_index(view)
            if gi != preview_group:
                window.set_view_index(view, preview_group, 0)
        except Exception:
            pass
    _assign_stnotes_syntax(view)

    def _after_load(retries: int = 40) -> None:
        if seq != _kb_preview_seq:
            return
        if view.is_loading() and retries > 0:
            sublime.set_timeout(lambda: _after_load(retries - 1), 50)
            return
        if seq != _kb_preview_seq:
            return
        _assign_stnotes_syntax(view)
        _kb_protect_file_view(view)
        try:
            window.focus_view(view)
        except Exception:
            pass
        if goto_line is not None and int(goto_line) >= 0:
            try:
                pt = view.text_point(int(goto_line), 0)
                view.sel().clear()
                view.sel().add(sublime.Region(pt))
                view.show_at_center(pt)
            except Exception:
                pass

    sublime.set_timeout(_after_load, 0)


def _kb_run_after_panel(callback, delay_ms: int = 60) -> None:
    """Run after quick-panel dismisses (Enter). Cancels in-flight highlight previews."""
    global _kb_preview_seq
    _kb_preview_seq += 1
    sublime.set_timeout(callback, delay_ms)


def _kb_open_file_side(window: sublime.Window, goto_line: int | None = None) -> None:
    """Open the full knowledge-base file in the side group (focused)."""
    _kb_goto_subject_line(window, {"start": goto_line}, focus=True)
    sublime.status_message(f"Notes: knowledge base → {_ensure_kb_file()}")


def _kb_show_topic_subjects_panel(
    window: sublime.Window,
    topic_slug: str,
    *,
    capture_target: bool = True,
    preview_on_highlight: bool = True,
) -> None:
    """Quick panel of subjects for one topic (hover topic link / Browse). Newest first."""
    if capture_target:
        _kb_capture_target_group(window)
    try:
        idx = _get_kb_index()
    except RuntimeError as exc:
        _error_message(f"Notes - knowledge base:\n\n{exc}")
        return
    tslug = (topic_slug or "").lower()
    subjects = _pure.sort_subjects_newest_first(
        idx.get("by_topic", {}).get(tslug) or []
    )
    if not subjects:
        sublime.status_message(f"Notes: no subjects for topic {tslug}")
        return
    topic_name = subjects[0].get("topic") or tslug
    # Topic already chosen — list subject titles only
    panel = [_kb_subject_panel_item(s, include_topic=False) for s in subjects]

    def _show() -> None:
        def on_highlight(index: int, _subjects=subjects) -> None:
            if not preview_on_highlight or not kb_preview_on_highlight():
                return
            if index < 0 or index >= len(_subjects):
                return
            try:
                _kb_goto_subject_line(window, _subjects[index], focus=False)
            except Exception:
                pass

        def on_pick(index: int, _subjects=subjects) -> None:
            if index < 0 or index >= len(_subjects):
                return
            sub = _subjects[index]
            _kb_run_after_panel(lambda s=sub: _kb_open_subject_side(window, s))

        kwargs = dict(
            flags=_KB_PANEL_FLAGS,
            selected_index=0,
            placeholder=f"KB · {topic_name} — subjects (newest first; Enter opens slice)",
        )
        if preview_on_highlight and kb_preview_on_highlight():
            kwargs["on_highlight"] = on_highlight
        window.show_quick_panel(panel, on_pick, **kwargs)
        if preview_on_highlight and kb_preview_on_highlight():
            sublime.set_timeout(lambda: on_highlight(0), 30)

    if preview_on_highlight and kb_preview_on_highlight():
        _kb_prepare_preview_then(window, _show)
    else:
        _show()


def _is_knowledge_base_view(view: sublime.View) -> bool:
    fname = view.file_name() or ""
    if not fname:
        return False
    try:
        return os.path.realpath(fname) == _knowledge_base_file()
    except OSError:
        return False


def _kb_protect_file_view(view: sublime.View) -> None:
    """KB file is a git-shared store — edit via slices only."""
    if view is None or not view.is_valid():
        return
    try:
        view.settings().set("stnotes_kb_file", True)
        view.settings().set("kb_file", True)
        if not view.is_read_only():
            view.set_read_only(True)
        _assign_stnotes_syntax(view)
    except Exception:
        pass


def _kb_subject_panel_item(sub: dict, *, include_topic: bool = True):
    topic = sub.get("topic") or _KB_DEFAULT_TOPIC
    title = sub.get("title") or ""
    trigger = f"{topic}  ·  {title}" if include_topic else title
    bits = []
    when = sub.get("modified") or sub.get("created")
    if when:
        bits.append(when)
    bits.append(sub.get("ref") or sub.get("preview") or "")
    ann = " · ".join(b for b in bits if b)
    try:
        return sublime.QuickPanelItem(trigger=trigger, annotation=ann[:100])
    except Exception:
        return [trigger, ann[:100]] if ann else [trigger]


def _build_kb_hover_html(subject: dict | None, ref: str) -> str:
    safe_ref = _h(ref)
    if subject is None:
        return (
            "<body id='stnotes-hover' "
            f"style='{_hover_body_style()}'>"
            f"<div style='color:#e06c75'>Unknown KB ref</div>"
            f"<div style='color:#abb2bf;margin-top:4px'>{safe_ref}</div>"
            "<div style='color:#5c6370;margin-top:6px'>"
            "Use .notes - Knowledge Base to browse topics.</div>"
            "</body>"
        )
    topic = _h(subject.get("topic") or "")
    title = _h(subject.get("title") or "")
    tslug = _h(subject.get("topic_slug") or _kb_slug(subject.get("topic") or ""))
    sslug = _h(subject.get("slug") or _kb_slug(subject.get("title") or ""))
    meta_bits = []
    if subject.get("modified"):
        meta_bits.append(f"modified {subject['modified']}")
    elif subject.get("created"):
        meta_bits.append(f"created {subject['created']}")
    meta_html = ""
    if meta_bits:
        meta_html = (
            f"<div style='color:#5c6370;margin:2px 0'>"
            f"{_h(' · '.join(meta_bits))}</div>"
        )
    body_html = _kb_render_segments_html(_kb_parse_body_segments(subject.get("body") or ""))
    return (
        "<body id='stnotes-hover' "
        f"style='{_hover_body_style()}'>"
        "<div>"
        f"<a href='kb-goto-topic:{tslug}' "
        f"style='color:#56b6c2;font-weight:bold;text-decoration:none'>{topic}</a>"
        "<span style='color:#5c6370'> / </span>"
        f"<a href='kb-goto-subject:{tslug}/{sslug}' "
        f"style='color:#cdd9e5;font-weight:bold;text-decoration:none'>{title}</a>"
        "</div>"
        f"<div style='margin:4px 0'>"
        f"<a href='kb-edit:{safe_ref}' "
        f"style='color:#5c6370;text-decoration:none'>{safe_ref}</a>"
        "</div>"
        f"{meta_html}"
        f"<div style='{_hover_sep_style()}'></div>"
        f"{body_html}"
        "</body>"
    )


# ---------------------------------------------------------------------------
# Knowledge base — commands
# ---------------------------------------------------------------------------


def _write_kb_lines(lines: list[str]) -> None:
    """Atomic rewrite of the knowledge-base file (canonical layout)."""
    path = _ensure_kb_file()
    canonical = _pure.format_kb_lines(lines)
    payload = "\n".join(canonical)
    if canonical:
        payload += "\n"
    _nc.atomic_write_utf8(
        path, payload, mode=_kb_file_mode(), replace_command="kb_replace_text"
    )
    _invalidate_kb_cache()


def _kb_find_editor_view(
    window: sublime.Window,
    topic_slug: str,
    subject_slug: str,
    *,
    is_new: bool | None = None,
):
    t = (topic_slug or "").lower()
    s = (subject_slug or "").lower()
    for view in window.views():
        try:
            st = view.settings()
            if not st.get("stnotes_kb_subject_edit"):
                continue
            if is_new is True and not st.get("stnotes_kb_is_new"):
                continue
            if (st.get("stnotes_kb_topic_slug") or "").lower() == t and (
                st.get("stnotes_kb_subject_slug") or ""
            ).lower() == s:
                return view
        except Exception:
            continue
    return None


def _kb_new_subject_dict(title: str, topic: str | None = None) -> dict:
    """In-memory subject for a slice that has not been written to the KB file."""
    from datetime import date

    topic_name = (topic or "").strip() or _KB_DEFAULT_TOPIC
    slug = _kb_slug(title)
    tslug = _kb_slug(topic_name)
    today = date.today().isoformat()
    return {
        "topic": topic_name,
        "title": title,
        "slug": slug,
        "topic_slug": tslug,
        "ref": _kb_ref(tslug, slug),
        "created": today,
        "modified": today,
        "body": "",
        "start": 0,
        "end": 0,
    }


def _kb_topic_names() -> list[str]:
    """Existing topic display names (other first), de-duplicated by slug."""
    try:
        topics = list((_get_kb_index().get("topics") or []))
    except Exception:
        topics = []
    ordered: list[str] = []
    seen: set[str] = set()
    for name in [_KB_DEFAULT_TOPIC] + topics:
        key = _kb_slug(name)
        if key in seen:
            continue
        seen.add(key)
        ordered.append(name)
    return ordered


def _kb_open_subject_editor(
    window: sublime.Window,
    subject: dict,
    *,
    focus: str = "body",
    is_new: bool = False,
) -> None:
    """Writable edit-slice tab for one subject (commits back into the KB file).

    focus: ``body`` (after ---), ``subject`` (select title), or ``topic`` (select topic).
    Opens in the captured target pane (opposite of where Browse/Search was invoked).
    """
    topic_slug = subject.get("topic_slug") or _KB_DEFAULT_TOPIC_SLUG
    subject_slug = subject.get("slug") or _kb_slug(subject.get("title") or "")
    existing = _kb_find_editor_view(
        window, topic_slug, subject_slug, is_new=True if is_new else None
    )
    content = _pure.subject_editor_document(subject)
    name = f".notes KB edit · {subject.get('ref') or subject_slug}"
    target = _kb_target_group(window)
    if existing is not None and existing.is_valid():
        try:
            gi, _vi = window.get_view_index(existing)
            if gi != target:
                window.set_view_index(existing, target, 0)
        except Exception:
            pass
        window.focus_view(existing)
        existing.set_read_only(False)
        existing.run_command("kb_replace_text", {"text": content})
        view = existing
        view.settings().set("stnotes_slice", True)
        view.settings().set("stnotes_slice_kind", "kb")
        view.settings().set("kb_slice", True)
        view.settings().set("kb_slice_kind", "kb")
        view.settings().set("stnotes_kb_is_new", bool(is_new))
        view.settings().set("kb_is_new", bool(is_new))
        created = (subject.get("created") or "").strip()
        if created:
            view.settings().set("stnotes_kb_created", created)
    else:
        window.focus_group(target)
        view = window.new_file()
        view.set_name(name)
        view.set_scratch(True)
        view.settings().set("stnotes_view_name", name)
        view.settings().set("stnotes_kb_subject_edit", True)
        view.settings().set("kb_subject_edit", True)
        view.settings().set("stnotes_slice", True)
        view.settings().set("stnotes_slice_kind", "kb")
        view.settings().set("kb_slice", True)
        view.settings().set("kb_slice_kind", "kb")
        view.settings().set("stnotes_kb_is_new", bool(is_new))
        view.settings().set("kb_is_new", bool(is_new))
        view.settings().set("stnotes_kb_topic_slug", topic_slug)
        view.settings().set("stnotes_kb_subject_slug", subject_slug)
        view.settings().set("stnotes_kb_path", _knowledge_base_file())
        created = (subject.get("created") or "").strip()
        if created:
            view.settings().set("stnotes_kb_created", created)
        view.run_command("kb_insert_text", {"text": content})
        _assign_stnotes_syntax(view)
    _kb_place_editor_caret(view, focus=focus)
    sublime.status_message(
        f"Notes: editing {subject.get('ref')} — Cmd/Ctrl+Shift+Enter commits; "
        "Cmd/Ctrl+Alt+T topic; Cmd/Ctrl+Alt+B code fence"
    )


def _kb_place_editor_caret(view: sublime.View, *, focus: str = "body") -> None:
    """Select topic/subject name, or place caret after ---."""
    try:
        text = view.substr(sublime.Region(0, view.size()))
        if focus == "topic":
            m = re.search(r"(?m)^#\s+TOPIC:\s*(.+?)\s*$", text)
            if m:
                a, b = m.start(1), m.end(1)
                view.sel().clear()
                view.sel().add(sublime.Region(a, b))
                view.show(a)
                return
        if focus == "subject":
            m = re.search(r"(?m)^##\s+SUBJECT:\s*(.*?)\s*$", text)
            if m:
                a, b = m.start(1), m.end(1)
                view.sel().clear()
                view.sel().add(sublime.Region(a, b))
                view.show(a)
                return
        idx = text.find("\n---\n")
        pt = (idx + 5) if idx >= 0 else view.size()
        view.sel().clear()
        view.sel().add(sublime.Region(pt))
        view.show(pt)
    except Exception:
        pass


def _kb_apply_topic_to_editor(view: sublime.View, topic_name: str) -> None:
    """Rewrite # TOPIC: and - ref: in the edit-slice buffer."""
    topic_name = (topic_name or "").strip() or _KB_DEFAULT_TOPIC
    text = view.substr(sublime.Region(0, view.size()))
    lines = text.splitlines()
    title = ""
    for line in lines:
        ms = _KB_SUBJECT_RE.match(line)
        if ms:
            title = ms.group(1).strip()
            break
    ref = _kb_ref(_kb_slug(topic_name), _kb_slug(title))
    out = []
    for line in lines:
        if _KB_TOPIC_RE.match(line):
            out.append(f"# TOPIC: {topic_name}")
            continue
        if re.match(r"^\s*-\s*ref:\s*", line, re.IGNORECASE):
            out.append(f"- ref: {ref}")
            continue
        out.append(line)
    new_text = "\n".join(out)
    if text.endswith("\n"):
        new_text += "\n"
    view.run_command("kb_replace_text", {"text": new_text})
    # Keep stnotes_kb_topic_slug as the *file* location until commit moves it.
    view.set_name(f".notes KB edit · {ref}")
    view.settings().set("stnotes_view_name", view.name())
    _kb_place_editor_caret(view, focus="subject")


def _kb_show_topic_picker(window: sublime.Window, view: sublime.View | None = None) -> None:
    """Quick-panel dropdown of all topics (+ type new)."""
    if view is None:
        view = window.active_view()
    if view is None or not view.settings().get("stnotes_kb_subject_edit"):
        sublime.status_message("Notes: open a KB subject editor first")
        return
    window.focus_view(view)
    topics = _kb_topic_names()
    items = [[name, "existing topic"] for name in topics]
    items.append(["New topic…", "Type a new topic name"])
    # Preselect current topic if present
    cur = ""
    try:
        m = _KB_TOPIC_RE.match(view.substr(view.line(0)))
        if m:
            cur = m.group(1).strip()
    except Exception:
        pass
    selected = 0
    for i, name in enumerate(topics):
        if _kb_slug(name) == _kb_slug(cur):
            selected = i
            break

    def on_pick(index: int) -> None:
        if index < 0:
            return

        def _apply(name: str) -> None:
            window.focus_view(view)
            _kb_apply_topic_to_editor(view, name)

        if index >= len(topics):
            window.show_input_panel(
                "New topic name:",
                "",
                lambda raw: _apply(raw) if (raw or "").strip() else None,
                None,
                None,
            )
            return
        sublime.set_timeout(lambda: _apply(topics[index]), 0)

    window.show_quick_panel(
        items,
        on_pick,
        flags=sublime.MONOSPACE_FONT,
        selected_index=selected,
        placeholder="Pick topic for this subject (type to filter)",
    )


def _kb_choose_topic_name(
    window: sublime.Window,
    *,
    current: str = "",
    placeholder: str = "Pick destination topic",
    on_chosen,
) -> None:
    """Quick-panel of topics (+ New topic…). Calls ``on_chosen(name)``."""
    topics = _kb_topic_names()
    items = [[name, "existing topic"] for name in topics]
    items.append(["New topic…", "Type a new topic name"])
    selected = 0
    cur_slug = _kb_slug(current)
    for i, name in enumerate(topics):
        if _kb_slug(name) == cur_slug:
            selected = i
            break

    def on_pick(index: int) -> None:
        if index < 0:
            return
        if index >= len(topics):
            window.show_input_panel(
                "New topic name:",
                "",
                lambda raw: on_chosen((raw or "").strip()) if (raw or "").strip() else None,
                None,
                None,
            )
            return
        on_chosen(topics[index])

    window.show_quick_panel(
        items,
        on_pick,
        flags=sublime.MONOSPACE_FONT,
        selected_index=selected,
        placeholder=placeholder,
    )


def _kb_commit_move_subject(window: sublime.Window, subject: dict, new_topic: str) -> None:
    """Move subject on disk to ``new_topic`` (canonical write)."""
    new_topic = (new_topic or "").strip() or _KB_DEFAULT_TOPIC
    old_tslug = (subject.get("topic_slug") or _KB_DEFAULT_TOPIC_SLUG).lower()
    sslug = (subject.get("slug") or "").lower()
    if _kb_slug(new_topic) == old_tslug:
        sublime.status_message(
            f"Notes: {subject.get('ref')} already under {subject.get('topic')}"
        )
        return
    lines = _read_kb_lines(force=True)
    idx = _parse_kb_index(lines)
    found = _pure.find_subject(idx, old_tslug, sslug)
    if found is None:
        raise RuntimeError(
            f"Could not find {subject.get('ref') or sslug} in knowledge base"
        )
    new_lines = _pure.splice_subject_body(
        lines,
        found,
        new_title=found.get("title") or sslug,
        new_created=found.get("created") or "",
        new_body=found.get("body") or "",
        new_topic=new_topic,
    )
    _write_kb_lines(new_lines)
    new_ref = _kb_ref(_kb_slug(new_topic), sslug)
    ed = _kb_find_editor_view(window, old_tslug, sslug)
    if ed is not None and ed.is_valid():
        _kb_apply_topic_to_editor(ed, new_topic)
        ed.settings().set("stnotes_kb_topic_slug", _kb_slug(new_topic))
    kv = _kb_find_kb_file_view(window)
    if kv is not None:
        try:
            kv.run_command("revert")
        except Exception:
            pass
        _kb_protect_file_view(kv)
    sublime.status_message(f"Notes: moved {subject.get('ref')} → {new_ref}")


def _kb_append_subject(title: str, topic: str | None = None) -> dict:
    """Append a new subject stub; return indexed subject dict."""
    topic_name = (topic or "").strip() or _KB_DEFAULT_TOPIC
    stub = _pure.build_subject_stub(title, topic=topic_name, body="")
    lines = _read_kb_lines(force=True)
    _write_kb_lines(list(lines) + stub.splitlines())
    idx = _get_kb_index(force=True)
    sub = _kb_find_subject(_kb_slug(topic_name), _kb_slug(title))
    if sub is None:
        # last subject as fallback
        all_subs = idx.get("all") or []
        if not all_subs:
            raise RuntimeError("Subject was appended but not found in index")
        sub = all_subs[-1]
    return sub


def _kb_locate_subject_in_index(
    idx: dict,
    *,
    file_topic_slug: str,
    subject_slug: str,
    buffer_topic: str,
    title: str,
) -> dict | None:
    """Find the on-disk subject even if the editor TOPIC was changed before commit."""
    file_topic_slug = (file_topic_slug or _KB_DEFAULT_TOPIC_SLUG).lower()
    subject_slug = (subject_slug or "").lower()
    title_slug = _kb_slug(title)
    dest_topic_slug = _kb_slug(buffer_topic) or _KB_DEFAULT_TOPIC_SLUG

    for tslug, sslug in (
        (file_topic_slug, subject_slug),
        (file_topic_slug, title_slug),
        (dest_topic_slug, subject_slug),
        (dest_topic_slug, title_slug),
    ):
        if not sslug:
            continue
        hit = _pure.find_subject(idx, tslug, sslug)
        if hit is not None:
            return hit

    # Unique slug match anywhere (handles topic picker before commit)
    candidates = []
    for sslug in dict.fromkeys([subject_slug, title_slug]):
        if not sslug:
            continue
        hits = [
            sub
            for sub in (idx.get("all") or [])
            if (sub.get("slug") or "").lower() == sslug
        ]
        if len(hits) == 1:
            return hits[0]
        candidates.extend(hits)
    return None


def _kb_commit_subject_view(view: sublime.View) -> None:
    """Splice edit-slice buffer back into the KB file."""
    if not view.settings().get("stnotes_kb_subject_edit"):
        raise RuntimeError("Not a KB subject editor view")
    file_topic_slug = view.settings().get("stnotes_kb_topic_slug") or _KB_DEFAULT_TOPIC_SLUG
    subject_slug = view.settings().get("stnotes_kb_subject_slug") or ""
    is_new = bool(view.settings().get("stnotes_kb_is_new"))
    text = view.substr(sublime.Region(0, view.size()))
    topic, title, created, body = _pure.parse_subject_editor_buffer(text)
    locked = (view.settings().get("stnotes_kb_created") or "").strip()
    if locked:
        created = locked
    if not title.strip():
        raise RuntimeError("Subject title is empty — keep the ## SUBJECT: line")
    lines = _read_kb_lines(force=True)
    if is_new:
        new_lines = _pure.append_subject_to_lines(
            lines,
            topic=topic,
            title=title,
            created=created,
            body=body,
        )
    else:
        idx = _parse_kb_index(lines)
        subject = _kb_locate_subject_in_index(
            idx,
            file_topic_slug=file_topic_slug,
            subject_slug=subject_slug,
            buffer_topic=topic,
            title=title,
        )
        if subject is None:
            raise RuntimeError(
                f"Could not find subject {file_topic_slug}/{subject_slug or _kb_slug(title)} in KB file"
            )
        new_lines = _pure.splice_subject_body(
            lines,
            subject,
            new_title=title,
            new_created=created,
            new_body=body,
            new_topic=topic,
        )
    _write_kb_lines(new_lines)
    # File location now matches destination topic
    new_slug = _kb_slug(title)
    view.settings().set("stnotes_kb_subject_slug", new_slug)
    view.settings().set("stnotes_kb_topic_slug", _kb_slug(topic) or file_topic_slug)
    view.settings().set("stnotes_kb_is_new", False)
    ref = _kb_ref(_kb_slug(topic) or file_topic_slug, new_slug)
    view.set_name(f".notes KB edit · {ref}")
    view.settings().set("stnotes_view_name", view.name())


class NotesKnowledgeBaseCommand(sublime_plugin.WindowCommand):
    """Command: notes_knowledge_base  |  Palette: .notes - Knowledge Base"""

    _BROWSE = "__browse__"
    _CREATE = "__create__"
    _MOVE = "__move__"
    _INSERT = "__insert__"
    _EDIT = "__edit__"
    _FORMAT = "__format__"
    _STUB = "__stub__"
    _HELP = "__help__"

    def run(self) -> None:
        try:
            idx = _get_kb_index()
        except RuntimeError as exc:
            _error_message(f"Notes - knowledge base:\n\n{exc}")
            return
        n_topics = len(idx.get("topics") or [])
        items = [
            ["Browse topics…", f"{n_topics} topic(s) → subjects"],
            ["Create subject…", "Edit-slice only — nothing written until Cmd/Ctrl+Shift+Enter"],
            ["Move subject to topic…", "Pick subject → pick topic (writes immediately)"],
            ["Insert ref at caret…", "Pick subject → insert kb:topic/subject"],
            ["View knowledge base file", "Read-only · Alt+Up/Down jumps subjects · edit via slice"],
            ["Format knowledge base", "Canonical layout (spacing / headers; no reorder)"],
            ["Append topic/subject stub", "Adds a template block at end of KB file"],
            ["Getting started", "How slices, refs, and hover work"],
        ]
        keys = [
            self._BROWSE,
            self._CREATE,
            self._MOVE,
            self._INSERT,
            self._EDIT,
            self._FORMAT,
            self._STUB,
            self._HELP,
        ]
        self._hub_keys = keys
        self.window.show_quick_panel(
            items,
            self._on_hub,
            flags=sublime.MONOSPACE_FONT,
            selected_index=0,
            placeholder=".kb hub — Enter selects",
        )

    def _on_hub(self, index: int) -> None:
        if index < 0:
            return
        key = self._hub_keys[index]
        if key == self._BROWSE:
            self.window.run_command("notes_kb_browse")
        elif key == self._CREATE:
            self.window.run_command("notes_kb_create_subject")
        elif key == self._MOVE:
            self.window.run_command("notes_kb_move_subject")
        elif key == self._INSERT:
            self.window.run_command("notes_kb_insert_ref")
        elif key == self._EDIT:
            try:
                _kb_open_file_side(self.window)
            except RuntimeError as exc:
                _error_message(f"Notes - knowledge base:\n\n{exc}")
        elif key == self._FORMAT:
            self.window.run_command("notes_kb_format")
        elif key == self._STUB:
            self._append_stub()
        elif key == self._HELP:
            self.window.run_command("kb_getting_started")

    def _append_stub(self) -> None:
        try:
            from datetime import date as _date
            today = _date.today().isoformat()
            lines = _read_kb_lines(force=True)
            new_lines = _pure.append_subject_to_lines(
                lines,
                topic="new-topic",
                title="new-subject",
                created=today,
                body="Describe commands here. Rename TOPIC/SUBJECT titles, then commit the slice.",
            )
            _write_kb_lines(new_lines)
            idx = _get_kb_index(force=True)
            sub = _pure.find_subject(idx, "new-topic", "new-subject")
            goto = int((sub or {}).get("start") or max(0, len(new_lines) - 4))
            _kb_open_file_side(self.window, goto_line=goto)
        except RuntimeError as exc:
            _error_message(f"Notes - knowledge base:\n\n{exc}")


class NotesKbMoveSubjectCommand(sublime_plugin.WindowCommand):
    """Hub: pick a subject, then a topic; writes the move immediately."""

    def run(self) -> None:
        _kb_capture_target_group(self.window)
        try:
            idx = _get_kb_index()
        except RuntimeError as exc:
            _error_message(f"Notes - knowledge base:\n\n{exc}")
            return
        subjects = _pure.sort_subjects_newest_first(idx.get("all") or [])
        if not subjects:
            sublime.status_message("Notes: KB empty — create a subject first")
            return
        self._subjects = subjects
        panel = [_kb_subject_panel_item(s) for s in subjects]

        def _show() -> None:
            def on_highlight(index: int) -> None:
                if not kb_preview_on_highlight():
                    return
                if index < 0 or index >= len(self._subjects):
                    return
                try:
                    _kb_goto_subject_line(self.window, self._subjects[index], focus=False)
                except Exception:
                    pass

            self.window.show_quick_panel(
                panel,
                self._on_subject,
                flags=_KB_PANEL_FLAGS,
                selected_index=0,
                placeholder="Move: pick subject, then topic",
                on_highlight=on_highlight,
            )
            sublime.set_timeout(lambda: on_highlight(0), 30)

        _kb_prepare_preview_then(self.window, _show)

    def _on_subject(self, index: int) -> None:
        if index < 0 or index >= len(self._subjects):
            return
        sub = self._subjects[index]
        win = self.window
        cur = sub.get("topic") or _KB_DEFAULT_TOPIC

        def after_panel() -> None:
            def on_topic(name: str) -> None:
                try:
                    _kb_commit_move_subject(win, sub, name)
                except RuntimeError as exc:
                    _error_message(f"Notes - move subject:\n\n{exc}")
                except Exception as exc:
                    _error_message(f"Notes - move subject failed:\n\n{exc}")

            _kb_choose_topic_name(
                win,
                current=cur,
                placeholder=f"Move {sub.get('title')} from {cur} →",
                on_chosen=on_topic,
            )

        _kb_run_after_panel(after_panel)


class NotesKbBrowseCommand(sublime_plugin.WindowCommand):
    """Command: notes_kb_browse  |  Palette: .notes - KB Browse"""

    def run(self) -> None:
        _kb_capture_target_group(self.window)
        try:
            self._idx = _get_kb_index()
        except RuntimeError as exc:
            _error_message(f"Notes - knowledge base:\n\n{exc}")
            return
        topics = self._idx.get("topics") or []
        if not topics:
            sublime.status_message("Notes: KB has no topics — use Knowledge Base → stub")
            self.window.run_command("notes_knowledge_base")
            return
        panel = []
        self._topic_slugs = []
        for tname in topics:
            ts = _kb_slug(tname)
            n = len(self._idx.get("by_topic", {}).get(ts, []))
            panel.append([tname, f"{n} subject(s)"])
            self._topic_slugs.append(ts)

        def _show() -> None:
            def on_highlight(index: int) -> None:
                if not kb_preview_on_highlight():
                    return
                if index < 0 or index >= len(self._topic_slugs):
                    return
                subs = list(self._idx.get("by_topic", {}).get(self._topic_slugs[index]) or [])
                if not subs:
                    return
                try:
                    anchor = {
                        "start": subs[0].get("topic_start", subs[0].get("start")),
                        "topic_start": subs[0].get("topic_start"),
                    }
                    _kb_goto_subject_line(self.window, anchor, focus=False)
                except Exception:
                    pass

            def on_topic(index: int) -> None:
                if index < 0:
                    return
                tslug = self._topic_slugs[index]
                _kb_run_after_panel(
                    lambda: _kb_show_topic_subjects_panel(
                        self.window, tslug, capture_target=False
                    )
                )

            self.window.show_quick_panel(
                panel,
                on_topic,
                flags=_KB_PANEL_FLAGS,
                selected_index=0,
                on_highlight=on_highlight,
                placeholder="KB topics — type to filter, Enter opens subjects",
            )
            sublime.set_timeout(lambda: on_highlight(0), 30)

        _kb_prepare_preview_then(self.window, _show)

    def _on_topic(self, index: int) -> None:
        # retained for compatibility; panel uses local on_topic
        if index < 0:
            return
        _kb_show_topic_subjects_panel(
            self.window, self._topic_slugs[index], capture_target=False
        )


class NotesKbSearchCommand(sublime_plugin.WindowCommand):
    """Command: notes_kb_search  |  Palette: .notes - KB Search"""

    def run(self, insert_ref: bool = False) -> None:
        forced = bool(self.window.settings().get("stnotes_kb_insert_ref"))
        self.window.settings().erase("stnotes_kb_insert_ref")
        self._insert_ref = bool(insert_ref) or forced
        if not self._insert_ref:
            _kb_capture_target_group(self.window)
        try:
            idx = _get_kb_index()
        except RuntimeError as exc:
            _error_message(f"Notes - knowledge base:\n\n{exc}")
            return
        subjects = _pure.sort_subjects_newest_first(idx.get("all") or [])
        if not subjects:
            sublime.status_message("Notes: KB empty — use .notes - Knowledge Base")
            return
        self._subjects = subjects
        panel = [_kb_subject_panel_item(s) for s in subjects]
        ph = (
            "KB insert ref — type to filter, Enter inserts at caret"
            if self._insert_ref
            else "KB search — Enter edits subject; Cmd/Ctrl+Shift+Enter saves"
        )

        def _show() -> None:
            def on_highlight(index: int) -> None:
                if self._insert_ref or not kb_preview_on_highlight():
                    return
                if index < 0 or index >= len(self._subjects):
                    return
                try:
                    _kb_goto_subject_line(self.window, self._subjects[index], focus=False)
                except Exception:
                    pass

            kwargs = dict(
                flags=_KB_PANEL_FLAGS,
                selected_index=0,
                placeholder=ph,
            )
            if not self._insert_ref:
                kwargs["on_highlight"] = on_highlight
            self.window.show_quick_panel(panel, self._on_pick, **kwargs)
            if not self._insert_ref:
                sublime.set_timeout(lambda: on_highlight(0), 30)

        if self._insert_ref:
            _show()
        else:
            _kb_prepare_preview_then(self.window, _show)

    def _on_pick(self, index: int) -> None:
        if index < 0 or index >= len(self._subjects):
            return
        sub = self._subjects[index]
        if self._insert_ref:
            ref = sub.get("ref") or ""
            view = self.window.active_view()
            if view is None:
                return
            view.run_command("kb_insert_at_caret", {"text": ref})
            sublime.status_message(f"Notes: inserted {ref}")
            return
        win = self.window
        _kb_run_after_panel(lambda s=sub: _kb_open_subject_side(win, s))


class NotesKbInsertRefCommand(sublime_plugin.WindowCommand):
    """Command: notes_kb_insert_ref  |  Palette: .notes - KB Insert Ref"""

    def run(self) -> None:
        self.window.settings().set("stnotes_kb_insert_ref", True)
        self.window.run_command("notes_kb_search", {"insert_ref": True})


def handle_kb_navigate(href: str) -> None:
    """Handle minihtml on_navigate hrefs for kb-* links."""
    href = (
        (href or "")
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
    )
    window = sublime.active_window()
    if window is None:
        return
    view = window.active_view()

    def _hide() -> None:
        try:
            if view:
                view.hide_popup()
        except Exception:
            pass

    if href.startswith("kb-copy:"):
        key = href.split(":", 1)[1].strip()
        payload = _kb_take_copy(key)
        if payload is None:
            sublime.status_message("Notes: copy expired — hover again")
            return
        sublime.set_clipboard(payload)
        nlines = payload.count("\n") + (1 if payload else 0)
        sublime.status_message(
            f"Notes: copied code block ({nlines} line{'s' if nlines != 1 else ''})"
        )
        return
    if href.startswith("kb-goto-topic:"):
        _hide()
        tslug = href.split(":", 1)[1].strip()
        # Subjects-only dropdown; highlight previews in the other pane without focus
        _kb_capture_target_group(window)
        _kb_show_topic_subjects_panel(
            window,
            tslug,
            capture_target=False,
            preview_on_highlight=True,
        )
        return
    if href.startswith("kb-goto-subject:"):
        _hide()
        raw = href.split(":", 1)[1].strip()
        parts = raw.split("/", 1)
        if len(parts) != 2:
            return
        try:
            sub = _kb_find_subject(parts[0], parts[1])
        except RuntimeError as exc:
            _error_message(f"Notes - knowledge base:\n\n{exc}")
            return
        if sub:
            _kb_open_subject_side(window, sub)
        else:
            sublime.status_message(f"Notes: unknown KB subject {raw}")
        return
    if href.startswith("kb-edit:"):
        _hide()
        raw = href.split(":", 1)[1]
        parsed = _kb_parse_ref(raw if raw.startswith("kb:") else f"kb:{raw}")
        if not parsed:
            return
        try:
            sub = _kb_find_subject(*parsed)
        except RuntimeError as exc:
            _error_message(f"Notes - knowledge base:\n\n{exc}")
            return
        if sub:
            _kb_open_subject_side(window, sub)
        else:
            sublime.status_message(f"Notes: unknown KB subject {raw}")
        return



class NotesKbCreateSubjectCommand(sublime_plugin.WindowCommand):
    """Command: notes_kb_create_subject | .notes - KB Create Subject"""

    def run(self, topic: str = "", title: str = "") -> None:
        # Instant edit-slice — no name/topic prompts; edit TOPIC/SUBJECT in the buffer.
        _kb_capture_target_group(self.window)
        topic_name = (topic or "").strip() or _KB_DEFAULT_TOPIC
        subject_title = (title or "").strip() or "new-subject"
        try:
            sub = _kb_new_subject_dict(subject_title, topic=topic_name)
            _kb_open_subject_editor(self.window, sub, focus="subject", is_new=True)
        except RuntimeError as exc:
            _error_message(f"Notes - create subject:\n\n{exc}")
        except Exception as exc:
            _error_message(f"Notes - create subject failed:\n\n{exc}")


class NotesKbCommitSubjectCommand(sublime_plugin.WindowCommand):
    """Command: notes_kb_commit_subject | commit edit-slice tab into KB file"""

    def is_enabled(self) -> bool:
        view = self.window.active_view()
        return bool(view and view.settings().get("stnotes_kb_subject_edit"))

    def run(self) -> None:
        view = self.window.active_view()
        if not view or not view.settings().get("stnotes_kb_subject_edit"):
            sublime.status_message("Notes: open a KB subject editor tab first")
            return
        try:
            _kb_commit_subject_view(view)
        except RuntimeError as exc:
            _error_message(f"Notes - commit subject:\n\n{exc}")
            return
        except Exception as exc:
            _error_message(f"Notes - commit subject failed:\n\n{exc}")
            return
        # Close editor after successful commit (same feel as Add description)
        view.set_scratch(True)
        self.window.focus_view(view)
        self.window.run_command("close")
        sublime.status_message("Notes: subject committed to knowledge base")


class NotesKbEditSubjectCommand(sublime_plugin.WindowCommand):
    """Open an existing subject in the edit-slice tab (from search / browse)."""

    def run(self, topic_slug: str = "", subject_slug: str = "") -> None:
        if not topic_slug or not subject_slug:
            # Fall back to search then edit — reuse search panel
            self.window.run_command("notes_kb_search")
            return
        try:
            sub = _kb_find_subject(topic_slug, subject_slug)
        except RuntimeError as exc:
            _error_message(f"Notes - knowledge base:\n\n{exc}")
            return
        if not sub:
            sublime.status_message("Notes: subject not found")
            return
        _kb_open_subject_editor(self.window, sub)



class NotesKbPickTopicCommand(sublime_plugin.WindowCommand):
    """Command: notes_kb_pick_topic | pick/change TOPIC in edit-slice"""

    def is_enabled(self) -> bool:
        view = self.window.active_view()
        return bool(view and view.settings().get("stnotes_kb_subject_edit"))

    def run(self) -> None:
        _kb_show_topic_picker(self.window)



class NotesSliceNextFieldCommand(sublime_plugin.TextCommand):
    """Command: notes_slice_next_field | Alt+Up/Down select next/prev field value.

    Same command name as notes so add/create-issue slices keep working when both
    packages are installed (identical implementation).
    """

    def is_enabled(self) -> bool:
        st = self.view.settings()
        return bool(st.get("stnotes_slice") or st.get("kb_slice"))

    def run(self, edit: sublime.Edit, forward: bool = True) -> None:
        view = self.view
        text = view.substr(sublime.Region(0, view.size()))
        kind = str(
            view.settings().get("stnotes_slice_kind")
            or view.settings().get("kb_slice_kind")
            or ""
        )
        fields = _pure.slice_field_regions(text, kind=kind)
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


class NotesKbJumpSubjectCommand(sublime_plugin.TextCommand):
    """Command: notes_kb_jump_subject | Alt+Up/Down between SUBJECT headers in the KB file."""

    def is_enabled(self) -> bool:
        return bool(
            self.view.settings().get("stnotes_kb_file")
            or _is_knowledge_base_view(self.view)
        )

    def run(self, edit: sublime.Edit, forward: bool = True) -> None:
        view = self.view
        _kb_protect_file_view(view)
        text = view.substr(sublime.Region(0, view.size()))
        regions = _pure.subject_header_regions(text)
        if not regions:
            sublime.status_message("Notes: no SUBJECT headers in knowledge base")
            return
        caret = view.sel()[0].begin() if view.sel() else 0
        if view.sel():
            sel = view.sel()[0]
            for a, b in regions:
                if sel.begin() == a and sel.end() == b:
                    caret = a
                    break
        idx = _pure.next_slice_field_index(regions, caret, forward=bool(forward))
        if idx is None:
            return
        a, b = regions[idx]
        view.sel().clear()
        view.sel().add(sublime.Region(a, b))
        view.show_at_center(a)


class NotesKbFormatCommand(sublime_plugin.WindowCommand):
    """Command: notes_kb_format | canonical spacing/headers; does not reorder."""

    def run(self) -> None:
        try:
            lines = _read_kb_lines(force=True)
            _write_kb_lines(_pure.format_kb_lines(lines))
        except RuntimeError as exc:
            _error_message(f"Notes - format knowledge base:\n\n{exc}")
            return
        sublime.status_message("Notes: knowledge base formatted (canonical layout)")
        view = _kb_find_kb_file_view(self.window)
        if view is not None:
            try:
                view.run_command("revert")
            except Exception:
                pass
            _kb_protect_file_view(view)


class NotesKbInsertCodeFenceCommand(sublime_plugin.TextCommand):
    """Command: notes_kb_insert_code_fence | insert/wrap fenced code block"""

    def is_enabled(self) -> bool:
        return bool(self.view.settings().get("stnotes_slice"))

    def run(self, edit: sublime.Edit, language: str = "bash") -> None:
        lang = (language or "bash").strip() or "bash"
        view = self.view
        # If called without lang chooser yet, offer common languages
        if language == "__pick__":
            langs = ["bash", "yaml", "json", "python", "text", "dockerfile", "sql"]
            window = view.window()
            if window is None:
                return

            def on_lang(i: int) -> None:
                if i < 0:
                    return
                view.run_command("notes_kb_insert_code_fence", {"language": langs[i]})

            window.show_quick_panel(
                langs,
                on_lang,
                flags=sublime.MONOSPACE_FONT,
                selected_index=0,
                placeholder="Code fence language",
            )
            return

        sels = list(view.sel())
        if not sels:
            return
        # Work last→first so offsets stay valid
        for region in sorted(sels, key=lambda r: r.begin(), reverse=True):
            if region.empty():
                snippet = f"```{lang}\n\n```\n"
                view.replace(edit, region, snippet)
                # caret between fences
                pt = region.begin() + len(f"```{lang}\n")
                view.sel().clear()
                view.sel().add(sublime.Region(pt))
                view.show(pt)
            else:
                body = view.substr(region)
                if not body.endswith("\n"):
                    body = body + "\n"
                wrapped = f"```{lang}\n{body}```\n"
                view.replace(edit, region, wrapped)
        sublime.status_message(f"Notes: inserted ```{lang} fence")


_kb_hover_seq = 0


def _kb_hover_lookup_async(
    view_id: int, point: int, ref: str, seq: int
) -> None:
    """Index/FS work off the UI thread; popup update via set_timeout."""
    global _kb_hover_seq
    if seq != _kb_hover_seq:
        return
    subject = None
    try:
        parsed = _kb_parse_ref(ref)
        if parsed:
            subject = _kb_find_subject(*parsed)
    except Exception:
        subject = None
    if seq != _kb_hover_seq:
        return
    href = ref if ref.startswith("kb:") else f"kb:{ref}"
    html = _build_kb_hover_html(subject, href)

    def _show() -> None:
        if seq != _kb_hover_seq:
            return
        for window in sublime.windows():
            for v in window.views():
                if v.id() == view_id and v.is_valid():
                    v.show_popup(
                        html,
                        flags=sublime.HIDE_ON_MOUSE_MOVE_AWAY,
                        location=point,
                        max_width=kb_popup_max_width(),
                        max_height=kb_popup_max_height(),
                        on_navigate=handle_kb_navigate,
                    )
                    return

    sublime.set_timeout(_show, 0)


class NotesKbFileViewListener(sublime_plugin.ViewEventListener):
    """Protect the on-disk KB file. Flag is set on load (any-view listener)."""

    @classmethod
    def is_applicable(cls, settings: sublime.Settings) -> bool:
        return bool(settings.get("stnotes_kb_file"))

    @classmethod
    def applies_to_primary_view_only(cls) -> bool:
        return True

    def on_activated(self) -> None:
        _kb_protect_file_view(self.view)

    def on_post_save_async(self) -> None:
        _invalidate_kb_cache()
        view = self.view
        sublime.set_timeout(lambda: _kb_protect_file_view(view), 0)


class NotesKbEventListener(sublime_plugin.EventListener):
    """KB file detect (async) + kb: hover in any buffer."""

    def on_load_async(self, view: sublime.View) -> None:
        try:
            if _is_knowledge_base_view(view):
                sublime.set_timeout(lambda v=view: _kb_protect_file_view(v), 0)
        except Exception:
            pass

    def on_hover(self, view: sublime.View, point: int, hover_zone: int) -> None:
        global _kb_hover_seq
        if hover_zone != sublime.HOVER_TEXT:
            return
        if not kb_hover_enabled():
            return
        if view.settings().get("stnotes_kb_file") or _is_knowledge_base_view(view):
            return
        line_region = view.line(point)
        line_text = view.substr(line_region)
        col = point - line_region.begin()
        ref = kb_ref_at_col(line_text, col)
        if not ref:
            return
        _kb_hover_seq += 1
        seq = _kb_hover_seq
        view.show_popup(
            "<body id='stnotes-hover'>Loading…</body>",
            flags=sublime.HIDE_ON_MOUSE_MOVE_AWAY,
            location=point,
            max_width=kb_popup_max_width(),
            max_height=kb_popup_max_height(),
        )
        vid = view.id()
        sublime.set_timeout_async(
            lambda: _kb_hover_lookup_async(vid, point, ref, seq),
            0,
        )



def _after_plugin_loaded() -> None:
    try:
        if seed_kb_demo_if_needed():
            sublime.status_message("KB: seeded example knowledge base")
    except Exception:
        pass
    try:
        _maybe_show_getting_started()
    except Exception:
        pass


_GETTING_STARTED_NAME = ".kb getting started"


def _getting_started_text() -> str:
    return _nc.load_package_resource(
        "messages/install.txt",
        ".kb — getting started\n\nCommand Palette → type .kb\n",
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
    view.settings().set("kb_getting_started", True)
    view.run_command("kb_insert_text", {"text": _getting_started_text()})
    view.set_read_only(True)


def _getting_started_flag_path() -> str:
    return os.path.join(sublime.cache_path(), "kb", "getting_started_shown")


def _maybe_show_getting_started() -> None:
    try:
        if not bool(sublime.load_settings("KB.sublime-settings").get("show_getting_started", True)):
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


class KbGettingStartedCommand(sublime_plugin.WindowCommand):
    """Command: kb_getting_started | Hub: Getting started"""

    def run(self) -> None:
        show_getting_started(self.window)


class KbSettingsCommand(sublime_plugin.WindowCommand):
    """Command: kb_settings | Palette: .kb - Settings"""

    def run(self) -> None:
        self.window.run_command(
            "edit_settings",
            {
                "base_file": "${packages}/kb/KB.sublime-settings",
                "default": (
                    "// kb — User overlay (local path only; no tokens)\n"
                    "// Override if Documents is OneDrive (Windows) or localized (Linux).\n"
                    "{\n"
                    '    "knowledge_base_file": "~/Documents/ST4Notes-kb",\n'
                    '    "kb_path_jail": "~"\n'
                    "}\n"
                ),
            },
        )


def plugin_loaded():
    try:
        s = sublime.load_settings("KB.sublime-settings")
        s.add_on_change("kb-settings", _invalidate_kb_cache)
    except Exception:
        pass
    sublime.set_timeout(_after_plugin_loaded, 400)


def plugin_unloaded():
    try:
        s = sublime.load_settings("KB.sublime-settings")
        s.clear_on_change("kb-settings")
    except Exception:
        pass
    try:
        _invalidate_kb_cache()
    except Exception:
        pass
