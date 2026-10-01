#!/usr/bin/env python3
"""Unit tests for kubetools_common (no Sublime required)."""
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import kubetools_common as ktc  # noqa: E402


class TestCommon(unittest.TestCase):
    def test_clamp_timeout(self):
        self.assertEqual(ktc.clamp_timeout("x", default=30), 30)
        self.assertEqual(ktc.clamp_timeout(1, minimum=5), 5)
        self.assertEqual(ktc.clamp_timeout(9999, maximum=600), 600)

    def test_validate_binary_metachar(self):
        self.assertIn("forbidden", ktc.validate_binary_path("kubectl;rm", "kubectl"))
        self.assertIsNone(ktc.validate_binary_path("kubectl", "kubectl"))

    def test_dangerous_name(self):
        self.assertTrue(ktc.is_dangerous_name("prod-a"))
        self.assertTrue(ktc.is_dangerous_name("my-production-cluster"))
        self.assertFalse(ktc.is_dangerous_name("dev-cluster"))

    def test_scrub_secret_text(self):
        msg = ktc.scrub_secret_text(
            "Bearer supersecrettoken123 failed", ["supersecrettoken123"]
        )
        self.assertNotIn("supersecrettoken123", msg)
        self.assertIn("[redacted]", msg)
        msg2 = ktc.scrub_secret_text(
            "tok=perm:user.ABCDEFG.xyz Authorization: Bearer abc"
        )
        self.assertNotIn("perm:user.ABCDEFG.xyz", msg2)
        self.assertNotIn("Bearer abc", msg2)
        self.assertIn("perm:[redacted]", msg2)
        self.assertIn("[redacted]", msg2)
        msg3 = ktc.scrub_secret_text("token glpat-ABCDEFGHIJKLMN end")
        self.assertNotIn("glpat-ABCDEFGHIJKLMN", msg3)
        self.assertIn("glpat-[redacted]", msg3)

    def test_write_temp_under_tmpdir(self):
        path = ktc.write_temp_under_tmpdir("kind: Pod\n", suffix=".yaml")
        try:
            self.assertTrue(path.startswith(tempfile.gettempdir()) or os.path.realpath(path).startswith(
                os.path.realpath(tempfile.gettempdir())
            ))
            with open(path, encoding="utf-8") as fh:
                self.assertIn("Pod", fh.read())
        finally:
            os.unlink(path)

    def test_run_subprocess_no_shell(self):
        code, out, err = ktc.run_subprocess(
            [sys.executable, "-c", "print('ok')"], timeout=10
        )
        self.assertEqual(code, 0)
        self.assertIn("ok", out)


class TestPathContextGuess(unittest.TestCase):
    CONTEXTS = [
        "dev-cluster",
        "dev-clusterlab",
        "test-cluster",
        "prod-cluster",
        "prod-core-a",
        "admin@lab",
    ]

    def test_exact_segment_prefers_dev_cluster_not_devlab(self):
        path = "/home/user/gitops/environments/dev/dev-cluster/apps/foo/bar.yaml"
        ordered, selected, reasons = ktc.rank_contexts_for_path(
            self.CONTEXTS, path, current_context="admin@lab"
        )
        self.assertEqual(ordered[selected], "dev-cluster")
        self.assertIn("dev-cluster", reasons)
        self.assertNotEqual(ordered[selected], "dev-clusterlab")

    def test_devlab_not_confused_with_dev(self):
        path = "/repo/environments/dev/dev-clusterlab/apps/x.yaml"
        ordered, selected, _r = ktc.rank_contexts_for_path(self.CONTEXTS, path)
        self.assertEqual(ordered[selected], "dev-clusterlab")

    def test_ambiguous_env_token_alone_does_not_match(self):
        # segment "dev" must not select a random *dev* context
        score, _ = ktc.score_name_against_path("dev", "/repo/environments/dev/foo/x.yaml")
        self.assertEqual(score, 0)

    def test_prod_path_still_only_preselects(self):
        path = "/repo/environments/prod/prod-cluster/sealed-secrets/ns/x.yaml"
        ordered, selected, reasons = ktc.rank_contexts_for_path(self.CONTEXTS, path)
        self.assertEqual(ordered[selected], "prod-cluster")
        self.assertTrue(ktc.is_dangerous_name(ordered[selected]))

    def test_alias(self):
        path = "/repo/environments/prod/prod-a/apps/x.yaml"
        contexts = ["org-prod-a", "dev-cluster"]
        ordered, selected, reasons = ktc.rank_contexts_for_path(
            contexts, path, aliases={"prod-a": "org-prod-a"}
        )
        self.assertEqual(ordered[selected], "org-prod-a")
        self.assertIn("alias", reasons["org-prod-a"])

    def test_no_match_falls_back_to_current(self):
        path = "/tmp/unrelated/file.yaml"
        ordered, selected, reasons = ktc.rank_contexts_for_path(
            self.CONTEXTS, path, current_context="test-cluster"
        )
        self.assertEqual(reasons, {})
        self.assertEqual(ordered[selected], "test-cluster")

    def test_guess_name_index_for_stages(self):
        stages = [{"name": "dev-cluster"}, {"name": "prod-cluster"}]
        path = "/repo/environments/dev/dev-cluster/apps/x.yaml"
        self.assertEqual(ktc.guess_name_index(stages, path), 0)


if __name__ == "__main__":
    unittest.main()
