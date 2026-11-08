"""Loaded trace identity must bind host source as well as model helpers."""

import unittest


class TraceBuildIdentityTest(unittest.TestCase):
    def test_host_source_helpers_checkpoint_and_edition_each_change_identity(self):
        from shared_player.compiler import trace_build_identity

        checkpoint, source, helpers = "a" * 64, "b" * 64, "c" * 64
        baseline = trace_build_identity("release", checkpoint, source, helpers)
        self.assertEqual(
            baseline,
            trace_build_identity("release", checkpoint, source, helpers),
        )
        self.assertTrue(baseline["build_id"].startswith("shared-release-" + "a" * 16))
        variants = [
            ("release", checkpoint, "d" * 64, helpers),
            ("release", checkpoint, source, "e" * 64),
            ("release", "f" * 64, source, helpers),
            ("diagnostic", checkpoint, source, helpers),
        ]
        ids = {baseline["build_id"]}
        for arguments in variants:
            ids.add(trace_build_identity(*arguments)["build_id"])
        self.assertEqual(len(ids), 5)
        self.assertEqual(baseline["authored_source_manifest_sha256"], source)
        self.assertEqual(baseline["core_helper_source_sha256"], helpers)
        self.assertEqual(baseline["checkpoint_sha256"], checkpoint)
        self.assertEqual(baseline["edition"], "release")
        self.assertEqual(len(baseline["sha256"]), 64)


if __name__ == "__main__":
    unittest.main()
