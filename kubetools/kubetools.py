# Kubetools — Sublime commands and plugin lifecycle.
# Implementation: kubetools_flow.py. Create: kubetools_create.py. Seal: kubetools_seal.py.

from datetime import datetime

import sublime
import sublime_plugin

try:
    from . import kubetools_common as _ktc
    from .kubetools_flow import (
        _KubetoolsFlow,
        _view_is_k8s_manifest,
        _view_can_apply,
        _view_can_dry_run,
        _view_has_compare_diffs,
        _view_can_seal,
        _view_can_unseal,
        _clear_compare_overlays,
        _goto_compare_diff,
        _copy_live_at_caret,
        _ui_notice,
    )
except ImportError:
    import kubetools_common as _ktc  # type: ignore
    from kubetools_flow import (  # type: ignore
        _KubetoolsFlow,
        _view_is_k8s_manifest,
        _view_can_apply,
        _view_can_dry_run,
        _view_has_compare_diffs,
        _view_can_seal,
        _view_can_unseal,
        _clear_compare_overlays,
        _goto_compare_diff,
        _copy_live_at_caret,
        _ui_notice,
    )

class KubetoolsCompareStateCommand(sublime_plugin.WindowCommand):
    """Command Palette: .kubetools - Compare State (inline live overlays)."""

    def is_visible(self):
        return _view_is_k8s_manifest(self.window.active_view() if self.window else None)

    def is_enabled(self):
        return self.is_visible()

    def run(self):
        view = self.window.active_view()
        _KubetoolsFlow(self.window, view, _KubetoolsFlow.MODE_COMPARE).start()


class KubetoolsClearCompareCommand(sublime_plugin.TextCommand):
    """Clear inline compare overlays on the active view."""

    def is_visible(self):
        return _view_has_compare_diffs(self.view)

    def is_enabled(self):
        return self.is_visible()

    def run(self, edit):
        _clear_compare_overlays(self.view)
        self.view.settings().erase("kubetools_compare_issues")
        sublime.status_message("Kubetools: cleared compare overlays")


class KubetoolsNextDiffCommand(sublime_plugin.TextCommand):
    """Jump caret to the next compare underline (wraps)."""

    def is_visible(self):
        return _view_has_compare_diffs(self.view)

    def is_enabled(self):
        return self.is_visible()

    def run(self, edit):
        _goto_compare_diff(self.view, +1)


class KubetoolsPrevDiffCommand(sublime_plugin.TextCommand):
    """Jump caret to the previous compare underline (wraps)."""

    def is_visible(self):
        return _view_has_compare_diffs(self.view)

    def is_enabled(self):
        return self.is_visible()

    def run(self, edit):
        _goto_compare_diff(self.view, -1)


class KubetoolsCopyLiveUnderCaretCommand(sublime_plugin.TextCommand):
    """Copy live value for the diff on the caret line (secrets stay redacted)."""

    def is_visible(self):
        return _view_has_compare_diffs(self.view)

    def is_enabled(self):
        return self.is_visible()

    def run(self, edit):
        _copy_live_at_caret(self.view)


class KubetoolsHubCommand(sublime_plugin.WindowCommand):
    """Single keyboard entry: pick Compare / Apply / Seal / … (context-filtered)."""

    def run(self):
        view = self.window.active_view()
        rows = [
            [".kubetools - Create…", "Deployment, Secret, ConfigMap, Ingress, PVC, …"],
        ]
        cmds = ["kubetools_create"]
        if _view_is_k8s_manifest(view):
            rows.extend(
                [
                    [".kubetools - Compare State", "Live vs manifest · read-only"],
                ]
            )
            cmds.extend(["kubetools_compare_state"])
            if _view_can_dry_run(view):
                rows.append(
                    [".kubetools - Dry-Run Open File", "Server dry-run · no persist"]
                )
                cmds.append("kubetools_dry_run_open_file")
            if _view_can_apply(view):
                rows.append(
                    [".kubetools - Apply Open File", "Mutates cluster after confirms"]
                )
                cmds.append("kubetools_open_file")
        if _view_can_seal(view):
            rows.append([".kubetools - Seal", "kubeseal encrypt selection or file"])
            cmds.append("kubetools_seal")
        if _view_can_unseal(view):
            rows.append([".kubetools - Unseal", "Offline decrypt selection"])
            cmds.append("kubetools_unseal")
        if _view_has_compare_diffs(view):
            rows.extend(
                [
                    [".kubetools - Next Diff", "Caret → next compare underline"],
                    [".kubetools - Prev Diff", "Caret → previous compare underline"],
                    [".kubetools - Copy Live Under Caret", "Clipboard ← live value"],
                    [".kubetools - Clear Compare Overlays", "Remove underlines / phantoms"],
                ]
            )
            cmds.extend(
                [
                    "kubetools_next_diff",
                    "kubetools_prev_diff",
                    "kubetools_copy_live_under_caret",
                    "kubetools_clear_compare",
                ]
            )
        rows.extend(
            [
                [".kubetools - Validate Seal Config", "Check stages / kubeseal binary"],
                [".kubetools - Settings", "Preferences Default | User"],
            ]
        )
        cmds.extend(["kubetools_seal_validate_config", "kubetools_open_settings"])

        def picked(index):
            if index < 0 or index >= len(cmds):
                return
            cmd = cmds[index]
            if cmd in (
                "kubetools_next_diff",
                "kubetools_prev_diff",
                "kubetools_copy_live_under_caret",
                "kubetools_clear_compare",
                "kubetools_seal",
                "kubetools_unseal",
            ):
                if view:
                    view.run_command(cmd)
                else:
                    _ui_notice(self.window, "No active view", "Open a buffer first")
            elif cmd in ("kubetools_seal_validate_config", "kubetools_open_settings"):
                self.window.run_command(cmd)
            else:
                self.window.run_command(cmd)

        self.window.show_quick_panel(rows, picked)


class KubetoolsOpenFileCommand(sublime_plugin.WindowCommand):
    """Command Palette: .kubetools - Apply Open File"""

    def is_visible(self):
        return _view_can_apply(self.window.active_view() if self.window else None)

    def is_enabled(self):
        return self.is_visible()

    def run(self):
        view = self.window.active_view()
        _KubetoolsFlow(self.window, view, _KubetoolsFlow.MODE_APPLY).start()


class KubetoolsDryRunOpenFileCommand(sublime_plugin.WindowCommand):
    """Command Palette: .kubetools - Dry-Run Open File"""

    def is_visible(self):
        return _view_can_dry_run(self.window.active_view() if self.window else None)

    def is_enabled(self):
        return self.is_visible()

    def run(self):
        view = self.window.active_view()
        _KubetoolsFlow(self.window, view, _KubetoolsFlow.MODE_DRY_RUN).start()


class KubetoolsDiffOpenFileCommand(sublime_plugin.WindowCommand):
    """Deprecated alias → Compare State (kept so old keybindings keep working)."""

    def is_visible(self):
        return _view_is_k8s_manifest(self.window.active_view() if self.window else None)

    def is_enabled(self):
        return self.is_visible()

    def run(self):
        view = self.window.active_view()
        _KubetoolsFlow(self.window, view, _KubetoolsFlow.MODE_COMPARE).start()


class KubetoolsInsertContentCommand(sublime_plugin.TextCommand):
    def run(self, edit, content, read_only=True):
        self.view.insert(edit, 0, content)
        if read_only:
            self.view.set_read_only(True)


class KubetoolsStampAppliedCommand(sublime_plugin.TextCommand):
    """Prepend # applied: timestamp and lock the slice read-only."""

    def run(self, edit, stamp=""):
        if not stamp:
            stamp = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %z")
        was_ro = self.view.is_read_only()
        if was_ro:
            self.view.set_read_only(False)
        text = self.view.substr(sublime.Region(0, self.view.size()))
        new = _ktc.stamp_applied_header(text, stamp)
        self.view.replace(edit, sublime.Region(0, self.view.size()), new)
        self.view.set_read_only(True)


# Default contents for a brand-new User settings file (edit_settings right pane)
_KUBETOOLS_USER_SETTINGS_DEFAULT = """\
{
\t// Local overlay only — do not copy this file into the package repo.
\t// Sublime GUI PATH is often thinner than a terminal. If kubectl/kubeseal
\t// are "not found", set absolute paths (Homebrew, ~/.local/bin, snap, .exe).
\t"kubectl_path": "kubectl",
\t"kubeseal_path": "",
\t"timeout": 60,

\t// Seal stages — replace with your cluster folder name + pub.pem / priv.key
\t"stages": [
\t\t{
\t\t\t"name": "my-cluster",
\t\t\t"description": "dev / my-cluster (encrypt + decrypt)",
\t\t\t"cert_path": "${home}/path/to/environments/dev/my-cluster/sealed-secrets/pub.pem",
\t\t\t"private_key_path": "${home}/path/to/environments/dev/my-cluster/sealed-secrets/priv.key"
\t\t}
\t],
\t"default_stage": "my-cluster",
\t"ask_stage_every_time": true,

\t"decrypt_output": "new_tab",
\t"decode_secret_data": true,
\t"confirm_overwrite_sealed": true,
\t"offer_compare_after_seal": true,
\t"default_namespace": "default",
\t"default_secret_name": "mysecret"
}
"""


class KubetoolsOpenSettingsCommand(sublime_plugin.WindowCommand):
    """
    Preferences → Package Settings → Kubetools → Settings
    Opens Default | User side-by-side (Sublime edit_settings).
    If User Kubetools settings are missing but Kubeseal User settings exist,
    offer to copy stages into Kubetools (one-time migration).
    """

    def run(self):
        try:
            self._maybe_migrate_from_kubeseal()
        except Exception:
            pass
        # edit_settings is a WindowCommand — must run on the window, not
        # sublime.run_command (that only dispatches ApplicationCommands).
        self.window.run_command(
            "edit_settings",
            {
                "base_file": "${packages}/kubetools/Kubetools.sublime-settings",
                "user_file": "${packages}/User/Kubetools.sublime-settings",
                "default": _KUBETOOLS_USER_SETTINGS_DEFAULT,
            },
        )

    def _maybe_migrate_from_kubeseal(self):
        user_kt = os.path.join(sublime.packages_path(), "User", "Kubetools.sublime-settings")
        if os.path.isfile(user_kt) and os.path.getsize(user_kt) > 10:
            return
        user_ks = os.path.join(sublime.packages_path(), "User", "Kubeseal.sublime-settings")
        if not os.path.isfile(user_ks):
            return
        try:
            with open(user_ks, "r", encoding="utf-8") as fh:
                raw = fh.read()
        except OSError:
            return
        if '"stages"' not in raw:
            return
        # Build a Kubetools User file that keeps Kubeseal keys + kubectl defaults
        header = (
            "// Migrated from User/Kubeseal.sublime-settings — edit freely.\n"
            "// Preferences → Package Settings → Kubetools → Settings\n"
            "{\n"
            '    "kubectl_path": "kubectl",\n'
            '    "timeout": 60,\n'
            "\n"
        )
        # Strip outer braces from Kubeseal file and nest content
        body = raw.strip()
        if body.startswith("{"):
            body = body[1:]
        if body.endswith("}"):
            body = body[:-1]
        out = header + body.rstrip().rstrip(",") + "\n}\n"
        try:
            os.makedirs(os.path.dirname(user_kt), exist_ok=True)
            with open(user_kt, "w", encoding="utf-8") as fh:
                fh.write(out)
            sublime.status_message(
                "Kubetools: migrated stages from User/Kubeseal.sublime-settings"
            )
        except OSError as exc:
            sublime.status_message("Kubetools: could not migrate Kubeseal settings: {}".format(exc))


# ---------------------------------------------------------------------------
# Package lifecycle (ST recommendation)
# ---------------------------------------------------------------------------

_SETTINGS_FILE = "Kubetools.sublime-settings"
_SETTINGS_LISTENER_KEY = "kubetools-settings-listener"


def plugin_loaded():
    try:
        s = sublime.load_settings(_SETTINGS_FILE)
        s.clear_on_change(_SETTINGS_LISTENER_KEY)
        s.add_on_change(_SETTINGS_LISTENER_KEY, lambda: None)
    except Exception:
        pass


def plugin_unloaded():
    try:
        _ktc.OPERATION_GATE.end()
    except Exception:
        pass
    try:
        s = sublime.load_settings(_SETTINGS_FILE)
        s.clear_on_change(_SETTINGS_LISTENER_KEY)
    except Exception:
        pass
