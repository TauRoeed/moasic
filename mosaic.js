const targetInput = document.getElementById("target-input");
const tilesInput = document.getElementById("tiles-input");
const colsInput = document.getElementById("cols");
const rowsInput = document.getElementById("rows");
const useSelfTiles = document.getElementById("use-self-tiles");
const generateBtn = document.getElementById("generate");
const downloadBtn = document.getElementById("download");
const canvas = document.getElementById("canvas");
const statusEl = document.getElementById("status");
const ctx = canvas.getContext("2d");

/** @type {HTMLImageElement | null} */
let targetImage = null;
/** @type {{ img: HTMLImageElement; r: number; g: number; b: number }[]} */
let tileLibrary = [];

function setStatus(text) {
  statusEl.textContent = text;
}

function readFileAsImage(file) {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(file);
    const img = new Image();
    img.onload = () => {
      URL.revokeObjectURL(url);
      resolve(img);
    };
    img.onerror = () => {
      URL.revokeObjectURL(url);
      reject(new Error("Failed to load image"));
    };
    img.src = url;
  });
}

function averageColorFromImage(img, sampleSize = 32) {
  const c = document.createElement("canvas");
  c.width = sampleSize;
  c.height = sampleSize;
  const cx = c.getContext("2d");
  cx.drawImage(img, 0, 0, sampleSize, sampleSize);
  const { data } = cx.getImageData(0, 0, sampleSize, sampleSize);
  let r = 0;
  let g = 0;
  let b = 0;
  const n = data.length / 4;
  for (let i = 0; i < data.length; i += 4) {
    r += data[i];
    g += data[i + 1];
    b += data[i + 2];
  }
  return { r: r / n, g: g / n, b: b / n };
}

function colorDistance(a, b) {
  const dr = a.r - b.r;
  const dg = a.g - b.g;
  const db = a.b - b.b;
  return dr * dr + dg * dg + db * db;
}

function buildSelfTiles(source, count) {
  const side = Math.ceil(Math.sqrt(count));
  const tileW = source.naturalWidth / side;
  const tileH = source.naturalHeight / side;
  const tiles = [];
  for (let y = 0; y < side; y++) {
    for (let x = 0; x < side; x++) {
      const slice = document.createElement("canvas");
      slice.width = tileW;
      slice.height = tileH;
      const sx = slice.getContext("2d");
      sx.drawImage(
        source,
        x * tileW,
        y * tileH,
        tileW,
        tileH,
        0,
        0,
        tileW,
        tileH
      );
      const img = new Image();
      img.src = slice.toDataURL();
      tiles.push({ img, ...averageColorFromImage(slice) });
    }
  }
  return tiles;
}

async function refreshTiles() {
  tileLibrary = [];
  const files = tilesInput.files;
  if (files && files.length > 0) {
    setStatus(`Loading ${files.length} tile image(s)…`);
    for (const file of files) {
      const img = await readFileAsImage(file);
      tileLibrary.push({ img, ...averageColorFromImage(img) });
    }
    setStatus(`${tileLibrary.length} tiles ready.`);
    return;
  }
  if (targetImage && useSelfTiles.checked) {
    tileLibrary = buildSelfTiles(targetImage, 64);
    setStatus("Using slices from target as tiles.");
  }
}

function pickTile(targetColor) {
  let best = tileLibrary[0];
  let bestDist = Infinity;
  for (const tile of tileLibrary) {
    const d = colorDistance(targetColor, tile);
    if (d < bestDist) {
      bestDist = d;
      best = tile;
    }
  }
  return best;
}

async function generateMosaic() {
  if (!targetImage) return;
  if (tileLibrary.length === 0) {
    await refreshTiles();
  }
  if (tileLibrary.length === 0) {
    setStatus("Add tile photos or enable self-tiles.");
    return;
  }

  const cols = Math.max(8, Math.min(120, Number(colsInput.value) || 40));
  const rows = Math.max(8, Math.min(120, Number(rowsInput.value) || 30));

  const srcW = targetImage.naturalWidth;
  const srcH = targetImage.naturalHeight;
  const cellW = srcW / cols;
  const cellH = srcH / rows;

  canvas.width = srcW;
  canvas.height = srcH;
  ctx.clearRect(0, 0, srcW, srcH);

  setStatus("Building mosaic…");
  generateBtn.disabled = true;

  const sample = document.createElement("canvas");
  sample.width = 8;
  sample.height = 8;
  const sampleCtx = sample.getContext("2d");

  await new Promise((r) => requestAnimationFrame(r));

  for (let row = 0; row < rows; row++) {
    for (let col = 0; col < cols; col++) {
      const x = col * cellW;
      const y = row * cellH;
      sampleCtx.drawImage(targetImage, x, y, cellW, cellH, 0, 0, 8, 8);
      const targetColor = averageColorFromImage(sample, 8);
      const tile = pickTile(targetColor);
      ctx.drawImage(tile.img, x, y, cellW, cellH);
    }
    if (row % 4 === 0) {
      await new Promise((r) => requestAnimationFrame(r));
    }
  }

  generateBtn.disabled = false;
  downloadBtn.disabled = false;
  setStatus(`Done — ${cols}×${rows} grid.`);
}

targetInput.addEventListener("change", async () => {
  const file = targetInput.files?.[0];
  if (!file) return;
  targetImage = await readFileAsImage(file);
  generateBtn.disabled = false;
  downloadBtn.disabled = true;
  tileLibrary = [];
  await refreshTiles();
  setStatus("Target loaded. Adjust grid and generate.");
});

tilesInput.addEventListener("change", refreshTiles);
useSelfTiles.addEventListener("change", async () => {
  if (!targetImage) return;
  tileLibrary = [];
  await refreshTiles();
});

generateBtn.addEventListener("click", () => {
  void generateMosaic();
});

downloadBtn.addEventListener("click", () => {
  const link = document.createElement("a");
  link.download = "moasic.png";
  link.href = canvas.toDataURL("image/png");
  link.click();
});
