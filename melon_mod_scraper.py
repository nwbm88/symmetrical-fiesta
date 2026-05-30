#!/usr/bin/env python3
"""Ethical Melon Sandbox mod finder and organiser.

This tool crawls user-supplied mod-index pages, discovers downloadable mod
archives, downloads them, and places them into a predictable folder structure.
It intentionally does not try to scrape "the whole internet" or bypass site
rules. Provide seed URLs from sites you are allowed to crawl.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable
from urllib import error, parse, request, robotparser

USER_AGENT = "MelonSandboxModOrganizer/1.0 (+https://example.invalid/ethical-scraper)"
DEFAULT_OUTPUT = "MelonSandbox_Mods"
DEFAULT_EXTENSIONS = (
    ".zip",
    ".rar",
    ".7z",
    ".melmod",
    ".msmod",
    ".melon",
    ".pack",
)
BLOCKED_EXTENSIONS = (
    ".apk",
    ".apks",
    ".xapk",
    ".exe",
    ".msi",
    ".dmg",
)
CATEGORY_KEYWORDS = {
    "weapons": ("weapon", "gun", "rifle", "pistol", "sword", "knife", "grenade"),
    "vehicles": ("vehicle", "car", "truck", "tank", "plane", "helicopter", "bike"),
    "characters": ("character", "npc", "skin", "people", "person", "ragdoll"),
    "maps": ("map", "level", "world", "arena", "building"),
    "packs": ("pack", "collection", "bundle"),
}


class LinkParser(HTMLParser):
    """Tiny dependency-free link extractor for normal HTML pages."""

    def __init__(self) -> None:
        super().__init__()
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        for key, value in attrs:
            if key.lower() == "href" and value:
                self.links.append(value)


@dataclass
class DownloadRecord:
    source_url: str
    saved_path: str
    bytes: int
    sha256: str
    category: str
    downloaded_at: str


def slugify(value: str, fallback: str = "untitled") -> str:
    """Return a filesystem-safe name while keeping it human readable."""

    cleaned = re.sub(r"[^A-Za-z0-9._ -]+", " ", value).strip()
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ._-")
    return cleaned[:120] or fallback


def domain_for(url: str) -> str:
    parsed = parse.urlparse(url)
    return slugify(parsed.netloc.lower().replace(":", "_"), "unknown-domain")


def filename_from_url(url: str) -> str:
    parsed = parse.urlparse(url)
    name = Path(parse.unquote(parsed.path)).name
    return slugify(name, "downloaded-mod")


def category_for(url: str) -> str:
    text = parse.unquote(url).lower()
    for category, keywords in CATEGORY_KEYWORDS.items():
        if any(keyword in text for keyword in keywords):
            return category
    return "uncategorized"


def has_allowed_extension(url: str, allowed_extensions: tuple[str, ...]) -> bool:
    path = parse.urlparse(url).path.lower()
    return any(path.endswith(ext) for ext in allowed_extensions)


def has_blocked_extension(url: str) -> bool:
    path = parse.urlparse(url).path.lower()
    return any(path.endswith(ext) for ext in BLOCKED_EXTENSIONS)


def same_domain(url: str, allowed_domains: set[str]) -> bool:
    host = parse.urlparse(url).netloc.lower()
    return host in allowed_domains


def validate_url(url: str) -> str:
    parsed = parse.urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise SystemExit(f"Invalid URL: {url!r}. Use a full http(s) URL.")
    return url


def read_seed_file(path: Path) -> list[str]:
    urls: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        value = line.strip()
        if value and not value.startswith("#"):
            urls.append(value)
    return urls


def open_url(url: str, timeout: float) -> request.addinfourl:
    req = request.Request(url, headers={"User-Agent": USER_AGENT})
    return request.urlopen(req, timeout=timeout)


def robot_for(url: str, timeout: float) -> robotparser.RobotFileParser:
    parsed = parse.urlparse(url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    rp = robotparser.RobotFileParser(robots_url)
    try:
        with open_url(robots_url, timeout) as response:
            body = response.read(500_000).decode("utf-8", errors="replace").splitlines()
        rp.parse(body)
    except Exception:
        # If robots.txt is unavailable, do not crash; the user can still limit
        # scope with --max-pages, --delay, and explicit seed URLs.
        rp.parse([])
    return rp


def discover_links(page_url: str, timeout: float) -> list[str]:
    try:
        with open_url(page_url, timeout) as response:
            content_type = response.headers.get("content-type", "")
            if "html" not in content_type.lower():
                return []
            html = response.read(2_000_000).decode("utf-8", errors="replace")
    except (error.HTTPError, error.URLError, TimeoutError) as exc:
        print(f"warning: could not fetch {page_url}: {exc}", file=sys.stderr)
        return []

    parser = LinkParser()
    parser.feed(html)
    return [parse.urljoin(page_url, href) for href in parser.links]


def download_file(url: str, destination: Path, timeout: float) -> tuple[int, str]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    total = 0
    temp_path = destination.with_suffix(destination.suffix + ".part")

    with open_url(url, timeout) as response, temp_path.open("wb") as file_obj:
        while True:
            chunk = response.read(1024 * 128)
            if not chunk:
                break
            digest.update(chunk)
            total += len(chunk)
            file_obj.write(chunk)

    if destination.exists():
        stem = destination.stem
        suffix = destination.suffix
        counter = 2
        while destination.exists():
            destination = destination.with_name(f"{stem}-{counter}{suffix}")
            counter += 1
    temp_path.replace(destination)
    return total, digest.hexdigest()


def write_manifest(output_dir: Path, records: Iterable[DownloadRecord]) -> None:
    manifest_json = output_dir / "manifest.json"
    manifest_csv = output_dir / "manifest.csv"
    data = [asdict(record) for record in records]
    manifest_json.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    with manifest_csv.open("w", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(
            csv_file,
            fieldnames=["source_url", "saved_path", "bytes", "sha256", "category", "downloaded_at"],
        )
        writer.writeheader()
        writer.writerows(data)


def crawl_and_download(args: argparse.Namespace) -> list[DownloadRecord]:
    seed_urls = [validate_url(url) for url in args.urls]
    if args.seed_file:
        seed_urls.extend(validate_url(url) for url in read_seed_file(args.seed_file))
    if not seed_urls:
        raise SystemExit("Provide at least one seed URL or --seed-file.")

    allowed_extensions = tuple(ext.lower() if ext.startswith(".") else f".{ext.lower()}" for ext in args.extensions)
    allowed_domains = {parse.urlparse(url).netloc.lower() for url in seed_urls}
    robots = {domain: robot_for(seed_urls_by_domain(seed_urls, domain)[0], args.timeout) for domain in allowed_domains}

    output_dir = args.output
    queue = list(dict.fromkeys(seed_urls))
    seen_pages: set[str] = set()
    found_downloads: list[str] = []

    while queue and len(seen_pages) < args.max_pages:
        page_url = queue.pop(0)
        if page_url in seen_pages or not same_domain(page_url, allowed_domains):
            continue
        seen_pages.add(page_url)
        host = parse.urlparse(page_url).netloc.lower()
        if args.respect_robots and not robots[host].can_fetch(USER_AGENT, page_url):
            print(f"skipping disallowed page: {page_url}", file=sys.stderr)
            continue

        time.sleep(args.delay)
        for link in discover_links(page_url, args.timeout):
            clean_link = link.split("#", 1)[0]
            if not same_domain(clean_link, allowed_domains):
                continue
            if has_blocked_extension(clean_link) and not args.allow_apk:
                continue
            if has_allowed_extension(clean_link, allowed_extensions):
                found_downloads.append(clean_link)
            elif clean_link not in seen_pages and len(queue) + len(seen_pages) < args.max_pages:
                queue.append(clean_link)

    records: list[DownloadRecord] = []
    for url in dict.fromkeys(found_downloads):
        host = parse.urlparse(url).netloc.lower()
        if args.respect_robots and not robots[host].can_fetch(USER_AGENT, url):
            print(f"skipping disallowed download: {url}", file=sys.stderr)
            continue
        category = category_for(url)
        destination = output_dir / domain_for(url) / category / filename_from_url(url)
        if args.dry_run:
            print(f"would download: {url} -> {destination}")
            continue
        try:
            time.sleep(args.delay)
            size, sha256 = download_file(url, destination, args.timeout)
        except (error.HTTPError, error.URLError, TimeoutError, OSError) as exc:
            print(f"warning: could not download {url}: {exc}", file=sys.stderr)
            continue
        records.append(
            DownloadRecord(
                source_url=url,
                saved_path=str(destination),
                bytes=size,
                sha256=sha256,
                category=category,
                downloaded_at=datetime.now(timezone.utc).isoformat(),
            )
        )
        print(f"downloaded: {destination}")

    if not args.dry_run:
        output_dir.mkdir(parents=True, exist_ok=True)
        write_manifest(output_dir, records)
    return records


def seed_urls_by_domain(seed_urls: list[str], domain: str) -> list[str]:
    return [url for url in seed_urls if parse.urlparse(url).netloc.lower() == domain]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Find and organise Melon Sandbox mod archives from user-supplied, "
            "allowed websites. The crawler stays on seed domains and respects robots.txt by default."
        )
    )
    parser.add_argument("urls", nargs="*", help="Seed pages to crawl, such as a mod listing or search results page.")
    parser.add_argument("--seed-file", type=Path, help="Text file containing one seed URL per line.")
    parser.add_argument("--output", type=Path, default=Path(DEFAULT_OUTPUT), help="Destination folder for organised mods.")
    parser.add_argument("--max-pages", type=int, default=50, help="Maximum HTML pages to inspect across all seed domains.")
    parser.add_argument("--delay", type=float, default=1.0, help="Seconds to wait between requests.")
    parser.add_argument("--timeout", type=float, default=20.0, help="Network timeout in seconds.")
    parser.add_argument("--extensions", nargs="+", default=list(DEFAULT_EXTENSIONS), help="Download extensions to keep.")
    parser.add_argument("--allow-apk", action="store_true", help="Allow APK/XAPK downloads. Disabled by default for safety.")
    parser.add_argument("--ignore-robots", dest="respect_robots", action="store_false", help="Do not check robots.txt.")
    parser.add_argument("--dry-run", action="store_true", help="Print planned downloads without saving files.")
    parser.set_defaults(respect_robots=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    records = crawl_and_download(args)
    if args.dry_run:
        return 0
    print(f"Saved {len(records)} file(s) under {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
