# Kubetools implementation — kubectl apply/compare flow (no Command classes).
# Sublime Text 4 / Python 3.14. Loaded as a module; commands live in kubetools.py.

import base64
import fnmatch
import json
import os
import re
import subprocess
import tempfile
from datetime import datetime

import sublime
import sublime_plugin

try:
    from . import kubetools_common as _ktc
except ImportError:
    import kubetools_common as _ktc  # type: ignore



# Common cluster-scoped kinds (Kind only). Unknown kinds are treated as
# namespaced so the plugin asks for a namespace when metadata.namespace is missing.
CLUSTER_SCOPED_KINDS = {
    "APIService",
    "CertificateSigningRequest",
    "ClusterIssuer",
    "ClusterRole",
    "ClusterRoleBinding",
    "ComponentStatus",
    "CSIDriver",
    "CSINode",
    "CustomResourceDefinition",
    "FlowSchema",
    "IngressClass",
    "MutatingWebhookConfiguration",
    "Namespace",
    "Node",
    "PersistentVolume",
    "PriorityClass",
    "PriorityLevelConfiguration",
    "RuntimeClass",
    "StorageClass",
    "ValidatingAdmissionPolicy",
    "ValidatingAdmissionPolicyBinding",
    "ValidatingWebhookConfiguration",
    "VolumeAttachment",
}


_MAX_MANIFEST_BYTES = 2 * 1024 * 1024  # 2 MiB
_MAX_RESOURCES = 50


def _clamp_timeout(value, default=60, minimum=5, maximum=600):
    return _ktc.clamp_timeout(value, default=default, minimum=minimum, maximum=maximum)


def _validate_kubectl_path(path):
    return _ktc.validate_binary_path(path, "kubectl")


def _try_begin_flow():
    return _ktc.OPERATION_GATE.try_begin()


def _end_flow():
    _ktc.OPERATION_GATE.end()


def _spawn(target, *args, **kwargs):
    return _ktc.spawn_daemon(target, *args, **kwargs)


def _settings():
    s = sublime.load_settings("Kubetools.sublime-settings")
    # Hard safety locks (cannot be disabled — prevents accidental apply):
    # - always pass --context
    # - always server-side dry-run before apply
    # - always require confirmation before apply
    return {
        "kubectl_path": s.get("kubectl_path", "kubectl") or "kubectl",
        "timeout": _clamp_timeout(s.get("timeout", 60)),
        "always_pass_context": True,
        "dry_run_before_apply": True,
        "show_diff_before_apply": bool(s.get("show_diff_before_apply", True)),
        "require_confirmation": True,
        "dangerous_context_patterns": list(
            s.get(
                "dangerous_context_patterns",
                ["*prod*", "*production*", "*-prod-*", "prod-*", "*prd*", "*live*"],
            )
            or []
        ),
        "dangerous_context_require_type_name": bool(
            s.get("dangerous_context_require_type_name", True)
        ),
        "warn_unsaved_buffer": bool(s.get("warn_unsaved_buffer", True)),
        "warn_secret_resources": bool(s.get("warn_secret_resources", True)),
        "require_secret_confirm": bool(s.get("require_secret_confirm", True)),
        "block_on_unverified_resources": bool(
            s.get("block_on_unverified_resources", True)
        ),
        "namespace_picker": s.get("namespace_picker", "quick_panel") or "quick_panel",
        "default_namespace": s.get("default_namespace", "default") or "default",
        "validate": bool(s.get("validate", True)),
        "server_side_apply": bool(s.get("server_side_apply", False)),
        "field_manager": s.get("field_manager", "sublime-kubetools")
        or "sublime-kubetools",
        "show_result_in_tab": True,
        "max_resources": int(s.get("max_resources", _MAX_RESOURCES) or _MAX_RESOURCES),
        "max_manifest_bytes": int(
            s.get("max_manifest_bytes", _MAX_MANIFEST_BYTES) or _MAX_MANIFEST_BYTES
        ),
        # Path → context preselect (never skips the context quick panel)
        "guess_context_from_path": bool(s.get("guess_context_from_path", True)),
        "context_path_aliases": dict(s.get("context_path_aliases", {}) or {}),
    }


def _run_kubectl(args, settings, stdin_data=None):
    """Run kubectl; returns (returncode, stdout, stderr). List argv only."""
    cmd = [settings["kubectl_path"]] + list(args)
    code, stdout, stderr = _ktc.run_subprocess(
        cmd, timeout=settings["timeout"], stdin_data=stdin_data, label="kubectl"
    )
    if code == -1 and "not found" in (stderr or "").lower():
        return 127, "", "kubectl not found: {}".format(settings["kubectl_path"])
    if code == -1 and "timed out" in (stderr or "").lower():
        return 124, "", stderr
    return code, stdout, stderr


# kubectl verbs that can change cluster state (without --dry-run)
_KUBECTL_MUTATING_VERBS = frozenset(
    {
        "apply",
        "create",
        "delete",
        "patch",
        "replace",
        "edit",
        "scale",
        "annotate",
        "label",
        "taint",
        "cordon",
        "uncordon",
        "drain",
        "set",
        "rollout",
        "expose",
        "run",
        "autoscale",
        "certificate",
        "cp",
        "attach",
        "exec",
        "port-forward",  # not a mutation but blocked in read-only for safety
        "proxy",
        "debug",
    }
)

_KUBECTL_READONLY_VERBS = frozenset(
    {
        "get",
        "diff",
        "describe",
        "explain",
        "api-resources",
        "api-versions",
        "version",
        "config",
        "auth",
        "cluster-info",
        "top",
        "logs",
        "events",
        "wait",
    }
)

# config subcommands that mutate kubeconfig (never allowed in read-only modes)
_KUBECTL_CONFIG_MUTATIONS = frozenset(
    {
        "set",
        "set-cluster",
        "set-credentials",
        "set-context",
        "use-context",
        "delete-cluster",
        "delete-context",
        "delete-user",
        "unset",
        "rename-context",
    }
)

_KUBECTL_FLAGS_WITH_VALUE = frozenset(
    {
        "--context",
        "--kubeconfig",
        "--cluster",
        "--user",
        "--namespace",
        "-n",
        "--filename",
        "-f",
        "--output",
        "-o",
        "--selector",
        "-l",
        "--field-manager",
        "--timeout",
        "--request-timeout",
        "--cache-dir",
        "--server",
        "-s",
        "--token",
        "--client-certificate",
        "--client-key",
        "--certificate-authority",
        "--dry-run",
    }
)


def _kubectl_primary_verb(args):
    """First non-flag token (the kubectl verb)."""
    i = 0
    args = list(args or [])
    while i < len(args):
        a = args[i]
        if a in _KUBECTL_FLAGS_WITH_VALUE:
            i += 2
            continue
        if a.startswith("--") and "=" in a:
            i += 1
            continue
        if a.startswith("-"):
            i += 1
            continue
        return a
    return None


def _kubectl_dry_run_mode(args):
    """Return 'client' / 'server' / 'none' / True (bare) from args."""
    args = list(args or [])
    for i, a in enumerate(args):
        if a.startswith("--dry-run="):
            return a.split("=", 1)[1].strip() or "client"
        if a == "--dry-run":
            if i + 1 < len(args) and not args[i + 1].startswith("-"):
                return args[i + 1].strip()
            return "client"
    return None


def _assert_kubectl_allowed_for_mode(mode, args):
    """
    Hard stop: compare / diff / dry-run must never mutate the cluster.

    - compare/diff: only read verbs, plus create/apply with --dry-run=client
      (local YAML→JSON convert; never contacts the API for writes)
    - dry-run: read verbs + apply/create with any --dry-run (incl. server)
    - apply: unrestricted here (UI confirms separately)
    """
    mode = (mode or "").strip()
    if mode == "apply":
        return

    verb = _kubectl_primary_verb(args)
    if not verb:
        raise RuntimeError("Kubetools: refused empty kubectl command in mode {!r}".format(mode))

    dry = _kubectl_dry_run_mode(args)

    if verb == "config":
        # find subcommand after 'config'
        sub = None
        seen = False
        for a in args:
            if a.startswith("-"):
                continue
            if not seen:
                if a == "config":
                    seen = True
                continue
            sub = a
            break
        if sub in _KUBECTL_CONFIG_MUTATIONS:
            raise RuntimeError(
                "Kubetools REFUSED kubectl config {} in read-only mode {!r}".format(
                    sub, mode
                )
            )
        return

    if verb in _KUBECTL_READONLY_VERBS:
        return

    if verb in _KUBECTL_MUTATING_VERBS:
        if not dry:
            raise RuntimeError(
                "Kubetools REFUSED mutating kubectl '{}' without --dry-run "
                "in read-only mode {!r}".format(verb, mode)
            )
        if mode in ("compare", "diff") and dry == "server":
            raise RuntimeError(
                "Kubetools REFUSED --dry-run=server in {!r} mode "
                "(compare/diff must not hit the API for writes)".format(mode)
            )
        return

    raise RuntimeError(
        "Kubetools REFUSED kubectl '{}' in read-only mode {!r}".format(verb, mode)
    )


def _paths_are_same_file(a, b):
    return _ktc.paths_are_same_file(a, b)


def _is_dangerous_context(context, patterns):
    return _ktc.is_dangerous_name(context, patterns)


def _parse_resources(content):
    """
    Lightweight multi-doc YAML parse for kind / name / namespace.
    Avoids a PyYAML dependency (not bundled with Sublime).
    """
    resources = []
    # Split on document markers at line start
    docs = re.split(r"(?m)^---\s*$", content or "")
    compact_idx = 0
    for doc in docs:
        text = doc.strip()
        if not text or text.startswith("..."):
            continue

        kind = None
        name = None
        namespace = None
        api_version = None

        in_metadata = False
        metadata_indent = None

        for raw_line in text.splitlines():
            if not raw_line.strip() or raw_line.lstrip().startswith("#"):
                continue

            # Top-level keys only (no leading whitespace)
            top = re.match(r"^(apiVersion|kind|metadata)\s*:\s*(.*)$", raw_line)
            if top:
                key = top.group(1)
                value = top.group(2).strip().strip("\"'")
                if key == "apiVersion":
                    api_version = value or None
                    in_metadata = False
                elif key == "kind":
                    kind = value or None
                    in_metadata = False
                elif key == "metadata":
                    in_metadata = True
                    metadata_indent = None
                continue

            if in_metadata:
                # Leave metadata when indentation returns to top-level key
                if re.match(r"^[A-Za-z]", raw_line):
                    in_metadata = False
                    continue

                indent_match = re.match(r"^(\s+)(\S.*?)\s*:\s*(.*)$", raw_line)
                if not indent_match:
                    continue
                indent, key, value = indent_match.groups()
                indent_len = len(indent.replace("\t", "  "))
                if metadata_indent is None:
                    metadata_indent = indent_len
                # Only direct children of metadata (not labels/annotations nested)
                if indent_len != metadata_indent:
                    continue
                value = value.strip().strip("\"'")
                if key == "name" and value:
                    name = value
                elif key == "namespace" and value:
                    namespace = value

        if not kind and not name and not api_version:
            continue

        resources.append(
            {
                "index": compact_idx,
                "apiVersion": api_version,
                "kind": kind,
                "name": name,
                "namespace": namespace,
                "cluster_scoped": (kind in CLUSTER_SCOPED_KINDS) if kind else False,
            }
        )
        compact_idx += 1
    return resources


def _resource_label(resource, fallback_ns=None):
    kind = resource.get("kind") or "?"
    name = resource.get("name") or "?"
    if resource.get("cluster_scoped"):
        return "{}/{}".format(kind, name)
    ns = resource.get("namespace") or fallback_ns or "?"
    return "{}/{}/{}".format(ns, kind, name)


def _is_missing_namespace_error(text):
    """True when kubectl failed because the target namespace does not exist."""
    t = (text or "").lower()
    # Error from server (NotFound): namespaces "foo" not found
    # … error when creating "…": namespaces "foo" not found
    if re.search(r'namespaces?\s+"[^"]+"\s+not\s+found', t):
        return True
    if re.search(r"namespaces?\s+[a-z0-9]([-a-z0-9]*[a-z0-9])?\s+not\s+found", t):
        return True
    return False


def _is_resource_not_found(text):
    """True when the named resource is missing (not when its namespace is missing)."""
    combined = (text or "").lower()
    if _is_missing_namespace_error(combined):
        return False
    # Prefer explicit API NotFound markers; avoid bare "not found" false positives.
    compact = combined.replace(" ", "")
    if "notfound" in compact:
        return True
    if re.search(r'\berror from server \(notfound\)', combined):
        return True
    if re.search(r'\bnot found\b', combined) and (
        "error from server" in combined
        or re.search(r'\b(services?|pods?|deployments?|configmaps?|secrets?|'
                     r'statefulsets?|daemonsets?|jobs?|cronjobs?|ingresses?|'
                     r'roles?|rolebindings?|serviceaccounts?)\b', combined)
    ):
        return True
    return False


def _write_temp_manifest(content, suffix=".yaml", forbid_path=None):
    """
    Write a temp snapshot under the system temp dir (mode 0600 when the OS allows).
    Never writes the user's open file. ``forbid_path`` is refused if it would
    land on the same path (defense in depth).
    """
    return _ktc.write_temp_under_tmpdir(
        content, suffix=suffix, forbid_path=forbid_path
    )


# ---------------------------------------------------------------------------
# Live compare helpers (stdlib only — no PyYAML)
# ---------------------------------------------------------------------------

_COMPARE_REGION_KEY = "kubetools-compare"
_COMPARE_PHANTOM_KEY = "kubetools-compare-banner"
_COMPARE_INLINE_PHANTOM_KEY = "kubetools-compare-inline"
_COMPARE_LIVE_MAX_CHARS = 40


def _view_text_head(view, limit=16000):
    if view is None:
        return ""
    try:
        return view.substr(sublime.Region(0, min(view.size(), limit)))
    except Exception:
        return ""


def _view_is_k8s_manifest(view):
    return _ktc.looks_like_k8s_manifest(_view_text_head(view))


def _view_is_slice(view):
    return bool(view and view.settings().get("kubetools_slice"))


def _view_can_apply(view):
    """kubectl apply: create slices only, never Helm/GitOps/Argo CRs."""
    if not _view_is_slice(view) or not _view_is_k8s_manifest(view):
        return False
    path = view.file_name() if view else None
    return _ktc.mutating_source_block_reason(path, _view_text_head(view)) is None


def _view_can_dry_run(view):
    if not _view_is_k8s_manifest(view):
        return False
    path = view.file_name() if view else None
    return _ktc.mutating_source_block_reason(path, _view_text_head(view)) is None


def _view_has_compare_diffs(view):
    return bool(_compare_diff_regions(view))


def _view_can_seal(view):
    if view is None or view.is_read_only():
        return False
    content = _view_text_head(view)
    has_sel = any(not r.empty() for r in view.sel())
    if _ktc.looks_like_plain_secret(content):
        return not has_sel
    if _ktc.looks_like_sealed_secret(content):
        return has_sel
    return False


def _view_can_unseal(view):
    if view is None:
        return False
    if not any(not r.empty() for r in view.sel()):
        return False
    return _ktc.looks_like_sealed_secret(_view_text_head(view))


def _clear_compare_overlays(view):
    if view is None:
        return
    try:
        view.erase_regions(_COMPARE_REGION_KEY)
    except Exception:
        pass
    for key in (_COMPARE_PHANTOM_KEY, _COMPARE_INLINE_PHANTOM_KEY):
        try:
            view.erase_phantoms(key)
        except Exception:
            pass
    try:
        view.settings().erase("kubetools_compare_copies")
    except Exception:
        pass


def _compare_diff_regions(view):
    """Sorted underline regions from the last Compare State."""
    if view is None:
        return []
    try:
        regions = list(view.get_regions(_COMPARE_REGION_KEY) or [])
    except Exception:
        return []
    return sorted(regions, key=lambda r: r.begin())


def _goto_compare_diff(view, direction):
    """Move caret to next/prev compare underline (direction +1 / -1)."""
    regions = _compare_diff_regions(view)
    if not regions or view is None:
        sublime.status_message("Kubetools: no compare diffs — run Compare State")
        return False
    caret = view.sel()[0].begin() if view.sel() else 0
    target = None
    if direction >= 0:
        for r in regions:
            if r.begin() > caret:
                target = r
                break
        if target is None:
            target = regions[0]
    else:
        for r in reversed(regions):
            if r.begin() < caret:
                target = r
                break
        if target is None:
            target = regions[-1]
    view.sel().clear()
    view.sel().add(sublime.Region(target.begin(), target.end()))
    view.show(target)
    sublime.status_message(
        "Kubetools: diff {}/{}".format(regions.index(target) + 1, len(regions))
    )
    return True


def _copy_live_at_caret(view):
    """Copy live value for the compare diff on the caret's line."""
    if view is None:
        return
    entries = view.settings().get("kubetools_compare_copies") or []
    if not entries:
        sublime.status_message("Kubetools: no compare data — run Compare State")
        return
    caret = view.sel()[0].begin() if view.sel() else 0
    line_begin = view.line(caret).begin()
    idx = None
    for i, e in enumerate(entries):
        if not isinstance(e, dict):
            continue
        if int(e.get("line_begin", -1)) == line_begin:
            idx = i
            break
    if idx is None:
        # Caret on underline but line_begin mismatch — try region containment
        for i, e in enumerate(entries):
            if not isinstance(e, dict):
                continue
            a, b = e.get("a"), e.get("b")
            if a is not None and b is not None and a <= caret <= b:
                idx = i
                break
    if idx is None:
        sublime.status_message(
            "Kubetools: put caret on an underlined diff line, then copy"
        )
        return
    _on_compare_navigate(view, "copy:{}".format(idx))


def _ui_notice(window, title, detail="", on_done=None):
    """Keyboard-friendly notice (quick_panel) — prefer over error_message."""
    sublime.status_message("Kubetools: {}".format(title))
    if window is None:
        if on_done:
            on_done()
        return
    items = [[str(title), (detail or "Enter / Esc to dismiss")[:200]]]

    def picked(_index):
        if on_done:
            on_done()

    sublime.set_timeout(lambda: window.show_quick_panel(items, picked), 10)


def _h_mini(text):
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _truncate_anno(text, max_chars):
    s = "" if text is None else str(text)
    s = s.replace("\n", " ").replace("\r", " ")
    if len(s) <= max_chars:
        return s
    if max_chars <= 1:
        return "…"
    return s[: max_chars - 1] + "…"


def _overlay_live_label(live_text):
    """
    Compact label for the below-line phantom.
    Returns (text, is_absent).
    """
    s = "" if live_text is None else str(live_text)
    if s in ("absent on live", "<missing>"):
        return "absent on live", True
    if s.strip() == "" and s != '""':
        return "(whitespace)", False
    return _truncate_anno(s, _COMPARE_LIVE_MAX_CHARS), False


def _apply_compare_overlays(view, annotations_data, summary_html, context_name):
    """
    annotations_data: list of {a, b, live_text, path}

    Underline the differing value. Show live value on the **next line**
    (LAYOUT_BLOCK — same path as the banner) with a clickable copy link.
    """
    _clear_compare_overlays(view)
    if view is None:
        return

    underline_regions = []
    # line_begin -> (underline_a, underline_b, live_text, path)
    by_line = {}

    for item in annotations_data:
        a = item.get("a")
        b = item.get("b")
        if a is None or b is None or a >= b or b > view.size():
            continue
        underline_regions.append(sublime.Region(a, b))
        line = view.line(sublime.Region(a, b))
        by_line[line.begin()] = (a, b, item.get("live_text", ""), item.get("path", ""))

    if underline_regions:
        flags = 0
        try:
            flags = (
                sublime.DRAW_NO_FILL
                | sublime.DRAW_NO_OUTLINE
                | sublime.DRAW_SOLID_UNDERLINE
            )
        except Exception:
            flags = sublime.DRAW_NO_FILL
        view.add_regions(
            _COMPARE_REGION_KEY,
            underline_regions,
            scope="region.orangish",
            icon="",
            flags=flags,
        )

    def _nav(href, v=view):
        _on_compare_navigate(v, href)

    def _add_live_phantom(line_begin, html):
        """
        Place phantom below the line. Prefer LAYOUT_BLOCK (proven via banner).
        Use a zero-width region at line start. Return True if a phantom id > 0.
        """
        pt = sublime.Region(line_begin, line_begin)
        layouts = []
        try:
            layouts.append(sublime.LAYOUT_BLOCK)
        except Exception:
            pass
        try:
            layouts.append(sublime.LAYOUT_BELOW)
        except Exception:
            pass
        if not layouts:
            layouts = [1, 2]

        for layout in layouts:
            for with_nav in (True, False):
                try:
                    if with_nav:
                        pid = view.add_phantom(
                            _COMPARE_INLINE_PHANTOM_KEY,
                            pt,
                            html,
                            layout,
                            on_navigate=_nav,
                        )
                    else:
                        pid = view.add_phantom(
                            _COMPARE_INLINE_PHANTOM_KEY,
                            pt,
                            html,
                            layout,
                        )
                except TypeError:
                    continue
                except Exception as exc:
                    continue
                if isinstance(pid, int) and pid >= 0:
                    return True
        return False

    copy_entries = []
    phantoms_ok = 0
    phantoms_fail = 0
    for line_begin, (ua, ub, live_text, path) in sorted(by_line.items()):
        label, is_absent = _overlay_live_label(live_text)
        idx = len(copy_entries)
        redacted = str(live_text).strip() in ("***",) or str(live_text).startswith(
            "***"
        )
        copy_entries.append(
            {
                "path": path or "",
                "live": ""
                if is_absent
                else ("" if live_text is None else str(live_text)),
                "absent": bool(is_absent),
                "redacted": redacted,
                "line_begin": int(line_begin),
                "a": int(ua),
                "b": int(ub),
            }
        )
        # Concat only — never str.format (live values may contain braces).
        live_html = _h_mini(label)
        # Keep CSS minimal (px only) — minihtml is picky about units.
        # font-size in em tracks the view font; keep underlay clearly smaller.
        _body = (
            "margin:0;padding:1px 0 3px 24px;"
            "font-size:0.78em;line-height:1.2"
        )
        if is_absent:
            html = (
                "<body id='kubetools-live' style='"
                + _body
                + "'>"
                "<span style='color:#5c6370'>live | </span>"
                "<span style='color:#e06c75'>absent</span>"
                "</body>"
            )
        else:
            html = (
                "<body id='kubetools-live' style='"
                + _body
                + "'>"
                "<span style='color:#5c6370'>live | </span>"
                "<a href='copy:"
                + str(idx)
                + "' style='color:#e5c07b;text-decoration:underline'>"
                + live_html
                + "</a>"
                "<span style='color:#3e4451'> | </span>"
                "<a href='copy:"
                + str(idx)
                + "' style='color:#56b6c2;text-decoration:underline'>copy</a>"
                "</body>"
            )
        if _add_live_phantom(line_begin, html):
            phantoms_ok += 1
        else:
            phantoms_fail += 1

    try:
        view.settings().set("kubetools_compare_copies", copy_entries)
    except Exception:
        pass

    if summary_html:
        try:
            view.add_phantom(
                _COMPARE_PHANTOM_KEY,
                sublime.Region(0),
                summary_html,
                sublime.LAYOUT_BLOCK,
                on_navigate=_nav,
            )
        except TypeError:
            try:
                view.add_phantom(
                    _COMPARE_PHANTOM_KEY,
                    sublime.Region(0),
                    summary_html,
                    sublime.LAYOUT_BLOCK,
                )
            except Exception:
                pass
        except Exception:
            pass

    if phantoms_fail:
        sublime.status_message(
            "Kubetools: {} live row(s) ok, {} failed (see console)".format(
                phantoms_ok, phantoms_fail
            )
        )


def _on_compare_navigate(view, href):
    if view is None or not href:
        return
    if href == "clear":
        _clear_compare_overlays(view)
        try:
            view.settings().erase("kubetools_compare_copies")
        except Exception:
            pass
        sublime.status_message("Kubetools: cleared compare overlays")
        return
    if href == "focus-issues":
        issues = view.settings().get("kubetools_compare_issues") or ""
        if issues:
            win = view.window()
            if win:
                v = win.new_file()
                v.set_name("Kubetools Compare — safety")
                v.set_scratch(True)
                v.run_command("kubetools_insert_content", {"content": issues})
        return
    if href == "copy-all":
        entries = view.settings().get("kubetools_compare_copies") or []
        lines = []
        skipped = 0
        for e in entries:
            if not isinstance(e, dict) or e.get("absent"):
                continue
            if e.get("redacted"):
                skipped += 1
                continue
            lines.append("{}={}".format(e.get("path") or "?", e.get("live") or ""))
        if not lines:
            sublime.status_message(
                "Kubetools: nothing to copy"
                + (" ({} redacted)".format(skipped) if skipped else "")
            )
            return
        sublime.set_clipboard("\n".join(lines) + "\n")
        msg = "Kubetools: copied {} live value(s)".format(len(lines))
        if skipped:
            msg += ", skipped {} redacted".format(skipped)
        sublime.status_message(msg)
        return
    if href.startswith("copy:"):
        try:
            idx = int(href.split(":", 1)[1])
        except (TypeError, ValueError):
            return
        entries = view.settings().get("kubetools_compare_copies") or []
        if not (0 <= idx < len(entries)):
            return
        e = entries[idx]
        if not isinstance(e, dict):
            return
        if e.get("absent"):
            sublime.status_message("Kubetools: nothing to copy (absent on live)")
            return
        if e.get("redacted"):
            sublime.status_message(
                "Kubetools: live Secret value is redacted — not copied"
            )
            return
        text = e.get("live")
        if text is None:
            text = ""
        sublime.set_clipboard(str(text))
        path = e.get("path") or ""
        sublime.status_message(
            "Kubetools: copied live value"
            + (" ({})".format(path) if path else "")
        )
        return


def _build_compare_banner(
    context, n_diffs, n_issues, n_unmapped, n_missing=0
):
    """
    Top-of-buffer compare summary.

    When live resources are missing and there are no field diffs, say so
    explicitly — "0 diff(s)" is misleading for a resource that is not on
    the cluster at all.
    """
    parts = [
        "<body style='margin:6px 10px;font-size:0.9rem;padding:4px 0;"
        "border-bottom:1px solid color(var(--foreground) alpha(0.15))'>",
        "<strong style='color:#61afef'>Kubetools compare</strong>",
        " <span style='color:#5c6370'>@ {}</span>".format(_h_mini(context)),
    ]
    if n_missing and n_diffs == 0:
        if n_missing == 1:
            miss_label = "does not exist on cluster"
        else:
            miss_label = "{} resources not on cluster".format(n_missing)
        parts.append(
            " · <span style='color:#e06c75'>{}</span>".format(miss_label)
        )
    else:
        parts.append(
            " · <span style='color:#e5c07b'>{} diff(s)</span>".format(n_diffs)
        )
        if n_missing:
            parts.append(
                " · <span style='color:#e06c75'>{} not on cluster</span>".format(
                    n_missing
                )
            )
    if n_issues:
        parts.append(
            " · <a href='focus-issues' style='color:#e06c75'>"
            "{} safety</a>".format(n_issues)
        )
    if n_unmapped:
        parts.append(
            " · <span style='color:#5c6370'>{} unmapped to a line</span>".format(
                n_unmapped
            )
        )
    parts.append(
        " · <a href='copy-all' style='color:#56b6c2'>copy all</a>"
        " · <span style='color:#5c6370'>keys: ⌃K ⌃N/P/Y/X</span>"
        " · <a href='clear' style='color:#56b6c2'>clear</a>"
        "</body>"
    )
    return "".join(parts)


_COMPARE_IGNORE_EXACT = {
    "status",
    "metadata.uid",
    "metadata.resourceVersion",
    "metadata.generation",
    "metadata.creationTimestamp",
    "metadata.managedFields",
    "metadata.selfLink",
    "metadata.ownerReferences",
    "metadata.finalizers",
    "metadata.annotations.kubectl.kubernetes.io/last-applied-configuration",
    "metadata.annotations.deployment.kubernetes.io/revision",
    "metadata.annotations.kubectl.kubernetes.io/restartedAt",
}



def _split_yaml_docs_with_spans(content):
    """
    Split multi-doc YAML; return list of (start, end, text) char spans
    in the original buffer (end exclusive).
    """
    content = content or ""
    spans = []
    # Find --- separators at line starts
    parts = []
    last = 0
    for m in re.finditer(r"(?m)^---\s*$", content):
        parts.append((last, m.start()))
        last = m.end()
        if last < len(content) and content[last] == "\n":
            last += 1
    parts.append((last, len(content)))

    for start, end in parts:
        chunk = content[start:end]
        text = chunk.strip()
        if not text or text.startswith("..."):
            continue
        # trim trailing whitespace-only lines for strip check already done;
        # keep original span for offset mapping
        spans.append((start, end, chunk if chunk.endswith("\n") else chunk + "\n"))
    return spans


def _value_span_on_line(line, line_start, val_code):
    """Char span for a scalar on a physical line (absolute offsets)."""
    content_end = line_start + len(line.rstrip("\r\n"))
    if not val_code:
        content_start = line_start + (len(line) - len(line.lstrip(" \t")))
        return content_start, content_end
    idx = line.rfind(val_code)
    if idx < 0:
        return line_start, content_end
    return line_start + idx, line_start + idx + len(val_code)


def _yaml_path_value_regions(text):
    """
    Best-effort map of dotted/indexed YAML paths → (a, b) offsets of the
    scalar value on that line. Tuned for typical Kubernetes manifests.
    """
    regions = {}
    # stack: (indent, path, is_list_item_frame)
    stack = []
    list_counters = {}

    offset = 0
    for line in (text or "").splitlines(True):
        line_start = offset
        offset += len(line)

        stripped = line.lstrip(" \t")
        if not stripped or stripped.startswith("#"):
            continue

        indent = len(line) - len(line.lstrip(" \t"))
        is_list = stripped.startswith("- ")
        body = stripped[2:].lstrip() if is_list else stripped

        if is_list:
            # New list item replaces previous item frame at this indent
            while stack and stack[-1][0] >= indent and stack[-1][2]:
                stack.pop()
            while stack and stack[-1][0] > indent:
                stack.pop()
        else:
            while stack and stack[-1][0] >= indent:
                stack.pop()

        parent = stack[-1][1] if stack else ""

        if is_list:
            idx = list_counters.get(parent, 0)
            list_counters[parent] = idx + 1
            item_path = (
                "{}[{}]".format(parent, idx) if parent else "[{}]".format(idx)
            )

            m = re.match(r"^([^:\s][^:]*)\s*:\s*(.*)$", body)
            if m:
                key = m.group(1).strip().strip("\"'")
                val = m.group(2)
                val_code = re.split(r"\s+#", val, 1)[0].rstrip()
                path = "{}.{}".format(item_path, key)
                stack.append((indent, item_path, True))
                if val_code != "":
                    regions[path] = _value_span_on_line(line, line_start, val_code)
                else:
                    # ` - key:` with nested block under the key
                    stack.append((indent + 1, path, False))
            else:
                val_code = re.split(r"\s+#", body, 1)[0].rstrip()
                regions[item_path] = _value_span_on_line(line, line_start, val_code)
                stack.append((indent, item_path, True))
            continue

        m = re.match(r"^([^:\s][^:]*)\s*:\s*(.*)$", body)
        if not m:
            continue
        key = m.group(1).strip().strip("\"'")
        val = m.group(2)
        val_code = re.split(r"\s+#", val, 1)[0].rstrip()
        path = "{}.{}".format(parent, key) if parent else key
        if val_code != "":
            regions[path] = _value_span_on_line(line, line_start, val_code)
        else:
            stack.append((indent, path, False))
            list_counters[path] = 0

    return regions


def _compare_should_ignore(path):
    if not path:
        return True
    if path in _COMPARE_IGNORE_EXACT:
        return True
    if path == "status" or path.startswith("status."):
        return True
    if path.startswith("metadata.managedFields"):
        return True
    if path.startswith("metadata.ownerReferences"):
        return True
    # Auto-generated annotation noise
    if path.startswith(
        "metadata.annotations.kubectl.kubernetes.io/last-applied-configuration"
    ):
        return True
    return False


def _flatten_json(obj, prefix=""):
    """Flatten nested JSON to dotted / [index] paths → scalar values."""
    out = {}
    if isinstance(obj, dict):
        if not obj and prefix:
            out[prefix] = {}
            return out
        for key, val in obj.items():
            path = "{}.{}".format(prefix, key) if prefix else str(key)
            out.update(_flatten_json(val, path))
        return out
    if isinstance(obj, list):
        if not obj and prefix:
            out[prefix] = []
            return out
        for idx, val in enumerate(obj):
            path = "{}[{}]".format(prefix, idx)
            out.update(_flatten_json(val, path))
        return out
    out[prefix] = obj
    return out


def _fmt_compare_value(value):
    """Human-readable scalar for report lines / overlays."""
    if value is _MISSING:
        return "absent on live"
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        # Prefer bare text when safe; else JSON-quoted
        if value == "" or any(c in value for c in ":#{}[]&*!|>'\"%@`,?\n\r\t ") or value.lower() in (
            "true",
            "false",
            "null",
            "yes",
            "no",
            "~",
        ):
            return json.dumps(value, ensure_ascii=False)
        return value
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except TypeError:
        return repr(value)


_MISSING = object()


def _values_equal(a, b):
    if a is _MISSING and b is _MISSING:
        return True
    if a is _MISSING or b is _MISSING:
        return False
    # Numeric string vs int leniency: "30" vs 30
    if type(a) != type(b):
        try:
            if str(a) == str(b):
                return True
        except Exception:
            pass
    return a == b


def _redact_secret_path(kind, path):
    if (kind or "").lower() != "secret":
        return False
    return path.startswith("data.") or path.startswith("stringData.") or path in (
        "data",
        "stringData",
    )


def _live_value_for_path(live_flat, path, kind):
    """
    Look up live value for a local path.

    Secrets: templates often use stringData; kubectl get returns data (base64).
    Bridge stringData.X ↔ data.X so empty template keys are not reported as
    ``absent on live`` when the cluster actually has the key under data.
    """
    if path in live_flat:
        return live_flat[path]
    if (kind or "").lower() == "secret":
        if path.startswith("stringData."):
            alt = "data." + path[len("stringData.") :]
            if alt in live_flat:
                return live_flat[alt]
        elif path.startswith("data."):
            alt = "stringData." + path[len("data.") :]
            if alt in live_flat:
                return live_flat[alt]
    return _MISSING


def _secret_values_equal(local_val, live_val, path):
    """
    Compare Secret stringData (plain) to live data (base64) when possible.
    Empty local + any live bytes ⇒ differ (template placeholder vs cluster secret).
    """
    if live_val is _MISSING:
        return False
    if local_val is _MISSING:
        return False
    # Same path family already handled by caller for non-bridged keys
    if path.startswith("stringData.") or path.startswith("data."):
        # Never treat empty template as equal to a populated live secret
        if local_val == "" or local_val is None:
            return False
        if isinstance(local_val, str) and isinstance(live_val, str):
            try:
                decoded = base64.b64decode(live_val).decode("utf-8")
                return decoded == local_val
            except Exception:
                # Can't decode — if both non-empty, assume differ (safe)
                return False
    return _values_equal(local_val, live_val)


def _diff_local_vs_live(local_obj, live_obj, kind):
    """
    Compare local manifest JSON to live object.
    Only local keys are considered (manifest is the source of interest).
    Returns (diffs, issues) where diffs are (path, local_val, live_val).
    """
    diffs = []
    issues = []
    if not isinstance(local_obj, dict):
        issues.append("local object is not a JSON object (got {})".format(type(local_obj).__name__))
        return diffs, issues
    if live_obj is None:
        issues.append("live object missing")
        return diffs, issues
    if not isinstance(live_obj, dict):
        issues.append("live object is not a JSON object (got {})".format(type(live_obj).__name__))
        return diffs, issues

    try:
        local_flat = _flatten_json(local_obj)
    except Exception as exc:
        issues.append("failed to flatten local object: {}".format(exc))
        return diffs, issues
    try:
        live_flat = _flatten_json(live_obj)
    except Exception as exc:
        issues.append("failed to flatten live object: {}".format(exc))
        return diffs, issues

    is_secret = (kind or "").lower() == "secret"

    for path, local_val in sorted(local_flat.items()):
        if _compare_should_ignore(path):
            continue
        # Skip empty container maps that only exist as structure
        if local_val in ({}, []) and path in live_flat:
            continue
        live_val = _live_value_for_path(live_flat, path, kind)
        if is_secret and _redact_secret_path(kind, path):
            equal = _secret_values_equal(local_val, live_val, path)
        else:
            equal = _values_equal(local_val, live_val)
        if equal:
            continue
        if _redact_secret_path(kind, path):
            # Show presence, never plaintext. Empty template + set live → "***"
            if live_val is _MISSING:
                live_show = _MISSING
            elif live_val is None or live_val == "":
                live_show = '""'
            else:
                # Any non-empty live bytes (including whitespace / base64) → set
                live_show = "***"
            if local_val is None or local_val == "":
                local_show = '""'
            elif isinstance(local_val, str) and not local_val.strip():
                local_show = "***"
            else:
                local_show = "***"
            diffs.append((path, local_show, live_show))
        else:
            diffs.append((path, local_val, live_val))
    return diffs, issues



def _manifest_doc_to_json(settings, context, doc_text, forbid_path=None):
    """
    Convert one YAML doc to a JSON object via kubectl *client* dry-run only.
    Never mutates the cluster or the user's file (writes a temp snapshot only).
    Returns (obj_or_None, error_string_or_None).
    """
    path = None
    try:
        path = _write_temp_manifest(doc_text, forbid_path=forbid_path)
        # Prefer ``create --dry-run=client`` so we never invoke apply semantics.
        args = [
            "create",
            "--dry-run=client",
            "--validate=false",
            "-o",
            "json",
            "-f",
            path,
        ]
        if context:
            args = ["--context", context] + args
        # Hard read-only gate (compare/diff semantics)
        _assert_kubectl_allowed_for_mode("compare", args)
        code, out, err = _run_kubectl(args, settings)
        if code != 0:
            # Fallback: some resources prefer apply client dry-run for conversion
            args2 = [
                "apply",
                "--dry-run=client",
                "--validate=false",
                "-o",
                "json",
                "-f",
                path,
            ]
            if context:
                args2 = ["--context", context] + args2
            _assert_kubectl_allowed_for_mode("compare", args2)
            code, out, err = _run_kubectl(args2, settings)
        if code != 0:
            return None, (err or out or "kubectl client dry-run failed").strip()
        raw = (out or "").strip()
        if not raw:
            return None, "kubectl returned empty JSON for local manifest"
        # kubectl may print multiple JSON docs; take first object / List
        decoder = json.JSONDecoder()
        obj, _idx = decoder.raw_decode(raw)
        if isinstance(obj, dict) and obj.get("kind") == "List":
            items = obj.get("items") or []
            if not items:
                return None, "kubectl returned empty List for local manifest"
            if len(items) > 1:
                return None, "expected one resource in doc, got List with {}".format(
                    len(items)
                )
            return items[0], None
        if not isinstance(obj, dict):
            return None, "unexpected JSON type from kubectl: {}".format(type(obj).__name__)
        return obj, None
    except Exception as exc:
        return None, str(exc)
    finally:
        if path and os.path.exists(path):
            try:
                os.remove(path)
            except Exception:
                pass


def _inject_namespace_into_manifest(content, namespace, target_kinds_names):
    """
    Insert metadata.namespace into documents that lacked it.

    Prefer mutating the temp YAML over `kubectl -n`, which can surprise
    multi-doc files that already declare other namespaces.
    target_kinds_names: set of (kind, name) pairs to patch.
    """
    if not namespace or not target_kinds_names:
        return content

    # parts: [pre, '---', doc, '---', doc, ...]
    parts = re.split(r"(?m)^(---\s*)$", content or "")
    if len(parts) == 1:
        return _inject_ns_one_doc(parts[0], namespace, target_kinds_names)

    out = []
    for chunk in parts:
        if re.match(r"^---\s*$", chunk or ""):
            out.append(chunk if chunk.endswith("\n") else chunk + "\n")
            continue
        out.append(_inject_ns_one_doc(chunk, namespace, target_kinds_names))
    return "".join(out)


def _inject_ns_one_doc(doc, namespace, target_kinds_names):
    if not doc or not doc.strip():
        return doc

    kind = None
    name = None
    has_namespace = False
    in_metadata = False
    metadata_indent = None
    metadata_line_idx = None
    name_line_idx = None
    lines = doc.splitlines(True)  # keepends

    for idx, raw_line in enumerate(lines):
        stripped = raw_line.lstrip()
        if not stripped or stripped.startswith("#"):
            continue

        top = re.match(r"^(apiVersion|kind|metadata)\s*:\s*(.*)$", raw_line)
        if top:
            key = top.group(1)
            value = top.group(2).strip().strip("\"'")
            if key == "kind":
                kind = value or None
                in_metadata = False
            elif key == "metadata":
                in_metadata = True
                metadata_indent = None
                metadata_line_idx = idx
            else:
                in_metadata = False
            continue

        if in_metadata:
            if re.match(r"^[A-Za-z]", raw_line):
                in_metadata = False
                continue
            m = re.match(r"^(\s+)(\S.*?)\s*:\s*(.*)$", raw_line)
            if not m:
                continue
            indent, key, value = m.groups()
            indent_len = len(indent.replace("\t", "  "))
            if metadata_indent is None:
                metadata_indent = indent_len
            if indent_len != metadata_indent:
                continue
            value = value.strip().strip("\"'")
            if key == "name":
                name = value or None
                name_line_idx = idx
            elif key == "namespace" and value:
                has_namespace = True

    if has_namespace or (kind, name) not in target_kinds_names:
        return doc
    if metadata_line_idx is None:
        return doc

    indent = "  "
    if metadata_indent:
        indent = " " * metadata_indent
    ns_line = "{}namespace: {}\n".format(indent, namespace)
    insert_at = (name_line_idx + 1) if name_line_idx is not None else (metadata_line_idx + 1)
    lines.insert(insert_at, ns_line)
    return "".join(lines)


class _KubetoolsFlow(object):
    """Shared apply / dry-run / diff workflow."""

    MODE_APPLY = "apply"
    MODE_DRY_RUN = "dry-run"
    MODE_COMPARE = "compare"

    def __init__(self, window, view, mode):
        self.window = window
        self.view = view
        self.mode = mode
        self.settings = _settings()
        self.manifest_text = ""
        self.manifest_path = None
        self.temp_path = None
        self.resources = []
        self.context = None
        self.current_context = None
        self.contexts = []
        self.namespaces = []
        self.pending_ns_resources = []
        self.chosen_namespace = None
        self.exists_summary = []
        self.diff_text = ""
        self.dry_run_text = ""
        self.missing_namespaces = []
        self.created_namespaces = []
        self.file_path = view.file_name() if view is not None else None

    def _is_read_only_mode(self):
        """Compare / Dry-Run must never mutate cluster or user files."""
        return self.mode in (
            self.MODE_COMPARE,
            self.MODE_DRY_RUN,
        )

    def _user_file_path(self):
        return self.file_path

    def start(self):
        if self.view is None:
            _ui_notice(self.window, "No active view", "Open a Kubernetes YAML buffer first")
            return

        if not _try_begin_flow():
            _ui_notice(
                self.window,
                "Another Kubetools operation is in progress",
                "Finish or cancel it (Esc) before starting a new one",
            )
            return

        if self.view.size() == 0:
            _end_flow()
            _ui_notice(self.window, "Current file is empty", "Nothing to send to kubectl")
            return

        if self.mode == self.MODE_APPLY and not _view_is_slice(self.view):
            _end_flow()
            _ui_notice(
                self.window,
                "Apply is only allowed on a kubetools create slice",
                "Use .kubetools - Create… then Cmd/Ctrl+Shift+Enter",
            )
            return

        self.settings = _settings()
        path_err = _validate_kubectl_path(self.settings["kubectl_path"])
        if path_err:
            _end_flow()
            _ui_notice(self.window, path_err, "Fix kubectl_path in Kubetools settings")
            return

        self.manifest_text = self.view.substr(sublime.Region(0, self.view.size()))
        max_bytes = self.settings.get("max_manifest_bytes", _MAX_MANIFEST_BYTES)
        if len(self.manifest_text.encode("utf-8")) > max_bytes:
            _end_flow()
            _ui_notice(
                self.window,
                "Manifest too large (max {} bytes)".format(max_bytes),
                "Split the file or raise max_manifest_bytes only if intentional",
            )
            return

        self.resources = _parse_resources(self.manifest_text)
        if not self.resources:
            _end_flow()
            _ui_notice(
                self.window,
                "No Kubernetes resources found",
                "Need apiVersion / kind / metadata.name",
            )
            return

        max_res = self.settings.get("max_resources", _MAX_RESOURCES)
        if len(self.resources) > max_res:
            _end_flow()
            _ui_notice(
                self.window,
                "Too many resources ({} > max {})".format(len(self.resources), max_res),
                "Apply smaller documents, or raise max_resources only if intentional",
            )
            return

        missing = []
        for res in self.resources:
            if not res.get("kind"):
                missing.append("document #{}: missing kind".format(res["index"] + 1))
            if not res.get("name"):
                missing.append(
                    "document #{} ({}): missing metadata.name".format(
                        res["index"] + 1, res.get("kind") or "?"
                    )
                )
            if not res.get("apiVersion"):
                missing.append(
                    "document #{} ({}): missing apiVersion".format(
                        res["index"] + 1, res.get("kind") or "?"
                    )
                )
        if missing:
            _end_flow()
            _ui_notice(
                self.window,
                "Invalid manifest",
                " · ".join(missing[:4]),
            )
            return

        src_block = _ktc.mutating_source_block_reason(
            self.file_path, self.manifest_text
        )
        if src_block and self.mode in (self.MODE_APPLY, self.MODE_DRY_RUN):
            _end_flow()
            _ui_notice(self.window, "Refused", src_block)
            return

        if self.mode == self.MODE_APPLY and _ktc.example_namespace_unchanged(
            self.manifest_text
        ):
            _end_flow()
            _ui_notice(
                self.window,
                "Example namespace not changed",
                "Set metadata.namespace away from CHANGE-ME before Apply",
            )
            return

        if self.mode == self.MODE_APPLY:
            leftover = [
                res.get("name")
                for res in self.resources
                if _ktc.example_resource_name_unchanged(res.get("name"))
            ]
            if leftover:
                _end_flow()
                _ui_notice(
                    self.window,
                    "Example name not changed",
                    "Rename metadata.name away from example / example-secrets",
                )
                return
            protected = [
                res.get("namespace")
                for res in self.resources
                if _ktc.is_protected_namespace(res.get("namespace"))
            ]
            if protected:
                _end_flow()
                _ui_notice(
                    self.window,
                    "Protected namespace",
                    "Will not apply to kube-system / kube-public / kube-node-lease",
                )
                return

        if self.settings["warn_unsaved_buffer"] and self.view.is_dirty():
            # Never use ok_cancel_dialog here: dismissing a native sheet and then
            # opening a quick_panel hard-exits ST4 on macOS.
            if self._is_read_only_mode():
                yes = "Continue (read-only: unsaved buffer)"
                detail = (
                    "Buffer has unsaved changes — Kubetools compares/diffs the "
                    "buffer contents only. The on-disk file will NOT be modified."
                )
            else:
                yes = "Continue (apply unsaved buffer)"
                detail = (
                    "Buffer has unsaved changes — Kubetools uses buffer contents, "
                    "not the on-disk file."
                )
            self._confirm_quick(
                yes,
                detail,
                self._prepare_manifest_and_load_contexts,
            )
            return

        self._prepare_manifest_and_load_contexts()

    def _prepare_manifest_and_load_contexts(self):
        # Always use a frozen buffer snapshot via temp file. Never pass the
        # on-disk path: it can diverge from the buffer or change mid-flow.
        # Read-only modes: temp only under tempfile; never the user's file.
        try:
            self.temp_path = _write_temp_manifest(
                self.manifest_text, forbid_path=self._user_file_path()
            )
            self.manifest_path = self.temp_path
            if _paths_are_same_file(self.temp_path, self._user_file_path()):
                raise RuntimeError(
                    "internal error: temp path resolved to the open file"
                )
        except Exception as exc:
            self.cleanup()
            _ui_notice(
                self.window,
                "Failed to write temp manifest",
                str(exc),
            )
            return

        sublime.status_message("Kubetools: loading contexts...")
        _spawn(self._load_contexts_async)

    def _confirm_quick(
        self, yes_caption, detail, on_yes, on_no=None, cancel_first=False
    ):
        """
        Yes/No via quick_panel (safe on macOS ST4).

        cancel_first=True puts Cancel as index 0 (safer for dangerous apply —
        accidental Enter aborts).
        """
        yes = [yes_caption, detail]
        cancel = ["Cancel", "Esc / Enter here aborts — nothing applied"]
        if cancel_first:
            items = [cancel, yes]
            yes_index = 1
        else:
            items = [yes, cancel]
            yes_index = 0

        def picked(index):
            if index == yes_index:
                on_yes()
            else:
                if on_no is not None:
                    on_no()
                else:
                    self._on_cancel()

        sublime.set_timeout(
            lambda: self.window.show_quick_panel(items, picked),
            10,
        )

    def _flow_error(self, title, detail=""):
        """Non-modal error; cleans up the flow."""
        self.cleanup()
        _ui_notice(self.window, title, detail)

    def cleanup(self):
        if self.temp_path and os.path.exists(self.temp_path):
            try:
                os.remove(self.temp_path)
            except Exception:
                pass
            self.temp_path = None
        _end_flow()


    def _kubectl(self, args, stdin_data=None):
        # Absolute gate: compare/diff/dry-run cannot issue mutating kubectl.
        _assert_kubectl_allowed_for_mode(self.mode, args)
        return _run_kubectl(args, self.settings, stdin_data=stdin_data)

    def _with_context(self, args):
        out = list(args)
        # Hard requirement: every kubectl call targets the chosen context.
        if not self.context:
            raise RuntimeError("Kubetools internal error: context not selected")
        out.extend(["--context", self.context])
        return out

    def _load_contexts_async(self):
        code, out, err = self._kubectl(["config", "get-contexts", "-o", "name"])
        code_cur, out_cur, _ = self._kubectl(["config", "current-context"])
        current = out_cur.strip() if code_cur == 0 else None

        def done():
            if code != 0:
                self.cleanup()
                _ui_notice(
                    self.window,
                    "Failed to list kubectl contexts",
                    (err or out or "")[:180],
                )
                return
            contexts = [line.strip() for line in out.splitlines() if line.strip()]
            if not contexts:
                self.cleanup()
                _ui_notice(self.window, "No kubectl contexts found", "Check kubeconfig")
                return
            self.contexts = contexts
            self.current_context = current
            self._show_context_picker()

        sublime.set_timeout(done, 0)

    def _show_context_picker(self):
        """
        Always show the context quick panel (never auto-select / skip).

        When guess_context_from_path is on, path-matched contexts are sorted
        to the top and pre-highlighted. User must still confirm a choice.
        Dangerous contexts still require type-to-confirm after pick.
        """
        file_path = self.file_path

        contexts = list(self.contexts)
        selected = 0
        reasons = {}

        if self.settings.get("guess_context_from_path", True) and file_path:
            contexts, selected, reasons = _ktc.rank_contexts_for_path(
                contexts,
                file_path,
                aliases=self.settings.get("context_path_aliases") or {},
                current_context=self.current_context,
            )
            self.contexts = contexts  # keep index mapping aligned with panel

        items = []
        for ctx in contexts:
            mark = " (current)" if ctx == self.current_context else ""
            danger = ""
            if _is_dangerous_context(ctx, self.settings["dangerous_context_patterns"]):
                danger = " ⚠ PROD?"
            reason = reasons.get(ctx) or ""
            if reason:
                detail = "path match: {} · Enter=use · Esc=cancel{}".format(
                    reason, danger
                )
            else:
                detail = "kubectl context · Enter=use · Esc=cancel{}".format(danger)
            items.append([ctx + mark, detail])

        # Hard safety: panel is mandatory — no silent context assignment here.
        sublime.status_message(
            "Kubetools: choose context — Enter selects · Esc cancels"
            + (" · path hint highlighted" if reasons else "")
        )
        self.window.show_quick_panel(
            items,
            self._on_context_chosen,
            0,
            selected if 0 <= selected < len(items) else 0,
        )

    def _on_context_chosen(self, index):
        # Context is ONLY set from an explicit quick-panel choice (index >= 0).
        # Path matching never assigns self.context by itself.
        if index < 0:
            self.cleanup()
            sublime.status_message("Kubetools: cancelled")
            return
        self.context = self.contexts[index]

        if self.settings.get("guess_context_from_path", True) and self.file_path:
            guessed, _sel, reasons = _ktc.rank_contexts_for_path(
                list(self.contexts),
                self.file_path,
                aliases=self.settings.get("context_path_aliases") or {},
                current_context=self.current_context,
            )
            best = guessed[0] if guessed else None
            if reasons and best and best != self.context:
                self._confirm_quick(
                    "Use '{}' (path suggested '{}')".format(self.context, best),
                    "Chosen context does not match the file path. Enter confirms · Esc cancels",
                    lambda: self._continue_after_context_pick(),
                    cancel_first=True,
                )
                return

        self._continue_after_context_pick()

    def _continue_after_context_pick(self):
        if _is_dangerous_context(
            self.context, self.settings["dangerous_context_patterns"]
        ):
            if self.settings["dangerous_context_require_type_name"]:
                self.window.show_input_panel(
                    "Dangerous context. Type the context name to continue:",
                    "",
                    self._on_danger_context_typed,
                    None,
                    self._on_cancel,
                )
                return
            self._confirm_quick(
                "Continue with '{}'".format(self.context),
                "Context name looks like production. Enter confirms · Esc cancels",
                self._after_context_ready,
                cancel_first=True,
            )
            return

        self._after_context_ready()

    def _on_danger_context_typed(self, typed):
        if (typed or "").strip() != self.context:
            self.cleanup()
            _ui_notice(
                self.window,
                "Context name did not match — aborted",
                "Nothing was applied",
            )
            return
        self._after_context_ready()

    def _on_cancel(self):
        self.cleanup()
        sublime.status_message("Kubetools: cancelled")

    def _after_context_ready(self):
        # Collect namespaced resources missing metadata.namespace
        self.pending_ns_resources = [
            r
            for r in self.resources
            if not r.get("cluster_scoped") and not r.get("namespace")
        ]
        if not self.pending_ns_resources:
            self._ensure_target_namespaces()
            return

        sublime.status_message("Kubetools: loading namespaces...")
        _spawn(self._load_namespaces_async)

    def _load_namespaces_async(self):
        code, out, err = self._kubectl(
            self._with_context(["get", "namespaces", "-o", "jsonpath={.items[*].metadata.name}"])
        )

        def done():
            if code == 0 and out.strip():
                self.namespaces = sorted(out.strip().split())
            else:
                self.namespaces = []
            self._prompt_namespace()

        sublime.set_timeout(done, 0)

    def _prompt_namespace(self):
        kinds = sorted(
            {
                (r.get("kind") or "?")
                for r in self.pending_ns_resources
            }
        )
        prompt_note = (
            "Namespace required for: {}\n"
            "(not set in metadata.namespace)"
        ).format(", ".join(kinds))

        if (
            self.settings["namespace_picker"] == "quick_panel"
            and self.namespaces
        ):
            items = [["» Type a namespace…", prompt_note]]
            default = self.settings["default_namespace"]
            # Put default first when present
            ordered = list(self.namespaces)
            if default in ordered:
                ordered.remove(default)
                ordered.insert(0, default)
            for ns in ordered:
                items.append([ns, prompt_note])
            self.window.show_quick_panel(items, self._on_namespace_picked)
            return

        self.window.show_input_panel(
            "Namespace (missing in manifest):",
            self.settings["default_namespace"],
            self._on_namespace_typed,
            None,
            self._on_cancel,
        )

    def _on_namespace_picked(self, index):
        if index < 0:
            self._on_cancel()
            return
        if index == 0:
            self.window.show_input_panel(
                "Namespace (missing in manifest):",
                self.settings["default_namespace"],
                self._on_namespace_typed,
                None,
                self._on_cancel,
            )
            return
        # index 0 is "Type…", so namespaces start at 1
        default = self.settings["default_namespace"]
        ordered = list(self.namespaces)
        if default in ordered:
            ordered.remove(default)
            ordered.insert(0, default)
        self.chosen_namespace = ordered[index - 1]
        self._apply_chosen_namespace()

    def _on_namespace_typed(self, value):
        ns = (value or "").strip()
        if not ns:
            self.cleanup()
            _ui_notice(self.window, "Namespace cannot be empty", "Enter a valid namespace")
            return
        if not re.match(r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$", ns):
            self.cleanup()
            _ui_notice(
                self.window,
                "Invalid namespace DNS label: {}".format(ns),
                "Lowercase alphanumeric and '-' only",
            )
            return
        self.chosen_namespace = ns
        self._apply_chosen_namespace()

    def _apply_chosen_namespace(self):
        if _ktc.is_protected_namespace(self.chosen_namespace):
            self.cleanup()
            _ui_notice(
                self.window,
                "Protected namespace",
                "Will not apply to kube-system / kube-public / kube-node-lease",
            )
            return
        targets = set()
        for res in self.pending_ns_resources:
            res["namespace"] = self.chosen_namespace
            res["namespace_from_prompt"] = True
            targets.add((res.get("kind"), res.get("name")))

        # Rewrite a temp manifest with namespace injected (do not use kubectl -n
        # on multi-doc files — it can override namespaces already in YAML).
        try:
            patched = _inject_namespace_into_manifest(
                self.manifest_text, self.chosen_namespace, targets
            )
            if self.temp_path and os.path.exists(self.temp_path):
                os.remove(self.temp_path)
            self.temp_path = _write_temp_manifest(
                patched, forbid_path=self._user_file_path()
            )
            self.manifest_path = self.temp_path
            self.manifest_text = patched
            if _paths_are_same_file(self.temp_path, self._user_file_path()):
                raise RuntimeError(
                    "internal error: temp path resolved to the open file"
                )
        except Exception as exc:
            self.cleanup()
            _ui_notice(
                self.window,
                "Failed to inject namespace into manifest",
                str(exc),
            )
            return

        self._ensure_target_namespaces()

    def _required_namespaces(self):
        names = set()
        for res in self.resources:
            if res.get("cluster_scoped"):
                continue
            ns = res.get("namespace") or self.chosen_namespace
            if ns:
                names.add(ns)
        return sorted(names)

    def _ensure_target_namespaces(self):
        """Verify namespaces exist; prompt to create any that are missing."""
        required = self._required_namespaces()
        if not required:
            self._begin_safety_checks()
            return
        sublime.status_message("Kubetools: checking namespaces...")
        _spawn(self._check_namespaces_async, *(required,))

    def _check_namespaces_async(self, required):
        missing = []
        errors = []
        for ns in required:
            code, out, err = self._kubectl(
                self._with_context(["get", "namespace", ns, "-o", "name"])
            )
            if code == 0:
                continue
            msg = (err or out or "").strip()
            if _is_resource_not_found(msg) or _is_missing_namespace_error(msg):
                missing.append(ns)
            else:
                errors.append("namespace {}: {}".format(ns, msg or "unknown error"))

        def done():
            if errors:
                self._abort_with_report(
                    errors,
                    "Kubetools: could not verify namespace(s) — see report tab",
                )
                return
            self.missing_namespaces = list(missing)
            self._prompt_next_missing_namespace()

        sublime.set_timeout(done, 0)

    def _prompt_next_missing_namespace(self):
        if not self.missing_namespaces:
            self._begin_safety_checks()
            return

        ns = self.missing_namespaces[0]

        # Dry-Run / Diff must never mutate the cluster (including namespaces).
        if self.mode != self.MODE_APPLY:
            self._abort_with_report(
                [
                    "Namespace '{}' does not exist on context '{}'.".format(
                        ns, self.context
                    ),
                    "Mode '{}' is read-only and will not create namespaces.".format(
                        self.mode
                    ),
                    "Create the namespace first, or run Kubetools: Apply Open File.",
                ],
                "Kubetools: missing namespace — {} will not create it".format(
                    self.mode
                ),
            )
            return

        def on_no():
            self.cleanup()
            sublime.status_message(
                "Kubetools: cancelled — namespace '{}' missing".format(ns)
            )

        def on_yes():
            sublime.status_message("Kubetools: creating namespace {}...".format(ns))
            _spawn(self._create_namespace_async, *(ns,))

        self._confirm_quick(
            "Create namespace '{}'".format(ns),
            "Does not exist on context '{}'. Create it, then continue apply.".format(
                self.context
            ),
            on_yes,
            on_no=on_no,
        )

    def _create_namespace_async(self, ns):
        # Hard lock: never create namespaces outside Apply mode.
        if self.mode != self.MODE_APPLY:
            self._abort_with_report(
                [
                    "REFUSED: create namespace blocked in mode {!r}.".format(self.mode),
                    "Compare / Diff / Dry-Run never mutate the cluster.",
                ],
                "Kubetools: refused namespace create in read-only mode",
            )
            return
        code, out, err = self._kubectl(
            self._with_context(["create", "namespace", ns])
        )

        def done():
            msg = (err or out or "").strip()
            if code != 0 and "already exists" not in msg.lower():
                self._abort_with_report(
                    ["Failed to create namespace {}: {}".format(ns, msg)],
                    "Kubetools: failed to create namespace — see report tab",
                )
                return
            if self.missing_namespaces and self.missing_namespaces[0] == ns:
                self.missing_namespaces.pop(0)
            if ns not in self.created_namespaces:
                self.created_namespaces.append(ns)
            if ns not in self.namespaces:
                self.namespaces.append(ns)
            sublime.status_message("Kubetools: namespace {} ready".format(ns))
            self._prompt_next_missing_namespace()

        sublime.set_timeout(done, 0)

    def _abort_with_report(self, errors, status_msg):
        """
        Show a report tab and abort.

        IMPORTANT: never call sublime.error_message / message_dialog / ok_cancel_dialog
        around window.new_file() — native sheets hard-exit ST4 on macOS.
        """
        self._show_output_tab(
            "Kubetools Blocked @ {}".format(self.context),
            self._format_report(errors),
        )
        self.cleanup()
        sublime.status_message(status_msg)

    def _begin_safety_checks(self):
        # Diff Open File is an alias of Compare State (inline live values).
        if self.mode == self.MODE_COMPARE:
            sublime.status_message("Kubetools: comparing to live cluster...")
            _spawn(self._compare_async)
            return
        sublime.status_message("Kubetools: checking live cluster state...")
        _spawn(self._safety_checks_async)

    def _compare_async(self):
        """
        Compare each local resource to live cluster state.
        Matching values omitted; diffs drawn inline on the manifest
        (right-edge annotations, GitLens-style). Safety issues collected
        for the banner / optional scratch tab.
        """
        doc_spans = _split_yaml_docs_with_spans(self.manifest_text)
        overlay_items = []
        safety_lines = []
        total_diffs = 0
        total_issues = 0
        unmapped = 0
        matched_resources = 0
        missing_on_cluster = 0

        for res in self.resources:
            label = _resource_label(res, self.chosen_namespace)
            kind = res.get("kind") or "?"
            name = res.get("name") or "?"
            ns = None if res.get("cluster_scoped") else (
                res.get("namespace") or self.chosen_namespace
            )

            idx = res.get("index")
            if not (isinstance(idx, int) and 0 <= idx < len(doc_spans)):
                total_issues += 1
                safety_lines.append(
                    "{}: internal error — could not map resource to a YAML "
                    "document in the buffer (plugin bug or empty doc)".format(label)
                )
                continue

            doc_start, _doc_end, doc_text = doc_spans[idx]
            path_index = _yaml_path_value_regions(doc_text)

            local_obj, local_err = _manifest_doc_to_json(
                self.settings,
                self.context,
                doc_text,
                forbid_path=self._user_file_path(),
            )
            if local_err or local_obj is None:
                total_issues += 1
                safety_lines.append(
                    "{}: cannot parse local manifest via kubectl: {}".format(
                        label, local_err or "unknown error"
                    )
                )
                continue

            get_args = ["get", kind, name, "-o", "json"]
            if ns:
                get_args.extend(["-n", ns])
            code, out, err = self._kubectl(self._with_context(get_args))
            if code != 0:
                msg = (err or out or "").strip()
                total_issues += 1
                if _is_resource_not_found(msg):
                    missing_on_cluster += 1
                    safety_lines.append(
                        "{}: does not exist on cluster (nothing to compare)".format(
                            label
                        )
                    )
                else:
                    safety_lines.append(
                        "{}: kubectl get failed: {}".format(label, msg or "unknown")
                    )
                continue

            try:
                live_obj = json.loads(out)
            except Exception as exc:
                total_issues += 1
                safety_lines.append(
                    "{}: cannot parse live JSON: {}".format(label, exc)
                )
                continue

            diffs, issues = _diff_local_vs_live(local_obj, live_obj, kind)
            for issue in issues:
                total_issues += 1
                safety_lines.append("{}: {}".format(label, issue))

            if not diffs and not issues:
                matched_resources += 1
                continue

            for path, _local_val, live_val in diffs:
                total_diffs += 1
                rel = path_index.get(path)
                if not rel:
                    unmapped += 1
                    safety_lines.append(
                        "{}: differ at {} but could not map to a buffer line "
                        "(manifest={} live={})".format(
                            label,
                            path,
                            _fmt_compare_value(_local_val),
                            _fmt_compare_value(live_val),
                        )
                    )
                    continue
                a, b = rel
                overlay_items.append(
                    {
                        "a": doc_start + a,
                        "b": doc_start + b,
                        "live_text": _fmt_compare_value(live_val),
                        "path": path,
                    }
                )

        issues_text = ""
        if safety_lines:
            issues_text = (
                "Kubetools compare — safety / unmapped @ {}\n\n".format(self.context)
                + "\n".join(safety_lines)
                + "\n"
            )

        banner = _build_compare_banner(
            self.context,
            len(overlay_items),
            len(safety_lines),
            unmapped,
            n_missing=missing_on_cluster,
        )

        def done():
            view = self.view
            if view is None or not view.is_valid():
                self.cleanup()
                sublime.status_message("Kubetools: compare aborted (view closed)")
                return

            view.settings().set("kubetools_compare_issues", issues_text)

            if total_diffs == 0 and total_issues == 0:
                _clear_compare_overlays(view)
                # tiny success banner
                try:
                    view.add_phantom(
                        _COMPARE_PHANTOM_KEY,
                        sublime.Region(0),
                        (
                            "<body style='margin:6px 10px;font-size:0.9rem'>"
                            "<span style='color:#98c379'>Kubetools compare</span>"
                            " <span style='color:#5c6370'>@ {} — all values match live"
                            "</span> · <a href='clear'>clear</a></body>"
                        ).format(_h_mini(self.context)),
                        sublime.LAYOUT_BLOCK,
                        on_navigate=lambda href: _on_compare_navigate(view, href),
                    )
                except Exception:
                    pass
                sublime.status_message(
                    "Kubetools: compare OK — all values match live "
                    "({} resource(s))".format(matched_resources or len(self.resources))
                )
            else:
                _apply_compare_overlays(view, overlay_items, banner, self.context)
                # Keep caret / scroll where the user left them (no jump to first diff)
                if missing_on_cluster and not overlay_items:
                    if missing_on_cluster == 1:
                        status = (
                            "Kubetools: resource does not exist on cluster"
                        )
                    else:
                        status = (
                            "Kubetools: {} resources do not exist on cluster".format(
                                missing_on_cluster
                            )
                        )
                    sublime.status_message(status)
                else:
                    sublime.status_message(
                        "Kubetools: {} diff(s) — next/prev: ctrl+k,ctrl+n/p · "
                        "copy: ctrl+k,ctrl+y · clear: ctrl+k,ctrl+x".format(
                            len(overlay_items)
                        )
                    )

            win = view.window()
            if win:
                win.focus_view(view)
            self.cleanup()

        sublime.set_timeout(done, 0)

    def _safety_checks_async(self):
        exists_summary = []
        errors = []

        for res in self.resources:
            kind = res["kind"]
            name = res["name"]
            args = ["get", kind, name, "-o", "name"]
            if not res.get("cluster_scoped"):
                ns = res.get("namespace") or self.chosen_namespace
                if ns:
                    args.extend(["-n", ns])
            code, out, err = self._kubectl(self._with_context(args))
            if code == 0:
                exists_summary.append(
                    {
                        "resource": res,
                        "exists": True,
                        "label": _resource_label(res, self.chosen_namespace),
                    }
                )
            else:
                msg = (err or out or "").strip()
                if _is_missing_namespace_error(msg):
                    # Should have been handled earlier; treat as hard error
                    exists_summary.append(
                        {
                            "resource": res,
                            "exists": None,
                            "label": _resource_label(res, self.chosen_namespace),
                            "error": msg,
                        }
                    )
                    errors.append(
                        "{}: {}".format(
                            _resource_label(res, self.chosen_namespace),
                            msg or "namespace missing",
                        )
                    )
                elif _is_resource_not_found(msg):
                    exists_summary.append(
                        {
                            "resource": res,
                            "exists": False,
                            "label": _resource_label(res, self.chosen_namespace),
                        }
                    )
                else:
                    # Could be unknown kind / auth — record and continue cautiously
                    exists_summary.append(
                        {
                            "resource": res,
                            "exists": None,
                            "label": _resource_label(res, self.chosen_namespace),
                            "error": msg,
                        }
                    )
                    errors.append(
                        "{}: {}".format(
                            _resource_label(res, self.chosen_namespace),
                            msg or "unknown error",
                        )
                    )

        diff_text = ""
        dry_run_text = ""

        if self.mode == self.MODE_APPLY and self.settings[
            "show_diff_before_apply"
        ]:
            diff_args = ["diff", "-f", self.manifest_path]
            dcode, dout, derr = self._kubectl(self._with_context(diff_args))
            # kubectl diff: 0 = no diff, 1 = differences, >1 = error
            if dcode in (0, 1):
                diff_text = dout or "(no differences — live object matches manifest)"
            else:
                diff_text = "kubectl diff failed (rc={}):\n{}".format(
                    dcode, derr or dout
                )

        if self.mode == self.MODE_APPLY and self.settings["dry_run_before_apply"]:
            dry_args = self._build_apply_args(dry_run=True)
            drcode, drout, drerr = self._kubectl(self._with_context(dry_args))
            if drcode == 0:
                dry_run_text = drout.strip() or "(dry-run ok)"
            else:
                dry_run_text = ""
                errors.append("dry-run failed:\n{}".format(drerr or drout))

        if self.mode == self.MODE_DRY_RUN:
            dry_args = self._build_apply_args(dry_run=True)
            drcode, drout, drerr = self._kubectl(self._with_context(dry_args))
            dry_run_text = drout if drcode == 0 else (drerr or drout)
            if drcode != 0:
                errors.append("dry-run failed (rc={})".format(drcode))

        self.exists_summary = exists_summary
        self.diff_text = diff_text
        self.dry_run_text = dry_run_text

        def done():
            if self.mode == self.MODE_DRY_RUN:
                self._show_output_tab(
                    "Kubetools Dry-Run @ {}".format(self.context),
                    self._format_report(errors),
                )
                self.cleanup()
                return

            # APPLY mode — any pre-check / dry-run error blocks apply.
            if errors:
                self._abort_with_report(
                    errors,
                    "Kubetools: dry-run / pre-checks failed — see report tab",
                )
                return

            self._confirm_and_apply()

        sublime.set_timeout(done, 0)

    def _build_apply_args(self, dry_run=False):
        # Namespace is written into the temp manifest when prompted — do not
        # pass -n here (avoids overriding other docs' namespaces).
        args = ["apply", "-f", self.manifest_path]
        if self.settings["validate"]:
            args.append("--validate=true")
        if dry_run:
            args.append("--dry-run=server")
        if self.settings["server_side_apply"]:
            args.append("--server-side")
            args.extend(["--field-manager", self.settings["field_manager"]])
        # Hard safety: never pass --force / --force-conflicts from this plugin
        return args

    def _format_report(self, errors):
        lines = []
        lines.append("Context: {}".format(self.context))
        if self.chosen_namespace:
            lines.append(
                "Namespace override (prompted): {}".format(self.chosen_namespace)
            )
        if self.created_namespaces:
            lines.append(
                "Namespaces created: {}".format(", ".join(self.created_namespaces))
            )
        lines.append("Mode: {}".format(self.mode))
        lines.append("")
        lines.append("Resources:")
        for item in self.exists_summary:
            state = (
                "EXISTS (will UPDATE / overwrite fields)"
                if item["exists"] is True
                else (
                    "NEW (will CREATE)"
                    if item["exists"] is False
                    else "UNKNOWN ({})".format(item.get("error") or "check failed")
                )
            )
            lines.append("  - {}  →  {}".format(item["label"], state))
        if self.dry_run_text:
            lines.append("")
            lines.append("=== dry-run=server ===")
            lines.append(self.dry_run_text)
        if self.diff_text:
            lines.append("")
            lines.append("=== kubectl diff ===")
            lines.append(self.diff_text)
        if errors:
            lines.append("")
            lines.append("=== errors ===")
            for err in errors:
                lines.append(err)
        return "\n".join(lines) + "\n"

    def _format_diff_review_content(self, errors=None):
        """
        Build a tab body that Diff.sublime-syntax can colorize.

        Keep metadata as plain lines (no leading +/-), then emit the raw
        kubectl unified diff so + is green and - is red.
        """
        lines = []
        lines.append("Kubetools — review kubectl diff before apply")
        lines.append("Context: {}".format(self.context))
        if self.chosen_namespace:
            lines.append(
                "Namespace override (prompted): {}".format(self.chosen_namespace)
            )
        if self.created_namespaces:
            lines.append(
                "Namespaces created: {}".format(", ".join(self.created_namespaces))
            )
        lines.append("Mode: {}".format(self.mode))
        lines.append("")
        lines.append("Resources:")
        for item in self.exists_summary:
            if item["exists"] is True:
                mark = "~"
                state = "EXISTS (will UPDATE)"
            elif item["exists"] is False:
                mark = "*"
                state = "NEW (will CREATE)"
            else:
                mark = "?"
                state = "UNKNOWN ({})".format(item.get("error") or "check failed")
            # Avoid a leading "-" so Diff syntax does not paint summary red.
            lines.append("  {} {}  →  {}".format(mark, item["label"], state))

        if self.dry_run_text:
            lines.append("")
            lines.append("dry-run=server:")
            for dry_line in (self.dry_run_text or "").splitlines():
                # Prefix so Diff does not treat dry-run names as deletions.
                if dry_line.startswith("+") or dry_line.startswith("-"):
                    lines.append("  {}".format(dry_line))
                else:
                    lines.append(dry_line)

        if errors:
            lines.append("")
            lines.append("errors:")
            for err in errors:
                lines.append("  {}".format(err))

        lines.append("")
        lines.append("=" * 72)
        lines.append("kubectl diff (unified) — + added / - removed")
        lines.append("=" * 72)
        lines.append("")
        if self.diff_text:
            lines.append(self.diff_text.rstrip("\n"))
        else:
            lines.append("(no diff output)")
        lines.append("")
        return "\n".join(lines)

    def _show_output_tab(self, title, content, focus=True, syntax=None):
        # Always use a scratch tab (native dialogs hard-exit ST4 on macOS).
        new_view = self.window.new_file()
        new_view.set_name(title)
        new_view.set_scratch(True)
        new_view.set_read_only(False)
        # Insert command sets read_only when done (avoid racing set_read_only).
        new_view.run_command("kubetools_insert_content", {"content": content})
        if syntax:
            self._assign_syntax(new_view, syntax)
        if focus:
            self.window.focus_view(new_view)
        return new_view

    def _assign_syntax(self, view, syntax):
        """Prefer ST4 assign_syntax; fall back to set_syntax_file."""
        try:
            view.assign_syntax(syntax)
        except Exception:
            try:
                view.set_syntax_file(syntax)
            except Exception:
                pass

    def _confirm_and_apply(self):
        updates = [i for i in self.exists_summary if i["exists"] is True]
        creates = [i for i in self.exists_summary if i["exists"] is False]
        unknowns = [i for i in self.exists_summary if i["exists"] is None]

        if unknowns and self.settings.get("block_on_unverified_resources", True):
            msgs = [
                "Refusing to apply: could not verify {} resource(s).".format(
                    len(unknowns)
                ),
                "Fix kubectl access / kind names, then retry.",
                "",
            ]
            for i in unknowns[:12]:
                msgs.append(
                    "  - {}: {}".format(
                        i["label"], i.get("error") or "existence check failed"
                    )
                )
            self._abort_with_report(
                msgs,
                "Kubetools: blocked — unverified resources (see report tab)",
            )
            return

        if not updates and not creates:
            self._abort_with_report(
                ["No creatable/updatable resources after pre-checks."],
                "Kubetools: nothing to apply",
            )
            return

        # Show kubectl diff in a tab BEFORE any apply confirmation so the user
        # can review changes prior to overwriting existing kinds.
        show_diff = self.settings["show_diff_before_apply"] and bool(self.diff_text)
        must_review_diff = show_diff and bool(updates)
        if show_diff:
            title = (
                "Kubetools DIFF — review before apply @ {}".format(self.context)
                if updates
                else "Kubetools Pre-Apply Report @ {}".format(self.context)
            )
            self._show_output_tab(
                title,
                self._format_diff_review_content([]),
                focus=True,
                syntax="Packages/Diff/Diff.sublime-syntax",
            )
            sublime.status_message(
                "Kubetools: review DIFF tab, then confirm in the quick panel"
            )

        summary_lines = [
            "Apply to context: {}".format(self.context),
        ]
        if self.created_namespaces:
            summary_lines.append(
                "Namespaces created: {}".format(", ".join(self.created_namespaces))
            )
        if self.chosen_namespace:
            summary_lines.append(
                "Namespace (prompted): {}".format(self.chosen_namespace)
            )
        if self.settings.get("warn_secret_resources", True):
            secrets = [
                r
                for r in self.resources
                if (r.get("kind") or "").lower() == "secret"
            ]
            if secrets:
                summary_lines.append(
                    "WARNING: {} Secret resource(s) — plaintext may appear in diff/report tabs".format(
                        len(secrets)
                    )
                )
        if creates:
            summary_lines.append("CREATE ({})".format(len(creates)))
            for i in creates[:6]:
                summary_lines.append("  + {}".format(i["label"]))
            if len(creates) > 6:
                summary_lines.append("  … {} more".format(len(creates) - 6))
        if updates:
            summary_lines.append("UPDATE existing ({})".format(len(updates)))
            for i in updates[:6]:
                summary_lines.append("  ~ {}".format(i["label"]))
            if len(updates) > 6:
                summary_lines.append("  … {} more".format(len(updates) - 6))
        if unknowns:
            summary_lines.append(
                "Could not verify existence ({})".format(len(unknowns))
            )
            for i in unknowns[:4]:
                summary_lines.append("  ? {}".format(i["label"]))
        if updates:
            summary_lines.append(
                "WARNING: existing live objects will be patched/overwritten."
            )
        if must_review_diff:
            summary_lines.append(
                "Diff is open in tab — review it before confirming."
            )

        detail = " | ".join(
            line for line in summary_lines if not line.startswith("  ")
        )
        # Keep quick_panel subtitle readable
        if len(detail) > 200:
            detail = detail[:197] + "…"

        if must_review_diff:
            ok_label = "Reviewed diff — apply UPDATE ({} resource(s))".format(
                len(updates)
            )
            if creates:
                ok_label = (
                    "Reviewed diff — apply ({} update / {} create)".format(
                        len(updates), len(creates)
                    )
                )
        elif updates and creates:
            ok_label = "Apply ({} update / {} create)".format(
                len(updates), len(creates)
            )
        elif updates:
            ok_label = "Update {} resource(s)".format(len(updates))
        elif creates:
            ok_label = "Create {} resource(s)".format(len(creates))
        else:
            ok_label = "Apply {} resource(s)".format(len(self.exists_summary))

        def after_first_confirm():
            scoped = [r for r in self.resources if r.get("cluster_scoped")]
            if scoped:
                self._confirm_quick(
                    "Apply {} cluster-scoped resource(s)".format(len(scoped)),
                    "CRD / ClusterRole / similar · Enter confirms · Esc cancels",
                    after_cluster_scoped,
                    cancel_first=True,
                )
                return
            after_cluster_scoped()

        def after_cluster_scoped():
            secrets = [
                r
                for r in self.resources
                if (r.get("kind") or "").lower() == "secret"
            ]
            if secrets and self.settings.get("require_secret_confirm", True):
                self._confirm_quick(
                    "Apply {} Secret resource(s) — confirm".format(len(secrets)),
                    "Plaintext may appear in diff/report · Enter confirms · Esc cancels",
                    lambda: self._after_secret_confirm(updates),
                    cancel_first=True,
                )
                return
            self._after_secret_confirm(updates)

        # Confirmation is mandatory (safety lock).
        delay_ms = 80 if show_diff else 10
        dangerous = _is_dangerous_context(
            self.context, self.settings["dangerous_context_patterns"]
        )
        # Cancel-first when overwriting or on prod-like contexts (Enter on #1 = abort)
        cancel_first = bool(updates) or dangerous or bool(
            any((r.get("kind") or "").lower() == "secret" for r in self.resources)
        )

        def show_confirm():
            self._confirm_quick(
                ok_label,
                detail + " · Enter confirms · Esc cancels",
                after_first_confirm,
                cancel_first=cancel_first,
            )

        sublime.set_timeout(show_confirm, delay_ms)

    def _after_secret_confirm(self, updates):
        if updates:
            names = ", ".join(i["label"] for i in updates[:6])
            if len(updates) > 6:
                names += ", …"
            must_review_diff = self.settings["show_diff_before_apply"] and bool(
                self.diff_text
            )
            dangerous = _is_dangerous_context(
                self.context, self.settings["dangerous_context_patterns"]
            )
            self._confirm_quick(
                "Overwrite on '{}' — final confirm".format(self.context),
                names
                + (
                    " | Diff tab shown — proceed only if OK"
                    if must_review_diff
                    else ""
                ),
                self._start_apply_after_confirm,
                cancel_first=True if (updates or dangerous) else False,
            )
            return
        self._start_apply_after_confirm()

    def _start_apply_after_confirm(self):
        if self.mode != self.MODE_APPLY:
            self._abort_with_report(
                [
                    "REFUSED: apply blocked in mode {!r}.".format(self.mode),
                    "Compare / Diff / Dry-Run never mutate the cluster or files.",
                ],
                "Kubetools: refused apply in read-only mode",
            )
            return
        sublime.status_message("Kubetools: applying...")
        _spawn(self._apply_async)

    def _apply_async(self):
        if self.mode != self.MODE_APPLY:
            def refuse():
                self._abort_with_report(
                    [
                        "REFUSED: apply blocked in mode {!r}.".format(self.mode),
                    ],
                    "Kubetools: refused apply in read-only mode",
                )

            sublime.set_timeout(refuse, 0)
            return
        args = self._build_apply_args(dry_run=False)
        # Explicit non-dry-run apply — only reachable in MODE_APPLY
        code, out, err = self._kubectl(self._with_context(args))

        def done():
            report = self._format_report([])
            report += "\n=== kubectl apply ===\n"
            report += (out or "") + (("\n" + err) if err else "")
            report += "\nexit code: {}\n".format(code)
            title = (
                "Kubetools Applied @ {}".format(self.context)
                if code == 0
                else "Kubetools Apply FAILED @ {}".format(self.context)
            )
            self._show_output_tab(title, report)
            if code == 0:
                if _view_is_slice(self.view):
                    stamp = datetime.now().astimezone().strftime(
                        "%Y-%m-%d %H:%M:%S %z"
                    )
                    self.view.run_command(
                        "kubetools_stamp_applied", {"stamp": stamp}
                    )
                sublime.status_message("Kubetools: apply succeeded")
            else:
                # Report tab already open — do not show error_message after new_file.
                sublime.status_message(
                    "Kubetools: apply failed (exit {}) — see report tab".format(code)
                )
            self.cleanup()

        sublime.set_timeout(done, 0)
