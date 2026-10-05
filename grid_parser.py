"""Parse anchor .grid files (format detected from file content, not assumed)."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Literal


CornerOrder = Literal["tl", "tr", "br", "bl"]


@dataclass(frozen=True)
class CornerPoints:
    """Four corners in file order: top-left, top-right, bottom-right, bottom-left."""

    top_left: tuple[float, float]
    top_right: tuple[float, float]
    bottom_right: tuple[float, float]
    bottom_left: tuple[float, float]

    def as_list(self) -> list[tuple[float, float]]:
        return [
            self.top_left,
            self.top_right,
            self.bottom_right,
            self.bottom_left,
        ]

    def validate(self, context: str) -> None:
        for name, (x, y) in (
            ("top_left", self.top_left),
            ("top_right", self.top_right),
            ("bottom_right", self.bottom_right),
            ("bottom_left", self.bottom_left),
        ):
            if not (math.isfinite(x) and math.isfinite(y)):
                raise GridParseError(f"{context}: non-finite coordinate at {name}")

        if _quad_area(self.as_list()) <= 0:
            raise GridParseError(f"{context}: degenerate or self-intersecting corner quad")


@dataclass
class GridAnchor:
    filename: str
    corners: CornerPoints
    line_number: int


@dataclass
class GridDocument:
    """Parsed grid file plus explicit coordinate metadata from headers."""

    anchors: list[GridAnchor]
    crs: str | None = None
    units: Literal["geographic", "projected", "unknown"] = "unknown"
    lon_lat_order: bool = True  # True => x=lon, y=lat when geographic
    source_path: Path | None = None
    raw_headers: dict[str, str] = field(default_factory=dict)


class GridParseError(ValueError):
    pass


def _quad_area(points: list[tuple[float, float]]) -> float:
    area = 0.0
    n = len(points)
    for i in range(n):
        x1, y1 = points[i]
        x2, y2 = points[(i + 1) % n]
        area += x1 * y2 - x2 * y1
    return abs(area) / 2.0


def _parse_header(line: str) -> tuple[str, str] | None:
    stripped = line.strip()
    if not stripped.startswith("#"):
        return None
    body = stripped.lstrip("#").strip()
    if ":" not in body:
        return None
    key, val = body.split(":", 1)
    return key.strip().lower(), val.strip()


class GridParser:
    """
    Parse .grid anchor files.

    Supported layout (see examples/grid_example.grid):

    - Optional comment headers starting with ``# key: value``
    - One record per line: ``filename x_tl y_tl x_tr y_tr x_br y_br x_bl y_bl``
    - Whitespace or comma separated; ``#`` starts an end-of-line comment.

    No industry-wide .grid standard exists in this repository; this parser is
    isolated so alternate layouts can be added (e.g. ``parse_esri_grid``).
    """

    RECORD_MIN_FIELDS = 9  # filename + 8 coordinates

    def parse_file(self, path: Path) -> GridDocument:
        text = path.read_text(encoding="utf-8")
        doc = self.parse_text(text)
        doc.source_path = path
        return doc

    def parse_text(self, text: str) -> GridDocument:
        doc = GridDocument(anchors=[])
        pending: list[tuple[int, str]] = []

        for line_no, raw in enumerate(text.splitlines(), start=1):
            line = raw.strip()
            if not line:
                continue
            header = _parse_header(line)
            if header is not None:
                key, val = header
                doc.raw_headers[key] = val
                self._apply_header(doc, key, val)
                continue
            if line.startswith("#"):
                continue
            pending.append((line_no, line))

        for line_no, line in pending:
            anchor = self._parse_record_line(line, line_no)
            doc.anchors.append(anchor)

        if not doc.anchors:
            raise GridParseError("No anchor records found in .grid file")

        return doc

    def _apply_header(self, doc: GridDocument, key: str, val: str) -> None:
        if key in ("crs", "epsg", "srs"):
            doc.crs = val if val.upper().startswith("EPSG:") else f"EPSG:{val}"
        elif key == "units":
            low = val.lower()
            if low in ("geographic", "geo", "latlon", "lat/lon"):
                doc.units = "geographic"
            elif low in ("projected", "metric", "cartesian", "meters", "metres"):
                doc.units = "projected"
        elif key in ("lon_lat_order", "coord_order"):
            low = val.lower().replace(" ", "")
            doc.lon_lat_order = low in ("lonlat", "lon,lat", "longitude,latitude", "x,y")

    def _parse_record_line(self, line: str, line_no: int) -> GridAnchor:
        if "#" in line:
            line = line.split("#", 1)[0].strip()
        parts = re.split(r"[\s,]+", line.strip())
        if len(parts) < self.RECORD_MIN_FIELDS:
            raise GridParseError(
                f"Line {line_no}: expected at least {self.RECORD_MIN_FIELDS} fields "
                f"(filename + 8 coordinates), got {len(parts)}"
            )
        filename = parts[0]
        try:
            nums = [float(p) for p in parts[1:9]]
        except ValueError as exc:
            raise GridParseError(f"Line {line_no}: invalid numeric coordinate") from exc

        corners = CornerPoints(
            top_left=(nums[0], nums[1]),
            top_right=(nums[2], nums[3]),
            bottom_right=(nums[4], nums[5]),
            bottom_left=(nums[6], nums[7]),
        )
        ctx = f"Line {line_no} ({filename})"
        corners.validate(ctx)
        return GridAnchor(filename=filename, corners=corners, line_number=line_no)

    def validate_against_images(
        self,
        doc: GridDocument,
        image_paths: Iterable[Path],
    ) -> tuple[dict[str, Path], list[str], list[str]]:
        """
        Returns (filename->path map, missing anchors for images, orphan anchors).
        """
        by_name: dict[str, Path] = {p.name: p for p in image_paths}
        anchored_names = {a.filename for a in doc.anchors}
        image_names = set(by_name)

        missing = sorted(image_names - anchored_names)
        orphan = sorted(anchored_names - image_names)
        return by_name, missing, orphan
