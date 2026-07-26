/**
 * ClearWave UI smoke tests.
 *
 * Loads the real ui/ front end in Chromium with a mock Tauri bridge
 * (mock-tauri.js) standing in for the Rust backend, then drives the actual
 * user flows: adding and removing tracks, batch progress, the Simple ⇄
 * Advanced mapping, dialogs and saved preferences.
 *
 *   cd ui-tests && npm install && npm test
 */
import { chromium } from "playwright";
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const UI = path.join(HERE, "..", "ui");
const BUILD = path.join(HERE, "_build");

/* Stage a copy of the UI with the mock backend injected. */
fs.rmSync(BUILD, { recursive: true, force: true });
fs.cpSync(UI, BUILD, { recursive: true });
fs.copyFileSync(path.join(HERE, "mock-tauri.js"), path.join(BUILD, "mock-tauri.js"));
const indexPath = path.join(BUILD, "index.html");
fs.writeFileSync(
  indexPath,
  fs.readFileSync(indexPath, "utf8").replace(
    '<script src="main.js"></script>',
    '<script src="mock-tauri.js"></script>\n  <script src="main.js"></script>'
  )
);

const launchOpts = {};
if (process.env.PLAYWRIGHT_BROWSERS_PATH) {
  const bundled = path.join(process.env.PLAYWRIGHT_BROWSERS_PATH, "chromium");
  if (fs.existsSync(bundled)) launchOpts.executablePath = bundled;
}

const browser = await chromium.launch(launchOpts);
const ctx = await browser.newContext({ viewport: { width: 1400, height: 900 } });
const page = await ctx.newPage();

const errors = [];
page.on("pageerror", (e) => errors.push(String(e)));
page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });

const results = [];
const check = (name, pass, detail = "") => {
  results.push({ name, pass });
  console.log(`${pass ? "  ok" : "FAIL"}  ${name}${detail ? "  — " + detail : ""}`);
};

const FILES = [
  "C:/Music/01 - One.mp3", "C:/Music/02 - Two.mp3", "C:/Music/03 - Three.mp3",
  "C:/Music/04 - Four.mp3", "C:/Music/05 - Five.mp3",
];

await page.goto("file://" + indexPath);
await page.waitForTimeout(300);

/* ── track list ── */
await page.evaluate((f) => { window.__mockPick = f; }, FILES);
await page.click("#btn-welcome-add");
await page.waitForTimeout(400);
check("adds selected files", (await page.locator("#track-list li").count()) === 5);
check("shows a track count", (await page.textContent("#track-count")).includes("5 tracks"));

await page.click("#btn-add-tracks");
await page.waitForTimeout(300);
check("ignores duplicate paths", (await page.locator("#track-list li").count()) === 5);

await page.locator("#track-list li").nth(1).locator(".track-del").click({ force: true });
await page.waitForTimeout(300);
const names = await page.locator("#track-list .tname").allTextContents();
check("removes a single track", names.length === 4 && !names.includes("02 - Two.mp3"));

await page.locator("#track-list li").nth(0).click();
await page.waitForTimeout(400);
await page.locator("#track-list li").nth(0).locator(".track-del").click({ force: true });
await page.waitForTimeout(500);
check(
  "removing the open track opens a neighbour",
  !(await page.locator("#workspace").evaluate((e) => e.classList.contains("hidden"))) &&
    !(await page.textContent("#track-title")).includes("No track")
);

/* ── batch progress mirrors onto the list ── */
await page.evaluate(() => {
  window.__emit({ index: 0, total: 3, file: "a", stage: "processing", done: false });
  window.__emit({ index: 0, total: 3, file: "a", stage: "done", done: false });
  window.__emit({ index: 1, total: 3, file: "b", stage: "error", error: "decode failed", done: false });
});
await page.waitForTimeout(250);
const states = await page.locator("#track-list .tstate").evaluateAll((els) =>
  els.map((e) => e.className.replace("tstate", "").trim()));
check("marks per-track done / error", states[0] === "done" && states[1] === "err", states.join("|"));

await page.evaluate(() => window.__emit({ index: 3, total: 3, file: "", stage: "finished", done: true }));
await page.waitForTimeout(200);
check("reports failures in the summary", (await page.textContent("#status-msg")).includes("failed"));

/* ── Simple ⇄ Advanced mapping ── */
await page.click("#mode-advanced");
await page.waitForTimeout(200);
await page.locator("#p-eq-ls").evaluate((el) => {
  el.value = -4; el.dispatchEvent(new Event("input", { bubbles: true }));
});
await page.click("#mode-simple");
await page.waitForTimeout(200);
await page.locator("#s-tone").evaluate((el) => {
  el.value = 3; el.dispatchEvent(new Event("input", { bubbles: true }));
});
await page.waitForTimeout(200);
check(
  "Tone leaves an existing low-shelf move alone",
  Number(await page.locator("#p-eq-ls").inputValue()) === -4 &&
    Number(await page.locator("#p-eq-hs").inputValue()) === 3
);

/* ── dialogs ── */
await page.evaluate(() => { window.__mockPick = ["C:/Ref/a.wav"]; });
await page.click("#btn-build-profile");
await page.waitForTimeout(300);
const opened = !(await page.locator("#modal-overlay").evaluate((e) => e.classList.contains("hidden")));
await page.keyboard.press("Escape");
await page.waitForTimeout(250);
check(
  "Escape closes the naming dialog",
  opened && (await page.locator("#modal-overlay").evaluate((e) => e.classList.contains("hidden")))
);

/* ── preferences persist ── */
await page.selectOption("#out-format", "flac");
await page.click("#btn-out-dir");
await page.waitForTimeout(200);
await page.click("#mode-advanced");
await page.waitForTimeout(200);
await page.reload();
await page.waitForTimeout(500);
check(
  "remembers format, folder and mode across restarts",
  (await page.locator("#out-format").inputValue()) === "flac" &&
    (await page.locator("#out-dir").inputValue()).length > 0 &&
    (await page.locator("#mode-advanced").evaluate((e) => e.classList.contains("active")))
);

/* ── window title ── */
await page.evaluate((f) => { window.__mockPick = f; }, FILES);
await page.click("#btn-add-tracks");
await page.waitForTimeout(500);
check("window title shows the open track", (await page.title()).includes("ClearWave"));

await browser.close();

const failed = results.filter((r) => !r.pass).length;
console.log(`\n${results.length - failed}/${results.length} checks passed`);
if (errors.length) {
  console.log("\nPage errors:\n" + errors.join("\n"));
}
process.exit(failed || errors.length ? 1 : 0);
