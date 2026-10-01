# Kubeseal package — DEPRECATED shim
#
# Seal/unseal now lives in Kubetools (.kubetools - Seal / Unseal).
# These commands forward so existing keymaps / muscle memory keep working
# while both packages are installed. Prefer installing only Kubetools.

import sublime
import sublime_plugin


def _warn_once():
    # Status only — avoid modal spam
    sublime.status_message(
        "Kubeseal package is deprecated — use .kubetools Seal/Unseal "
        "(settings: Kubetools stages)"
    )


class KubesealEncryptCommand(sublime_plugin.TextCommand):
    def run(self, edit):
        _warn_once()
        self.view.run_command("kubetools_seal")


class KubesealDecryptCommand(sublime_plugin.TextCommand):
    def run(self, edit):
        _warn_once()
        self.view.run_command("kubetools_unseal")


class KubesealValidateConfigCommand(sublime_plugin.ApplicationCommand):
    def run(self):
        _warn_once()
        sublime.run_command("kubetools_seal_validate_config")


class KubesealOpenSettingsCommand(sublime_plugin.ApplicationCommand):
    def run(self):
        _warn_once()
        sublime.run_command("edit_settings", {
            "base_file": "${packages}/kubetools/Kubetools.sublime-settings",
            "default": (
                "{\n"
                '\t"stages": [\n'
                "\t\t{\n"
                '\t\t\t"name": "dev",\n'
                '\t\t\t"cert_path": "${home}/path/to/sealed-secrets/pub.pem",\n'
                '\t\t\t"private_key_path": "${home}/path/to/sealed-secrets/priv.key"\n'
                "\t\t}\n"
                "\t]\n"
                "}\n"
            ),
        })
