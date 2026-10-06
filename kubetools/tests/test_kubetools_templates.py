#!/usr/bin/env python3
"""Create-slice YAML templates (no Sublime)."""
import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

import kubetools_common as ktc  # noqa: E402
import kubetools_templates as tpl  # noqa: E402


class TestTemplates(unittest.TestCase):
    def test_order_covers_all_keys(self):
        self.assertEqual(set(tpl.TEMPLATE_ORDER), set(tpl.TEMPLATES))

    def test_each_template_is_a_blocked_example(self):
        for key in tpl.TEMPLATE_ORDER:
            spec = tpl.TEMPLATES[key]
            body = spec["body"]
            self.assertTrue(ktc.looks_like_k8s_manifest(body), key)
            self.assertTrue(ktc.example_namespace_unchanged(body), key)
            m = re.search(r"(?m)^  name:\s+(\S+)", body)
            self.assertIsNotNone(m, key)
            self.assertTrue(ktc.example_resource_name_unchanged(m.group(1)), key)
            self.assertEqual(spec["caption"], spec["kind_label"], key)
            self.assertNotIn(".kubetools", spec["caption"])


if __name__ == "__main__":
    unittest.main()
