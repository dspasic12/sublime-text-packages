"""Pure knowledge-base parse/splice helpers (no Sublime imports).

Used by notes_plugin (create-issue / slice fields / kb: hover skip) and unit tests.
"""
from __future__ import annotations

import re
from datetime import date

_KB_DEFAULT_TOPIC = "other"
_KB_DEFAULT_TOPIC_SLUG = "other"

_KB_TOPIC_RE = re.compile(r"^#\s+TOPIC:\s*(.+?)\s*$", re.IGNORECASE)
_KB_SUBJECT_RE = re.compile(r"^##\s+SUBJECT:\s*(.+?)\s*$", re.IGNORECASE)
_KB_REF_RE = re.compile(
    r"\bkb:(?:([a-z0-9][a-z0-9._-]*)/)?([a-z0-9][a-z0-9._-]*)\b",
    re.IGNORECASE,
)
_KB_META_CREATED_RE = re.compile(
    r"^\s*-\s*created:\s*(\d{4}-\d{2}-\d{2})\s*$",
    re.IGNORECASE,
)
_KB_META_MODIFIED_RE = re.compile(
    r"^\s*-\s*modified:\s*(\d{4}-\d{2}-\d{2})\s*$",
    re.IGNORECASE,
)
_KB_META_TAGS_RE = re.compile(
    r"^\s*-\s*tags:\s*(.*?)\s*$",
    re.IGNORECASE,
)


def kb_slug(name: str) -> str:
    s = (name or "").strip().lower()
    s = re.sub(r"[\s_/]+", "-", s)
    s = re.sub(r"[^a-z0-9._-]+", "", s)
    s = re.sub(r"-{2,}", "-", s).strip("-._")
    return s or "untitled"


def kb_ref(topic_slug: str, subject_slug: str) -> str:
    ts = (topic_slug or _KB_DEFAULT_TOPIC_SLUG).lower()
    ss = (subject_slug or "").lower()
    if ts == _KB_DEFAULT_TOPIC_SLUG:
        return f"kb:{ss}"
    return f"kb:{ts}/{ss}"


def kb_parse_ref(ref: str) -> tuple[str, str] | None:
    m = _KB_REF_RE.search(ref or "")
    if not m:
        return None
    topic = (m.group(1) or _KB_DEFAULT_TOPIC_SLUG).lower()
    subject = (m.group(2) or "").lower()
    if not subject:
        return None
    return topic, subject


def kb_ref_at_col(line_text: str, col: int) -> str | None:
    for m in _KB_REF_RE.finditer(line_text or ""):
        if m.start() <= col <= m.end():
            return m.group(0)
    return None


def parse_kb_index(lines: list[str]) -> dict:
    """
    Return {
      "topics": ordered topic display names,
      "by_topic": {topic_slug: [subject dicts...]},
      "all": flat subject list,
    }
    Subject keys: title, slug, topic, topic_slug, ref, topic_start, start, end,
    created, modified, body, preview. Legacy ``- tags:`` lines are skipped (not body).
    """
    topics_order: list[str] = []
    by_topic: dict[str, list[dict]] = {}
    current_topic = ""
    current_topic_slug = ""
    current_topic_line = 0
    current: dict | None = None
    body_lines: list[str] = []

    def flush() -> None:
        nonlocal current, body_lines
        if current is None:
            body_lines = []
            return
        body = "\n".join(body_lines).strip("\n")
        preview = ""
        for ln in body_lines:
            t = ln.strip()
            if t and not t.startswith("#"):
                preview = t[:120]
                break
        current["body"] = body
        current["preview"] = preview
        if not current.get("modified"):
            current["modified"] = current.get("created") or ""
        by_topic.setdefault(current["topic_slug"], []).append(current)
        current = None
        body_lines = []

    for i, line in enumerate(lines):
        mt = _KB_TOPIC_RE.match(line)
        if mt:
            flush()
            current_topic = mt.group(1).strip()
            current_topic_slug = kb_slug(current_topic)
            current_topic_line = i
            if current_topic_slug not in by_topic:
                by_topic[current_topic_slug] = []
                topics_order.append(current_topic)
            continue
        ms = _KB_SUBJECT_RE.match(line)
        if ms:
            flush()
            title = ms.group(1).strip()
            slug = kb_slug(title)
            if not current_topic_slug:
                current_topic = _KB_DEFAULT_TOPIC
                current_topic_slug = _KB_DEFAULT_TOPIC_SLUG
                if current_topic_slug not in by_topic:
                    by_topic[current_topic_slug] = []
                    topics_order.append(current_topic)
            current = {
                "title": title,
                "slug": slug,
                "topic": current_topic,
                "topic_slug": current_topic_slug,
                "ref": kb_ref(current_topic_slug, slug),
                "topic_start": current_topic_line,
                "start": i,
                "end": i,
                "created": "",
                "modified": "",
            }
            body_lines = []
            continue
        if current is not None:
            mc = _KB_META_CREATED_RE.match(line)
            if mc and not current.get("created"):
                current["created"] = mc.group(1)
                current["end"] = i
                continue
            mm = _KB_META_MODIFIED_RE.match(line)
            if mm and not current.get("modified"):
                current["modified"] = mm.group(1)
                current["end"] = i
                continue
            mtg = _KB_META_TAGS_RE.match(line)
            if mtg:
                # Legacy tags: ignore (do not put into body)
                current["end"] = i
                continue
            body_lines.append(line)
            current["end"] = i

    flush()

    all_subjects: list[dict] = []
    for tname in topics_order:
        ts = kb_slug(tname)
        all_subjects.extend(by_topic.get(ts, []))

    return {
        "topics": topics_order,
        "by_topic": by_topic,
        "all": all_subjects,
    }


def find_subject(
    index: dict,
    topic_slug: str,
    subject_slug: str,
) -> dict | None:
    t = (topic_slug or _KB_DEFAULT_TOPIC_SLUG).lower()
    s = (subject_slug or "").lower()
    if not s:
        return None
    for sub in index.get("by_topic", {}).get(t, []):
        if (sub.get("slug") or "").lower() == s:
            return sub
    if t == _KB_DEFAULT_TOPIC_SLUG or topic_slug in (None, ""):
        hits = [
            sub
            for sub in (index.get("all") or [])
            if (sub.get("slug") or "").lower() == s
        ]
        if len(hits) == 1:
            return hits[0]
    return None


def build_subject_stub(
    title: str,
    *,
    topic: str | None = None,
    created: str | None = None,
    body: str = "",
) -> str:
    """Text block to append to the KB file (includes leading newline)."""
    today = created or date.today().isoformat()
    title = (title or "").strip() or "new-subject"
    topic_name = (topic or "").strip()
    body = (body or "").rstrip()
    if not body.strip():
        body = "Write notes / commands here.\n"
    parts: list[str] = ["\n"]
    if topic_name and kb_slug(topic_name) != _KB_DEFAULT_TOPIC_SLUG:
        parts.append(f"# TOPIC: {topic_name}\n")
    elif not topic_name:
        # Explicit other section header only when starting a bare subject after other topics;
        # callers may pass topic="other" to force the header.
        pass
    if topic_name and kb_slug(topic_name) == _KB_DEFAULT_TOPIC_SLUG:
        parts.append(f"# TOPIC: {_KB_DEFAULT_TOPIC}\n")
    parts.append(f"## SUBJECT: {title}\n")
    parts.append(f"- created: {today}\n")
    parts.append(f"- modified: {today}\n")
    if not body.endswith("\n"):
        body += "\n"
    parts.append(body)
    return "".join(parts)


def subject_editor_document(subject: dict) -> str:
    """Full buffer content for the edit-slice tab."""
    topic = subject.get("topic") or _KB_DEFAULT_TOPIC
    title = subject.get("title") or ""
    ref = subject.get("ref") or ""
    created = subject.get("created") or date.today().isoformat()
    body = _strip_leading_meta_from_body(subject.get("body") or "")
    lines = [
        f"# TOPIC: {topic}",
        f"## SUBJECT: {title}",
        f"# created: {created}",
        f"- ref: {ref}",
        "# Cmd/Ctrl+Shift+Enter commits.  Cmd/Ctrl+Alt+T topic.  Cmd/Ctrl+Alt+B code fence.",
        "---",
    ]
    if body:
        lines.append(body.rstrip("\n"))
    else:
        lines.append("")
    return "\n".join(lines) + "\n"


def _strip_leading_meta_from_body(body: str) -> str:
    """Drop leaked created/legacy-tags lines from body."""
    lines = (body or "").splitlines()
    i = 0
    while i < len(lines):
        if (
            _KB_META_CREATED_RE.match(lines[i])
            or _KB_META_MODIFIED_RE.match(lines[i])
            or _KB_META_TAGS_RE.match(lines[i])
        ):
            i += 1
            continue
        if not lines[i].strip():
            i += 1
            continue
        break
    return "\n".join(lines[i:]).strip("\n")


def parse_subject_editor_buffer(text: str) -> tuple[str, str, str, str]:
    """From edit-slice buffer → (topic, title, created, body)."""
    lines = (text or "").splitlines()
    topic = _KB_DEFAULT_TOPIC
    title = ""
    created = ""
    sep = None
    for i, line in enumerate(lines):
        if line.strip() == "---":
            sep = i
            break
        mt = _KB_TOPIC_RE.match(line)
        if mt:
            topic = mt.group(1).strip()
            continue
        ms = _KB_SUBJECT_RE.match(line)
        if ms:
            title = ms.group(1).strip()
            continue
        mc = _KB_META_CREATED_RE.match(line)
        if mc:
            created = mc.group(1)
            continue
        mcc = re.match(r"^#\s*created:\s*(\d{4}-\d{2}-\d{2})\s*$", line, re.IGNORECASE)
        if mcc and not created:
            created = mcc.group(1)
            continue
        # ignore modified / legacy tags in header (modified is set on commit)
        if _KB_META_MODIFIED_RE.match(line) or _KB_META_TAGS_RE.match(line):
            continue
    if sep is None:
        body = ""
    else:
        body = "\n".join(lines[sep + 1 :]).strip("\n")
    return topic, title, created, body


def _subject_block_lines(
    title: str,
    created: str,
    body: str,
    *,
    modified: str | None = None,
) -> list[str]:
    today = date.today().isoformat()
    block = [
        f"## SUBJECT: {title}",
        f"- created: {created}",
        f"- modified: {(modified or today)}",
    ]
    body = (body or "").strip("\n")
    if body:
        block.extend(body.splitlines())
    return block


def sort_subjects_newest_first(subjects: list[dict]) -> list[dict]:
    """Sort by modified (else created) desc, then file position desc."""
    return sorted(
        list(subjects or []),
        key=lambda s: (
            s.get("modified") or s.get("created") or "0000-00-00",
            int(s.get("end") or s.get("start") or 0),
        ),
        reverse=True,
    )


def drop_empty_topic_headers(lines: list[str]) -> list[str]:
    """Remove ``# TOPIC:`` blocks that contain no ``## SUBJECT:``."""
    out: list[str] = []
    i = 0
    n = len(lines)
    while i < n:
        mt = _KB_TOPIC_RE.match(lines[i])
        if not mt:
            out.append(lines[i])
            i += 1
            continue
        j = i + 1
        while j < n and not _KB_TOPIC_RE.match(lines[j]):
            j += 1
        chunk = lines[i:j]
        if any(_KB_SUBJECT_RE.match(ln) for ln in chunk):
            out.extend(chunk)
        i = j
    while out and not out[-1].strip():
        out.pop()
    return out


def _kb_preamble_lines(lines: list[str]) -> list[str]:
    """Comment / blank lines before the first TOPIC or SUBJECT header."""
    preamble: list[str] = []
    for line in lines:
        if _KB_TOPIC_RE.match(line) or _KB_SUBJECT_RE.match(line):
            break
        preamble.append(line.rstrip())
    while preamble and not preamble[-1].strip():
        preamble.pop()
    return preamble


def format_kb_lines(lines: list[str]) -> list[str]:
    """Canonical on-disk layout. Does not reorder topics/subjects or bump dates."""
    src = [ln.rstrip() for ln in (lines or [])]
    preamble = _kb_preamble_lines(src)
    idx = parse_kb_index(src)
    out: list[str] = list(preamble)
    topics = [t for t in (idx.get("topics") or []) if idx.get("by_topic", {}).get(kb_slug(t))]
    for ti, tname in enumerate(topics):
        subs = [s for s in (idx["by_topic"].get(kb_slug(tname)) or []) if s]
        if not subs:
            continue
        if out:
            while out and not out[-1].strip():
                out.pop()
            out.append("")
            if ti > 0:
                out.append("")
        out.append(f"# TOPIC: {tname}")
        out.append("")
        for si, sub in enumerate(subs):
            if si > 0:
                while out and not out[-1].strip():
                    out.pop()
                out.append("")
            title = (sub.get("title") or sub.get("slug") or "untitled").strip()
            created = (sub.get("created") or "").strip()
            modified = (sub.get("modified") or created).strip()
            out.append(f"## SUBJECT: {title}")
            if created:
                out.append(f"- created: {created}")
            if modified:
                out.append(f"- modified: {modified}")
            body = (sub.get("body") or "").strip("\n")
            if body:
                out.extend(ln.rstrip() for ln in body.splitlines())
    while out and not out[-1].strip():
        out.pop()
    return out


def append_subject_to_lines(
    lines: list[str],
    *,
    topic: str,
    title: str,
    created: str,
    body: str,
) -> list[str]:
    """Append a new subject under ``topic``, creating the topic header if needed."""
    topic_name = (topic or "").strip() or _KB_DEFAULT_TOPIC
    topic_slug = kb_slug(topic_name)
    created = (created or "").strip() or date.today().isoformat()
    block = _subject_block_lines(title, created, body)
    insert_header = True
    insert_at = len(lines)
    for i, line in enumerate(lines):
        mt = _KB_TOPIC_RE.match(line)
        if not mt or kb_slug(mt.group(1)) != topic_slug:
            continue
        insert_header = False
        insert_at = i + 1
        j = i + 1
        while j < len(lines):
            if _KB_TOPIC_RE.match(lines[j]):
                break
            if _KB_SUBJECT_RE.match(lines[j]):
                k = j + 1
                while k < len(lines):
                    if _KB_SUBJECT_RE.match(lines[k]) or _KB_TOPIC_RE.match(lines[k]):
                        break
                    k += 1
                insert_at = k
                j = k
                continue
            j += 1
        break
    if insert_header:
        out = list(lines)
        while out and not out[-1].strip():
            out.pop()
        out.append("")
        out.append(f"# TOPIC: {topic_name}")
        out.extend(block)
        out.append("")
        return format_kb_lines(drop_empty_topic_headers(out))
    chunk = list(block) + [""]
    return format_kb_lines(drop_empty_topic_headers(lines[:insert_at] + chunk + lines[insert_at:]))


def splice_subject_body(
    lines: list[str],
    subject: dict,
    *,
    new_title: str,
    new_created: str,
    new_body: str,
    new_topic: str | None = None,
) -> list[str]:
    """
    Replace one subject's header+body in ``lines``.
    If ``new_topic`` differs from the subject's topic, move the block under that topic
    (creating ``# TOPIC:`` when needed). Legacy ``- tags:`` lines are dropped.
    """
    slug = (subject.get("slug") or "").lower()
    topic_slug = (subject.get("topic_slug") or _KB_DEFAULT_TOPIC_SLUG).lower()

    idx = parse_kb_index(lines)
    found = find_subject(idx, topic_slug, slug)
    if not found:
        raise ValueError(f"subject not found: {topic_slug}/{slug}")
    start = int(found["start"])
    end = int(found["end"])
    old_topic_slug = (found.get("topic_slug") or topic_slug).lower()

    title = (new_title or found.get("title") or slug).strip()
    created = (new_created or "").strip() or date.today().isoformat()
    block_lines = _subject_block_lines(
        title, created, new_body, modified=date.today().isoformat()
    )

    new_topic_name = (new_topic or found.get("topic") or _KB_DEFAULT_TOPIC).strip()
    new_topic_slug = kb_slug(new_topic_name)

    # In-place replace when topic unchanged
    if new_topic_slug == old_topic_slug:
        if end + 1 < len(lines) and lines[end + 1].strip() == "":
            return format_kb_lines(
                drop_empty_topic_headers(lines[:start] + block_lines + lines[end + 1 :])
            )
        return format_kb_lines(
            drop_empty_topic_headers(lines[:start] + block_lines + lines[end + 1 :])
        )

    # Topic changed: remove from old location, insert under new topic
    before = lines[:start]
    after = lines[end + 1 :]
    if after and after[0].strip() == "":
        after = after[1:]
    remaining = before + after

    # Drop orphaned blank after previous TOPIC if we emptied a section mid-file — keep simple
    insert_topic_header = True
    insert_at = len(remaining)
    for i, line in enumerate(remaining):
        mt = _KB_TOPIC_RE.match(line)
        if mt and kb_slug(mt.group(1)) == new_topic_slug:
            insert_topic_header = False
            # after last subject in this topic, or right after header
            insert_at = i + 1
            j = i + 1
            while j < len(remaining):
                if _KB_TOPIC_RE.match(remaining[j]):
                    break
                if _KB_SUBJECT_RE.match(remaining[j]):
                    # find end of this subject via re-parse is heavy; scan until next SUBJECT/TOPIC
                    k = j + 1
                    while k < len(remaining):
                        if _KB_SUBJECT_RE.match(remaining[k]) or _KB_TOPIC_RE.match(remaining[k]):
                            break
                        k += 1
                    insert_at = k
                    j = k
                    continue
                j += 1
            break

    chunk: list[str] = []
    if insert_topic_header:
        # append at EOF
        out = list(remaining)
        while out and not out[-1].strip():
            out.pop()
        out.append("")
        out.append(f"# TOPIC: {new_topic_name}")
        out.extend(block_lines)
        out.append("")
        return format_kb_lines(drop_empty_topic_headers(out))

    chunk.extend(block_lines)
    chunk.append("")
    return format_kb_lines(
        drop_empty_topic_headers(remaining[:insert_at] + chunk + remaining[insert_at:])
    )


_CI_FIELD_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.*)$")
_CI_SUBTASK_RE = re.compile(r"^\s*-\s*(.+)$")


def expand_issue_template(template: str, summary: str, *, stage: str = "") -> str:
    """Expand ``$summary`` / ``$name`` (parent title) and ``$stage`` (sub-task label)."""
    s = summary or ""
    st = stage or ""
    return (
        (template or "")
        .replace("$summary", s)
        .replace("$name", s)
        .replace("$stage", st)
    ).strip()


_TICKET_ID_RE = re.compile(
    r"([A-Za-z][A-Za-z0-9]*)-(\d+)",
)
_YT_ISSUE_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9]*-\d+$")
_TICKET_IN_URL_RE = re.compile(
    r"/issue(?:s)?/([A-Za-z][A-Za-z0-9]*-\d+)",
    re.IGNORECASE,
)


def is_youtrack_issue_id(raw: str) -> bool:
    """True only for a whole-string id like ``PROJ-1234`` (not ``proj 1234``)."""
    s = (raw or "").strip().lstrip("#").strip()
    if s.endswith(":"):
        s = s[:-1].strip()
    return bool(_YT_ISSUE_ID_RE.fullmatch(s))


def journal_topics_equivalent(a: str, b: str) -> bool:
    """Same journal bucket: case-insensitive exact topic, or same YouTrack id.

    ``PROJ-1234`` matches ``proj-1234``. Does **not** match ``proj 1234``
    or ``proj_1234``.
    """
    na, ea = normalize_journal_topic(a)
    nb, eb = normalize_journal_topic(b)
    left = na if not ea else (a or "").strip()
    right = nb if not eb else (b or "").strip()
    if not left or not right:
        return False
    a_yt = is_youtrack_issue_id(left)
    b_yt = is_youtrack_issue_id(right)
    if a_yt and b_yt:
        return normalize_ticket_id(left) == normalize_ticket_id(right)
    if a_yt or b_yt:
        return False
    return left.casefold() == right.casefold()


def normalize_ticket_id(raw: str) -> str:
    """Accept ``PROJ-12``, ``#PROJ-12``, or a YouTrack issue URL."""
    s = (raw or "").strip()
    if not s:
        return ""
    m = _TICKET_IN_URL_RE.search(s)
    if m:
        return m.group(1).upper()
    s = s.lstrip("#").strip()
    m = _TICKET_ID_RE.search(s)
    if m:
        return f"{m.group(1).upper()}-{m.group(2)}"
    return s.upper()


def parse_stages_flag(raw: str) -> bool:
    """``true``/``false`` (also ``yes``/``no``). Empty defaults to false."""
    v = (raw or "").strip().lower()
    if v in ("true", "yes", "y", "1"):
        return True
    return False


def _is_issue_sep_line(line: str) -> bool:
    """Unindented ``---`` only (so description can mention a rule)."""
    return bool(line) and not line[:1] in " \t" and line.strip() == "---"


def _dedent_issue_cont(line: str) -> str:
    if line.startswith("  "):
        return line[2:]
    if line.startswith("\t"):
        return line[1:]
    return line.lstrip(" \t")


def format_create_issue_description(value: str) -> str:
    """Always a block field so Alt+Down can select every line."""
    raw = (value or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = raw.split("\n")
    while lines and lines[-1] == "":
        lines.pop()
    if not lines:
        return "description:\n  "
    return "description:\n" + "\n".join("  " + ln for ln in lines)


def parse_create_issue_slice(text: str) -> dict:
    """Parse create-issue slice → fields + subtasks ``[(label, title_template)]``."""
    lines = (text or "").splitlines()
    fields: dict[str, str] = {}
    sep = None
    i = 0
    while i < len(lines):
        line = lines[i]
        if _is_issue_sep_line(line):
            sep = i
            break
        if line[:1] in " \t":
            i += 1
            continue
        s = line.strip()
        if not s or s.startswith("#"):
            i += 1
            continue
        m = _CI_FIELD_RE.match(s)
        if not m:
            i += 1
            continue
        key = m.group(1).strip().lower()
        val = (m.group(2) or "").strip()
        if val in ("|", ">"):
            val = ""
        block: list[str] = []
        j = i + 1
        while j < len(lines):
            nxt = lines[j]
            if _is_issue_sep_line(nxt):
                break
            if nxt[:1] in " \t":
                block.append(_dedent_issue_cont(nxt))
                j += 1
                continue
            if nxt.strip() == "" or nxt.strip().startswith("#"):
                break
            break
        if block:
            while block and block[-1] == "":
                block.pop()
            body = "\n".join(block)
            fields[key] = f"{val}\n{body}".strip() if val else body.strip("\n")
            if not fields[key].strip():
                fields[key] = ""
        else:
            fields[key] = val
        i = j
        continue
    subtasks: list[tuple[str, str]] = []
    if sep is not None:
        for line in lines[sep + 1 :]:
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            m = _CI_SUBTASK_RE.match(line)
            if not m:
                continue
            body = m.group(1).strip()
            if "|" in body:
                label, title = body.split("|", 1)
                label, title = label.strip(), title.strip()
            else:
                label, title = body, body
            if title:
                subtasks.append((label or title, title))
    return {
        "ticket": normalize_ticket_id(fields.get("ticket") or ""),
        "project": (fields.get("project") or "").strip().upper(),
        "summary": (fields.get("summary") or "").strip(),
        "description": (fields.get("description") or "").strip(),
        "assignee": (fields.get("assignee") or "").strip(),
        "reporter": (fields.get("reporter") or "").strip(),
        "due": (fields.get("due") or "").strip(),
        "state": (fields.get("state") or "").strip(),
        "priority": (fields.get("priority") or "").strip(),
        "parent": normalize_ticket_id(fields.get("parent") or ""),
        "stages_yes": parse_stages_flag(fields.get("stages") or ""),
        "subtasks": subtasks,
    }


_SLICE_SKIP_META = frozenset({"ref", "modified", "tags", "created"})
_SLICE_LOCKED_ISSUE_KEYS = frozenset({"ticket"})
_SLICE_TOPIC_RE = re.compile(r"^#\s+TOPIC:\s*(.*?)\s*$", re.IGNORECASE)
_SLICE_SUBJECT_RE = re.compile(r"^##\s+SUBJECT:\s*(.*?)\s*$", re.IGNORECASE)
_SLICE_DASH_FIELD_RE = re.compile(r"^-\s*([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.*?)\s*$")
_SLICE_PLAIN_FIELD_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.*?)\s*$")
_SLICE_SUBTASK_LINE_RE = re.compile(r"^(\s*-\s*)(?:(.+?)(\s*\|\s*))?(.*)$")


def slice_field_regions(text: str, *, kind: str = "") -> list[tuple[int, int]]:
    """Offsets of editable field *values* in document order (header, then body).

    ``kind``: ``kb`` / ``create_issue`` / ``add``. Empty infers from the header.
    Add slices have no ``---``; comment lines are skipped and each body line is a field.
    """
    fields: list[tuple[int, int]] = []
    pos = 0
    seen_sep = False
    in_fence = False
    saw_topic = False
    kb_have_prose = False
    kind_l = (kind or "").strip().lower()

    def _value_span(raw: str) -> tuple[int, int]:
        lead = len(raw) - len(raw.lstrip(" \t"))
        stripped = raw.strip()
        a = pos + lead
        return a, a + len(stripped)

    if kind_l in ("add", "note"):
        seen_sep = False
        for line in (text or "").splitlines(keepends=True):
            raw = line.rstrip("\r\n")
            stripped = raw.strip()
            if not seen_sep:
                if stripped == "---":
                    seen_sep = True
                    pos += len(line)
                    continue
                mp = _SLICE_PLAIN_FIELD_RE.match(raw)
                if (
                    mp
                    and not raw.lstrip().startswith("#")
                    and not raw.lstrip().startswith("-")
                ):
                    fields.append((pos + mp.start(2), pos + mp.end(2)))
                pos += len(line)
                continue
            if stripped and not stripped.startswith("#"):
                if raw.lstrip().startswith("-"):
                    sm = _SLICE_SUBTASK_LINE_RE.match(raw)
                    if sm:
                        fields.append((pos + sm.start(4), pos + sm.end(4)))
                    else:
                        fields.append(_value_span(raw))
                else:
                    fields.append(_value_span(raw))
            pos += len(line)
        return fields

    skip_ticket = kind_l == "create_issue"
    block_key: str | None = None
    block_start: int | None = None
    block_end: int | None = None

    def _flush_block() -> None:
        nonlocal block_key, block_start, block_end
        if (
            block_key
            and not (skip_ticket and block_key in _SLICE_LOCKED_ISSUE_KEYS)
            and block_start is not None
            and block_end is not None
        ):
            fields.append((block_start, block_end))
        block_key = None
        block_start = None
        block_end = None

    for line in (text or "").splitlines(keepends=True):
        raw = line.rstrip("\r\n")
        stripped = raw.strip()
        if not seen_sep and _is_issue_sep_line(raw):
            _flush_block()
            seen_sep = True
            pos += len(line)
            continue
        if not seen_sep:
            if raw[:1] in " \t" and block_key:
                lead = len(raw) - len(raw.lstrip(" \t"))
                cont_a = pos + lead
                cont_b = pos + len(raw)
                if block_start is None or block_start == block_end:
                    block_start = cont_a
                block_end = cont_b
                pos += len(line)
                continue
            mt = _SLICE_TOPIC_RE.match(raw)
            ms = _SLICE_SUBJECT_RE.match(raw)
            md = _SLICE_DASH_FIELD_RE.match(raw)
            mp = _SLICE_PLAIN_FIELD_RE.match(raw)
            if mt:
                _flush_block()
                saw_topic = True
                fields.append((pos + mt.start(1), pos + mt.end(1)))
            elif ms:
                _flush_block()
                saw_topic = True
                fields.append((pos + ms.start(1), pos + ms.end(1)))
            elif md and md.group(1).lower() not in _SLICE_SKIP_META:
                _flush_block()
                fields.append((pos + md.start(2), pos + md.end(2)))
            elif (
                mp
                and not raw.lstrip().startswith("#")
                and not raw.lstrip().startswith("-")
            ):
                _flush_block()
                block_key = mp.group(1).lower()
                block_start = pos + mp.start(2)
                block_end = pos + mp.end(2)
                if skip_ticket and block_key in _SLICE_LOCKED_ISSUE_KEYS:
                    block_start = None
                    block_end = None
            else:
                _flush_block()
            pos += len(line)
            continue

        if stripped.startswith("```"):
            in_fence = not in_fence
            pos += len(line)
            continue
        if in_fence or not stripped or stripped.startswith("#"):
            pos += len(line)
            continue

        is_issue = kind_l == "create_issue" or (not kind_l and not saw_topic)
        if is_issue:
            if raw.lstrip().startswith("-"):
                sm = _SLICE_SUBTASK_LINE_RE.match(raw)
                if sm:
                    fields.append((pos + sm.start(4), pos + sm.end(4)))
            pos += len(line)
            continue

        # KB / add body: first prose line, then `- ` bullets, in file order
        if raw.lstrip().startswith("-"):
            fields.append(_value_span(raw))
        elif not kb_have_prose:
            fields.append(_value_span(raw))
            kb_have_prose = True
        pos += len(line)
    return fields


def parse_add_slice(text: str) -> tuple[str, str]:
    """Return (ticket_id, description) from an add-slice buffer."""
    ticket = ""
    body: list[str] = []
    seen_sep = False
    for raw in (text or "").splitlines():
        stripped = raw.strip()
        if not seen_sep:
            if stripped == "---":
                seen_sep = True
                continue
            m = _SLICE_PLAIN_FIELD_RE.match(raw)
            if m and m.group(1).lower() in _NOTE_NAME_FIELDS:
                ticket = (m.group(2) or "").strip()
            continue
        if not stripped or stripped.startswith("#"):
            continue
        body.append(raw.rstrip())
    return ticket, "\n".join(body)


_NOTES_TICKET_HEADER_RE = re.compile(
    r"^#\s+((?!DATE\b).+?):\s*$",
    re.IGNORECASE,
)
_NOTE_NAME_FIELDS = frozenset({"topic", "name", "header", "ticket"})
_JOURNAL_TOPIC_MAX = 80

parse_note_slice = parse_add_slice

_JOURNAL_SEP_RE = re.compile(r"^# =+\s*$")
_JOURNAL_DATE_RE = re.compile(
    r"^#\s+(?:DATE:\s+)?(\d{4})[.\-](\d{1,2})[.\-](\d{1,2})"
    r"(?:\s+[A-Za-z][A-Za-z ,-]*)?\s*$",
    re.IGNORECASE,
)


def parse_journal_date_header(line: str) -> tuple[int, int, int] | None:
    m = _JOURNAL_DATE_RE.match((line or "").strip())
    if not m:
        return None
    try:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if not (1 <= mo <= 12 and 1 <= d <= 31):
            return None
        return (y, mo, d)
    except ValueError:
        return None


def journal_date_iso(date: tuple[int, int, int] | None) -> str:
    if not date:
        return ""
    return f"{date[0]:04d}-{date[1]:02d}-{date[2]:02d}"


def parse_journal_date_iso(raw: str) -> tuple[int, int, int] | None:
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", (raw or "").strip())
    if not m:
        return None
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)))


def journal_ticket_header_name(line: str) -> str | None:
    raw = (line or "").rstrip("\r\n")
    if parse_journal_date_header(raw):
        return None
    m = _NOTES_TICKET_HEADER_RE.match(raw)
    if not m:
        return None
    name = (m.group(1) or "").strip()
    if not name or name.casefold() == "date":
        return None
    return name


def normalize_journal_topic(raw: str) -> tuple[str, str | None]:
    """Return (topic, error). Topic is any single-line journal header, not only a ticket id."""
    s = " ".join((raw or "").strip().split())
    s = s.lstrip("#").strip()
    if s.endswith(":"):
        s = s[:-1].strip()
    if not s:
        return "", "Topic cannot be empty."
    if s.casefold() == "date":
        return "", "Topic cannot be DATE (reserved for the day header)."
    if parse_journal_date_header(f"# {s}") or parse_journal_date_header(f"# {s}:"):
        return "", "Topic cannot look like a date header."
    if ":" in s:
        return "", "Topic cannot contain a colon (it becomes '# topic:' in the journal)."
    if len(s) > _JOURNAL_TOPIC_MAX:
        return "", f"Topic is too long (max {_JOURNAL_TOPIC_MAX} characters)."
    return s, None


def _journal_rows(text: str) -> list[tuple[int, str]]:
    pos = 0
    rows: list[tuple[int, str]] = []
    for line in (text or "").splitlines(keepends=True):
        rows.append((pos, line.rstrip("\r\n")))
        pos += len(line)
    return rows


def find_journal_block_at_offset(text: str, caret: int) -> dict | None:
    """Ticket block containing caret, or None if not inside a ``# NAME:`` section."""
    rows = _journal_rows(text)
    if not rows:
        return None
    line_i = 0
    for i, (start, _raw) in enumerate(rows):
        if start <= caret:
            line_i = i
        else:
            break
    header_i: int | None = None
    for i in range(line_i, -1, -1):
        raw = rows[i][1]
        if parse_journal_date_header(raw) or _JOURNAL_SEP_RE.match(raw.strip()):
            break
        if journal_ticket_header_name(raw):
            header_i = i
            break
    if header_i is None:
        return None
    ticket = journal_ticket_header_name(rows[header_i][1]) or ""
    date = None
    for i in range(header_i, -1, -1):
        date = parse_journal_date_header(rows[i][1])
        if date:
            break
    end = header_i + 1
    while end < len(rows):
        raw = rows[end][1]
        if (
            _JOURNAL_SEP_RE.match(raw.strip())
            or parse_journal_date_header(raw)
            or journal_ticket_header_name(raw)
        ):
            break
        end += 1
    body = [rows[i][1] for i in range(header_i + 1, end) if rows[i][1].strip()]
    return {
        "ticket": ticket,
        "date": date,
        "header_line": header_i,
        "block_start": header_i,
        "block_end": end,
        "body": body,
    }


def find_journal_block_by_date_ticket(
    lines: list[str],
    date: tuple[int, int, int],
    ticket: str,
) -> tuple[int, int] | None:
    """Return ``(block_start, block_end)`` exclusive end, or None."""
    want = (ticket or "").strip()
    if not want or not date:
        return None
    in_date = False
    i = 0
    n = len(lines)
    while i < n:
        raw = lines[i]
        d = parse_journal_date_header(raw)
        if d:
            in_date = d == date
            i += 1
            continue
        if not in_date:
            i += 1
            continue
        if _JOURNAL_SEP_RE.match((raw or "").strip()):
            if i + 1 < n and parse_journal_date_header(lines[i + 1]):
                in_date = False
            i += 1
            continue
        name = journal_ticket_header_name(raw)
        if name and journal_topics_equivalent(name, want):
            end = i + 1
            while end < n:
                nxt = lines[end]
                if (
                    _JOURNAL_SEP_RE.match((nxt or "").strip())
                    or parse_journal_date_header(nxt)
                    or journal_ticket_header_name(nxt)
                ):
                    break
                end += 1
            return i, end
        i += 1
    return None


def replace_journal_ticket_block(
    lines: list[str],
    start: int,
    end: int,
    ticket: str,
    body_lines: list[str],
) -> list[str]:
    header = f"# {(ticket or '').strip()}:"
    block = [header]
    for ln in body_lines:
        s = (ln or "").rstrip()
        if s:
            block.append(s)
    spliced = list(lines[:start]) + block + list(lines[end:])
    return ensure_blank_between_journal_blocks(spliced)


def ensure_blank_between_journal_blocks(lines: list[str]) -> list[str]:
    """One blank line after each ``# HEADER:`` block so the next header is separated."""
    out: list[str] = []
    for line in lines:
        if journal_ticket_header_name(line) and out:
            prev = out[-1]
            if prev.strip() and not _JOURNAL_SEP_RE.match(prev.strip()):
                out.append("")
        out.append(line)
    if out and out[-1].strip():
        out.append("")
    return out


def notes_ticket_header_regions(text: str) -> list[tuple[int, int]]:
    """Offsets of ``# TODO:`` / ``# PROJ-123:`` titles in the journal file."""
    regions: list[tuple[int, int]] = []
    pos = 0
    for line in (text or "").splitlines(keepends=True):
        raw = line.rstrip("\r\n")
        m = _NOTES_TICKET_HEADER_RE.match(raw)
        if m:
            regions.append((pos + m.start(1), pos + m.end(1)))
        pos += len(line)
    return regions


def subject_header_regions(text: str) -> list[tuple[int, int]]:
    """Offsets of ``## SUBJECT:`` title values in the on-disk KB file."""
    regions: list[tuple[int, int]] = []
    pos = 0
    for line in (text or "").splitlines(keepends=True):
        raw = line.rstrip("\r\n")
        ms = _SLICE_SUBJECT_RE.match(raw) or _KB_SUBJECT_RE.match(raw)
        if ms:
            regions.append((pos + ms.start(1), pos + ms.end(1)))
        pos += len(line)
    return regions


def next_slice_field_index(
    fields: list[tuple[int, int]],
    caret: int,
    *,
    forward: bool,
) -> int | None:
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

