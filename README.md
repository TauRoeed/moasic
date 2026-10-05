# moasic — anchored TIFF mosaic

Build a large georeferenced mosaic from overlapping TIFF images using known corner anchors in a `.grid` file (no SIFT / feature matching).

## Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Requires GDAL (used by rasterio). On Debian/Ubuntu: `sudo apt install gdal-bin libgdal-dev`.

## Usage

```bash
python anchored_mosaic.py \
  --images ./images \
  --grid anchors.grid \
  --output mosaic.tif
```

Optional flags:

| Flag | Purpose |
|------|---------|
| `--resolution 0.10` | Output pixel size in CRS units (m/px when projected) |
| `--crs EPSG:32636` | Force working/output CRS |
| `--tile-size 512` | Processing window size (memory control) |
| `--compression deflate` | `deflate`, `lzw`, or `none` |
| `--blend feather` | `feather` (default) or `max` |
| `--overwrite` | Replace existing output |

## `.grid` format

There is **no single industry `.grid` standard** in this repository. The parser in `grid_parser.py` is isolated so you can change it after inspecting your real files.

**Supported example layout** (see `examples/grid_example.grid`):

1. Optional comment headers: `# crs: EPSG:32636`, `# units: projected` or `geographic`
2. One line per image:

```text
filename  x_tl y_tl  x_tr y_tr  x_br y_br  x_bl y_bl
```

Corners match image pixels:

```text
(0, 0)              (width-1, 0)
(0, height-1)       (width-1, height-1)
```

Coordinates may be projected (metres) or geographic (lon/lat). Geographic inputs are reprojected to an auto-selected UTM zone unless `--crs` or a projected `# crs:` header is set.

## Architecture

```text
GridParser → ImageMetadata → CoordinateSystemResolver → MosaicGeometry
    → TileBasedMosaicRenderer → GeoTIFFWriter
```

Main CLI: `anchored_mosaic.py`

## Tests

```bash
python test_grid_parser.py
```

## Notes

- Output is **GeoTIFF** with CRS and geotransform.
- Rendering is **tile-based** to limit RAM use.
- Multi-band TIFFs are supported (same warp for all bands).
- Overlaps use **distance-feather** blending by default.

If your `.grid` layout differs, update `GridParser` only and re-run `test_grid_parser.py`.
