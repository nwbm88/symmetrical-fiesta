# Melon Sandbox Mod Organiser

A small, dependency-free Python program that finds Melon Sandbox mod archive links from websites you provide, downloads them, and organises them into tidy folders.

The tool is intentionally ethical and scoped:

- It does **not** scrape the entire internet, because that is not realistic or respectful to site owners.
- It only crawls domains from the seed URLs you provide.
- It respects `robots.txt` by default.
- It skips APK/XAPK/EXE-style executables by default and focuses on mod archive files.

## Folder layout

Downloads are saved as:

```text
MelonSandbox_Mods/
  example-mod-site.com/
    weapons/
      cool-weapon-pack.zip
    vehicles/
      truck-mod.7z
  manifest.csv
  manifest.json
```

The manifest files record each source URL, saved path, file size, SHA-256 hash, category, and download time.

## Usage

Run a dry run first so you can see what would be downloaded:

```bash
python3 melon_mod_scraper.py --dry-run --max-pages 10 https://example.com/melon-sandbox-mods
```

Download from one or more seed pages:

```bash
python3 melon_mod_scraper.py --output MelonSandbox_Mods https://example.com/melon-sandbox-mods
```

Use a seed file:

```bash
python3 melon_mod_scraper.py --seed-file seeds.txt --max-pages 100 --delay 2
```

Example `seeds.txt`:

```text
https://example.com/melon-sandbox-mods
https://another-example.net/android/melon-sandbox/
```

## Options

- `--max-pages N`: limits how many HTML pages are inspected.
- `--delay SECONDS`: waits between requests to avoid hammering websites.
- `--extensions .zip .7z .melmod`: controls which mod file types are downloaded.
- `--allow-apk`: permits APK/XAPK downloads if you really want them. Leave this off unless you trust the source.
- `--ignore-robots`: disables `robots.txt` checks. Use only when you have permission.
- `--dry-run`: shows planned downloads without writing files.

## Safety notes

Only use this with websites you have permission to crawl and files you have permission to download. Always scan downloaded files before installing them on Android.
