"""Unit tests for kb_pure (parity with notes_kb_pure) (no Sublime runtime required)."""
from __future__ import annotations

import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from kb_pure import (
    append_subject_to_lines,
    build_subject_stub,
    drop_empty_topic_headers,
    expand_issue_template,
    find_subject,
    kb_parse_ref,
    kb_ref,
    kb_ref_at_col,
    kb_slug,
    parse_create_issue_slice,
    parse_kb_index,
    parse_stages_flag,
    parse_subject_editor_buffer,
    sort_subjects_newest_first,
    splice_subject_body,
    subject_editor_document,
    normalize_ticket_id,
    next_slice_field_index,
    slice_field_regions,
    subject_header_regions,
    notes_ticket_header_regions,
    format_kb_lines,
)


class TestSlugRef(unittest.TestCase):
    def test_slug(self):
        self.assertEqual(kb_slug("PROJ-5124"), "proj-5124")
        self.assertEqual(kb_slug("Sealed Secrets"), "sealed-secrets")

    def test_ref_short_other(self):
        self.assertEqual(kb_ref("other", "proj-5124"), "kb:proj-5124")
        self.assertEqual(kb_ref("registry", "cleanup-images"), "kb:registry/cleanup-images")

    def test_parse_ref(self):
        self.assertEqual(kb_parse_ref("kb:proj-5124"), ("other", "proj-5124"))
        self.assertEqual(
            kb_parse_ref("see kb:registry/cleanup-images here"),
            ("registry", "cleanup-images"),
        )

    def test_ref_at_col_beats_ticket_shape(self):
        line = "- x → kb:other/proj-5124"
        col = line.index("5124")
        self.assertEqual(kb_ref_at_col(line, col), "kb:other/proj-5124")


class TestParseIndex(unittest.TestCase):
    def test_other_without_topic_header(self):
        lines = [
            "## SUBJECT: PROJ-1",
            "- created: 2026-10-02",
            "- tags: ticket",  # legacy — ignored, not body
            "body line",
            "",
            "# TOPIC: Registry",
            "## SUBJECT: cleanup",
            "cmd",
        ]
        idx = parse_kb_index(lines)
        self.assertIn("other", [kb_slug(t) for t in idx["topics"]])
        other = find_subject(idx, "other", "proj-1")
        self.assertIsNotNone(other)
        self.assertEqual(other["ref"], "kb:proj-1")
        self.assertEqual(other["created"], "2026-10-02")
        self.assertNotIn("tags", other)
        self.assertEqual(other["body"], "body line")
        reg = find_subject(idx, "registry", "cleanup")
        self.assertEqual(reg["body"].strip(), "cmd")


class TestEditorRoundTrip(unittest.TestCase):
    def test_document_and_parse(self):
        sub = {
            "topic": "other",
            "title": "PROJ-9",
            "slug": "proj-9",
            "topic_slug": "other",
            "ref": "kb:proj-9",
            "created": "2026-10-02",
            "body": "hello\n```bash\necho hi\n```",
        }
        doc = subject_editor_document(sub)
        self.assertIn("---", doc)
        self.assertNotIn("- tags:", doc)
        topic, title, created, body = parse_subject_editor_buffer(doc)
        self.assertEqual(topic, "other")
        self.assertEqual(title, "PROJ-9")
        self.assertEqual(created, "2026-10-02")
        self.assertIn("echo hi", body)

    def test_splice(self):
        lines = [
            "# TOPIC: other",
            "## SUBJECT: PROJ-1",
            "- created: 2026-01-01",
            "- tags:",
            "old body",
            "",
            "# TOPIC: Registry",
            "## SUBJECT: cleanup",
            "stay",
        ]
        idx = parse_kb_index(lines)
        sub = find_subject(idx, "other", "proj-1")
        new_lines = splice_subject_body(
            lines,
            sub,
            new_title="PROJ-1",
            new_created="2026-10-02",
            new_body="new body\nline2",
        )
        joined = "\n".join(new_lines)
        self.assertNotIn("- tags:", joined)
        idx2 = parse_kb_index(new_lines)
        s2 = find_subject(idx2, "other", "proj-1")
        self.assertEqual(s2["body"], "new body\nline2")
        self.assertEqual(s2["created"], "2026-10-02")
        self.assertEqual(find_subject(idx2, "registry", "cleanup")["body"], "stay")

    def test_splice_moves_topic(self):
        lines = [
            "# TOPIC: other",
            "## SUBJECT: PROJ-1",
            "- created: 2026-01-01",
            "body",
            "",
            "# TOPIC: Registry",
            "## SUBJECT: cleanup",
            "stay",
        ]
        idx = parse_kb_index(lines)
        sub = find_subject(idx, "other", "proj-1")
        new_lines = splice_subject_body(
            lines,
            sub,
            new_title="PROJ-1",
            new_created="2026-10-02",
            new_body="moved",
            new_topic="Registry",
        )
        idx2 = parse_kb_index(new_lines)
        self.assertEqual(idx2["by_topic"].get("other") or [], [])
        moved = find_subject(idx2, "registry", "proj-1")
        self.assertIsNotNone(moved)
        self.assertEqual(moved["body"], "moved")
        self.assertEqual(find_subject(idx2, "registry", "cleanup")["body"], "stay")

    def test_build_stub_other(self):
        stub = build_subject_stub("PROJ-2", topic="other", created="2026-10-02")
        self.assertIn("# TOPIC: other", stub)
        self.assertIn("## SUBJECT: PROJ-2", stub)
        self.assertNotIn("- tags:", stub)
        idx = parse_kb_index(stub.strip("\n").splitlines())
        self.assertIsNotNone(find_subject(idx, "other", "proj-2"))

    def test_legacy_tags_stripped_from_editor_body(self):
        sub = {
            "topic": "other",
            "title": "legacy",
            "slug": "legacy",
            "topic_slug": "other",
            "ref": "kb:legacy",
            "created": "2026-10-02",
            "body": "- tags: a, b\n\nreal body",
        }
        doc = subject_editor_document(sub)
        self.assertNotIn("- tags:", doc)
        self.assertNotIn("- created:", doc)
        self.assertIn("# created: 2026-10-02", doc)
        self.assertIn("---\nreal body", doc)



class TestSortNewest(unittest.TestCase):
    def test_sort_by_modified(self):
        subjects = [
            {"slug": "a", "modified": "2026-01-01", "created": "2026-01-01", "end": 1},
            {"slug": "b", "modified": "2026-10-02", "created": "2026-01-01", "end": 2},
            {"slug": "c", "modified": "", "created": "2026-09-01", "end": 3},
        ]
        ordered = sort_subjects_newest_first(subjects)
        self.assertEqual([s["slug"] for s in ordered], ["b", "c", "a"])

    def test_splice_writes_modified(self):
        lines = [
            "# TOPIC: other",
            "## SUBJECT: X",
            "- created: 2026-01-01",
            "body",
        ]
        idx = parse_kb_index(lines)
        sub = find_subject(idx, "other", "x")
        new_lines = splice_subject_body(
            lines, sub, new_title="X", new_created="2026-01-01", new_body="body2"
        )
        joined = "\n".join(new_lines)
        self.assertIn("- modified:", joined)
        self.assertIn("- created: 2026-01-01", joined)


class TestEmptyTopicsAndAppend(unittest.TestCase):
    def test_splice_drops_empty_topic(self):
        lines = [
            "# TOPIC: other",
            "## SUBJECT: PROJ-1",
            "- created: 2026-01-01",
            "body",
            "",
            "# TOPIC: Registry",
            "## SUBJECT: cleanup",
            "stay",
        ]
        idx = parse_kb_index(lines)
        sub = find_subject(idx, "other", "proj-1")
        new_lines = splice_subject_body(
            lines,
            sub,
            new_title="PROJ-1",
            new_created="2026-01-01",
            new_body="moved",
            new_topic="Registry",
        )
        joined = "\n".join(new_lines)
        self.assertNotIn("# TOPIC: other", joined)
        self.assertEqual(drop_empty_topic_headers(new_lines), new_lines)
        self.assertEqual(parse_kb_index(new_lines)["by_topic"].get("other") or [], [])
        self.assertIsNotNone(find_subject(parse_kb_index(new_lines), "registry", "proj-1"))

    def test_append_subject_without_prior_stub(self):
        lines = [
            "# TOPIC: Registry",
            "## SUBJECT: cleanup",
            "stay",
        ]
        new_lines = append_subject_to_lines(
            lines,
            topic="other",
            title="new-runbook",
            created="2026-10-05",
            body="hello",
        )
        idx = parse_kb_index(new_lines)
        sub = find_subject(idx, "other", "new-runbook")
        self.assertIsNotNone(sub)
        self.assertEqual(sub["body"].strip(), "hello")
        self.assertEqual(find_subject(idx, "registry", "cleanup")["body"].strip(), "stay")


class TestCreateIssueSlice(unittest.TestCase):
    def test_parse_name_and_env_override(self):
        text = (
            "project: PROJ\n"
            "summary: Roll out docs\n"
            "stages: true\n"
            "---\n"
            "- env-dev | $name - env-dev\n"
            "- env-test | $summary - $stage\n"
        )
        data = parse_create_issue_slice(text)
        self.assertEqual(data["summary"], "Roll out docs")
        self.assertTrue(data["stages_yes"])
        self.assertEqual(data["subtasks"][0][0], "env-dev")
        self.assertEqual(
            expand_issue_template(data["subtasks"][0][1], data["summary"], stage="env-dev"),
            "Roll out docs - env-dev",
        )
        self.assertEqual(
            expand_issue_template(data["subtasks"][1][1], data["summary"], stage="env-test"),
            "Roll out docs - env-test",
        )

    def test_stages_default_false(self):
        data = parse_create_issue_slice("project: PROJ\nsummary: X\n---\n")
        self.assertFalse(data["stages_yes"])
        self.assertFalse(parse_stages_flag(""))
        self.assertFalse(parse_stages_flag("false"))
        self.assertTrue(parse_stages_flag("true"))
        self.assertTrue(parse_stages_flag("yes"))

    def test_parent_url_and_assignee_not_uppercased(self):
        text = (
            "project: PROJ\n"
            "assignee:  Ada.Lovelace@example.com \n"
            "parent: https://youtrack.example.com/issue/PROJ-99\n"
            "stages: true\n"
            "---\n"
            "- env-dev | $name - env-dev\n"
        )
        data = parse_create_issue_slice(text)
        self.assertEqual(data["parent"], "PROJ-99")
        self.assertEqual(data["assignee"], "Ada.Lovelace@example.com")
        self.assertEqual(normalize_ticket_id("#proj-12"), "PROJ-12")


class TestSliceFields(unittest.TestCase):
    def test_kb_fields_skip_ref(self):
        text = (
            "# TOPIC: Registry\n"
            "## SUBJECT: cleanup\n"
            "- created: 2026-01-01\n"
            "- ref: kb:registry/cleanup\n"
            "---\n"
            "first body line\n"
            "second\n"
        )
        regions = slice_field_regions(text)
        values = [text[a:b] for a, b in regions]
        self.assertEqual(values, ["Registry", "cleanup", "first body line"])
        i = next_slice_field_index(regions, regions[0][0], forward=True)
        self.assertEqual(text[regions[i][0]:regions[i][1]], "cleanup")
        wrap = next_slice_field_index(regions, regions[-1][0], forward=True)
        self.assertEqual(wrap, 0)

    def test_kb_body_fields_document_order(self):
        text = (
            "# TOPIC: Registry\n"
            "## SUBJECT: robot account\n"
            "# created: 2026-10-02\n"
            "- ref: kb:registry/robot-account\n"
            "---\n"
            "Pull-only on demo/*\n"
            "\n"
            "- robot token: ⟦secret:abc⟧\n"
            "\n"
            "```bash\n"
            "curl -fsS\n"
            "```\n"
        )
        regions = slice_field_regions(text, kind="kb")
        values = [text[a:b] for a, b in regions]
        self.assertEqual(
            values,
            [
                "Registry",
                "robot account",
                "Pull-only on demo/*",
                "- robot token: ⟦secret:abc⟧",
            ],
        )
        i = next_slice_field_index(regions, regions[1][0], forward=True)
        self.assertEqual(values[i], "Pull-only on demo/*")
        j = next_slice_field_index(regions, regions[i][0], forward=True)
        self.assertEqual(values[j], "- robot token: ⟦secret:abc⟧")

    def test_create_issue_fields_and_subtask_titles(self):
        text = (
            "project: PROJ\n"
            "summary: \n"
            "assignee: a@b.c\n"
            "stages: false\n"
            "---\n"
            "- env-dev | $name - env-dev\n"
        )
        regions = slice_field_regions(text)
        values = [text[a:b] for a, b in regions]
        self.assertEqual(values[0], "PROJ")
        self.assertEqual(values[1], "")
        self.assertEqual(values[2], "a@b.c")
        self.assertEqual(values[3], "false")
        self.assertEqual(values[-1], "$name - env-dev")

    def test_subject_headers_in_kb_file(self):
        text = (
            "# TOPIC: Registry\n"
            "## SUBJECT: one\n"
            "body\n"
            "## SUBJECT: two\n"
            "more\n"
        )
        regions = subject_header_regions(text)
        self.assertEqual([text[a:b] for a, b in regions], ["one", "two"])
        i = next_slice_field_index(regions, regions[0][0], forward=True)
        self.assertEqual(text[regions[i][0]:regions[i][1]], "two")

    def test_add_slice_cycles_ticket_and_body(self):
        text = (
            "# .notes add\n"
            "ticket: PROJ-1\n"
            "---\n"
            "- checked registry GC\n"
            "- follow-up tomorrow\n"
        )
        regions = slice_field_regions(text, kind="add")
        values = [text[a:b] for a, b in regions]
        self.assertEqual(
            values,
            ["PROJ-1", "checked registry GC", "follow-up tomorrow"],
        )
        i = next_slice_field_index(regions, regions[0][0], forward=True)
        self.assertEqual(values[i], "checked registry GC")

    def test_journal_ticket_headers_skip_date(self):
        text = (
            "# DATE: 2026-10-06  Tuesday\n"
            "# TODO:\n"
            "- a\n"
            "# PROJ-5124:\n"
            "- [CREATED]\n"
        )
        regions = notes_ticket_header_regions(text)
        self.assertEqual([text[a:b] for a, b in regions], ["TODO", "PROJ-5124"])


class TestFormatKb(unittest.TestCase):
    def test_canonical_spacing_idempotent(self):
        lines = [
            "# banner",
            "",
            "# TOPIC: Registry",
            "## SUBJECT: a",
            "- created: 2026-01-01",
            "cmd  ",
            "## SUBJECT: b",
            "- created: 2026-01-02",
            "- modified: 2026-02-02",
            "stay",
            "# TOPIC: GitOps",
            "## SUBJECT: waves",
            "anno",
        ]
        out = format_kb_lines(lines)
        joined = "\n".join(out)
        self.assertEqual(out[0], "# banner")
        self.assertIn("\n\n# TOPIC: Registry\n\n## SUBJECT: a\n", joined)
        self.assertIn("\n\n## SUBJECT: b\n", joined)
        self.assertIn("\n\n\n# TOPIC: GitOps\n\n## SUBJECT: waves\n", joined)
        self.assertEqual(out[out.index("## SUBJECT: a") + 3], "cmd")
        self.assertEqual(format_kb_lines(out), out)
        idx = parse_kb_index(out)
        self.assertEqual(find_subject(idx, "registry", "a")["created"], "2026-01-01")
        self.assertEqual(find_subject(idx, "registry", "b")["modified"], "2026-02-02")

    def test_format_does_not_reorder(self):
        lines = [
            "# TOPIC: Zulu",
            "## SUBJECT: later",
            "x",
            "# TOPIC: Alpha",
            "## SUBJECT: first",
            "y",
        ]
        out = format_kb_lines(lines)
        topics = [ln for ln in out if ln.startswith("# TOPIC:")]
        self.assertEqual(topics, ["# TOPIC: Zulu", "# TOPIC: Alpha"])


if __name__ == "__main__":
    unittest.main()
