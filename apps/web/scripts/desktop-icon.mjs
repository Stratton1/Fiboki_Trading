// Render scripts/desktop/fiboki-icon.svg into Fiboki.iconset/ at every size
// macOS wants (iconutil -c icns builds the .icns from it on the Mac).
// Uses the Playwright Chromium already installed for the web tests; no new
// dependency; lives under apps/web so `playwright` resolves. From apps/web:
//   node scripts/desktop-icon.mjs
import { chromium } from "playwright";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = resolve(dirname(fileURLToPath(import.meta.url)), "..", "..", "..", "scripts", "desktop");
const svg = readFileSync(join(here, "fiboki-icon.svg"), "utf8");
const out = join(here, "Fiboki.iconset");
mkdirSync(out, { recursive: true });

// name -> pixel size. @2x files are the same pixel size as the next size up.
const files = {
  "icon_16x16.png": 16, "icon_16x16@2x.png": 32,
  "icon_32x32.png": 32, "icon_32x32@2x.png": 64,
  "icon_128x128.png": 128, "icon_128x128@2x.png": 256,
  "icon_256x256.png": 256, "icon_256x256@2x.png": 512,
  "icon_512x512.png": 512, "icon_512x512@2x.png": 1024,
};

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1024, height: 1024 }, deviceScaleFactor: 1 });
await page.setContent(
  `<!doctype html><html><body style="margin:0;background:transparent">${svg}</body></html>`,
);
const master = await page.screenshot({ omitBackground: true, clip: { x: 0, y: 0, width: 1024, height: 1024 } });
writeFileSync(join(out, "icon_512x512@2x.png"), master);
// Downsample from the 1024 master with the browser's own resampler.
for (const [name, size] of Object.entries(files)) {
  if (size === 1024) continue;
  await page.setViewportSize({ width: size, height: size });
  await page.setContent(
    `<!doctype html><html><body style="margin:0;background:transparent"><img src="data:image/png;base64,${master.toString("base64")}" width="${size}" height="${size}" style="display:block;image-rendering:auto"></body></html>`,
  );
  const png = await page.screenshot({ omitBackground: true, clip: { x: 0, y: 0, width: size, height: size } });
  writeFileSync(join(out, name), png);
}
await browser.close();
console.log(`wrote ${Object.keys(files).length} files to ${resolve(out)}`);
