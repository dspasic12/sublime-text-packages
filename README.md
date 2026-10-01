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
3. Preferences → Package Settings → Kubetools → set `stages` for seal keys

### ST4Notes

1. Preferences → Package Settings → ST4Notes → Settings – User
2. Set tokens only in User settings — never commit them
3. `notes_file` must stay under `notes_path_jail` (default `$HOME`)

## Requirements

- Sublime Text 4 Build 4205+ (plugin host **Python 3.14**)
- `kubectl` + optional `kubeseal` for Kubetools
- Optional YouTrack / GitLab tokens for ST4Notes

## Issues

Report issues at: [GitHub Issues](https://github.com/dspasic12/sublime-text-packages/issues)
