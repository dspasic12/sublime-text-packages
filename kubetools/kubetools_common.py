# Shared helpers for Kubetools (kubectl + seal). Pure / ST-light utilities.
# Sublime loads every top-level .py; this module defines no commands.

from __future__ import annotations

import fnmatch
import os
import re
import shlex
import subprocess
import sys
import tempfile
import threading

try:
    import sublime
except ImportError:  # unit tests without ST
    sublime = None  # type: ignore


_DEFAULT_DANGEROUS_PATTERNS = [
    "*prod*",
    "*production*",
    "*-prod-*",
    "prod-*",
    "*prd*",
    "*live*",
]


def clamp_timeout(value, default=60, minimum=5, maximum=600):
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    if n < minimum:
        return minimum
    if n > maximum:
        return maximum
    return n


def try_fchmod(fd, mode=0o600):
    """Best-effort fd mode. ``os.fchmod`` is missing on Windows."""
    fn = getattr(os, "fchmod", None)
    if not callable(fn):
        return
    try:
        fn(fd, mode)
    except OSError:
        pass


def try_chmod(path, mode=0o600):
    try:
        os.chmod(path, mode)
    except OSError:
        pass


def path_is_under(path, root):
    """True if ``path`` is ``root`` or a descendant (normcase — Windows-safe)."""
    try:
        real = os.path.normcase(os.path.realpath(path))
        base = os.path.normcase(os.path.realpath(root))
    except OSError:
        return False
    return real == base or real.startswith(base + os.sep)


def expand_path(path):
    """Expand ${home}/${packages}/~ and normalize."""
    if not path:
        return ""
    try:
        if sublime is not None:
            variables = {
                "home": os.path.expanduser("~"),
                "packages": sublime.packages_path(),
            }
            try:
                variables["installed_packages"] = sublime.installed_packages_path()
            except Exception:
                pass
            path = sublime.expand_variables(path, variables)
    except Exception:
        pass
    path = os.path.expanduser(path)
    path = os.path.expandvars(path)
    return os.path.normpath(path)


def validate_binary_path(path, label="binary"):
    """
    Return error string or None.
    Refuse shell metacharacters; require existing executable when absolute/relative.
    """
    if path is None or not isinstance(path, str):
        return "{} path is empty".format(label)
    path = path.strip()
    if not path:
        return "{} path is empty".format(label)
    if any(ch in path for ch in (";", "|", "&", "`", "$", "\n", "\r", "\0")):
        return "{} path contains forbidden characters".format(label)
    # Bare command name (e.g. kubectl / kubeseal) — resolve via PATH later
    if "/" not in path and "\\" not in path:
        return None
    if not os.path.isfile(path):
        return "{} not found: {}".format(label, path)
    # Windows has no Unix execute bit; os.access(X_OK) is not meaningful.
    if os.name != "nt" and not os.access(path, os.X_OK):
        return "{} is not executable: {}".format(label, path)
    return None


def find_binary(name, extra_dirs=None):
    """Return absolute path to ``name`` on PATH (+ extras) or None."""
    path_env = os.environ.get("PATH", "")
    extras = list(extra_dirs or []) + [
        os.path.expanduser("~/.local/bin"),
        "/opt/homebrew/bin",
        "/usr/local/bin",
        "/usr/bin",
        "/snap/bin",
    ]
    if os.name == "nt":
        extras.extend(
            [
                os.path.expanduser(r"~\scoop\shims"),
                os.path.expandvars(r"%ProgramData%\chocolatey\bin"),
            ]
        )
    search_dirs = path_env.split(os.pathsep) + extras
    seen = set()
    for d in search_dirs:
        if not d or d in seen:
            continue
        seen.add(d)
        candidate = os.path.join(d, name)
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
        candidate_exe = candidate + ".exe"
        if os.path.isfile(candidate_exe):
            return candidate_exe
    return None


def is_dangerous_name(name, patterns=None):
    """Case-insensitive glob match (same rules as Kubetools apply)."""
    if not name:
        return False
    pats = patterns if patterns is not None else _DEFAULT_DANGEROUS_PATTERNS
    lower = name.lower()
    for pat in pats or []:
        if fnmatch.fnmatch(lower, (pat or "").lower()):
            return True
    return False


def run_subprocess(cmd, timeout, stdin_data=None, label="process"):
    """
    Run argv list only (never shell). Returns (returncode, stdout, stderr).
    Kills the child on timeout.
    """
    if not isinstance(cmd, (list, tuple)) or not cmd:
        return -1, "", "{}: empty command".format(label)
    if any(not isinstance(x, str) for x in cmd):
        return -1, "", "{}: non-string argv".format(label)
    try:
        process = subprocess.Popen(
            list(cmd),
            stdin=subprocess.PIPE if stdin_data is not None else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
        )
    except OSError as exc:
        return -1, "", "{} failed to start: {}".format(label, exc)
    try:
        stdout, stderr = process.communicate(input=stdin_data, timeout=timeout)
        return process.returncode, stdout or "", stderr or ""
    except subprocess.TimeoutExpired:
        try:
            process.kill()
        except OSError:
            pass
        try:
            process.communicate(timeout=2)
        except Exception:
            pass
        return -1, "", "{} timed out after {}s".format(label, timeout)
    except Exception as exc:
        try:
            process.kill()
        except Exception:
            pass
        return -1, "", "{} failed: {}".format(label, exc)


def paths_are_same_file(a, b):
    if not a or not b:
        return False
    try:
        return os.path.samefile(a, b)
    except OSError:
        try:
            return os.path.normcase(os.path.realpath(a)) == os.path.normcase(
                os.path.realpath(b)
            )
        except OSError:
            return False


def write_temp_under_tmpdir(content, suffix=".yaml", forbid_path=None, prefix="kubetools-"):
    """
    Write content under system temp only. Never the user's open file.
    Returns path. Caller must unlink.
    """
    fd, path = tempfile.mkstemp(prefix=prefix, suffix=suffix)
    try:
        if forbid_path and paths_are_same_file(path, forbid_path):
            raise RuntimeError("Refused temp path colliding with open file")
        tmp_root = tempfile.gettempdir()
        if not path_is_under(path, tmp_root):
            raise RuntimeError("Refused temp path outside tempfile dir: {}".format(path))
        try_fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            if content and not content.endswith("\n"):
                handle.write("\n")
        return path
    except Exception:
        try:
            os.close(fd)
        except Exception:
            pass
        try:
            os.unlink(path)
        except OSError:
            pass
        raise


def write_atomic_in_dir(output_path, content, prefix=".kubetools-tmp-"):
    """Atomic write next to output_path (same directory)."""
    directory = os.path.dirname(output_path) or "."
    fd, tmp_path = tempfile.mkstemp(prefix=prefix, suffix=".yaml", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(content if content.endswith("\n") else content + "\n")
            fh.flush()
            try:
                os.fsync(fh.fileno())
            except OSError:
                pass
        try_chmod(tmp_path, 0o600)
        os.replace(tmp_path, output_path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def scrub_secret_text(message, secrets=None):
    """Remove known secret substrings from error text before showing UI."""
    msg = "" if message is None else str(message)
    for s in secrets or []:
        if s and isinstance(s, str) and len(s) >= 8:
            msg = msg.replace(s, "[redacted]")
    # Bearer / PRIVATE-TOKEN / Authorization style
    msg = re.sub(r"(?i)(bearer\s+)\S+", r"\1[redacted]", msg)
    msg = re.sub(r"(?i)(private-token:\s*)\S+", r"\1[redacted]", msg)
    msg = re.sub(r"(?i)(authorization:\s*)\S+", r"\1[redacted]", msg)
    # Common PAT shapes if they leak into stderr
    msg = re.sub(r"(?i)\bperm:[A-Za-z0-9._\-+=/]+", "perm:[redacted]", msg)
    msg = re.sub(r"(?i)\bglpat-[A-Za-z0-9_\-]+", "glpat-[redacted]", msg)
    return msg


class FlowGate:
    """One interactive flow at a time."""

    def __init__(self):
        self._lock = threading.Lock()
        self._active = False

    def try_begin(self):
        with self._lock:
            if self._active:
                return False
            self._active = True
            return True

    def end(self):
        with self._lock:
            self._active = False

    def is_active(self):
        with self._lock:
            return self._active


# Shared across kubectl apply/compare and seal/unseal (one UI wizard at a time).
OPERATION_GATE = FlowGate()


def spawn_daemon(target, *args, **kwargs):
    """Background worker that will not block Sublime shutdown."""
    t = threading.Thread(target=target, args=args, kwargs=kwargs, daemon=True)
    t.start()
    return t


def confirm_quick(window, yes_caption, detail, on_yes, on_no=None, cancel_detail="Abort"):
    """Yes/Cancel via quick_panel (macOS ST4-safe)."""
    if window is None:
        if on_no:
            on_no()
        return
    items = [[yes_caption, detail], ["Cancel", cancel_detail]]

    def picked(index):
        if index == 0:
            on_yes()
        elif on_no is not None:
            on_no()

    if sublime is None:
        on_yes()
        return
    sublime.set_timeout(lambda: window.show_quick_panel(items, picked), 10)


# ---------------------------------------------------------------------------
# Path → kubectl context / seal stage guessing (preselect only — never auto-run)
# ---------------------------------------------------------------------------

# Env / folder tokens that must not alone select a cluster context
_PATH_AMBIGUOUS_TOKENS = frozenset(
    {
        "dev",
        "test",
        "stage",
        "staging",
        "prod",
        "production",
        "demo",
        "qa",
        "dmz",
        "common",
        "apps",
        "bootstrap",
        "environments",
        "values",
        "configmaps",
        "sealed-secrets",
        "shared-configmaps",
        "overlays",
        "base",
        "files",
        "chart",
        "charts",
        "templates",
        "src",
        "git",
        "tmp",
        "user",
        "users",
        "home",
    }
)


def normalize_path_segments(file_path):
    """Lowercased path segments (no empty)."""
    if not file_path:
        return []
    norm = os.path.normpath(str(file_path)).replace("\\", "/")
    return [p for p in norm.split("/") if p and p not in (".", "..")]


def gitops_cluster_from_path(file_path):
    """
    If path contains ``.../environments/<env>/<cluster>/...``, return <cluster>.
    Otherwise None.
    """
    parts = normalize_path_segments(file_path)
    lower = [p.lower() for p in parts]
    try:
        i = lower.index("environments")
    except ValueError:
        return None
    if i + 2 >= len(parts):
        return None
    cluster = parts[i + 2]
    if cluster.lower() in _PATH_AMBIGUOUS_TOKENS:
        return None
    if len(cluster) < 3:
        return None
    return cluster


def _alias_target(context_name, aliases):
    """Return alias map target if context_name is a key, else None."""
    if not aliases or not isinstance(aliases, dict):
        return None
    # exact then case-insensitive
    if context_name in aliases:
        return str(aliases[context_name] or "").strip() or None
    lower_map = {str(k).lower(): v for k, v in aliases.items()}
    hit = lower_map.get((context_name or "").lower())
    if hit is None:
        return None
    return str(hit).strip() or None


def score_name_against_path(name, file_path, aliases=None):
    """
    Score how well ``name`` (context or stage) matches ``file_path``.

    Returns (score, reason) where score 0 = no match.
      100 — exact path segment
       95 — environments/<env>/<cluster> equals name
       85 — alias maps path cluster/segment → name
    """
    if not name or not file_path:
        return 0, ""
    name = str(name).strip()
    if not name or len(name) < 3:
        return 0, ""
    if name.lower() in _PATH_AMBIGUOUS_TOKENS:
        return 0, ""

    parts = normalize_path_segments(file_path)
    parts_lower = [p.lower() for p in parts]
    name_l = name.lower()

    # Exact segment (case-insensitive)
    if name_l in parts_lower:
        return 100, "path segment"

    # GitOps cluster folder
    cluster = gitops_cluster_from_path(file_path)
    if cluster and cluster.lower() == name_l:
        return 95, "environments/…/{}".format(cluster)

    # Aliases: path token → context name
    if aliases and isinstance(aliases, dict):
        # If any path segment (or gitops cluster) aliases to this name
        candidates = list(parts)
        if cluster:
            candidates.append(cluster)
        for tok in candidates:
            target = _alias_target(tok, aliases)
            if target and target.lower() == name_l:
                return 85, "alias {}→{}".format(tok, name)
        # Or name aliases to a path segment (less common)
        target = _alias_target(name, aliases)
        if target and target.lower() in parts_lower:
            return 85, "alias {}→{}".format(name, target)

    return 0, ""


def rank_contexts_for_path(contexts, file_path, aliases=None, current_context=None):
    """
    Order contexts for the quick panel and pick a preselect index.

    Returns (ordered_names, selected_index, reasons_by_name).

    Safety contract for callers:
    - Always show the quick panel (never skip).
    - selected_index is only a default highlight.
    - Do not apply/compare until the user confirms a panel choice.
    """
    contexts = [c for c in (contexts or []) if c]
    if not contexts:
        return [], 0, {}

    scored = []
    reasons = {}
    for ctx in contexts:
        score, reason = score_name_against_path(ctx, file_path, aliases=aliases)
        if score > 0:
            reasons[ctx] = reason
        # Slight boost so current context wins ties among non-matches
        tie = 1 if current_context and ctx == current_context else 0
        scored.append((score, tie, ctx))

    # High score first, then current, then stable name order
    scored.sort(key=lambda t: (-t[0], -t[1], t[2].lower()))
    ordered = [t[2] for t in scored]

    # Preselect best path match if any; else current context; else 0
    selected = 0
    best_score = scored[0][0] if scored else 0
    if best_score > 0:
        selected = 0
    elif current_context and current_context in ordered:
        selected = ordered.index(current_context)
    return ordered, selected, reasons


def guess_name_index(names, file_path, aliases=None):
    """
    Index into ``names`` for the best path match, or -1.
    Used by seal stage picker (same rules as contexts).
    """
    if not names or not file_path:
        return -1
    best_i = -1
    best_score = 0
    for i, name in enumerate(names):
        # stages are dicts or strings
        if isinstance(name, dict):
            label = name.get("name") or ""
        else:
            label = name
        score, _reason = score_name_against_path(label, file_path, aliases=aliases)
        if score > best_score:
            best_score = score
            best_i = i
    return best_i if best_score > 0 else -1


def looks_like_sealed_secret(content):
    """Heuristic: buffer already looks like a SealedSecret."""
    if not content:
        return False
    head = content[:4000]
    return bool(re.search(r"(?m)^kind:\s*SealedSecret\s*$", head)) or (
        '"kind"' in head and "SealedSecret" in head
    )


def looks_like_plain_secret(content):
    """Heuristic: core/v1 Secret (not a SealedSecret)."""
    if not content:
        return False
    head = content[:4000]
    has_secret_kind = bool(re.search(r"(?m)^kind:\s*Secret\s*$", head)) or (
        '"kind"' in head and re.search(r'"kind"\s*:\s*"Secret"', head)
    )
    return bool(has_secret_kind) and not looks_like_sealed_secret(content)


def looks_like_k8s_manifest(content):
    """True when the buffer has apiVersion + kind + metadata.name (YAML)."""
    if not content or not str(content).strip():
        return False
    head = str(content)[:16000]
    if looks_like_sealed_secret(head) or looks_like_plain_secret(head):
        return True
    has_kind = re.search(r"(?m)^kind:\s*\S+", head)
    has_api = re.search(r"(?m)^apiVersion:\s*\S+", head)
    has_name = re.search(r"(?m)^[ \t]+name:\s*\S+", head)
    return bool(has_kind and has_api and has_name)


EXAMPLE_TEMPLATE_NAMESPACE = "CHANGE-ME"

_EXAMPLE_NS_LINE_RE = re.compile(
    r'(?m)^[ \t]+namespace:\s*["\']?CHANGE-ME["\']?\s*$'
)


def example_namespace_unchanged(content):
    """True when a create-template still has the sentinel namespace."""
    if not content:
        return False
    return bool(_EXAMPLE_NS_LINE_RE.search(str(content)))


TEMPLATE_RESOURCE_NAMES = frozenset({"example", "example-secrets"})
PROTECTED_NAMESPACES = frozenset(
    {"kube-system", "kube-public", "kube-node-lease"}
)

_HELM_BASENAME_EXACT = frozenset(
    {"chart.yaml", "chart.lock", "values.yaml", "cluster-common.yaml"}
)
_HELM_TPL_RE = re.compile(r"\{\{-?")
_ARGO_KIND_RE = re.compile(
    r"(?m)^kind:\s*(Application|ApplicationSet|AppProject)\s*$"
)
_APPLIED_LINE_RE = re.compile(r"(?m)^# applied:.*\n")
_GITOPS_PATH_PARTS = frozenset(
    {"bootstrap", "environments", "sealed-secrets", "shared-configmaps"}
)


def looks_like_helm_source(path, content=""):
    """Helm chart / values / Go-template — never kubectl apply."""
    base = os.path.basename(path or "").lower()
    if base in _HELM_BASENAME_EXACT:
        return True
    if base.endswith("-values.yaml") or base.endswith("-values-override.yaml"):
        return True
    if base.startswith("values-") and base.endswith((".yaml", ".yml")):
        return True
    parts = [p.lower() for p in normalize_path_segments(path)]
    if "templates" in parts or "charts" in parts:
        return True
    head = str(content or "")[:16000]
    if _HELM_TPL_RE.search(head):
        return True
    if (
        re.search(r"(?m)^apiVersion:\s*v2\s*$", head)
        and re.search(r"(?m)^version:\s*\S", head)
        and not re.search(r"(?m)^kind:\s+\S", head)
    ):
        return True
    return False


def looks_like_gitops_source(path):
    """apps-gitops / Argo bootstrap trees — never kubectl apply."""
    if not path:
        return False
    base = os.path.basename(path).lower()
    if base in ("kustomization.yaml", "kustomization.yml"):
        return True
    parts = [p.lower() for p in normalize_path_segments(path)]
    return bool(_GITOPS_PATH_PARTS.intersection(parts))


def looks_like_argo_cd_manifest(content):
    head = str(content or "")[:16000]
    if "argoproj.io" not in head:
        return False
    return bool(_ARGO_KIND_RE.search(head))


def example_resource_name_unchanged(name):
    n = (name or "").strip().strip("\"'")
    return n in TEMPLATE_RESOURCE_NAMES


def is_protected_namespace(name):
    return (name or "").strip().lower() in PROTECTED_NAMESPACES


def stamp_applied_header(text, stamp):
    """Prepend or refresh ``# applied: <stamp>`` at the top of the buffer."""
    line = "# applied: {}\n".format(stamp)
    body = text or ""
    if _APPLIED_LINE_RE.search(body):
        return _APPLIED_LINE_RE.sub(line, body, count=1)
    return line + body


# CLI ``-c`` aliases (k9s docs: ``k9s -c pod``, not in-app ``:pods``).
# A leading colon makes k9s treat this as a goto command and often land on :ctx.
K9S_VIEW_BY_KIND = {
    "Deployment": "deploy",
    "StatefulSet": "sts",
    "DaemonSet": "ds",
    "ReplicaSet": "rs",
    "Job": "job",
    "CronJob": "cj",
    "Pod": "pod",
    "Service": "svc",
    "Ingress": "ing",
    "IngressClass": "ingressclass",
    "ConfigMap": "cm",
    "Secret": "secret",
    "PersistentVolumeClaim": "pvc",
    "PersistentVolume": "pv",
    "ServiceAccount": "sa",
    "HorizontalPodAutoscaler": "hpa",
    "NetworkPolicy": "netpol",
    "Role": "role",
    "RoleBinding": "rolebinding",
    "ClusterRole": "clusterrole",
    "ClusterRoleBinding": "clusterrolebinding",
    "Namespace": "ns",
    "Node": "node",
    "SealedSecret": "sealedsecret",
}

# GVR keys for views.yaml (k9s custom views). Alias keys also work (v0.40.8+).
K9S_GVR_BY_KIND = {
    "Deployment": "apps/v1/deployments",
    "StatefulSet": "apps/v1/statefulsets",
    "DaemonSet": "apps/v1/daemonsets",
    "ReplicaSet": "apps/v1/replicasets",
    "Job": "batch/v1/jobs",
    "CronJob": "batch/v1/cronjobs",
    "Pod": "v1/pods",
    "Service": "v1/services",
    "Ingress": "networking.k8s.io/v1/ingresses",
    "IngressClass": "networking.k8s.io/v1/ingressclasses",
    "ConfigMap": "v1/configmaps",
    "Secret": "v1/secrets",
    "PersistentVolumeClaim": "v1/persistentvolumeclaims",
    "PersistentVolume": "v1/persistentvolumes",
    "ServiceAccount": "v1/serviceaccounts",
    "HorizontalPodAutoscaler": "autoscaling/v2/horizontalpodautoscalers",
    "NetworkPolicy": "networking.k8s.io/v1/networkpolicies",
    "Role": "rbac.authorization.k8s.io/v1/roles",
    "RoleBinding": "rbac.authorization.k8s.io/v1/rolebindings",
    "ClusterRole": "rbac.authorization.k8s.io/v1/clusterroles",
    "ClusterRoleBinding": "rbac.authorization.k8s.io/v1/clusterrolebindings",
    "Namespace": "v1/namespaces",
    "Node": "v1/nodes",
    "SealedSecret": "bitnami.com/v1alpha1/sealedsecrets",
}

_K9S_SAFE_CONTEXT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/@-]*$")
_K9S_SAFE_VIEW = re.compile(r"^:?[A-Za-z0-9][A-Za-z0-9._/-]*$")
_K9S_SAFE_NS = re.compile(r"^[a-z0-9]([a-z0-9.-]{0,61}[a-z0-9])?$")
_CREATE_NEW_CONSOLE = 0x00000010


def k9s_view_command(kind):
    """k9s ``-c`` alias (no colon), e.g. Deployment → ``deploy``."""
    k = (kind or "").strip()
    if k in K9S_VIEW_BY_KIND:
        return K9S_VIEW_BY_KIND[k]
    slug = re.sub(r"[^A-Za-z0-9]", "", k).lower()
    return slug or "pod"


def pick_k9s_target(resources):
    """
    Namespace + view for the applied docs.
    Prefer the first namespaced object (typical Create slice is one kind).
    """
    named = [r for r in (resources or []) if r.get("kind")]
    if not named:
        return None
    namespaced = [
        r
        for r in named
        if not r.get("cluster_scoped") and (r.get("namespace") or "").strip()
    ]
    chosen = namespaced[0] if namespaced else named[0]
    ns = (chosen.get("namespace") or "").strip()
    kind = chosen.get("kind")
    return {
        "kind": kind,
        "name": chosen.get("name") or "",
        "namespace": ns,
        "cluster_scoped": bool(chosen.get("cluster_scoped")) or not ns,
        "view": k9s_view_command(kind),
    }


def resolve_binary(configured, default_name):
    """Absolute path when found; otherwise the bare command name."""
    raw = (configured or default_name or "").strip() or default_name
    expanded = expand_path(raw)
    err = validate_binary_path(expanded, default_name)
    if err:
        return None, err
    if "/" in expanded or "\\" in expanded:
        return expanded, None
    found = find_binary(expanded)
    return (found or expanded), None


def build_k9s_argv(k9s_path, context, namespace, view, cluster_scoped=False):
    """List argv for k9s, or ``(None, error)``."""
    path = (k9s_path or "k9s").strip() or "k9s"
    err = validate_binary_path(path, "k9s")
    if err:
        return None, err
    ctx = (context or "").strip()
    if not ctx or not _K9S_SAFE_CONTEXT.match(ctx):
        return None, "kubectl context is missing or unsafe for k9s"
    view = (view or "").strip()
    if view.startswith(":"):
        view = view[1:]
    if view and not _K9S_SAFE_VIEW.match(view):
        return None, "k9s view is unsafe"
    argv = [path, "--splashless", "--context", ctx]
    if not cluster_scoped:
        ns = (namespace or "").strip().lower()
        if not ns or not _K9S_SAFE_NS.match(ns):
            return None, "namespace is missing or unsafe for k9s"
        argv.extend(["-n", ns])
    if view:
        # CLI -c is a resource alias (pod, deploy). Colon form is in-app only
        # and lands k9s on the :ctx picker when it does not match an alias.
        argv.extend(["-c", view])
    return argv, None


def k9s_views_yaml_age_newest(view, kind=None):
    """
    k9s views.yaml: sort AGE ascending (smaller age = newer on top).
    Docs: sortColumn: AGE:asc  (https://k9scli.io/topics/columns/)
    """
    keys = []
    v = (view or "").strip()
    if v.startswith(":"):
        v = v[1:]
    if v:
        keys.append(v)
    gvr = K9S_GVR_BY_KIND.get((kind or "").strip())
    if gvr and gvr not in keys:
        keys.append(gvr)
    if not keys:
        keys.append("v1/pods")
    lines = ["views:"]
    for key in keys:
        lines.append("  {}:".format(key))
        lines.append("    sortColumn: AGE:asc")
    return "\n".join(lines) + "\n"


def prepare_k9s_session_dir(view, kind=None):
    """Temp K9S_CONFIG_DIR with AGE:asc views. Does not rewrite the user config."""
    root = os.path.join(tempfile.gettempdir(), "kubetools-k9s")
    try:
        os.makedirs(root, exist_ok=True)
        path = os.path.join(root, "views.yaml")
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(k9s_views_yaml_age_newest(view, kind))
    except OSError:
        return None
    return root


def _applescript_string(s):
    return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _linux_terminal_argv(name, path, k9s_argv):
    if name in ("gnome-terminal", "gnome-console", "kgx"):
        return [path, "--"] + list(k9s_argv)
    if name == "kitty":
        return [path] + list(k9s_argv)
    if name == "xfce4-terminal":
        return [path, "-x"] + list(k9s_argv)
    return [path, "-e"] + list(k9s_argv)


def k9s_launch_plan(
    k9s_argv, terminal_pref="", platform=None, which=None, environ=None, extra_env=None
):
    """
    How to open an interactive TTY for k9s (never Sublime's output panel).
    Returns ``(argv, popen_kwargs, error)``.
    """
    if not isinstance(k9s_argv, (list, tuple)) or not k9s_argv:
        return None, None, "empty k9s command"
    if any(not isinstance(x, str) for x in k9s_argv):
        return None, None, "non-string k9s argv"
    plat = platform if platform is not None else sys.platform
    env = environ if environ is not None else os.environ
    which_fn = which
    if which_fn is None:
        try:
            import shutil

            which_fn = shutil.which
        except Exception:
            which_fn = lambda n: None  # noqa: E731
    pref = (terminal_pref or "").strip().lower()
    if pref in ("", "auto"):
        pref = "auto"

    if plat == "darwin":
        quoted = [shlex.quote(a) for a in k9s_argv]
        env_bits = []
        for k, v in (extra_env or {}).items():
            if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", k):
                continue
            env_bits.append("{}={}".format(k, shlex.quote(str(v))))
        script_body = " ".join(env_bits + ["exec"] + quoted)
        use_iterm = pref in ("auto", "iterm", "iterm2")
        if use_iterm:
            ascript = (
                'tell application "iTerm"\n'
                "activate\n"
                "create window with default profile\n"
                "tell current session of current window\n"
                "set columns to 220\n"
                "set rows to 60\n"
                "write text {}\n"
                "end tell\n"
                "try\n"
                'tell application "System Events"\n'
                "set {{sx, sy}} to position of desktop 1\n"
                "set {{sw, sh}} to size of desktop 1\n"
                "end tell\n"
                "tell current window\n"
                "set {{wx, wy, wr, wb}} to bounds\n"
                "set winW to wr - wx\n"
                "set winH to wb - wy\n"
                "set nx to sx + (sw - winW) / 2\n"
                "set ny to sy + (sh - winH) / 2\n"
                "set bounds to {{nx, ny, nx + winW, ny + winH}}\n"
                "end tell\n"
                "end try\n"
                "end tell"
            ).format(_applescript_string(script_body))
        else:
            ascript = (
                'tell application "Terminal"\n'
                "activate\n"
                "do script {}\n"
                "end tell"
            ).format(_applescript_string(script_body))
        kwargs = {
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
            "start_new_session": True,
        }
        return ["osascript", "-e", ascript], kwargs, None

    if plat == "win32":
        merged_env = None
        if extra_env:
            merged_env = dict(env)
            merged_env.update(extra_env)
        wt = None
        if pref in ("auto", "windows-terminal", "wt"):
            try:
                wt = which_fn("wt")
            except Exception:
                wt = None
        if wt:
            kwargs = {
                "stdin": subprocess.DEVNULL,
                "stdout": subprocess.DEVNULL,
                "stderr": subprocess.DEVNULL,
            }
            if merged_env is not None:
                kwargs["env"] = merged_env
            return [wt, "new-tab", "--title", "k9s", "--"] + list(k9s_argv), kwargs, None
        wkwargs = {"creationflags": _CREATE_NEW_CONSOLE}
        if merged_env is not None:
            wkwargs["env"] = merged_env
        return list(k9s_argv), wkwargs, None

    term = pref if pref != "auto" else (env.get("TERMINAL") or "")
    if term:
        term = os.path.basename(term.replace("\\", "/"))
    order = []
    if term and term not in ("auto",):
        order.append(term)
    if pref == "auto":
        order.extend(
            [
                "gnome-terminal",
                "konsole",
                "xfce4-terminal",
                "kitty",
                "alacritty",
                "x-terminal-emulator",
                "xterm",
            ]
        )
    seen = set()
    for name in order:
        if not name or name in seen:
            continue
        seen.add(name)
        try:
            path = which_fn(name)
        except Exception:
            path = None
        if not path:
            continue
        kwargs = {
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
            "start_new_session": True,
        }
        linux_argv = _linux_terminal_argv(name, path, k9s_argv)
        if extra_env:
            env_cmd = ["env"]
            for k, v in extra_env.items():
                if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", k):
                    continue
                env_cmd.append("{}={}".format(k, v))
            # Insert env PREFIX before k9s_argv inside the terminal wrapper.
            prefix_len = len(linux_argv) - len(k9s_argv)
            linux_argv = linux_argv[:prefix_len] + env_cmd + list(k9s_argv)
        return linux_argv, kwargs, None
    return None, None, "no terminal emulator found (set k9s_terminal)"


def launch_k9s_in_terminal(k9s_argv, terminal_pref="", popen=None, extra_env=None):
    """Start k9s in a real terminal. Returns error string or None."""
    argv, kwargs, err = k9s_launch_plan(
        k9s_argv, terminal_pref=terminal_pref, extra_env=extra_env
    )
    if err:
        return err
    fn = popen or subprocess.Popen
    try:
        fn(list(argv), **(kwargs or {}))
    except OSError as exc:
        return "failed to start k9s: {}".format(exc)
    except TypeError as exc:
        return "failed to start k9s: {}".format(exc)
    return None


def mutating_source_block_reason(path, content):
    """Why Apply/Dry-Run must not run (Helm / GitOps / Argo CD). Else None."""
    if looks_like_helm_source(path, content):
        return "Helm charts, values, and templates are never applied"
    if looks_like_gitops_source(path):
        return "GitOps / bootstrap paths are never applied"
    if looks_like_argo_cd_manifest(content):
        return "Argo CD Application CRs are never applied from Kubetools"
    return None


_YAML_SCALAR_FIELD_RE = re.compile(
    r"^(\s*(?:-\s+)*)([A-Za-z0-9][A-Za-z0-9./_-]*)\s*:\s+(\S.*)$"
)


def yaml_scalar_field_regions(text):
    """Offsets of YAML ``key: scalar`` values (skip comments, empties, block keys)."""
    fields = []
    pos = 0
    for line in (text or "").splitlines(keepends=True):
        raw = line.rstrip("\r\n")
        stripped = raw.strip()
        if stripped and not stripped.startswith("#") and stripped != "---":
            m = _YAML_SCALAR_FIELD_RE.match(raw)
            if m:
                val = m.group(3).strip()
                if val not in ("|", ">", "|-", ">-", "{}", "[]"):
                    fields.append((pos + m.start(3), pos + m.end(3)))
        pos += len(line)
    return fields


def next_field_index(fields, caret, *, forward=True):
    """Index of the next/previous field, wrapping. None if no fields."""
    if not fields:
        return None
    current = None
    for i, (a, b) in enumerate(fields):
        end = b if b > a else a
        if a <= caret <= end:
            current = i
            break
    n = len(fields)
    if current is None:
        if forward:
            for i, (a, _b) in enumerate(fields):
                if a >= caret:
                    return i
            return 0
        for i, (_a, b) in reversed(list(enumerate(fields))):
            if b <= caret:
                return i
        return n - 1
    return (current + (1 if forward else -1)) % n

