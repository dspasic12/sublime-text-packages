import sublime
import sublime_plugin
import subprocess
import threading
import os
import re

class KubesealCommand(sublime_plugin.TextCommand):
    """Base class for kubeseal operations"""

    def get_settings(self):
        """Load plugin settings with defaults"""
        settings = sublime.load_settings("Kubeseal.sublime-settings")
        return {
            'cert_path': settings.get('cert_path', ''),
            'private_key_path': settings.get('private_key_path', ''),
            'timeout': settings.get('timeout', 30),
            'decrypt_output': settings.get('decrypt_output', 'new_tab')  # 'new_tab' or 'popup'
        }

    def show_error(self, message):
        """Display error message to user"""
        sublime.error_message("Kubeseal Error: {}".format(message))

    def show_status(self, message):
        """Show status message"""
        sublime.status_message("Kubeseal: {}".format(message))

    def extract_metadata_from_file(self):
        """Extract namespace and secret name from current file's YAML metadata"""
        try:
            file_content = self.view.substr(sublime.Region(0, self.view.size()))

            namespace = None
            secret_name = None

            namespace_pattern = r'^\s*namespace:\s*([^\s\n]+)'
            name_pattern = r'^\s*name:\s*([^\s\n]+)'

            in_metadata = False
            lines = file_content.split('\n')

            for line in lines:
                if re.match(r'^\s*metadata:\s*$', line):
                    in_metadata = True
                    continue

                if in_metadata and re.match(r'^[a-zA-Z]', line):
                    in_metadata = False

                if in_metadata:
                    namespace_match = re.match(namespace_pattern, line)
                    if namespace_match:
                        namespace = namespace_match.group(1).strip()

                    name_match = re.match(name_pattern, line)
                    if name_match:
                        secret_name = name_match.group(1).strip()

            return namespace, secret_name

        except Exception as e:
            return None, None

class KubesealEncryptCommand(KubesealCommand):
    """Encrypt selected text using kubeseal raw mode"""

    def run(self, edit):
        settings = self.get_settings()

        if not settings['cert_path']:
            self.show_error("Certificate path not configured. Please set 'cert_path' in settings.")
            return

        if not os.path.exists(settings['cert_path']):
            self.show_error("Certificate file not found: {}".format(settings['cert_path']))
            return

        # Check if text is selected
        has_selection = False
        for region in self.view.sel():
            if not region.empty():
                has_selection = True
                break

        if not has_selection:
            self.show_error("Please select text to encrypt")
            return

        self.settings = settings

        # Try to extract metadata from file
        namespace, secret_name = self.extract_metadata_from_file()

        if namespace and secret_name:
            self.show_status("Using metadata from file: namespace={}, name={}".format(namespace, secret_name))
            self.proceed_with_encryption(namespace, secret_name)
        else:
            self.show_status("No metadata found in file, prompting for values...")
            self.window = self.view.window()
            self.window.show_input_panel(
                "Enter namespace:",
                "default",
                self.on_namespace_entered,
                None,
                None
            )

    def on_namespace_entered(self, namespace):
        """Called when user enters namespace"""
        self.namespace = namespace.strip()
        if not self.namespace:
            self.show_error("Namespace cannot be empty")
            return

        self.window.show_input_panel(
            "Enter secret name:",
            "mysecret",
            self.on_secret_name_entered,
            None,
            None
        )

    def on_secret_name_entered(self, secret_name):
        """Called when user enters secret name"""
        self.secret_name = secret_name.strip()
        if not self.secret_name:
            self.show_error("Secret name cannot be empty")
            return

        self.proceed_with_encryption(self.namespace, self.secret_name)

    def proceed_with_encryption(self, namespace, secret_name):
        """Proceed with encryption using provided namespace and secret name"""
        self.show_status("Encrypting...")

        # Get all selected regions
        self.regions = []
        for region in self.view.sel():
            if not region.empty():
                self.regions.append({
                    'region': region,
                    'text': self.view.substr(region)
                })

        # Start encryption for first region
        if self.regions:
            threading.Thread(
                target=self._encrypt_async,
                args=(self.regions[0]['text'], namespace, secret_name, 0)
            ).start()

    def _encrypt_async(self, text, namespace, secret_name, region_index):
        """Perform encryption in background thread"""
        try:
            cmd = [
                'kubeseal', '--raw', '--cert', self.settings['cert_path'],
                '--namespace', namespace, '--name', secret_name
            ]

            process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True
            )

            encrypted_text, error = process.communicate(input=text)

            sublime.set_timeout(
                lambda: self._handle_encrypt_result(encrypted_text, error, process.returncode, region_index),
                0
            )

        except Exception as e:
            sublime.set_timeout(
                lambda: self.show_error("Encryption failed: {}".format(str(e))), 
                0
            )

    def _handle_encrypt_result(self, encrypted_text, error, return_code, region_index):
        """Handle encryption result in main thread"""
        if return_code == 0:
            self.view.run_command('kubeseal_replace_text', {
                'region_start': self.regions[region_index]['region'].begin(),
                'region_end': self.regions[region_index]['region'].end(),
                'new_text': encrypted_text.strip()
            })
            self.show_status("Text encrypted successfully")
        else:
            self.show_error("Encryption failed: {}".format(error))

class KubesealDecryptCommand(KubesealCommand):
    """Decrypt sealed secret using private key (offline) - shows result in new tab"""

    def run(self, edit):
        settings = self.get_settings()

        if not settings['private_key_path']:
            self.show_error("Private key path not configured. Please set 'private_key_path' in settings.")
            return

        if not os.path.exists(settings['private_key_path']):
            self.show_error("Private key file not found: {}".format(settings['private_key_path']))
            return

        # Check if text is selected
        has_selection = False
        selected_text = ""
        for region in self.view.sel():
            if not region.empty():
                has_selection = True
                selected_text = self.view.substr(region)
                break  # Just use the first selection

        if not has_selection:
            self.show_error("Please select encrypted text to decrypt")
            return

        self.settings = settings
        self.selected_encrypted_text = selected_text

        # Try to extract metadata from file
        namespace, secret_name = self.extract_metadata_from_file()

        if namespace and secret_name:
            self.show_status("Using metadata from file: namespace={}, name={}".format(namespace, secret_name))
            self.proceed_with_decryption(namespace, secret_name)
        else:
            self.show_status("No metadata found in file, prompting for values...")
            self.window = self.view.window()
            self.window.show_input_panel(
                "Enter namespace (used during encryption):",
                "default",
                self.on_decrypt_namespace_entered,
                None,
                None
            )

    def on_decrypt_namespace_entered(self, namespace):
        """Called when user enters namespace for decryption"""
        self.namespace = namespace.strip()
        if not self.namespace:
            self.show_error("Namespace cannot be empty")
            return

        self.window.show_input_panel(
            "Enter secret name (used during encryption):",
            "mysecret",
            self.on_decrypt_secret_name_entered,
            None,
            None
        )

    def on_decrypt_secret_name_entered(self, secret_name):
        """Called when user enters secret name for decryption"""
        self.secret_name = secret_name.strip()
        if not self.secret_name:
            self.show_error("Secret name cannot be empty")
            return

        self.proceed_with_decryption(self.namespace, self.secret_name)

    def proceed_with_decryption(self, namespace, secret_name):
        """Proceed with decryption using provided namespace and secret name"""
        self.show_status("Decrypting...")

        threading.Thread(
            target=self._decrypt_async,
            args=(self.selected_encrypted_text, namespace, secret_name)
        ).start()

    def _decrypt_async(self, encrypted_text, namespace, secret_name):
        """Perform offline decryption in background thread"""
        try:
            sealed_secret_yaml = self._create_sealed_secret_yaml(encrypted_text.strip(), namespace, secret_name)

            cmd = [
                'kubeseal',
                '--recovery-unseal',
                '--recovery-private-key', self.settings['private_key_path']
            ]

            process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True
            )

            decrypted_output, error = process.communicate(input=sealed_secret_yaml)

            sublime.set_timeout(
                lambda: self._handle_decrypt_result(decrypted_output, error, process.returncode, namespace, secret_name),
                0
            )

        except Exception as e:
            sublime.set_timeout(
                lambda: self.show_error("Decryption failed: {}".format(str(e))), 
                0
            )

    def _create_sealed_secret_yaml(self, encrypted_text, namespace, secret_name):
        """Create minimal SealedSecret YAML from encrypted text"""
        yaml_content = """apiVersion: bitnami.com/v1alpha1
kind: SealedSecret
metadata:
  name: {name}
  namespace: {namespace}
spec:
  encryptedData:
    data: {encrypted_data}
  template:
    metadata:
      name: {name}
      namespace: {namespace}
""".format(
            encrypted_data=encrypted_text,
            name=secret_name,
            namespace=namespace
        )
        return yaml_content

    def _handle_decrypt_result(self, decrypted_output, error, return_code, namespace, secret_name):
        """Handle decryption result in main thread"""
        if return_code == 0:
            # Show result based on settings
            if self.settings['decrypt_output'] == 'popup':
                self._show_in_popup(decrypted_output, namespace, secret_name)
            else:
                self._show_in_new_tab(decrypted_output, namespace, secret_name)

            self.show_status("Decryption completed - check new tab/popup")
        else:
            self.show_error("Decryption failed: {}".format(error))

    def _show_in_new_tab(self, content, namespace, secret_name):
        """Show decrypted content in a new tab"""
        new_view = self.view.window().new_file()
        new_view.set_name("Decrypted Secret: {}/{}".format(namespace, secret_name))
        new_view.set_syntax_file("Packages/YAML/YAML.sublime-syntax")
        new_view.run_command('kubeseal_insert_content', {'content': content})

    def _show_in_popup(self, content, namespace, secret_name):
        """Show decrypted content in a popup"""
        # Format content for popup display
        popup_content = """
        <body>
        <style>
        body { font-family: monospace; font-size: 12px; }
        .header { color: #569cd6; font-weight: bold; margin-bottom: 10px; }
        .content { background: #1e1e1e; color: #d4d4d4; padding: 10px; white-space: pre-wrap; }
        </style>
        <div class="header">Decrypted Secret: {}/{}</div>
        <div class="content">{}</div>
        </body>
        """.format(namespace, secret_name, content.replace('<', '&lt;').replace('>', '&gt;'))

        self.view.show_popup(
            popup_content,
            flags=sublime.HIDE_ON_MOUSE_MOVE_AWAY,
            max_width=800,
            max_height=600
        )

# Helper command for text replacement
class KubesealReplaceTextCommand(sublime_plugin.TextCommand):
    """Helper command to replace text with a new edit context"""

    def run(self, edit, region_start, region_end, new_text):
        region = sublime.Region(region_start, region_end)
        self.view.replace(edit, region, new_text)

# Helper command for inserting content in new tab
class KubesealInsertContentCommand(sublime_plugin.TextCommand):
    """Helper command to insert content in a new tab"""

    def run(self, edit, content):
        self.view.insert(edit, 0, content)

class KubesealOpenSettingsCommand(sublime_plugin.ApplicationCommand):
    """Open Kubeseal settings file"""

    def run(self):
        sublime.run_command('open_file', {
            'file': '${packages}/User/Kubeseal.sublime-settings'
        })
