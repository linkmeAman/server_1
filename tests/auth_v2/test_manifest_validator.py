"""Tests for authz resources manifest validator."""

from __future__ import annotations

import tempfile
import unittest

from scripts.auth_v2.validate_authz_manifest import _load_manifest, _validate_manifest_records


class TestManifestValidator(unittest.TestCase):
    def test_duplicate_code_and_missing_parent_are_rejected(self):
        content = """
resources:
  - code: reports
    name: Reports
    parent: null
  - code: reports
    name: Reports 2
    parent: null
  - code: reports.child
    name: Child
    parent: missing
"""
        with tempfile.NamedTemporaryFile("w+", suffix=".yaml") as handle:
            handle.write(content)
            handle.flush()
            records = _load_manifest(handle.name)
            errors = _validate_manifest_records(records)

        self.assertTrue(any("duplicate resource code" in error for error in errors))
        self.assertTrue(any("parent not found" in error for error in errors))

    def test_valid_manifest_passes(self):
        content = """
resources:
  - code: global
    name: Global
    parent: null
  - code: reports
    name: Reports
    parent: null
  - code: reports.center_performance
    name: Center Performance
    parent: reports
"""
        with tempfile.NamedTemporaryFile("w+", suffix=".yaml") as handle:
            handle.write(content)
            handle.flush()
            records = _load_manifest(handle.name)
            errors = _validate_manifest_records(records)

        self.assertEqual([], errors)


if __name__ == "__main__":
    unittest.main()
