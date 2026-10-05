#!/usr/bin/env python3
"""Build a georeferenced mosaic from anchored TIFF images and a .grid file."""

from __future__ import annotations

import argparse
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import cv2
import numpy as np
import pyproj
import rasterio
from rasterio.crs import CRS
from rasterio.transform import Affine, from_bounds
from rasterio.windows import Window

from grid_parser import GridAnchor, GridDocument, GridParseError, GridParser


BlendMode = Literal["feather", "max"]


@dataclass
class ImageMetadata:
    path: Path
    anchor: GridAnchor
    width: int
    height: int
    band_count: int
    dtype: np.dtype
    nodata: float | None


@dataclass
class MosaicGeometry:
    crs: CRS
    resolution: float  # metres (or CRS units) per pixel
    min_x: float
    min_y: float
    max_x: float
    max_y: float
    width: int
    height: int
    transform: Affine

    @property
    def extent_str(self) -> str:
        return (
            f"({self.min_x:.3f}, {self.min_y:.3f}) – "
            f"({self.max_x:.3f}, {self.max_y:.3f})"
        )


class CoordinateSystemResolver:
    """Resolve working CRS and project corner coordinates."""

    def __init__(self, doc: GridDocument, crs_override: str | None) -> None:
        self.doc = doc
        self.crs_override = crs_override

    def resolve(self, anchors_projected: list[tuple[float, float]]) -> CRS:
        if self.crs_override:
            return CRS.from_user_input(self.crs_override)

        if self.doc.crs:
            crs = CRS.from_user_input(self.doc.crs)
            if self.doc.units == "geographic" or crs.is_geographic:
                return self._auto_utm(anchors_projected)
            return crs

        if self.doc.units == "geographic":
            return self._auto_utm(anchors_projected)

        if self._looks_geographic(anchors_projected):
            return self._auto_utm(anchors_projected)

        raise ValueError(
            "Could not determine coordinate system. "
            "Add '# crs: EPSG:xxxx' to the .grid file or pass --crs."
        )

    @staticmethod
    def _looks_geographic(points: list[tuple[float, float]]) -> bool:
        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        return (
            min(xs) >= -180.0
            and max(xs) <= 180.0
            and min(ys) >= -90.0
            and max(ys) <= 90.0
        )

    @staticmethod
    def _auto_utm(points: list[tuple[float, float]]) -> CRS:
        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        lon = sum(xs) / len(xs)
        lat = sum(ys) / len(ys)
        zone = int((lon + 180) / 6) + 1
        zone = max(1, min(60, zone))
        epsg = 32600 + zone if lat >= 0 else 32700 + zone
        return CRS.from_epsg(epsg)

    def project_corners(
        self, corners: list[tuple[float, float]], target_crs: CRS
    ) -> list[tuple[float, float]]:
        if self.doc.crs:
            src = CRS.from_user_input(self.doc.crs)
            if not src.is_geographic and self.doc.units != "geographic":
                if src == target_crs:
                    return corners
                transformer = pyproj.Transformer.from_crs(
                    src, target_crs, always_xy=True
                )
                return [tuple(transformer.transform(x, y)) for x, y in corners]

        if self.doc.units == "projected" and not self.doc.crs:
            return corners

        if self.doc.units == "geographic" or (
            self.doc.crs and CRS.from_user_input(self.doc.crs).is_geographic
        ):
            src = CRS.from_user_input(self.doc.crs or "EPSG:4326")
            transformer = pyproj.Transformer.from_crs(src, target_crs, always_xy=True)
            return [tuple(transformer.transform(x, y)) for x, y in corners]

        if self.crs_override:
            src = CRS.from_user_input(self.crs_override)
            if src == target_crs:
                return corners
            transformer = pyproj.Transformer.from_crs(src, target_crs, always_xy=True)
            return [tuple(transformer.transform(x, y)) for x, y in corners]

        return corners


def discover_tiffs(folder: Path) -> list[Path]:
    exts = {".tif", ".tiff", ".TIF", ".TIFF"}
    return sorted(p for p in folder.iterdir() if p.suffix in exts and p.is_file())


def load_image_metadata(path: Path, anchor: GridAnchor) -> ImageMetadata:
    with rasterio.open(path) as src:
        return ImageMetadata(
            path=path,
            anchor=anchor,
            width=src.width,
            height=src.height,
            band_count=src.count,
            dtype=src.dtypes[0],
            nodata=src.nodata,
        )


def estimate_resolution(meta: ImageMetadata, corners_m: list[tuple[float, float]]) -> tuple[float, float]:
    tl, tr, br, bl = corners_m
    w_px = max(meta.width - 1, 1)
    h_px = max(meta.height - 1, 1)
    width_m = math.hypot(tr[0] - tl[0], tr[1] - tl[1])
    height_m = math.hypot(bl[0] - tl[0], bl[1] - tl[1])
    return width_m / w_px, height_m / h_px


def build_mosaic_geometry(
    all_corners_m: list[list[tuple[float, float]]],
    resolution: float,
    crs: CRS,
) -> MosaicGeometry:
    flat = [pt for corners in all_corners_m for pt in corners]
    xs = [p[0] for p in flat]
    ys = [p[1] for p in flat]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)

    width = max(1, int(math.ceil((max_x - min_x) / resolution)))
    height = max(1, int(math.ceil((max_y - min_y) / resolution)))
    transform = from_bounds(min_x, min_y, max_x, max_y, width, height)
    return MosaicGeometry(
        crs=crs,
        resolution=resolution,
        min_x=min_x,
        min_y=min_y,
        max_x=max_x,
        max_y=max_y,
        width=width,
        height=height,
        transform=transform,
    )


def world_to_pixel(
    x: float, y: float, geom: MosaicGeometry
) -> tuple[float, float]:
    col, row = ~geom.transform * (x, y)
    return col, row


def pixel_corners_for_image(
    meta: ImageMetadata, corners_m: list[tuple[float, float]], geom: MosaicGeometry
) -> np.ndarray:
    pts = []
    for x, y in corners_m:
        col, row = world_to_pixel(x, y, geom)
        pts.append([col, row])
    return np.float32(pts)


def image_world_bounds(corners_m: list[tuple[float, float]]) -> tuple[float, float, float, float]:
    xs = [p[0] for p in corners_m]
    ys = [p[1] for p in corners_m]
    return min(xs), min(ys), max(xs), max(ys)


def tile_world_bounds(
    geom: MosaicGeometry, col_off: int, row_off: int, w: int, h: int
) -> tuple[float, float, float, float]:
    x0, y0 = geom.transform * (col_off, row_off + h)
    x1, y1 = geom.transform * (col_off + w, row_off)
    return min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)


def bounds_intersect(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> bool:
    return not (a[2] < b[0] or a[0] > b[2] or a[3] < b[1] or a[1] > b[3])


def read_image_bands(path: Path) -> tuple[np.ndarray, float | None]:
    with rasterio.open(path) as src:
        data = src.read(out_dtype=src.dtypes[0])
        return data, src.nodata


def warp_image_to_tile(
    data: np.ndarray,
    meta: ImageMetadata,
    dst_corners_px: np.ndarray,
    tile_col: int,
    tile_row: int,
    tile_w: int,
    tile_h: int,
) -> tuple[np.ndarray, np.ndarray]:
    h, w = meta.height, meta.width
    src_pts = np.float32([[0, 0], [w - 1, 0], [w - 1, h - 1], [0, h - 1]])
    H = cv2.getPerspectiveTransform(src_pts, dst_corners_px)
    T = np.array([[1, 0, -tile_col], [0, 1, -tile_row], [0, 0, 1]], dtype=np.float64)
    H_tile = T @ H

    bands, _, _ = data.shape
    warped_bands = []
    for b in range(bands):
        band = data[b]
        if band.dtype != np.float32:
            band_f = band.astype(np.float32)
        else:
            band_f = band
        warped = cv2.warpPerspective(
            band_f,
            H_tile,
            (tile_w, tile_h),
            flags=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_CONSTANT,
            borderValue=0,
        )
        warped_bands.append(warped)
    warped_data = np.stack(warped_bands, axis=0)

    mask = cv2.warpPerspective(
        np.ones((h, w), dtype=np.uint8),
        H_tile,
        (tile_w, tile_h),
        flags=cv2.INTER_NEAREST,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=0,
    )
    return warped_data, mask.astype(np.float32)


def feather_weights(mask: np.ndarray) -> np.ndarray:
    if mask.max() <= 0:
        return mask
    dist = cv2.distanceTransform((mask > 0).astype(np.uint8), cv2.DIST_L2, 3)
    return dist.astype(np.float32)


class TileBasedMosaicRenderer:
    def __init__(
        self,
        geometry: MosaicGeometry,
        images: list[ImageMetadata],
        projected_corners: list[list[tuple[float, float]]],
        blend: BlendMode,
        tile_size: int,
    ) -> None:
        self.geometry = geometry
        self.images = images
        self.projected_corners = projected_corners
        self.blend = blend
        self.tile_size = tile_size
        self._px_corners = [
            pixel_corners_for_image(meta, corners, geometry)
            for meta, corners in zip(images, projected_corners, strict=True)
        ]
        self._world_bounds = [
            image_world_bounds(c) for c in projected_corners
        ]

    def render_tile(self, col_off: int, row_off: int, w: int, h: int) -> tuple[np.ndarray, np.ndarray]:
        tb = tile_world_bounds(self.geometry, col_off, row_off, w, h)
        band_count = self.images[0].band_count
        dtype = self.images[0].dtype
        accum = np.zeros((band_count, h, w), dtype=np.float64)
        weight_sum = np.zeros((h, w), dtype=np.float64)

        for meta, corners_px, wb, corners_m in zip(
            self.images,
            self._px_corners,
            self._world_bounds,
            self.projected_corners,
            strict=True,
        ):
            if not bounds_intersect(wb, tb):
                continue
            data, nodata = read_image_bands(meta.path)
            if nodata is not None:
                data = np.where(data == nodata, 0, data)

            warped, mask = warp_image_to_tile(
                data, meta, corners_px, col_off, row_off, w, h
            )
            if self.blend == "feather":
                wgt = feather_weights(mask)
            else:
                wgt = mask

            if wgt.max() <= 0:
                continue

            for b in range(band_count):
                accum[b] += warped[b].astype(np.float64) * wgt
            weight_sum += wgt

        out = np.zeros((band_count, h, w), dtype=dtype)
        valid = weight_sum > 0
        for b in range(band_count):
            band = np.zeros((h, w), dtype=np.float64)
            band[valid] = accum[b][valid] / weight_sum[valid]
            if np.issubdtype(dtype, np.integer):
                info = np.iinfo(dtype)
                band = np.clip(band, info.min, info.max)
            out[b] = band.astype(dtype)
        return out, valid.astype(np.uint8)


class GeoTIFFWriter:
    def __init__(
        self,
        path: Path,
        geometry: MosaicGeometry,
        band_count: int,
        dtype: str,
        compression: str,
        overwrite: bool,
    ) -> None:
        self.path = path
        self.geometry = geometry
        self.band_count = band_count
        self.dtype = dtype
        self.compression = compression
        self.overwrite = overwrite

    def write_tiled(self, renderer: TileBasedMosaicRenderer, tile_size: int) -> None:
        if self.path.exists():
            if not self.overwrite:
                raise FileExistsError(f"Output exists: {self.path} (use --overwrite)")
            self.path.unlink()

        profile = {
            "driver": "GTiff",
            "height": self.geometry.height,
            "width": self.geometry.width,
            "count": self.band_count,
            "dtype": self.dtype,
            "crs": self.geometry.crs,
            "transform": self.geometry.transform,
            "tiled": True,
            "blockxsize": min(tile_size, self.geometry.width),
            "blockysize": min(tile_size, self.geometry.height),
            "compress": self.compression if self.compression != "none" else None,
            "BIGTIFF": "IF_SAFER",
        }
        profile = {k: v for k, v in profile.items() if v is not None}

        with rasterio.open(self.path, "w", **profile) as dst:
            for row_off in range(0, self.geometry.height, tile_size):
                th = min(tile_size, self.geometry.height - row_off)
                for col_off in range(0, self.geometry.width, tile_size):
                    tw = min(tile_size, self.geometry.width - col_off)
                    tile_data, _ = renderer.render_tile(col_off, row_off, tw, th)
                    window = Window(col_off, row_off, tw, th)
                    dst.write(tile_data, window=window)


def format_bytes(num: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if num < 1024:
            return f"{num:.1f} {unit}"
        num /= 1024
    return f"{num:.1f} PB"


def estimate_output_bytes(width: int, height: int, bands: int, dtype: np.dtype) -> float:
    itemsize = np.dtype(dtype).itemsize
    return width * height * bands * itemsize * 0.6  # rough compression factor


def run(args: argparse.Namespace) -> int:
    images_dir = Path(args.images)
    grid_path = Path(args.grid)
    output_path = Path(args.output)

    if not images_dir.is_dir():
        print(f"Error: images folder not found: {images_dir}", file=sys.stderr)
        return 1
    if not grid_path.is_file():
        print(f"Error: grid file not found: {grid_path}", file=sys.stderr)
        return 1

    tiff_paths = discover_tiffs(images_dir)
    parser = GridParser()
    try:
        doc = parser.parse_file(grid_path)
    except GridParseError as exc:
        print(f"Grid parse error: {exc}", file=sys.stderr)
        return 1

    by_name, missing_anchors, orphan_anchors = parser.validate_against_images(doc, tiff_paths)
    if missing_anchors:
        print("Error: TIFF files without grid anchors:", file=sys.stderr)
        for name in missing_anchors:
            print(f"  - {name}", file=sys.stderr)
        return 1
    if orphan_anchors:
        print("Error: grid entries without matching TIFF:", file=sys.stderr)
        for name in orphan_anchors:
            print(f"  - {name}", file=sys.stderr)
        return 1

    anchor_by_name = {a.filename: a for a in doc.anchors}
    metas = [
        load_image_metadata(path, anchor_by_name[path.name]) for path in tiff_paths
    ]

    all_src_corners = [m.anchor.corners.as_list() for m in metas]
    flat_corners = [pt for corners in all_src_corners for pt in corners]
    crs_resolver = CoordinateSystemResolver(doc, args.crs)
    working_crs = crs_resolver.resolve(flat_corners)

    projected_corners = [
        crs_resolver.project_corners(meta.anchor.corners.as_list(), working_crs)
        for meta in metas
    ]

    if args.resolution is not None:
        resolution = float(args.resolution)
    else:
        res_estimates = [
            estimate_resolution(meta, corners)
            for meta, corners in zip(metas, projected_corners, strict=True)
        ]
        rx = sorted(r[0] for r in res_estimates)
        ry = sorted(r[1] for r in res_estimates)
        resolution = (rx[len(rx) // 2] + ry[len(ry) // 2]) / 2.0

    geometry = build_mosaic_geometry(projected_corners, resolution, working_crs)
    est_bytes = estimate_output_bytes(
        geometry.width, geometry.height, metas[0].band_count, metas[0].dtype
    )

    crs_label = working_crs.to_string() if working_crs else "unknown"
    print(f"Images found: {len(tiff_paths)}")
    print(f"Images successfully anchored: {len(metas)}")
    print(f"Coordinate system: {crs_label}")
    print(f"Estimated resolution: {resolution:.4f} m/pixel")
    print(f"Mosaic extent: {geometry.extent_str}")
    print(f"Mosaic dimensions: {geometry.width} x {geometry.height}")
    print(f"Estimated output size: ~{format_bytes(est_bytes)}")

    renderer = TileBasedMosaicRenderer(
        geometry=geometry,
        images=metas,
        projected_corners=projected_corners,
        blend=args.blend,
        tile_size=args.tile_size,
    )
    writer = GeoTIFFWriter(
        path=output_path,
        geometry=geometry,
        band_count=metas[0].band_count,
        dtype=metas[0].dtype.name,
        compression=args.compression,
        overwrite=args.overwrite,
    )
    writer.write_tiled(renderer, args.tile_size)
    print(f"Wrote {output_path}")
    return 0


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Build a georeferenced mosaic from anchored TIFF images."
    )
    p.add_argument("--images", required=True, help="Folder containing .tif/.tiff files")
    p.add_argument("--grid", required=True, help="Anchor .grid file")
    p.add_argument("--output", required=True, help="Output GeoTIFF path")
    p.add_argument(
        "--resolution",
        type=float,
        default=None,
        help="Output pixel size in CRS units (default: median from anchors)",
    )
    p.add_argument(
        "--crs",
        default=None,
        help="Force output/working CRS (e.g. EPSG:32636). Overrides auto-detection.",
    )
    p.add_argument(
        "--tile-size",
        type=int,
        default=512,
        help="Output processing/window size in pixels (default: 512)",
    )
    p.add_argument(
        "--compression",
        choices=("lzw", "deflate", "none"),
        default="deflate",
        help="GeoTIFF compression (default: deflate)",
    )
    p.add_argument(
        "--blend",
        choices=("feather", "max"),
        default="feather",
        help="Overlap blending mode (default: feather)",
    )
    p.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace existing output file",
    )
    return p


def main() -> None:
    args = build_arg_parser().parse_args()
    raise SystemExit(run(args))


if __name__ == "__main__":
    main()
