# User manual — Kubetools, notes, kb

Author: **Dusan Spasic**

Quick onboarding for new users. Packages are **optional**: install Kubetools
for Kubernetes YAML, **notes** for daily notes and YouTrack/GitLab, **kb** for
the knowledge base (`.kb`). Overlap notes↔kb is only `kb:` refs and hover.

| Package folder | Command Palette | Preferences → Package Settings | Settings file |
|----------------|-----------------|--------------------------------|---------------|
| `kubetools` | **`.kubetools`** | Kubetools | `Kubetools.sublime-settings` |
| `notes` | **`.notes`** | notes | `ST4Notes.sublime-settings` (legacy basename) |
| `kb` | **`.kb`** | kb | `KB.sublime-settings` (falls back to ST4Notes `knowledge_base_file`) |

`ST4Notes` is only the **syntax / settings resource name** (and default notes
filename). The installable package and palette prefix are **`notes` / `.notes`**.

---

## Where settings live

| What | File | Commit to git? |
|------|------|----------------|
| Kubetools defaults | `Packages/kubetools/Kubetools.sublime-settings` | Yes (in repo) |
| Kubetools overrides | `Packages/User/Kubetools.sublime-settings` | **No** — local only |
| ST4Notes defaults | `Packages/notes/ST4Notes.sublime-settings` | Yes (in repo) |
| ST4Notes overrides | `Packages/User/ST4Notes.sublime-settings` | **No** — tokens here |
| KB defaults | `Packages/kb/KB.sublime-settings` | Yes (in repo) |
| KB overrides | `Packages/User/KB.sublime-settings` | **No** — local path / hover prefs |
| Legacy User file (optional) | `Packages/User/Kubeseal.sublime-settings` | **No** — leave `stages: []` if the file still exists |

Sublime merges **Default + User**. Put secrets, cluster cert paths, and personal
URLs only in **User**.

---

## Kubetools — seal stages (important)

### What `stages` are

Each **stage** is one Kubernetes cluster’s Sealed Secrets keypair:

- `name` — should match the **cluster folder** in your GitOps layout (e.g.
  `my-cluster`), so opening a file under
  `environments/<env>/my-cluster/...` can pre-select the right key.
- `cert_path` — path to `pub.pem` (encrypt / seal).
- `private_key_path` — path to `priv.key` (decrypt / unseal). Leave `""` for
  production-like clusters unless you deliberately keep a local key.

Example stage entry (paths are yours — never commit real paths into the package
repo):

```json
{
    "name": "my-cluster",
    "description": "dev / my-cluster (encrypt + decrypt)",
    "cert_path": "${home}/path/to/gitops/environments/dev/my-cluster/sealed-secrets/pub.pem",
    "private_key_path": "${home}/path/to/gitops/environments/dev/my-cluster/sealed-secrets/priv.key"
}
```

### Single source of truth

Maintain **`stages` only in `User/Kubetools.sublime-settings`**.

Set any leftover **`User/Kubeseal.sublime-settings`** to `"stages": []`. The
Kubeseal *package* is gone; Kubetools still reads that User file once as a
migration fallback.

### When GitOps adds a cluster

If your GitOps repo gains:

`environments/<env>/<cluster>/sealed-secrets/pub.pem`

add a matching block to Kubetools User `stages` (same `<cluster>` as `name`).
Then run **`.kubetools - Validate Seal Config`**.

**Dedicated keys:** some clusters use their own TLS keypair (documented next to
that cluster’s `pub.pem`). Do not copy sealed YAML from another cluster without
re-sealing with the correct cert.

### kubectl context vs stage `name`

Compare/Apply uses **kubectl context** (from `kubectl config get-contexts`).
Seal uses **stage `name`**. If they differ, set `context_path_aliases` in
Kubetools User settings, e.g.:

```json
"context_path_aliases": {
    "my-cluster": "org-my-cluster"
}
```

---

## notes — `issue_stages` (do not confuse with Kubetools)

### What `issue_stages` are

Used only by **.notes - Add → Issue** when you create **sub-tasks** under a parent
ticket. Each entry becomes part of the child summary:

```text
{parent summary} - {stage}
```

Examples: `Design`, `Dev`, `QA`, `Deploy` → issues named like
`PROJ-123 - Dev`.

They are **not** deployment targets and **not** seal stages. Putting Kubernetes
cluster folder names here creates YouTrack issues with those words in the title —
usually wrong.

### Recommended setup

**New users:** leave empty in User settings:

```json
"issue_stages": []
```

Create Issue will prompt once and can save your comma-separated list for next time.

**Teams with a fixed workflow:**

```json
"issue_stages": ["Design", "Dev", "QA", "Deploy"]
```

### Other notes keys

| Key | Purpose |
|-----|---------|
| `default_project` | YouTrack project shortName (e.g. `MYPROJECT`) |
| `youtrack_base` / `youtrack_token` | API + browser links (HTTPS only) |
| `gitlab_base` / `gitlab_token` | MR hover (`read_api`) |
| `post_comments` | YouTrack comment on Add-from-list / Import only, when the heading looks like `PROJ-1234`. Off by default. New note never comments. |
| `notes_file` / `notes_path_jail` | Local notes file must stay under jail (default `$HOME`) |

Never commit real tokens. Package defaults keep tokens empty. Your
`Packages/User/*.sublime-settings` files stay local and are not part of this
repo.

---

## Platforms (macOS, Windows, Linux)

All three packages run on **Sublime Text 4 Build 4205+** (Python 3.14 plugin host)
on macOS, Windows, Ubuntu, and Arch.

| | Default |
|--|---------|
| Journal | `~/Documents/ST4Notes` (`%USERPROFILE%\Documents\ST4Notes` on Windows) |
| Knowledge base | `~/Documents/ST4Notes-kb` |
| Jail | home directory (`~`) |

The `Documents` folder is created if missing. If Windows OneDrive or a localized
Linux XDG name (`Dokumente`, …) is your real Documents library, set `notes_file`
/ `knowledge_base_file` in User settings.

GUI Sublime often has a thinner `PATH` than a login shell. If `kubectl` / `kubeseal`
are not found: set `kubectl_path` / `kubeseal_path`, or on Linux ensure
`~/.local/bin` / `/snap/bin` (already searched). Homebrew paths are searched on
macOS.

Commit a slice with **Cmd+Shift+Enter** (macOS) or **Ctrl+Shift+Enter** (Windows /
Linux). Kubetools hub chords are **Ctrl+K** then a letter (YAML/JSON only).

---

## First-time checklist

### Kubetools

1. Install package folder **`kubetools`** (symlink or Package Control).
2. Ensure `kubectl` (and `kubeseal` for seal) on PATH, or set paths in User settings.
3. Add `stages` in **User/Kubetools.sublime-settings** pointing at your GitOps `pub.pem` files.
4. If `User/Kubeseal.sublime-settings` still exists, set `"stages": []`.
5. Open a Kubernetes YAML → **`.kubetools - Compare State`** or hub (**Ctrl+K**, **H** in YAML).

### notes

1. Install package folder **`notes`**.
2. **User/ST4Notes.sublime-settings**: `youtrack_base`, `youtrack_token`, `default_project`.
3. Set `issue_stages` to `[]` or workflow labels — **not** cluster names.
4. Command Palette → type **`.notes`** → Hub / Add / View / Search.
5. **.notes - View** for the daily file; **`.kb`** for topics/subjects.

---

## Should Kubetools and notes be one package?

**Short answer: keep them separate** unless you only ever ship to yourself and want one symlink.

| | Two packages (current) | One merged package |
|--|------------------------|---------------------|
| Install | Pick Kubetools and/or Notes | One install, larger bundle |
| Settings | Clear split (`Kubetools.sublime-settings` vs `ST4Notes.sublime-settings`) | One settings file or nested keys — harder for new users |
| Command palette | `.kubetools…` vs `.notes…` | Still need naming; easy to clutter |
| Dependencies | kubectl/kubeseal vs YouTrack/GitLab | Users who only want notes still load k8s code (and vice versa) |
| Releases | Version/fix notes independently | Every notes fix re-releases kubetools |
| Code sharing | Small overlap (HTTP scrub, daemon threads) | Could add `shared_util.py` **without** merging packages |
| ST conventions | One plugin folder = one concern | Valid as monorepo folder with multiple `.py` files, but Package Control = one package name |

**Reasonable middle ground (if sharing grows):**

- Extract a tiny **`st4_common.py`** (scrub secrets, `spawn_daemon`, path expand) copied or submodule’d into both packages — **not** a user-visible merge.
- Single **git repo** (already true) with two installable folders — **keep two Package Control entries**.

Merging into a single Package Control package only pays off if you want one install
and shared menus; cost is coupling, bigger load, and muddier docs for newcomers.
For Kubernetes tooling + notes, **two packages + this manual** is the better default.

---

## Further reading

- [kubetools/readme](../kubetools/readme) — compare, apply safety, keymap
- [notes/readme](../notes/readme) — commands, hover, safety
