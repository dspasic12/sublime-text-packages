"""Shared helpers for the kb package (no commands / listeners)."""
from __future__ import annotations

import os
import re
import logging
import tempfile

import sublime

_SETTINGS_FILE = "KB.sublime-settings"
_NOTES_SETTINGS_FILE = "ST4Notes.sublime-settings"
_DEFAULT_KB_PATH = "~/Documents/ST4Notes-kb"
_PACKAGE_NAME = "kb"
_NOTES_MAX_FILE_BYTES = 8 * 1024 * 1024  # 8 MiB
_SEP = "# " + "=" * 77
_SEP_RE = re.compile(r"^# =+\s*$")

log = logging.getLogger("KB")


def settings() -> sublime.Settings:
    return sublime.load_settings(_SETTINGS_FILE)


def notes_settings() -> sublime.Settings:
    return sublime.load_settings(_NOTES_SETTINGS_FILE)


def _user_kb_settings_exist() -> bool:
    try:
        return os.path.isfile(
            os.path.join(sublime.packages_path(), "User", "KB.sublime-settings")
        )
    except Exception:
        return False


def setting(key: str, default=None):
    """KB setting, falling back to ST4Notes so existing User files keep working."""
    kb = settings()
    if _user_kb_settings_exist():
        try:
            return kb.get(key, default)
        except Exception:
            return default
    try:
        ns = notes_settings()
        if ns.has(key):
            val = ns.get(key, default)
            if val is not None and val != "":
                return val
    except Exception:
        pass
    try:
        return kb.get(key, default)
    except Exception:
        return default


def load_package_resource(rel: str, fallback: str = "") -> str:
    rel = (rel or "").lstrip("/")
    try:
        text = sublime.load_resource(f"Packages/{_PACKAGE_NAME}/{rel}")
    except Exception:
        return fallback
    if not text:
        return fallback
    text = text.replace("\r\n", "\n")
    if not text.endswith("\n"):
        text += "\n"
    return text


_settings = settings


def scrub_error_text(message: object) -> str:
    msg = "" if message is None else str(message)
    msg = re.sub(r"(?i)(bearer\s+)\S+", r"\1[redacted]", msg)
    msg = re.sub(r"(?i)(private-token:\s*)\S+", r"\1[redacted]", msg)
    msg = re.sub(r"(?i)(authorization:\s*)\S+", r"\1[redacted]", msg)
    return msg


def safe_log(level: str, fmt: str, *args) -> None:
    scrubbed = tuple(scrub_error_text(a) for a in args)
    getattr(log, level, log.warning)(fmt, *scrubbed)


def path_is_under(path: str, root: str) -> bool:
    try:
        real = os.path.normcase(os.path.realpath(path))
        base = os.path.normcase(os.path.realpath(root))
    except OSError:
        return False
    return real == base or real.startswith(base + os.sep)


def kb_root_jail() -> str:
    raw = setting("kb_path_jail", None)
    if not isinstance(raw, str) or not raw.strip():
        raw = setting("notes_path_jail", "~")
    if not isinstance(raw, str) or not raw.strip():
        raw = "~"
    return os.path.realpath(os.path.expanduser(raw.strip()))


def assert_kb_path_in_jail(abs_path: str) -> None:
    jail = kb_root_jail()
    if path_is_under(abs_path, jail):
        return
    try:
        real = os.path.realpath(abs_path)
    except OSError:
        real = abs_path
    raise RuntimeError(
        f"Refusing knowledge_base_file outside kb_path_jail.\n"
        f"path: {real}\njail: {jail}\n"
        "Set kb_path_jail in KB settings (or notes_path_jail in ST4Notes) if intentional."
    )


# Historical aliases used by kb_plugin (copied from notes_kb)
assert_notes_path_in_jail = assert_kb_path_in_jail
notes_root_jail = kb_root_jail


def knowledge_base_file() -> str:
    path = setting("knowledge_base_file", _DEFAULT_KB_PATH)
    if not isinstance(path, str) or not path.strip():
        path = _DEFAULT_KB_PATH
    abs_path = os.path.expanduser(path.strip())
    assert_kb_path_in_jail(abs_path)
    try:
        return os.path.realpath(abs_path)
    except OSError:
        return os.path.abspath(abs_path)


def error_message(message: object) -> None:
    sublime.error_message(scrub_error_text(message))


def status_message(message: object) -> None:
    sublime.status_message(scrub_error_text(message))


def message_dialog(message: object) -> None:
    sublime.message_dialog(scrub_error_text(message))


def h(text: str) -> str:
    return (
        text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
    )


def is_sep(line: str) -> bool:
    return bool(_SEP_RE.match(line))


def is_stnotes_view(view: sublime.View) -> bool:
    if "stnotes" in view.scope_name(0) or "text.kb" in view.scope_name(0):
        return True
    fname = view.file_name() or ""
    if not fname:
        return False
    try:
        real = os.path.realpath(fname)
    except OSError:
        real = fname
    try:
        return real == knowledge_base_file()
    except Exception:
        return False


def is_notes_scratch_view(view: sublime.View) -> bool:
    name = view.settings().get("stnotes_view_name", "") or view.settings().get("kb_view_name", "")
    return bool(name)


def assign_stnotes_syntax(view: sublime.View) -> None:
    syntax = sublime.find_syntax_for_file("file.stnotes")
    if syntax:
        view.assign_syntax(syntax)
        return
    try:
        view.assign_syntax("Packages/kb/KB.sublime-syntax")
    except Exception:
        pass


def open_scratch_view(window: sublime.Window, name: str, content: str) -> None:
    view = window.new_file()
    view.set_name(name)
    view.set_scratch(True)
    view.set_read_only(False)
    view.settings().set("stnotes_view_name", name)
    view.settings().set("kb_view_name", name)
    view.run_command("kb_insert_text", {"text": content})
    view.set_read_only(True)
    assign_stnotes_syntax(view)


def _open_views_for_path(path: str) -> list:
    views = []
    try:
        dest = os.path.normcase(os.path.realpath(path))
    except OSError:
        dest = os.path.normcase(os.path.abspath(path))
    try:
        windows = sublime.windows()
    except Exception:
        return views
    for window in windows:
        for view in window.views():
            fname = view.file_name() or ""
            if not fname:
                continue
            try:
                if os.path.normcase(os.path.realpath(fname)) == dest:
                    views.append(view)
            except OSError:
                continue
    return views


def push_text_to_open_views(path: str, content: str, command: str) -> bool:
    views = _open_views_for_path(path)
    if not views:
        return False
    for view in views:
        ro = view.is_read_only()
        if ro:
            view.set_read_only(False)
        view.run_command(command, {"text": content})
        view.run_command("save")
        if ro:
            view.set_read_only(True)
    return True


def atomic_write_utf8(
    path: str, content: str, *, mode: int | None = 0o600, replace_command: str = ""
) -> None:
    parent = os.path.dirname(path) or os.path.expanduser("~")
    os.makedirs(parent, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=parent, prefix=".st_tmp_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        if mode is not None:
            try:
                os.chmod(tmp_path, mode)
            except OSError:
                pass
        try:
            os.replace(tmp_path, path)
            return
        except OSError:
            if replace_command and push_text_to_open_views(
                path, content, replace_command
            ):
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                return
            with open(path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(content)
                fh.flush()
                os.fsync(fh.fileno())
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


_h = h
_error_message = error_message
_status_message = status_message
_message_dialog = message_dialog
_scrub_error_text = scrub_error_text
_safe_log = safe_log
_knowledge_base_file = knowledge_base_file
_assert_notes_path_in_jail = assert_kb_path_in_jail
_notes_root_jail = kb_root_jail
_is_sep = is_sep
_is_stnotes_view = is_stnotes_view
_is_notes_scratch_view = is_notes_scratch_view
_assign_stnotes_syntax = assign_stnotes_syntax
_open_scratch_view = open_scratch_view

_HOVER_FONT_FACE = "Berkeley Mono"
_HOVER_FONT_SIZE = "12px"
_HOVER_LINE_HEIGHT = "1.45"
_HOVER_PAD = "8px 12px"


def hover_font_family() -> str:
    return f'"{_HOVER_FONT_FACE}", ui-monospace, monospace'


def hover_font_size() -> str:
    return _HOVER_FONT_SIZE


def hover_line_height() -> str:
    return _HOVER_LINE_HEIGHT


def hover_body_style() -> str:
    return (
        f"margin:0;padding:{_HOVER_PAD};"
        f"font-family:{hover_font_family()};"
        f"font-size:{hover_font_size()};"
        f"line-height:{hover_line_height()};"
        f"font-weight:normal;"
        f"white-space:normal"
    )


def hover_pre_style() -> str:
    return (
        f"font-family:{hover_font_family()};"
        f"font-size:{hover_font_size()};"
        f"line-height:{hover_line_height()};"
        f"white-space:pre"
    )


def hover_sep_style() -> str:
    return "border-top:1px solid #3e4451;margin:6px 0"


def hover_code_block_style() -> str:
    return (
        f"margin:6px 0;padding:8px 10px;"
        f"background-color:#3e4451;border:1px solid #5c6370;border-radius:3px;"
        f"color:#d7dae0;"
        f"font-family:{hover_font_family()};"
        f"font-size:{hover_font_size()};"
        f"line-height:{hover_line_height()}"
    )


_hover_font_family = hover_font_family
_hover_font_size = hover_font_size
_hover_line_height = hover_line_height
_hover_body_style = hover_body_style
_hover_pre_style = hover_pre_style
_hover_sep_style = hover_sep_style
_hover_code_block_style = hover_code_block_style
