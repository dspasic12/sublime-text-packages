# Shared helpers for Kubetools (kubectl + seal). Pure / ST-light utilities.
# Sublime loads every top-level .py; this module defines no commands.

from __future__ import annotations

import fnmatch
import os
import re
import subprocess
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
    if not os.access(path, os.X_OK):
        return "{} is not executable: {}".format(label, path)
    return None


def find_binary(name, extra_dirs=None):
    """Return absolute path to ``name`` on PATH (+ extras) or None."""
    path_env = os.environ.get("PATH", "")
    extras = list(extra_dirs or []) + [
        "/opt/homebrew/bin",
        "/usr/local/bin",
        "/usr/bin",
    ]
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
        tmp_root = os.path.realpath(tempfile.gettempdir())
        if not os.path.realpath(path).startswith(tmp_root + os.sep) and os.path.realpath(
            path
        ) != tmp_root:
            raise RuntimeError("Refused temp path outside tempfile dir: {}".format(path))
        try:
            os.fchmod(fd, 0o600)
        except OSError:
            pass
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
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
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content if content.endswith("\n") else content + "\n")
            fh.flush()
            try:
                os.fsync(fh.fileno())
            except OSError:
                pass
        try:
            os.chmod(tmp_path, 0o600)
        except OSError:
            pass
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
