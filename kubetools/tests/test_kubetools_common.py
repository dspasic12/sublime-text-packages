#!/usr/bin/env python3
"""Unit tests for kubetools_common (no Sublime required)."""
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
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

    def test_looks_like_manifest_and_secret(self):
        deploy = (
            "apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: demo-app\n"
        )
        self.assertTrue(ktc.looks_like_k8s_manifest(deploy))
        self.assertFalse(ktc.looks_like_k8s_manifest("hello: world\n"))
        secret = (
            "apiVersion: v1\nkind: Secret\nmetadata:\n  name: x\nstringData:\n  a: b\n"
        )
        self.assertTrue(ktc.looks_like_plain_secret(secret))
        self.assertFalse(ktc.looks_like_sealed_secret(secret))
        sealed = "apiVersion: bitnami.com/v1alpha1\nkind: SealedSecret\n"
        self.assertTrue(ktc.looks_like_sealed_secret(sealed))
        self.assertFalse(ktc.looks_like_plain_secret(sealed))

    def test_example_namespace_unchanged(self):
        blocked = (
            "apiVersion: v1\nkind: Secret\nmetadata:\n"
            "  name: x\n  namespace: CHANGE-ME\n"
        )
        ok = blocked.replace("CHANGE-ME", "demo-ns")
        self.assertTrue(ktc.example_namespace_unchanged(blocked))
        self.assertFalse(ktc.example_namespace_unchanged(ok))
        self.assertFalse(ktc.example_namespace_unchanged(""))

    def test_helm_gitops_and_stamp(self):
        deploy = (
            "apiVersion: apps/v1\nkind: Deployment\nmetadata:\n"
            "  name: nginx\n  namespace: demo-ns\n"
        )
        self.assertIsNone(ktc.mutating_source_block_reason(None, deploy))
        self.assertTrue(
            ktc.looks_like_helm_source(
                "/chart/templates/deploy.yaml",
                "kind: Deployment\n  image: {{ .Values.image }}\n",
            )
        )
        self.assertTrue(
            ktc.looks_like_helm_source(
                "/apps/foo/demo-app-values-override.yaml", deploy
            )
        )
        self.assertTrue(ktc.looks_like_helm_source("/charts/app/Chart.yaml", "apiVersion: v2\nname: x\nversion: 1.0.0\n"))
        self.assertTrue(
            ktc.looks_like_gitops_source(
                "/repo/bootstrap/dev/env-dev/foo.yaml"
            )
        )
        argo = (
            "apiVersion: argoproj.io/v1alpha1\nkind: Application\n"
            "metadata:\n  name: x\n"
        )
        self.assertTrue(ktc.looks_like_argo_cd_manifest(argo))
        self.assertIsNotNone(ktc.mutating_source_block_reason(None, argo))
        self.assertTrue(ktc.example_resource_name_unchanged("example"))
        self.assertTrue(ktc.example_resource_name_unchanged("example-secrets"))
        self.assertFalse(ktc.example_resource_name_unchanged("nginx"))
        self.assertTrue(ktc.is_protected_namespace("kube-system"))
        stamped = ktc.stamp_applied_header("# created: x\nkind: X\n", "NOW")
        self.assertTrue(stamped.startswith("# applied: NOW\n"))
        again = ktc.stamp_applied_header(stamped, "LATER")
        self.assertTrue(again.startswith("# applied: LATER\n"))
        self.assertEqual(again.count("# applied:"), 1)

    def test_yaml_scalar_field_regions(self):
        text = (
            "apiVersion: apps/v1\n"
            "kind: Deployment\n"
            "metadata:\n"
            "  name: example\n"
            "  namespace: demo-ns\n"
            "spec:\n"
            "  replicas: 1\n"
        )
        values = [text[a:b] for a, b in ktc.yaml_scalar_field_regions(text)]
        self.assertEqual(
            values, ["apps/v1", "Deployment", "example", "demo-ns", "1"]
        )
        idx = ktc.next_field_index(
            ktc.yaml_scalar_field_regions(text), 0, forward=True
        )
        self.assertEqual(idx, 0)

    def test_write_temp_under_tmpdir(self):
        path = ktc.write_temp_under_tmpdir("kind: Pod\n", suffix=".yaml")
        try:
            self.assertTrue(ktc.path_is_under(path, tempfile.gettempdir()))
            with open(path, encoding="utf-8") as fh:
                self.assertIn("Pod", fh.read())
        finally:
            os.unlink(path)

    def test_path_is_under(self):
        root = tempfile.mkdtemp()
        try:
            child = os.path.join(root, "n", "f.txt")
            os.makedirs(os.path.dirname(child))
            with open(child, "w", encoding="utf-8") as fh:
                fh.write("x")
            self.assertTrue(ktc.path_is_under(child, root))
            self.assertTrue(ktc.path_is_under(root, root))
            self.assertFalse(ktc.path_is_under(root + "_other", root))
        finally:
            import shutil
            shutil.rmtree(root, ignore_errors=True)

    def test_try_fchmod_missing_is_noop(self):
        ktc.try_fchmod(-1)

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
