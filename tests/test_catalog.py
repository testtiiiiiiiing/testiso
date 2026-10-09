import json
from pathlib import Path
import os
import tempfile
import unittest
from unittest.mock import patch

from winslim.catalog import CatalogCache, scan_identity


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.iso = Path(self.temp.name) / "source.iso"
        self.iso.write_bytes(b"source")
        self.cache = CatalogCache(Path(self.temp.name) / "catalog")
        self.identity = scan_identity(self.iso, 3)
        self.rows = [("features", "FeatureA", "Enabled")]

    def test_completed_catalog_survives_new_cache_instance(self):
        self.assertTrue(self.cache.save(self.identity, self.rows))
        reopened = CatalogCache(self.cache.folder)
        self.assertEqual(reopened.load(self.identity), self.rows)

    def test_changed_iso_or_edition_cannot_reuse_old_catalog(self):
        self.cache.save(self.identity, self.rows)
        self.assertIsNone(self.cache.load(scan_identity(self.iso, 4)))
        old = self.iso.stat()
        self.iso.write_bytes(b"edited")
        os.utime(self.iso, ns=(old.st_atime_ns, old.st_mtime_ns + 1000000000))
        self.assertIsNone(self.cache.load(scan_identity(self.iso, 3)))

    def test_corrupt_mismatched_and_oversized_cache_are_ignored(self):
        self.cache.save(self.identity, self.rows)
        file = Path(self.cache.path(self.identity))
        for data in (
            b"not-json",
            b"null",
            json.dumps(
                {"identity": list(self.identity), "rows": [["invalid", "Name", "State"]]}
            ).encode(),
            json.dumps({"identity": [], "rows": self.rows}).encode(),
            b"x" * (self.cache.MAX_BYTES + 1),
        ):
            with self.subTest(size=len(data)):
                file.write_bytes(data)
                self.assertIsNone(self.cache.load(self.identity))

    def test_unwritable_cache_does_not_fail_scan(self):
        with patch("winslim.catalog.atomic_json", side_effect=PermissionError("denied")):
            self.assertFalse(self.cache.save(self.identity, self.rows))
        self.assertIsNone(self.cache.load(self.identity))
