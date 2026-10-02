# Sublime Text Packages

This repository contains custom Sublime Text packages.

## Available Packages

### Kubetools (`kubetools/`) — recommended for Kubernetes
Compare open YAML to live cluster state, apply / dry-run, and **seal / unseal**
(kubeseal). One package for kubectl + sealed secrets.

### Kubeseal (deprecated shim)
Forwards to Kubetools Seal/Unseal. Prefer Kubetools only; migrate `stages` into
`Kubetools.sublime-settings`.

### Kubeapply (deprecated)
Superseded by Kubetools — do not install alongside kubetools.

### ST4Notes (`notes/`)
Daily notes file + optional YouTrack / GitLab integration.

### User manual

**[docs/USER-MANUAL.md](docs/USER-MANUAL.md)** — settings layout, seal stages vs
`issue_stages`, gitops checklist, first-time setup. Read this before copying
cluster names into ST4Notes settings.

## Installation

### Using Package Control

1. Install Package Control if needed
2. **Package Control: Add Repository** →
   `https://raw.githubusercontent.com/dspasic12/sublime-text-packages/main/repository.json`
3. **Install Package** → **Kubetools** and/or **ST4Notes**

### Manual / symlink (dev)

```bash
ln -s "/path/to/sublime-text-packages/kubetools" \
  "$HOME/Library/Application Support/Sublime Text/Packages/kubetools"
```

## Usage

### Kubetools

1. Open Kubernetes YAML
2. Cmd+Shift+P → `.kube` → Compare / Apply / Dry-Run / Seal / Unseal
3. **User/Kubetools.sublime-settings** → `stages` (one entry per cluster `pub.pem`)
4. Keep **User/Kubeseal.sublime-settings** `stages` empty

### ST4Notes

1. **User/ST4Notes.sublime-settings** — tokens, `default_project`, optional `issue_stages`
2. Use workflow labels for `issue_stages` (or `[]`), not cluster names — see user manual
3. `notes_file` must stay under `notes_path_jail` (default `$HOME`)

## Requirements

- Sublime Text 4 Build 4205+ (plugin host **Python 3.14**)
- `kubectl` + optional `kubeseal` for Kubetools
- Optional YouTrack / GitLab tokens for ST4Notes

## Issues

Report issues at: [GitHub Issues](https://github.com/dspasic12/sublime-text-packages/issues)
