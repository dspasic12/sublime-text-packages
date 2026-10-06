# Kubetools — seal / unseal (kubeseal) integrated with compare/apply
#
# - Buffer mutations only inside TextCommand.run(edit, ...)
# - Settings: Kubetools.sublime-settings (migrates stages from User Kubeseal settings)
# - Sublime Text 4 / Python 3.14
#

import sublime
import sublime_plugin

try:
    from . import kubetools_common as _ktc
except ImportError:
    import kubetools_common as _ktc  # type: ignore
import subprocess
import os
import re
import json
import base64
import tempfile
from datetime import datetime


# Module state (no API at import) — concurrency via kubetools_common.OPERATION_GATE

_RFC1123_NAME = re.compile(r'^[a-z0-9]([-a-z0-9]*[a-z0-9])?$')
_RFC1123_DNS = re.compile(
    r'^[a-z0-9]([-a-z0-9]*[a-z0-9])?(\.[a-z0-9]([-a-z0-9]*[a-z0-9])?)*$'
)


def b64decode_to_text(value):
    """Decode a base64 string to UTF-8 text. Returns original value on failure."""
    if value is None or not isinstance(value, str):
        return value
    stripped = value.strip().strip('"').strip("'")
    if not stripped:
        return value
    try:
        # validate= not available on older plugin-host Pythons
        raw = base64.b64decode(stripped)
    except Exception:
        return value
    try:
        return raw.decode('utf-8')
    except UnicodeDecodeError:
        return raw.decode('utf-8', errors='replace')


def _yaml_escape_scalar(text):
    """Quote a YAML scalar when needed so decoded plaintext stays valid YAML."""
    if text is None:
        return '""'
    if text == '':
        return '""'
    needs_quote = (
        text[0] in ' \t' or text[-1] in ' \t'
        or any(ch in text for ch in ':#{}[]&*!|>\'"%@`,?\n\r')
        or text.lower() in ('true', 'false', 'null', 'yes', 'no', '~')
        or re.match(r'^[-0-9]', text)
    )
    if not needs_quote:
        return text
    return json.dumps(text)


def decode_secret_data_fields(content):
    """
    After kubeseal --recovery-unseal, Secret.data values are still base64.
    Decode every key under data (e.g. data.data) for human-readable output.
    Supports JSON (kubeseal default) and simple YAML.
    """
    if not content or not content.strip():
        return content

    stripped = content.strip()

    try:
        obj = json.loads(stripped)
    except (ValueError, TypeError):
        obj = None

    if isinstance(obj, dict) and isinstance(obj.get('data'), dict):
        decoded = {}
        for key, val in obj['data'].items():
            decoded[key] = b64decode_to_text(val) if isinstance(val, str) else val
        obj['data'] = decoded
        return json.dumps(obj, indent=2, ensure_ascii=False) + '\n'

    return _decode_secret_data_yamlish(content)


def _decode_secret_data_yamlish(content):
    lines = content.split('\n')
    result = []
    in_data = False
    data_indent = None

    for line in lines:
        if re.match(r'^data:\s*$', line):
            in_data = True
            data_indent = None
            result.append(line)
            continue

        if in_data:
            if re.match(r'^\s*#', line) or line.strip() == '':
                result.append(line)
                continue

            m = re.match(r'^(\s*)([^:\s][^:]*):\s*(.*)$', line)
            if not m:
                in_data = False
                result.append(line)
                continue

            indent, key, val = m.group(1), m.group(2), m.group(3)
            indent_len = len(indent)

            if data_indent is None:
                data_indent = indent_len

            if indent_len < data_indent:
                in_data = False
                result.append(line)
                continue

            if indent_len == data_indent and val.strip() != '':
                decoded = b64decode_to_text(val.strip())
                result.append('{}{}: {}'.format(indent, key, _yaml_escape_scalar(decoded)))
                continue

            result.append(line)
            continue

        result.append(line)

    return '\n'.join(result)


def derive_sealedsecret_output_path(input_path):
    """
    Map an open template file to its sealed output path.

    Examples:
      foo-secret-template.yaml  -> foo-sealedsecret.yaml
      foo-secret-template       -> foo-sealedsecret.yaml
      foo-template.yaml         -> foo-sealedsecret.yaml
      foo.yaml                  -> foo-sealedsecret.yaml
    """
    directory = os.path.dirname(input_path)
    basename = os.path.basename(input_path)
    name, _ext = os.path.splitext(basename)

    if name.endswith('-secret-template'):
        out_name = name[: -len('-secret-template')] + '-sealedsecret.yaml'
    elif name.endswith('-template'):
        out_name = name[: -len('-template')] + '-sealedsecret.yaml'
    else:
        out_name = name + '-sealedsecret.yaml'

    return os.path.join(directory, out_name)


def derive_sealedsecret_backup_path(output_path):
    """
    Backup path for an existing sealedsecret file: .bckp-<oldFileName>

    If that backup already exists, append a timestamp suffix so prior
    backups are not overwritten.
    """
    directory = os.path.dirname(output_path) or '.'
    basename = os.path.basename(output_path)
    backup_name = '.bckp-' + basename
    backup_path = os.path.join(directory, backup_name)
    if not os.path.exists(backup_path):
        return backup_path
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    return os.path.join(directory, '.bckp-{}.{}'.format(basename, stamp))


def backup_existing_sealedsecret(output_path):
    """
    Rename existing sealedsecret to .bckp-<name> before overwrite.

    Returns the backup path, or None if output_path did not exist.
    Raises OSError on rename failure.
    """
    if not output_path or not os.path.exists(output_path):
        return None
    backup_path = derive_sealedsecret_backup_path(output_path)
    os.rename(output_path, backup_path)
    return backup_path


def build_full_seal_command(cert_path):
    """kubeseal command for sealing a whole Secret manifest (stdin -> stdout)."""
    return ['kubeseal', '--format=yaml', '--cert', cert_path]


def _view_text_head(view, limit=16000):
    if view is None:
        return ""
    try:
        return view.substr(sublime.Region(0, min(view.size(), limit)))
    except Exception:
        return ""


def _view_can_seal(view):
    if view is None or view.is_read_only():
        return False
    content = _view_text_head(view)
    has_sel = any(not r.empty() for r in view.sel())
    if looks_like_plain_secret(content):
        return not has_sel
    if looks_like_sealed_secret(content):
        return has_sel
    return False


def _view_can_unseal(view):
    if view is None:
        return False
    if not any(not r.empty() for r in view.sel()):
        return False
    return looks_like_sealed_secret(_view_text_head(view))


def looks_like_sealed_secret(content):
    return _ktc.looks_like_sealed_secret(content)


def looks_like_plain_secret(content):
    return _ktc.looks_like_plain_secret(content)


def secret_has_payload(content):
    """
    True when a Secret manifest has at least one data / stringData entry.
    Prevents sealing empty templates by accident.
    """
    if not content:
        return False
    # JSON Secret
    try:
        obj = json.loads(content.strip())
    except (ValueError, TypeError):
        obj = None
    if isinstance(obj, dict):
        for key in ('data', 'stringData'):
            block = obj.get(key)
            if isinstance(block, dict) and any(
                isinstance(v, str) and v != '' for v in block.values()
            ):
                return True
        return False

    # YAML-ish: look for indented key under data: / stringData:
    in_block = False
    block_indent = None
    for line in content.split('\n'):
        if re.match(r'^(data|stringData):\s*$', line):
            in_block = True
            block_indent = None
            continue
        if not in_block:
            continue
        if re.match(r'^\s*#', line) or line.strip() == '':
            continue
        m = re.match(r'^(\s*)([^:\s][^:]*):\s*(.*)$', line)
        if not m:
            in_block = False
            continue
        indent_len = len(m.group(1))
        if block_indent is None:
            block_indent = indent_len
        if indent_len < block_indent:
            in_block = False
            continue
        if indent_len == block_indent and m.group(3).strip() != '':
            return True
    return False


def looks_like_already_encrypted_blob(text):
    """Heuristic: selection already looks like kubeseal --raw output."""
    if not text:
        return False
    s = text.strip()
    # SealedSecrets raw ciphertext is typically long base64-ish starting with Ag
    if len(s) < 80:
        return False
    if s.startswith('Ag') and re.match(r'^[A-Za-z0-9+/=]+$', s):
        return True
    return False



# ---------------------------------------------------------------------------
# Delegate duplicated helpers to kubetools_common
# ---------------------------------------------------------------------------

_MAX_KUBESEAL_INPUT_BYTES = 2 * 1024 * 1024  # 2 MiB



def validate_k8s_dns_label(value, field_name):
    """
    Return error string or None.

    Kubernetes namespaces (and most secret names) are RFC 1123 DNS *labels*:
    lowercase alphanumeric and '-', max 63, no dots.
    """
    if not value:
        return '{} cannot be empty'.format(field_name)
    if field_name in ('namespace', 'secret name', 'name'):
        if len(value) > 63:
            return '{} is too long (max 63)'.format(field_name)
        if not _RFC1123_NAME.match(value):
            return (
                "{} must be a valid DNS label (lowercase alphanumeric and '-', "
                "max 63, no dots; e.g. my-namespace)".format(field_name)
            )
        return None
    if len(value) > 253:
        return '{} is too long (max 253)'.format(field_name)
    if not _RFC1123_DNS.match(value):
        return (
            "{} must be a valid DNS subdomain (lowercase alphanumeric and '-', "
            "e.g. my.namespace.example)".format(field_name)
        )
    return None


def normalize_stages(raw_stages):
    """Normalize settings stages list into dicts with name/cert/key."""
    stages = []
    if not isinstance(raw_stages, list):
        return stages
    for item in raw_stages:
        if not isinstance(item, dict):
            continue
        name = (item.get('name') or item.get('stage') or '').strip()
        if not name:
            continue
        stages.append({
            'name': name,
            'cert_path': (item.get('cert_path') or item.get('public_key_path') or '').strip(),
            'private_key_path': (item.get('private_key_path') or '').strip(),
            'description': (item.get('description') or '').strip(),
        })
    return stages


def guess_stage_index(file_path, stages):
    """
    Prefer a stage whose name matches a path segment / environments/…/cluster.
    Delegates to kubetools_common (same rules as kubectl context preselect).
    """
    return _ktc.guess_name_index(stages, file_path, aliases=None)


def stage_requires_cert(stage):
    return bool(stage and stage.get('cert_path'))


def stage_requires_key(stage):
    return bool(stage and stage.get('private_key_path'))

def clamp_timeout(value, default=30, minimum=5, maximum=300):
    return _ktc.clamp_timeout(value, default=default, minimum=minimum, maximum=maximum)


def _expand_path(path):
    return _ktc.expand_path(path)


def find_kubeseal_binary():
    return _ktc.find_binary("kubeseal")


def write_sealed_output_atomic(output_path, content):
    _ktc.write_atomic_in_dir(output_path, content, prefix=".kubeseal-tmp-")


def stage_name_looks_prod(stage_name, patterns=None):
    return _ktc.is_dangerous_name(stage_name, patterns)


def private_key_world_readable(path):
    """True when private key is group/world-readable (Unix)."""
    if not path or not os.path.isfile(path):
        return False
    try:
        mode = os.stat(path).st_mode
    except OSError:
        return False
    return bool(mode & 0o044)


class _MergedSettings(object):
    """Read stages/keys from legacy Kubeseal while writing last_stage to Kubetools."""

    def __init__(self, primary, legacy):
        self._primary = primary
        self._legacy = legacy

    def get(self, key, default=None):
        # Prefer primary non-empty stages; else legacy
        if key == "stages":
            s = self._primary.get("stages", [])
            if s:
                return s
            return self._legacy.get("stages", []) or []
        if key in (
            "default_stage",
            "ask_stage_every_time",
            "last_stage",
            "kubeseal_path",
            "cert_path",
            "private_key_path",
            "decrypt_output",
            "default_secret_name",
            "max_selections",
            "decode_secret_data",
            "validate_k8s_names",
            "confirm_reseal_selection",
            "confirm_overwrite_sealed",
            "offer_compare_after_seal",
        ):
            v = self._primary.get(key, None)
            if v is None or v == "" or v == []:
                return self._legacy.get(key, default)
            return v
        if key == "dangerous_context_patterns":
            return self._primary.get(key, default)
        if key == "timeout":
            # seal-specific default often lower; prefer primary if set
            if self._primary.get("seal_timeout") is not None:
                return self._primary.get("seal_timeout")
            v = self._primary.get("timeout", None)
            if v is not None:
                return v
            return self._legacy.get("timeout", default)
        return self._primary.get(key, default)

    def set(self, key, value):
        self._primary.set(key, value)


def load_seal_settings_obj():
    """Prefer Kubetools settings; fall back to User Kubeseal stages if empty."""
    kt = sublime.load_settings("Kubetools.sublime-settings")
    stages = kt.get("stages", [])
    if stages:
        return kt
    try:
        legacy = sublime.load_settings("Kubeseal.sublime-settings")
        if legacy.get("stages"):
            return _MergedSettings(kt, legacy)
    except Exception:
        pass
    return kt


class _SealBase(sublime_plugin.TextCommand):
    """Base class for kubeseal operations"""


    def _load_settings_obj(self):
        return load_seal_settings_obj()
        # Migration: read stages from Kubeseal User/package settings
        try:
            legacy = sublime.load_settings("Kubeseal.sublime-settings")
            if legacy.get("stages"):
                return _MergedSettings(kt, legacy)
        except Exception:
            pass
        return kt

    def get_settings(self):
        settings = self._load_settings_obj()
        stages = normalize_stages(settings.get('stages', []))
        # Expand paths in stages
        for stage in stages:
            stage['cert_path'] = _expand_path(stage.get('cert_path', ''))
            stage['private_key_path'] = _expand_path(stage.get('private_key_path', ''))

        legacy_cert = _expand_path(settings.get('cert_path', '') or '')
        legacy_key = _expand_path(settings.get('private_key_path', '') or '')

        # Backward compatible: inject legacy single-key as a synthetic stage
        if not stages and (legacy_cert or legacy_key):
            stages = [{
                'name': 'default',
                'cert_path': legacy_cert,
                'private_key_path': legacy_key,
                'description': 'Legacy cert_path / private_key_path',
            }]

        return {
            'stages': stages,
            'default_stage': (settings.get('default_stage') or '').strip(),
            'ask_stage_every_time': True,  # hard-locked: always show stage panel
            'last_stage': (settings.get('last_stage') or '').strip(),
            'timeout': clamp_timeout(settings.get('timeout', 30), default=30),
            'decrypt_output': settings.get('decrypt_output', 'new_tab'),
            'default_namespace': settings.get('default_namespace', 'default'),
            'default_secret_name': settings.get('default_secret_name', 'mysecret'),
            'max_selections': int(settings.get('max_selections', 10) or 10),
            'decode_secret_data': settings.get('decode_secret_data', True),
            'validate_k8s_names': settings.get('validate_k8s_names', True),
            'confirm_reseal_selection': settings.get('confirm_reseal_selection', True),
            'confirm_overwrite_sealed': settings.get('confirm_overwrite_sealed', True),
            'offer_compare_after_seal': settings.get('offer_compare_after_seal', True),
            'kubeseal_path': _expand_path(settings.get('kubeseal_path', '') or ''),
            'dangerous_context_patterns': list(
                settings.get(
                    'dangerous_context_patterns',
                    [
                        '*prod*',
                        '*production*',
                        '*-prod-*',
                        'prod-*',
                        '*prd*',
                        '*live*',
                    ],
                )
                or []
            ),
            # keep legacy fields for validate / fallbacks
            'cert_path': legacy_cert,
            'private_key_path': legacy_key,
        }

    def show_error(self, message):
        secrets = []
        if hasattr(self, 'settings') and self.settings:
            for k in ('private_key_path', 'cert_path', 'kubeseal_path'):
                v = self.settings.get(k)
                if v:
                    secrets.append(v)
            for st in self.settings.get('stages') or []:
                for k in ('private_key_path', 'cert_path'):
                    if st.get(k):
                        secrets.append(st[k])
        msg = _ktc.scrub_secret_text(message, secrets)
        if len(msg) > 1200:
            msg = msg[:1200] + "\n\n... (truncated)"
        sublime.error_message('Kubetools Seal Error: {}'.format(msg))

    def show_status(self, message):
        sublime.status_message('Kubetools: {}'.format(message))

    def confirm_quick(self, yes_caption, detail, on_yes, on_no=None):
        """
        Yes/Cancel via quick_panel (Command Palette–style).

        Prefer this over sublime.ok_cancel_dialog: native sheets force mouse
        focus and can hard-exit ST4 on macOS when mixed with other UI.
        """
        window = self.view.window() if self.view is not None else None
        if window is None:
            self.show_error('No active window')
            if on_no:
                on_no()
            return

        items = [
            [yes_caption, detail],
            ['Cancel', 'Abort'],
        ]

        def picked(index):
            if index == 0:
                on_yes()
            else:
                if on_no is not None:
                    on_no()
                else:
                    self.show_status('Cancelled')

        # Defer so we never stack UI transitions in the same event turn.
        sublime.set_timeout(
            lambda: window.show_quick_panel(items, picked),
            10,
        )

    def remember_stage(self, stage_name):
        settings = self._load_settings_obj()
        settings.set('last_stage', stage_name)
        sublime.save_settings('Kubetools.sublime-settings')

    def resolve_kubeseal(self):
        configured = self.settings.get('kubeseal_path') if hasattr(self, 'settings') else ''
        if configured:
            err = _ktc.validate_binary_path(configured, 'kubeseal')
            if err:
                self.show_error(err)
                return None
            return configured
        return find_kubeseal_binary()

    def extract_metadata_from_file(self):
        """Extract metadata.name / metadata.namespace (first document only)."""
        try:
            file_content = self.view.substr(sublime.Region(0, self.view.size()))
            namespace = None
            secret_name = None
            in_metadata = False
            meta_indent = None

            for line in file_content.split('\n'):
                if line.strip().startswith('#'):
                    continue
                # Stop at next document
                if line.strip() == '---' and (namespace or secret_name or in_metadata):
                    break
                if re.match(r'^\s*metadata:\s*$', line):
                    in_metadata = True
                    meta_indent = len(line) - len(line.lstrip(' '))
                    continue
                if not in_metadata:
                    continue
                if not line.strip():
                    continue
                indent = len(line) - len(line.lstrip(' '))
                # Left metadata block
                if indent <= meta_indent and not line.lstrip().startswith('#'):
                    in_metadata = False
                    continue
                # Only direct children of metadata (indent == meta_indent + 2 typical)
                m_ns = re.match(r'^\s*namespace:\s*[\'"]?([^\s\'"]+)[\'"]?', line)
                m_name = re.match(r'^\s*name:\s*[\'"]?([^\s\'"]+)[\'"]?', line)
                # Ignore nested maps under labels/annotations: require indent close to meta+2
                if indent > meta_indent + 4:
                    continue
                if m_ns and namespace is None:
                    namespace = m_ns.group(1)
                elif m_name and secret_name is None:
                    secret_name = m_name.group(1)
                if namespace and secret_name:
                    break

            return namespace, secret_name
        except Exception:
            return None, None

    def _run_kubeseal(self, cmd, input_text, timeout):
        """Run kubeseal synchronously; returns (stdout, stderr, returncode)."""
        # Avoid leaking secrets into argv — plaintext/ciphertext only on stdin
        code, stdout, stderr = _ktc.run_subprocess(
            cmd, timeout=timeout, stdin_data=input_text, label='kubeseal'
        )
        return stdout, stderr, code

    def _begin_operation(self):
        if not _ktc.OPERATION_GATE.try_begin():
            self.show_error(
                'Another Kubetools operation is already running. Wait for it to finish.'
            )
            return False
        return True

    def _end_operation(self):
        _ktc.OPERATION_GATE.end()

    def pick_stage(self, need_cert, need_private_key, on_picked):
        """
        Show quick panel of stages. on_picked(stage_dict) or not called on cancel.
        Filters out stages missing required key files when possible; still lists
        them with a warning so the user sees misconfiguration.
        """
        stages = self.settings.get('stages') or []
        if not stages:
            self.show_error(
                'No stages configured. Add a "stages" list in Kubetools settings '
                '(name, cert_path, private_key_path).'
            )
            return

        file_name = getattr(self, "_file_name", None)
        if file_name is None and self.view is not None:
            file_name = self.view.file_name()
            self._file_name = file_name
        guessed = guess_stage_index(file_name, stages)

        default_name = self.settings.get('last_stage') or self.settings.get('default_stage')
        selected_index = 0
        if guessed >= 0:
            selected_index = guessed
        elif default_name:
            for i, s in enumerate(stages):
                if s['name'] == default_name:
                    selected_index = i
                    break

        # Single stage: still show panel (safety — never skip stage selection).
        # ask_stage_every_time is hard-locked True in get_settings.

        items = []
        for s in stages:
            cert_ok = s.get('cert_path') and os.path.isfile(s['cert_path'])
            key_ok = s.get('private_key_path') and os.path.isfile(s['private_key_path'])
            flags = []
            if need_cert:
                flags.append('cert {}'.format('OK' if cert_ok else 'MISSING'))
            if need_private_key:
                flags.append('key {}'.format('OK' if key_ok else 'MISSING'))
            if not need_cert and not need_private_key:
                flags.append('cert {}'.format('OK' if cert_ok else '—'))
                flags.append('key {}'.format('OK' if key_ok else '—'))
            detail = s.get('description') or ', '.join(flags)
            if s.get('description') and flags:
                detail = '{}  ({})'.format(s['description'], ', '.join(flags))
            items.append([s['name'], detail])

        window = self.view.window()
        if not window:
            self.show_error('No active window')
            return

        def on_select(index):
            if index < 0:
                self.show_status('Cancelled')
                return
            stage = stages[index]
            err = self._validate_stage_paths(stage, need_cert, need_private_key)
            if err:
                self.show_error(err)
                return
            self.remember_stage(stage['name'])
            on_picked(stage)

        placeholder = 'Select sealed-secrets stage / cluster key'
        try:
            window.show_quick_panel(
                items, on_select, 0, selected_index, None, placeholder
            )
        except TypeError:
            # Older API without placeholder
            window.show_quick_panel(items, on_select, 0, selected_index)

    def _validate_stage_paths(self, stage, need_cert, need_private_key):
        if need_cert:
            path = stage.get('cert_path') or ''
            if not path:
                return 'Stage "{}" has no cert_path (public key) configured'.format(stage['name'])
            if not os.path.isfile(path):
                return 'Stage "{}" cert not found:\n{}'.format(stage['name'], path)
            if not os.access(path, os.R_OK):
                return 'Stage "{}" cert is not readable:\n{}'.format(stage['name'], path)
        if need_private_key:
            path = stage.get('private_key_path') or ''
            if not path:
                return (
                    'Stage "{}" has no private_key_path configured '
                    '(required for decrypt / recovery-unseal)'
                ).format(stage['name'])
            if not os.path.isfile(path):
                return 'Stage "{}" private key not found:\n{}'.format(stage['name'], path)
            if not os.access(path, os.R_OK):
                return 'Stage "{}" private key is not readable:\n{}'.format(stage['name'], path)
            if private_key_world_readable(path):
                return (
                    'Stage "{}" private key is group/world-readable:\n{}\n\n'
                    'Fix with: chmod 600 "{}"'
                ).format(stage['name'], path, path)
        return None


# ---------------------------------------------------------------------------
# Encrypt / full-file seal
# ---------------------------------------------------------------------------

class KubetoolsSealCommand(_SealBase):
    """
    Encrypt:
      - with selection: kubeseal --raw (replace selection in place)
      - without selection: seal entire open file to sibling *-sealedsecret.yaml
    Always asks which stage key/cert to use (unless ask_stage_every_time=false).
    """

    def is_enabled(self):
        return _view_can_seal(self.view)

    def is_visible(self):
        return self.is_enabled()

    def run(self, edit):
        if self.view.is_read_only():
            self.show_error('View is read-only')
            return

        self.settings = self.get_settings()
        self._file_name = self.view.file_name() if self.view else None
        self._mode = None
        self.stage = None
        self.regions = []

        has_selection = any(not r.empty() for r in self.view.sel())
        self._mode = 'raw' if has_selection else 'file'

        if self._mode == 'file':
            err = self._precheck_full_file_seal()
            if err:
                self.show_error(err)
                return
        else:
            err = self._precheck_raw_encrypt()
            if err:
                self.show_error(err)
                return

        self.pick_stage(
            need_cert=True,
            need_private_key=False,
            on_picked=self._on_stage_picked_for_encrypt
        )

    def _precheck_raw_encrypt(self):
        """
        Raw --raw encrypt only makes sense inside a SealedSecret document
        (encryptedData values). Plain Secret templates must use full-file seal.
        """
        content = self.view.substr(sublime.Region(0, self.view.size()))
        if looks_like_plain_secret(content):
            return (
                'Raw string encrypt is not allowed in a Secret manifest.\n\n'
                'Clear the selection and run Encrypt again to seal the whole '
                'Secret → *-sealedsecret.yaml, or open a SealedSecret to encrypt '
                'individual encryptedData values with --raw.'
            )
        if not looks_like_sealed_secret(content):
            return (
                'Raw string encrypt requires an open SealedSecret '
                '(kind: SealedSecret).\n\n'
                'For a plain Secret template, clear the selection and seal the '
                'whole file instead.'
            )
        return None

    def _precheck_full_file_seal(self):
        file_name = getattr(self, "_file_name", None)
        if file_name is None and self.view is not None:
            file_name = self.view.file_name()
            self._file_name = file_name
        if not file_name:
            return (
                'Save the file first (needed to derive *-sealedsecret.yaml output path), '
                'or select text for raw encrypt inside a SealedSecret.'
            )
        content = self.view.substr(sublime.Region(0, self.view.size()))
        if not content.strip():
            return 'File is empty — nothing to seal'
        if looks_like_sealed_secret(content):
            return (
                'This file looks like a SealedSecret already. '
                'Refusing to seal it again (would nest ciphertext).\n\n'
                'To encrypt individual values, select the plaintext string and '
                'use raw encrypt inside this SealedSecret.'
            )
        if not looks_like_plain_secret(content):
            return (
                'Full-file seal expects a Kubernetes Secret manifest (kind: Secret). '
                'Select a string inside a SealedSecret for raw encrypt, or open a Secret template.'
            )
        if not secret_has_payload(content):
            return (
                'Secret has no non-empty data / stringData values — nothing useful to seal.\n'
                'Add secret keys first, then Encrypt again.'
            )
        if len(content.encode('utf-8')) > _MAX_KUBESEAL_INPUT_BYTES:
            return (
                'Secret template is too large (max {} bytes). '
                'Split secrets or seal offline.'
            ).format(_MAX_KUBESEAL_INPUT_BYTES)
        output_path = derive_sealedsecret_output_path(file_name)
        if os.path.abspath(output_path) == os.path.abspath(file_name):
            return 'Refusing to overwrite the source file with sealed output'
        out_dir = os.path.dirname(output_path) or '.'
        if not os.path.isdir(out_dir):
            return 'Output directory does not exist:\n{}'.format(out_dir)
        if not os.access(out_dir, os.W_OK):
            return 'Output directory is not writable:\n{}'.format(out_dir)
        return None

    def _on_stage_picked_for_encrypt(self, stage):
        self.stage = stage
        self.settings['cert_path'] = stage['cert_path']
        self.show_status('Using stage: {}'.format(stage['name']))

        if self._mode == 'file':
            self._seal_entire_file()
            return

        namespace, secret_name = self.extract_metadata_from_file()
        if namespace and secret_name:
            if self.settings.get('validate_k8s_names', True):
                for field, val in (('namespace', namespace), ('secret name', secret_name)):
                    err = validate_k8s_dns_label(val, field)
                    if err:
                        self.show_error(err)
                        return
            self.show_status(
                'Using metadata from file: namespace={}, name={}'.format(namespace, secret_name)
            )
            self.proceed_with_encryption(namespace, secret_name)
        else:
            self.show_status('No metadata found in file, prompting for values...')
            window = self.view.window()
            if not window:
                self.show_error('No active window')
                return
            self.window = window
            window.show_input_panel(
                'Enter namespace:',
                self.settings.get('default_namespace', 'default'),
                self.on_namespace_entered,
                None,
                None
            )

    def _seal_entire_file(self):
        file_name = getattr(self, "_file_name", None) or (
            self.view.file_name() if self.view else None
        )
        output_path = derive_sealedsecret_output_path(file_name)
        file_content = self.view.substr(sublime.Region(0, self.view.size()))

        if self.view.is_dirty():
            self.show_status('Buffer has unsaved changes — sealing current buffer contents')

        def go():
            self._seal_entire_file_run(file_content, output_path)

        if (
            os.path.exists(output_path)
            and self.settings.get('confirm_overwrite_sealed', True)
        ):
            self.confirm_quick(
                'Overwrite {} (backup previous)'.format(os.path.basename(output_path)),
                'Existing sealed secret will be renamed to .bckp-* then replaced',
                go,
            )
            return
        go()

    def _seal_entire_file_run(self, file_content, output_path):
        kubeseal = self.resolve_kubeseal()
        if not kubeseal:
            self.show_error(
                'kubeseal binary not found. Install it or set kubeseal_path in settings.'
            )
            return

        if not self._begin_operation():
            return

        self.show_status(
            '[{}] Sealing entire file → {}'.format(
                self.stage['name'], os.path.basename(output_path)
            )
        )
        _ktc.spawn_daemon(
            self._seal_file_async, kubeseal, file_content, output_path
        )

    def _seal_file_async(self, kubeseal, file_content, output_path):
        try:
            cmd = [kubeseal] + build_full_seal_command(self.settings['cert_path'])[1:]
            sealed, error, returncode = self._run_kubeseal(
                cmd, file_content, self.settings.get('timeout', 30)
            )

            def done():
                try:
                    if returncode != 0:
                        self.show_error('Full-file seal failed: {}'.format(error or 'unknown error'))
                        return
                    if not sealed.strip() or 'SealedSecret' not in sealed:
                        self.show_error(
                            'kubeseal returned unexpected output (no SealedSecret). '
                            'stderr: {}'.format(error or '(empty)')
                        )
                        return
                    backup_path = None
                    try:
                        if os.path.exists(output_path):
                            backup_path = backup_existing_sealedsecret(output_path)
                        write_sealed_output_atomic(output_path, sealed)
                    except Exception as e:
                        self.show_error(
                            'Failed to write {}{}: {}'.format(
                                output_path,
                                ' (after backup {})'.format(backup_path) if backup_path else '',
                                e,
                            )
                        )
                        return

                    window = self.view.window()
                    if window:
                        window.open_file(output_path)
                    self._maybe_offer_compare_after_seal()
                    if backup_path:
                        self.show_status(
                            '[{}] Sealed secret written: {} (previous → {})'.format(
                                self.stage['name'],
                                os.path.basename(output_path),
                                os.path.basename(backup_path),
                            )
                        )
                    else:
                        self.show_status(
                            '[{}] Sealed secret written: {}'.format(
                                self.stage['name'], output_path
                            )
                        )
                finally:
                    self._end_operation()

            sublime.set_timeout(done, 0)

        except Exception as e:
            def fail():
                self._end_operation()
                self.show_error('Full-file seal failed: {}'.format(str(e)))
            sublime.set_timeout(fail, 0)


    def _maybe_offer_compare_after_seal(self):
        if not self.settings.get('offer_compare_after_seal', True):
            return
        window = self.view.window() if self.view else None
        if window is None:
            return

        def run_compare():
            window.run_command('kubetools_compare_state')

        self.confirm_quick(
            'Compare sealed file to live cluster',
            'Runs .kubetools Compare State on the sealed secret just written',
            run_compare,
        )

    def on_namespace_entered(self, namespace):
        self.namespace = (namespace or '').strip()
        if not self.namespace:
            self.show_error('Namespace cannot be empty')
            return
        if self.settings.get('validate_k8s_names', True):
            err = validate_k8s_dns_label(self.namespace, 'namespace')
            if err:
                self.show_error(err)
                return
        self.window.show_input_panel(
            'Enter secret name:',
            self.settings.get('default_secret_name', 'mysecret'),
            self.on_secret_name_entered,
            None,
            None
        )

    def on_secret_name_entered(self, secret_name):
        self.secret_name = (secret_name or '').strip()
        if not self.secret_name:
            self.show_error('Secret name cannot be empty')
            return
        if self.settings.get('validate_k8s_names', True):
            err = validate_k8s_dns_label(self.secret_name, 'secret name')
            if err:
                self.show_error(err)
                return
        self.proceed_with_encryption(self.namespace, self.secret_name)

    def proceed_with_encryption(self, namespace, secret_name):
        max_sel = self.settings.get('max_selections', 10)
        candidates = []
        reseal_count = 0
        for region in self.view.sel():
            if region.empty():
                continue
            text = self.view.substr(region)
            if not text:
                continue
            looks_sealed = looks_like_already_encrypted_blob(text)
            if looks_sealed:
                reseal_count += 1
            candidates.append({
                'start': region.begin(),
                'end': region.end(),
                'expected': text,
                'looks_sealed': looks_sealed,
            })
            if len(candidates) >= max_sel:
                break

        total_nonempty = sum(
            1 for r in self.view.sel() if (not r.empty()) and self.view.substr(r)
        )
        if total_nonempty > max_sel:
            self.show_status(
                'Encrypting first {} of {} selections (max_selections)'.format(
                    max_sel, total_nonempty
                )
            )

        if not candidates:
            self.show_error('No non-empty selection to encrypt')
            return

        need_reseal_confirm = (
            reseal_count > 0
            and self.settings.get('confirm_reseal_selection', True)
        )
        if need_reseal_confirm:
            self.confirm_quick(
                'Encrypt anyway ({} selection(s) look sealed)'.format(reseal_count),
                'Re-encrypting nest/corrupt existing kubeseal --raw ciphertext',
                lambda: self._encrypt_candidates(candidates, namespace, secret_name),
            )
            return

        self._encrypt_candidates(candidates, namespace, secret_name)

    def _encrypt_candidates(self, candidates, namespace, secret_name):
        self.regions = [
            {
                'start': c['start'],
                'end': c['end'],
                'expected': c['expected'],
            }
            for c in candidates
        ]

        kubeseal = self.resolve_kubeseal()
        if not kubeseal:
            self.show_error(
                'kubeseal binary not found. Install it or set kubeseal_path in settings.'
            )
            return

        if not self._begin_operation():
            return

        self.show_status('[{}] Encrypting...'.format(self.stage['name']))
        _ktc.spawn_daemon(
            self._encrypt_all_async, kubeseal, namespace, secret_name
        )

    def _encrypt_all_async(self, kubeseal, namespace, secret_name):
        """Encrypt all selections off-UI, then apply replacements in one edit."""
        results = []
        try:
            for item in self.regions:
                cmd = [
                    kubeseal, '--raw',
                    '--cert', self.settings['cert_path'],
                    '--namespace', namespace,
                    '--name', secret_name,
                ]
                encrypted, error, returncode = self._run_kubeseal(
                    cmd, item['expected'], self.settings.get('timeout', 30)
                )
                if returncode != 0:
                    results.append({'error': error or 'encryption failed'})
                    break
                results.append({
                    'start': item['start'],
                    'end': item['end'],
                    'expected': item['expected'],
                    'new_text': encrypted.strip(),
                })

            def done():
                try:
                    if results and 'error' in results[-1] and 'new_text' not in results[-1]:
                        self.show_error('Encryption failed: {}'.format(results[-1]['error']))
                        return
                    # Apply reverse-order so offsets stay valid (ST edit best practice)
                    self.view.run_command('kubetools_seal_replace_regions', {
                        'replacements': [r for r in results if 'new_text' in r]
                    })
                    self.show_status(
                        '[{}] Encrypted {} selection(s)'.format(
                            self.stage['name'], len(results)
                        )
                    )
                finally:
                    self._end_operation()

            sublime.set_timeout(done, 0)
        except Exception as e:
            def fail():
                self._end_operation()
                self.show_error('Encryption failed: {}'.format(str(e)))
            sublime.set_timeout(fail, 0)


# ---------------------------------------------------------------------------
# Decrypt
# ---------------------------------------------------------------------------

class KubetoolsUnsealCommand(_SealBase):
    """Decrypt sealed secret using private key (offline)."""

    def is_enabled(self):
        return _view_can_unseal(self.view)

    def is_visible(self):
        return self.is_enabled()

    def run(self, edit):
        self.settings = self.get_settings()
        self._file_name = self.view.file_name() if self.view else None

        selected_text = ''
        for region in self.view.sel():
            if not region.empty():
                selected_text = self.view.substr(region)
                break

        if not selected_text.strip():
            self.show_error('Please select encrypted text to decrypt')
            return

        # Safety: selection should look like ciphertext, not a whole YAML doc by mistake
        if '\n' in selected_text.strip() and 'kind:' in selected_text:
            self.selected_encrypted_text = selected_text.strip()
            self.confirm_quick(
                'Continue decrypt (selection looks like YAML)',
                'Raw decrypt expects a single ciphertext blob, not a full document',
                self._start_decrypt_after_confirm,
            )
            return

        # Short / non-Ag blobs are usually plaintext — confirm before using private key
        if not looks_like_already_encrypted_blob(selected_text.strip()):
            self.selected_encrypted_text = selected_text.strip()
            self.confirm_quick(
                'Continue decrypt (selection does not look like kubeseal ciphertext)',
                'Expected long base64 starting with Ag — wrong selection wastes private-key use',
                self._start_decrypt_after_confirm,
            )
            return

        self.selected_encrypted_text = selected_text.strip()
        self._start_decrypt_after_confirm()

    def _start_decrypt_after_confirm(self):
        self.pick_stage(
            need_cert=False,
            need_private_key=True,
            on_picked=self._on_stage_picked_for_decrypt
        )

    def _on_stage_picked_for_decrypt(self, stage):
        self.stage = stage
        self.settings['private_key_path'] = stage['private_key_path']
        self.show_status('Using stage: {}'.format(stage['name']))

        if stage_name_looks_prod(stage.get('name'), self.settings.get('dangerous_context_patterns')):
            self.confirm_quick(
                "Decrypt with '{}' private key".format(stage['name']),
                'Stage name looks production — plaintext will appear in a scratch tab',
                self._continue_decrypt_after_prod_confirm,
            )
            return
        self._continue_decrypt_after_prod_confirm()

    def _continue_decrypt_after_prod_confirm(self):
        namespace, secret_name = self.extract_metadata_from_file()
        if namespace and secret_name:
            if self.settings.get('validate_k8s_names', True):
                for field, val in (('namespace', namespace), ('secret name', secret_name)):
                    err = validate_k8s_dns_label(val, field)
                    if err:
                        self.show_error(err)
                        return
            self.show_status(
                'Using metadata from file: namespace={}, name={}'.format(namespace, secret_name)
            )
            self.proceed_with_decryption(namespace, secret_name)
        else:
            window = self.view.window()
            if not window:
                self.show_error('No active window')
                return
            self.window = window
            window.show_input_panel(
                'Enter namespace (used during encryption):',
                self.settings.get('default_namespace', 'default'),
                self.on_decrypt_namespace_entered,
                None,
                None
            )

    def on_decrypt_namespace_entered(self, namespace):
        self.namespace = (namespace or '').strip()
        if not self.namespace:
            self.show_error('Namespace cannot be empty')
            return
        if self.settings.get('validate_k8s_names', True):
            err = validate_k8s_dns_label(self.namespace, 'namespace')
            if err:
                self.show_error(err)
                return
        self.window.show_input_panel(
            'Enter secret name (used during encryption):',
            self.settings.get('default_secret_name', 'mysecret'),
            self.on_decrypt_secret_name_entered,
            None,
            None
        )

    def on_decrypt_secret_name_entered(self, secret_name):
        self.secret_name = (secret_name or '').strip()
        if not self.secret_name:
            self.show_error('Secret name cannot be empty')
            return
        if self.settings.get('validate_k8s_names', True):
            err = validate_k8s_dns_label(self.secret_name, 'secret name')
            if err:
                self.show_error(err)
                return
        self.proceed_with_decryption(self.namespace, self.secret_name)

    def proceed_with_decryption(self, namespace, secret_name):
        kubeseal = self.resolve_kubeseal()
        if not kubeseal:
            self.show_error(
                'kubeseal binary not found. Install it or set kubeseal_path in settings.'
            )
            return
        if not self._begin_operation():
            return

        self.show_status('[{}] Decrypting...'.format(self.stage['name']))
        _ktc.spawn_daemon(
            self._decrypt_async,
            kubeseal,
            self.selected_encrypted_text,
            namespace,
            secret_name,
        )

    def _decrypt_async(self, kubeseal, encrypted_text, namespace, secret_name):
        try:
            sealed_secret_yaml = self._create_sealed_secret_yaml(
                encrypted_text, namespace, secret_name
            )
            cmd = [
                kubeseal,
                '--recovery-unseal',
                '--recovery-private-key', self.settings['private_key_path'],
            ]
            decrypted_output, error, returncode = self._run_kubeseal(
                cmd, sealed_secret_yaml, self.settings.get('timeout', 30)
            )

            def done():
                try:
                    self._handle_decrypt_result(
                        decrypted_output, error, returncode, namespace, secret_name
                    )
                finally:
                    self._end_operation()

            sublime.set_timeout(done, 0)
        except Exception as e:
            def fail():
                self._end_operation()
                self.show_error('Decryption failed: {}'.format(str(e)))
            sublime.set_timeout(fail, 0)

    def _create_sealed_secret_yaml(self, encrypted_text, namespace, secret_name):
        # Guard against YAML injection breaking the wrapper document
        if re.search(r'[\n\r]', encrypted_text):
            raise ValueError('Encrypted blob must be a single line')
        yaml_content = (
            'apiVersion: bitnami.com/v1alpha1\n'
            'kind: SealedSecret\n'
            'metadata:\n'
            '  name: {name}\n'
            '  namespace: {namespace}\n'
            'spec:\n'
            '  encryptedData:\n'
            '    data: {encrypted_data}\n'
            '  template:\n'
            '    metadata:\n'
            '      name: {name}\n'
            '      namespace: {namespace}\n'
        ).format(
            encrypted_data=encrypted_text,
            name=secret_name,
            namespace=namespace,
        )
        return yaml_content

    def _handle_decrypt_result(self, decrypted_output, error, return_code, namespace, secret_name):
        if return_code != 0:
            self.show_error('Decryption failed: {}'.format(error or 'unknown error'))
            return
        if not decrypted_output.strip():
            self.show_error('Decryption returned empty output')
            return

        content = decrypted_output
        if self.settings.get('decode_secret_data', True):
            content = decode_secret_data_fields(decrypted_output)

        # Mark scratch buffer so user is less likely to save plaintext into git by accident
        if self.settings.get('decrypt_output') == 'popup':
            self._show_in_popup(content, namespace, secret_name)
        else:
            self._show_in_new_tab(content, namespace, secret_name)

        decoded_note = (
            'data fields base64-decoded' if self.settings.get('decode_secret_data', True)
            else 'raw Secret'
        )
        self.show_status(
            '[{}] Decryption completed ({})'.format(self.stage['name'], decoded_note)
        )

    def _show_in_new_tab(self, content, namespace, secret_name):
        window = self.view.window()
        if not window:
            self.show_error('No active window')
            return
        new_view = window.new_file()
        stage = self.stage['name'] if self.stage else '?'
        new_view.set_name('DO-NOT-COMMIT decrypted [{}]: {}/{}'.format(stage, namespace, secret_name))
        new_view.set_scratch(True)  # avoid accidental save prompts / git commits
        if content.lstrip().startswith('{'):
            try:
                new_view.set_syntax_file('Packages/JSON/JSON.sublime-syntax')
            except Exception:
                try:
                    new_view.set_syntax_file('Packages/JavaScript/JSON.sublime-syntax')
                except Exception:
                    new_view.set_syntax_file('Packages/YAML/YAML.sublime-syntax')
        else:
            new_view.set_syntax_file('Packages/YAML/YAML.sublime-syntax')
        new_view.run_command('kubetools_seal_insert_content', {'content': content})

    def _show_in_popup(self, content, namespace, secret_name):
        # Cap popup size — huge secrets freeze UI
        display = content
        max_chars = 8000
        if len(display) > max_chars:
            display = display[:max_chars] + '\n… [truncated]'
        popup_content = (
            '<body>'
            '<style>'
            'body {{ font-family: monospace; font-size: 12px; }}'
            '.header {{ color: #569cd6; font-weight: bold; margin-bottom: 10px; }}'
            '.content {{ background: #1e1e1e; color: #d4d4d4; padding: 10px; white-space: pre-wrap; }}'
            '</style>'
            '<div class="header">Decrypted Secret: {}/{}</div>'
            '<div class="content">{}</div>'
            '</body>'
        ).format(
            namespace,
            secret_name,
            display.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;'),
        )
        self.view.show_popup(
            popup_content,
            flags=sublime.HIDE_ON_MOUSE_MOVE_AWAY,
            max_width=800,
            max_height=600,
        )


# ---------------------------------------------------------------------------
# Helper text commands (required: only TextCommand may own Edit objects)
# ---------------------------------------------------------------------------

class KubetoolsSealReplaceTextCommand(sublime_plugin.TextCommand):
    def run(self, edit, region_start, region_end, new_text):
        region = sublime.Region(region_start, region_end)
        self.view.replace(edit, region, new_text)


class KubetoolsSealReplaceRegionsCommand(sublime_plugin.TextCommand):
    """
    Apply multiple replacements safely:
    - reverse order by start offset (length changes don't invalidate earlier regions)
    - skip / abort if buffer text no longer matches expected snapshot
    """

    def run(self, edit, replacements):
        if not replacements:
            return
        ordered = sorted(replacements, key=lambda r: r['start'], reverse=True)
        skipped = 0
        for item in ordered:
            region = sublime.Region(item['start'], item['end'])
            current = self.view.substr(region)
            if current != item.get('expected', current):
                skipped += 1
                continue
            self.view.replace(edit, region, item['new_text'])
        if skipped:
            sublime.error_message(
                'Kubetools: {} selection(s) were skipped because the buffer changed '
                'during encryption. Re-select and try again.'.format(skipped)
            )


class KubetoolsSealInsertContentCommand(sublime_plugin.TextCommand):
    def run(self, edit, content):
        self.view.insert(edit, 0, content)
        self.view.set_read_only(True)


# ---------------------------------------------------------------------------
# Config / settings commands
# ---------------------------------------------------------------------------

class KubetoolsSealValidateConfigCommand(sublime_plugin.ApplicationCommand):
    def run(self):
        sublime.status_message("Kubetools: validating seal config...")
        sublime.set_timeout_async(self._validate_async, 0)

    def _validate_async(self):
        settings = load_seal_settings_obj()
        stages = normalize_stages(settings.get('stages', []))
        for stage in stages:
            stage['cert_path'] = _expand_path(stage.get('cert_path', ''))
            stage['private_key_path'] = _expand_path(stage.get('private_key_path', ''))

        problems = []
        kubeseal_path = _expand_path(settings.get('kubeseal_path', '') or '')
        binary = kubeseal_path if kubeseal_path and os.path.isfile(kubeseal_path) else find_kubeseal_binary()
        version = None
        if not binary:
            problems.append('kubeseal binary not found in PATH (set kubeseal_path if needed)')
        else:
            try:
                err_path = _ktc.validate_binary_path(binary, 'kubeseal')
                if err_path:
                    problems.append(err_path)
                else:
                    code, out, err = _ktc.run_subprocess(
                        [binary, '--version'], timeout=10, label='kubeseal'
                    )
                    version = (out or err or '').strip() or 'unknown'
                    if code != 0 and not version:
                        problems.append('kubeseal --version failed')
            except Exception as e:
                problems.append('Failed to run kubeseal: {}'.format(e))

        if not stages:
            legacy_cert = _expand_path(settings.get('cert_path', '') or '')
            legacy_key = _expand_path(settings.get('private_key_path', '') or '')
            if not legacy_cert and not legacy_key:
                problems.append('No stages configured and no legacy cert_path/private_key_path')
            else:
                if legacy_cert and not os.path.isfile(legacy_cert):
                    problems.append('legacy cert_path not found: {}'.format(legacy_cert))
                if legacy_key and not os.path.isfile(legacy_key):
                    problems.append('legacy private_key_path not found: {}'.format(legacy_key))
        else:
            for s in stages:
                if s.get('cert_path') and not os.path.isfile(s['cert_path']):
                    problems.append('[{}] cert missing: {}'.format(s['name'], s['cert_path']))
                if s.get('private_key_path') and not os.path.isfile(s['private_key_path']):
                    problems.append(
                        '[{}] private key missing: {}'.format(s['name'], s['private_key_path'])
                    )
                if not s.get('cert_path') and not s.get('private_key_path'):
                    problems.append('[{}] has neither cert_path nor private_key_path'.format(s['name']))

        if problems:
            msg = 'Kubetools seal configuration problems:\n- ' + '\n- '.join(problems)
            sublime.set_timeout(lambda m=msg: sublime.error_message(m), 0)
        else:
            stage_lines = []
            for s in stages:
                stage_lines.append(
                    '  {}  cert={}  key={}'.format(
                        s['name'],
                        'yes' if s.get('cert_path') and os.path.isfile(s['cert_path']) else 'no',
                        'yes' if s.get('private_key_path') and os.path.isfile(s['private_key_path']) else 'no',
                    )
                )
            ok = 'Kubetools seal configuration OK.\n\nkubeseal: {}\n\nStages:\n{}'.format(
                version or binary,
                '\n'.join(stage_lines) if stage_lines else '(legacy single key)',
            )
            sublime.set_timeout(lambda m=ok: sublime.message_dialog(m), 0)


class KubetoolsOpenSealSettingsCommand(sublime_plugin.WindowCommand):
    """Alias → Kubetools settings (stages live there now)."""

    def run(self):
        self.window.run_command("kubetools_open_settings")
