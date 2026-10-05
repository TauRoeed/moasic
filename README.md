# moasic

Browser photo mosaic maker — no build step.

## Run

Open `index.html` in a browser, or serve locally:

```bash
python3 -m http.server 8080
```

Then visit `http://localhost:8080`.

## Use

1. Pick a **target** photo.
2. Optionally upload many **tile** photos (thumbnails). If you skip this, the app reuses slices of the target as tiles.
3. Set grid **columns** and **rows**, click **Generate mosaic**, then **Download PNG**.

## Files

- `index.html` — page layout
- `styles.css` — dark UI
- `mosaic.js` — mosaic generation (average-color matching)
