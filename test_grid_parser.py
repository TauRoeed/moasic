#!/usr/bin/env python3
"""Validation tests for grid_parser.py (no TIFF data required)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from grid_parser import GridParseError, GridParser


SAMPLE = """\
# crs: EPSG:32636
# units: projected
img_a.tif 0 10 10 10 10 0 0 0
img_b.tif 5 5 15 5 15 15 5 15
"""


class GridParserTests(unittest.TestCase):
    def setUp(self) -> None:
        self.parser = GridParser()

    def test_parse_headers_and_records(self) -> None:
        doc = self.parser.parse_text(SAMPLE)
        self.assertEqual(doc.crs, "EPSG:32636")
        self.assertEqual(doc.units, "projected")
        self.assertEqual(len(doc.anchors), 2)
        self.assertEqual(doc.anchors[0].filename, "img_a.tif")
        self.assertEqual(doc.anchors[0].corners.top_left, (0.0, 10.0))

    def test_missing_records_raises(self) -> None:
        with self.assertRaises(GridParseError):
            self.parser.parse_text("# crs: EPSG:4326\n")

    def test_too_few_fields_raises(self) -> None:
        with self.assertRaises(GridParseError):
            self.parser.parse_text("bad.tif 1 2 3\n")

    def test_validate_against_images(self) -> None:
        doc = self.parser.parse_text(SAMPLE)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "img_a.tif").write_bytes(b"x")
            (root / "extra.tif").write_bytes(b"x")
            by_name, missing, orphan = self.parser.validate_against_images(
                doc, [root / "img_a.tif", root / "extra.tif"]
            )
            self.assertIn("img_a.tif", by_name)
            self.assertEqual(missing, ["extra.tif"])
            self.assertEqual(orphan, ["img_b.tif"])

    def test_geographic_header(self) -> None:
        text = "# units: geographic\n# crs: EPSG:4326\na.tif 34 32 34.1 32 34.1 31.9 34 31.9\n"
        doc = self.parser.parse_text(text)
        self.assertEqual(doc.units, "geographic")


if __name__ == "__main__":
    unittest.main()
