"""Post-split parity: kb_pure must match notes_kb_pure for shared KB helpers."""
from __future__ import annotations

import importlib.util
import os
import sys
import unittest

_KB_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_NOTES_ROOT = os.path.join(os.path.dirname(_KB_ROOT), "notes")
for p in (_KB_ROOT, _NOTES_ROOT):
    if p not in sys.path:
        sys.path.insert(0, p)


def _load(name: str, path: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


_kb = _load("kb_pure_under_test", os.path.join(_KB_ROOT, "kb_pure.py"))
_notes = _load("notes_kb_pure_under_test", os.path.join(_NOTES_ROOT, "notes_kb_pure.py"))

_SHARED = (
    "kb_slug",
    "kb_ref",
    "kb_parse_ref",
    "kb_ref_at_col",
    "parse_kb_index",
    "find_subject",
    "append_subject_to_lines",
    "build_subject_stub",
    "drop_empty_topic_headers",
    "splice_subject_body",
    "subject_editor_document",
    "parse_subject_editor_buffer",
    "sort_subjects_newest_first",
    "subject_header_regions",
    "format_kb_lines",
)


class TestKbNotesPureParity(unittest.TestCase):
    def test_shared_helpers_same_source(self):
        missing = [n for n in _SHARED if not hasattr(_kb, n) or not hasattr(_notes, n)]
        self.assertEqual(missing, [])
        import inspect

        for name in _SHARED:
            a = inspect.getsource(getattr(_kb, name))
            b = inspect.getsource(getattr(_notes, name))
            self.assertEqual(a, b, msg=f"drift in {name}")

    def test_roundtrip_sample(self):
        sample = [
            "# TOPIC: Registry",
            "## SUBJECT: cleanup images",
            "- created: 2026-10-06",
            "gc now",
        ]
        idx_a = _kb.parse_kb_index(sample)
        idx_b = _notes.parse_kb_index(sample)
        self.assertEqual(idx_a["topics"], idx_b["topics"])
        self.assertEqual(
            _kb.kb_ref_at_col("see kb:registry/cleanup-images today", 10),
            _notes.kb_ref_at_col("see kb:registry/cleanup-images today", 10),
        )


if __name__ == "__main__":
    unittest.main()
