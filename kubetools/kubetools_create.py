# Kubetools — create starter manifests in a scratch slice
#
# Alt+Up/Down cycles YAML scalar values (same idea as .notes slices).
# Cmd/Ctrl+Shift+Enter commits the slice (kubectl apply — not git).
# Sublime Text 4 / Python 3.14

from datetime import datetime
import getpass
import os

import sublime
import sublime_plugin

try:
    from . import kubetools_common as _ktc
except ImportError:
    import kubetools_common as _ktc  # type: ignore


_SLICE_COMMIT_HINT = "Cmd+Shift+Enter (Mac) / Ctrl+Shift+Enter (Win/Linux)"

_TEMPLATES = {
    "deployment": {
        "caption": "Deployment",
        "detail": "Minimal apps/v1 — change namespace (CHANGE-ME) before Apply",
        "name": ".kubetools create · Deployment",
        "kind_label": "Deployment",
        "body": """\
apiVersion: apps/v1
kind: Deployment
metadata:
  name: example
  namespace: CHANGE-ME
spec:
  selector:
    matchLabels:
      app: example
  template:
    metadata:
      labels:
        app: example
    spec:
      containers:
        - name: example
          image: nginx:1.27-alpine
""",
    },
    "secret": {
        "caption": "Secret template",
        "detail": "Minimal Opaque Secret — change namespace (CHANGE-ME) before Apply / Seal",
        "name": ".kubetools create · Secret",
        "kind_label": "Secret",
        "body": """\
apiVersion: v1
kind: Secret
metadata:
  name: example-secrets
  namespace: CHANGE-ME
type: Opaque
stringData:
  EXAMPLE_KEY: "changeme"
""",
    },
}


def _author():
    try:
        return getpass.getuser() or "unknown"
    except Exception:
        return os.environ.get("USER") or os.environ.get("USERNAME") or "unknown"


def _slice_header(kind_label):
    stamp = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %z")
    ns = _ktc.EXAMPLE_TEMPLATE_NAMESPACE
    return (
        "#\n"
        "# kubetools create · {kind}\n"
        "# created: {stamp}\n"
        "# author: {author}\n"
        "#\n"
        "# Commit: {hint}   ·   Cancel: close tab\n"
        "# Apply is blocked while metadata.namespace is {ns}.\n"
        "# Rename metadata.name away from example / example-secrets.\n"
        "# Scratch slice — File → Save As… to keep. Not git commit.\n"
        "# Alt+Up / Alt+Down cycles YAML values.\n"
        "#\n"
        "\n".format(
            kind=kind_label,
            stamp=stamp,
            author=_author(),
            hint=_SLICE_COMMIT_HINT,
            ns=ns,
        )
    )


def _open_create_slice(window, key):
    spec = _TEMPLATES.get(key)
    if not spec or window is None:
        return
    body = _slice_header(spec["kind_label"]) + spec["body"]
    view = window.new_file()
    view.set_name(spec["name"])
    view.set_scratch(True)
    view.settings().set("kubetools_slice", True)
    view.settings().set("kubetools_slice_kind", key)
    try:
        view.assign_syntax("Packages/YAML/YAML.sublime-syntax")
    except Exception:
        pass
    view.run_command(
        "kubetools_insert_content",
        {"content": body, "read_only": False},
    )
    fields = _ktc.yaml_scalar_field_regions(body)
    target = None
    sentinel = _ktc.EXAMPLE_TEMPLATE_NAMESPACE
    for a, b in fields:
        if body[a:b].strip().strip("\"'") == sentinel:
            target = (a, b)
            break
    if target is None and fields:
        target = fields[0]
    if target:
        a, b = target
        view.sel().clear()
        view.sel().add(sublime.Region(a, b))
        view.show(a)
    sublime.status_message(
        "Kubetools: set namespace (not {}) · {} to apply".format(
            sentinel, _SLICE_COMMIT_HINT
        )
    )


class KubetoolsCreateCommand(sublime_plugin.WindowCommand):
    """Command Palette: .kubetools - Create…"""

    def run(self, kind=""):
        key = (kind or "").strip().lower()
        if key in _TEMPLATES:
            _open_create_slice(self.window, key)
            return
        keys = list(_TEMPLATES.keys())
        items = [
            [".kubetools - Create " + _TEMPLATES[k]["caption"], _TEMPLATES[k]["detail"]]
            for k in keys
        ]

        def picked(index):
            if index < 0 or index >= len(keys):
                return
            _open_create_slice(self.window, keys[index])

        self.window.show_quick_panel(items, picked)


class KubetoolsCommitSliceCommand(sublime_plugin.WindowCommand):
    """Cmd/Ctrl+Shift+Enter — apply this create slice (not git)."""

    def _view(self):
        return self.window.active_view() if self.window else None

    def is_visible(self):
        view = self._view()
        return bool(view and view.settings().get("kubetools_slice"))

    def is_enabled(self):
        view = self._view()
        if not view or not view.settings().get("kubetools_slice"):
            return False
        text = view.substr(sublime.Region(0, min(view.size(), 16000)))
        path = view.file_name()
        if _ktc.looks_like_helm_source(path, text):
            return False
        return _ktc.looks_like_k8s_manifest(text)

    def run(self):
        view = self._view()
        if not view or not view.settings().get("kubetools_slice"):
            sublime.status_message(
                "Kubetools: Apply is only allowed on a create slice"
            )
            return
        self.window.run_command("kubetools_open_file")


class KubetoolsSliceNextFieldCommand(sublime_plugin.TextCommand):
    """Alt+Up/Down on a create slice — select next/prev YAML scalar value."""

    def is_enabled(self):
        return bool(self.view.settings().get("kubetools_slice"))

    def run(self, edit, forward=True):
        text = self.view.substr(sublime.Region(0, self.view.size()))
        fields = _ktc.yaml_scalar_field_regions(text)
        if not fields:
            return
        caret = self.view.sel()[0].begin() if self.view.sel() else 0
        if self.view.sel():
            sel = self.view.sel()[0]
            for a, b in fields:
                if sel.begin() == a and sel.end() == b:
                    caret = a
                    break
        idx = _ktc.next_field_index(fields, caret, forward=bool(forward))
        if idx is None:
            return
        a, b = fields[idx]
        self.view.sel().clear()
        self.view.sel().add(sublime.Region(a, b))
        self.view.show(a)
