# Sublime Text Packages

This repository contains three Sublime Text 4 packages (plugin host **Python 3.14**).

## Available packages

### Kubetools (`kubetools/`)

Kubernetes YAML: **create slices** (Deployment / Secret), **apply only from those slices**,
compare to live cluster, dry-run, **seal / unseal** (kubeseal).

Do **not** install old **Kubeapply** or **Kubeseal** packages; they have been removed
from this repo. If they are still in `Packages/`, uninstall them. Seal `stages` live
in **User/Kubetools.sublime-settings**. If you still have
`Packages/User/Kubeseal.sublime-settings`, leave `"stages": []` (Kubetools can
one-time migrate leftover stages).

### notes (`notes/`)

Daily notes file + optional YouTrack / GitLab.
Command Palette: **`.notes`**. Settings basename remains `ST4Notes.sublime-settings`.

### kb (`kb/`)

Knowledge base (topics / subjects / `kb:` refs). Command Palette: **`.kb`**.
Settings: `KB.sublime-settings` (falls back to ST4Notes `knowledge_base_file` until
you create User/KB settings). Overlap with notes is insert + hover of `kb:` refs.

### User manual

**[docs/USER-MANUAL.md](docs/USER-MANUAL.md)** — settings layout, seal stages vs
`issue_stages`, gitops checklist, first-time setup.

## Installation

### Package Control

1. Install Package Control if needed
2. **Package Control: Add Repository** →
   `https://raw.githubusercontent.com/dspasic12/sublime-text-packages/main/repository.json`
3. **Install Package** → **kubetools**, **notes**, and/or **kb**

### Manual / symlink (dev)

Sublime Text 4 Packages directory:

| OS | Path |
|----|------|
| macOS | `~/Library/Application Support/Sublime Text/Packages` |
| Linux (Ubuntu / Arch) | `~/.config/sublime-text/Packages` |
| Windows | `%APPDATA%\Sublime Text\Packages` |

**macOS / Linux**

```bash
PACKAGES="$HOME/Library/Application Support/Sublime Text/Packages"   # macOS
# PACKAGES="$HOME/.config/sublime-text/Packages"                     # Ubuntu / Arch
ln -s "/path/to/sublime-text-packages/kubetools" "$PACKAGES/kubetools"
ln -s "/path/to/sublime-text-packages/notes" "$PACKAGES/notes"
ln -s "/path/to/sublime-text-packages/kb" "$PACKAGES/kb"
```

**Windows** (Developer Command Prompt / PowerShell)

```bat
mklink /D "%APPDATA%\Sublime Text\Packages\kubetools" C:\path\to\sublime-text-packages\kubetools
mklink /D "%APPDATA%\Sublime Text\Packages\notes" C:\path\to\sublime-text-packages\notes
mklink /D "%APPDATA%\Sublime Text\Packages\kb" C:\path\to\sublime-text-packages\kb
```

Uninstall leftover `Packages/kubeapply` and `Packages/Kubeseal` if present.

Default data files (created on first use, under `$HOME`):

- Journal: `~/Documents/ST4Notes`
- Knowledge base: `~/Documents/ST4Notes-kb`

Override `notes_file` / `knowledge_base_file` in **User** settings if your Documents folder is OneDrive, XDG-localized, or missing.

## Usage

### Kubetools

1. **`.kubetools - Create…`** → edit slice → **Cmd/Ctrl+Shift+Enter** to apply
   (not git). Apply does not run on Helm, GitOps trees, or other open files.
2. Compare / Dry-Run / Seal / Unseal from Hub (**Ctrl+K, H** in YAML)
3. **User/Kubetools.sublime-settings** → `stages` (one entry per cluster `pub.pem`)

### notes

1. **User/ST4Notes.sublime-settings** — tokens, `default_project`, optional `issue_stages`
2. Command Palette → type **`.notes`**
3. Slice commit is also **Cmd/Ctrl+Shift+Enter**

### kb

1. Command Palette → type **`.kb`**
2. Slice commit is **Cmd/Ctrl+Shift+Enter**
3. From notes: hover `kb:topic/subject` or **`.kb - Insert Ref`** to insert a ref

## Requirements

- Sublime Text 4 Build 4205+ (plugin host **Python 3.14**)
- `kubectl` + optional `kubeseal` for Kubetools
- Optional YouTrack / GitLab tokens for notes

## Issues

Report issues at: [GitHub Issues](https://github.com/dspasic12/sublime-text-packages/issues)
