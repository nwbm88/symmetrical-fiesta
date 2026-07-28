"""Windows/macOS/Linux differences, kept in one place.

Windows needs more care than the others:

* every ffmpeg/ffprobe/ffplay call would otherwise flash up a black console
  window, dozens of times during a split;
* ffmpeg is very often not on PATH, because the usual install is "unzip it
  somewhere", so we look in the places people actually put it;
* the filesystem rejects names the other platforms accept (``CON``, ``NUL``,
  trailing dots, ``:`` in "Live at 20:30"), and paths are capped at 260
  characters unless long paths are enabled.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

IS_WINDOWS = os.name == "nt"
IS_MAC = sys.platform == "darwin"


# ----------------------------------------------------------- subprocesses

def _no_window_flags() -> int:
    """CREATE_NO_WINDOW, so ffmpeg doesn't flash a console on Windows."""
    if IS_WINDOWS:
        return getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    return 0


CREATION_FLAGS = _no_window_flags()


def popen_kwargs(**extra) -> dict:
    """Keyword arguments every subprocess call in the app should use."""
    kw = {"creationflags": CREATION_FLAGS} if IS_WINDOWS else {}
    kw.update(extra)
    return kw


# --------------------------------------------------------- finding ffmpeg

def _candidate_dirs() -> list[Path]:
    """Where a Windows user's ffmpeg realistically lives."""
    dirs: list[Path] = []
    # next to the app itself (ffmpeg/bin/ffmpeg.exe beside run.bat)
    root = Path(__file__).resolve().parent.parent
    dirs += [root / "ffmpeg" / "bin", root / "ffmpeg", root / "bin", root]
    if IS_WINDOWS:
        for base in filter(None, [os.environ.get("ProgramFiles"),
                                  os.environ.get("ProgramFiles(x86)"),
                                  os.environ.get("LOCALAPPDATA"),
                                  "C:\\"]):
            b = Path(base)
            dirs += [b / "ffmpeg" / "bin", b / "ffmpeg",
                     b / "Programs" / "ffmpeg" / "bin"]
        # winget / chocolatey / scoop defaults
        home = Path.home()
        dirs += [home / "scoop" / "shims",
                 Path("C:\\ProgramData\\chocolatey\\bin")]
    else:
        dirs += [Path("/usr/local/bin"), Path("/opt/homebrew/bin")]
    return dirs


_TOOL_CACHE: dict[str, Optional[str]] = {}


def find_tool(name: str) -> Optional[str]:
    """Full path to ffmpeg/ffprobe/ffplay, or None. Result is cached."""
    if name in _TOOL_CACHE:
        return _TOOL_CACHE[name]
    exe = f"{name}.exe" if IS_WINDOWS else name
    found = shutil.which(exe) or shutil.which(name)
    if not found:
        for d in _candidate_dirs():
            p = d / exe
            if p.is_file():
                found = str(p)
                break
    _TOOL_CACHE[name] = found
    return found


def ffmpeg() -> str:
    return find_tool("ffmpeg") or "ffmpeg"


def ffprobe() -> str:
    return find_tool("ffprobe") or "ffprobe"


def ffplay() -> Optional[str]:
    return find_tool("ffplay")


def ffmpeg_missing_message() -> str:
    where = ("Download it from https://www.gyan.dev/ffmpeg/builds/ "
             "(get the 'release full' zip), unzip it, and either add its "
             "bin folder to PATH or drop the ffmpeg folder next to run.bat."
             if IS_WINDOWS else
             "Install it with your package manager, e.g. "
             "'sudo apt install ffmpeg' or 'brew install ffmpeg'.")
    return f"ffmpeg was not found on this computer.\n\n{where}"


def check_ffmpeg() -> Optional[str]:
    """None when ffmpeg and ffprobe are usable, else an explanation."""
    if find_tool("ffmpeg") and find_tool("ffprobe"):
        return None
    return ffmpeg_missing_message()


# ------------------------------------------------------------- filenames

# Windows refuses these as file or folder names, with or without extension.
_RESERVED = {"con", "prn", "aux", "nul",
             *(f"com{i}" for i in range(1, 10)),
             *(f"lpt{i}" for i in range(1, 10))}

_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def safe_filename(name: str, maxlen: int = 120) -> str:
    """A name that is legal on every platform we support.

    Windows is the strict one: no ``<>:"/\\|?*``, no trailing dot or space,
    and a handful of reserved device names.  We apply its rules everywhere so
    a collection copied from Linux to Windows keeps working.
    """
    name = _ILLEGAL.sub("_", name)
    name = name.strip(" .") or "untitled"
    if name.split(".")[0].lower() in _RESERVED:
        name = f"_{name}"
    name = name[:maxlen].strip(" .")
    return name or "untitled"


# ------------------------------------------------------ JavaScript runtime

# Recent yt-dlp runs YouTube's player JavaScript to work out media URLs.
# Without a runtime it warns that formats may be missing, and treats the
# JS-less path as deprecated — so downloads fail or come back lower quality
# for reasons that are not obvious from the error.
JS_RUNTIMES = ("deno", "node", "bun")


def _extra_runtime_locations(name: str) -> list[Path]:
    """Places an installer drops a runtime without putting it on PATH."""
    home = Path.home()
    exe = f"{name}.exe" if IS_WINDOWS else name
    spots = [home / f".{name}" / "bin" / exe]
    if IS_WINDOWS:
        for base in filter(None, (os.environ.get("ProgramFiles"),
                                  os.environ.get("LOCALAPPDATA"))):
            spots += [Path(base) / name / exe,
                      Path(base) / "Programs" / name / exe]
    else:
        spots += [Path("/usr/local/bin") / exe, Path("/opt/homebrew/bin") / exe]
    return spots


def find_js_runtimes() -> dict[str, str]:
    """Every JavaScript runtime we can find, as {name: full path}."""
    found: dict[str, str] = {}
    for name in JS_RUNTIMES:
        exe = f"{name}.exe" if IS_WINDOWS else name
        path = shutil.which(exe) or shutil.which(name)
        if not path:
            path = next((str(p) for p in _extra_runtime_locations(name)
                         if p.is_file()), None)
        if path:
            found[name] = path
    return found


def find_js_runtime() -> Optional[str]:
    runtimes = find_js_runtimes()
    for name in JS_RUNTIMES:                 # deno first — yt-dlp prefers it
        if name in runtimes:
            return runtimes[name]
    return None


def js_runtime_options() -> dict:
    """The ``js_runtimes`` value to hand yt-dlp.

    yt-dlp only enables Deno by default, so a machine with Node.js installed
    still falls back to the degraded JS-less path and warns that formats may
    be missing.  Passing this replaces that default, so we list everything
    we found — with explicit paths, since an installer may not have put the
    runtime on PATH at all.
    """
    return {name: {"path": path} for name, path in find_js_runtimes().items()}


def js_runtime_message() -> str:
    if IS_WINDOWS:
        how = ("Install Deno with:\n"
               "    winget install DenoLand.Deno\n"
               "or download it from https://deno.com/ and add it to PATH. "
               "Node.js works too.")
    else:
        how = ("Install Deno with:\n"
               "    curl -fsSL https://deno.land/install.sh | sh\n"
               "or use your package manager. Node.js works too.")
    return ("No JavaScript runtime found. YouTube downloads still work in "
            "many cases, but yt-dlp needs one to read YouTube's player "
            "properly — without it some formats are unavailable and "
            "downloads fail more often.\n\n" + how)


def check_js_runtime() -> Optional[str]:
    """None when a runtime is available, else an explanation."""
    return None if find_js_runtime() else js_runtime_message()


# --------------------------------------------------------- yt-dlp cookies

# YouTube increasingly asks for proof you are not a bot.  Handing yt-dlp the
# cookies from a browser you are already signed into is the standard fix.
COOKIE_BROWSERS = [
    ("Don't use browser cookies", ""),
    ("Chrome", "chrome"),
    ("Edge", "edge"),
    ("Firefox", "firefox"),
    ("Brave", "brave"),
    ("Opera", "opera"),
    ("Vivaldi", "vivaldi"),
    ("Chromium", "chromium"),
    ("Safari", "safari"),
]


def is_bot_check(message: str) -> bool:
    m = (message or "").lower()
    return ("sign in to confirm" in m or "not a bot" in m
            or "confirm you're not a bot" in m
            or "cookies-from-browser" in m)


BOT_CHECK_HELP = (
    "YouTube asked this computer to prove it isn't a bot.\n\n"
    "Fix: open Settings and pick the browser you normally watch YouTube in "
    "under “Use cookies from browser”. Sign in to YouTube in that browser "
    "first, then try again.\n\n"
    "If it keeps happening, updating yt-dlp usually helps (run update.bat on "
    "Windows, or: pip install -U yt-dlp)."
)


def shorten_path_component(name: str, budget: int) -> str:
    """Trim a folder/file name so long collection paths stay under the
    Windows 260-character limit."""
    if budget < 20:
        budget = 20
    return name[:budget].strip(" .") or "untitled"


# ------------------------------------------------------- Windows path length

# Unless long paths are enabled, Windows refuses anything over 260 characters.
# The archive nests collection/<artist>/<date - venue>/audio/<NN - title>.flac,
# so a deep collection folder plus a wordy venue and song title can cross it —
# and the failure looks like a random "cannot create file".
MAX_PATH = 260
PATH_SAFETY_MARGIN = 15


def long_paths_enabled() -> bool:
    """True when Windows has had the 260-character limit lifted."""
    if not IS_WINDOWS:
        return True
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                             r"SYSTEM\CurrentControlSet\Control\FileSystem")
        value, _ = winreg.QueryValueEx(key, "LongPathsEnabled")
        return bool(value)
    except Exception:
        return False


def name_budget(collection_root, reserve: int = 0) -> int:
    """How many characters a single name may use inside the collection.

    Budgets for the deepest thing we create:
        <root>/<artist>/<date - venue>/audio/<NN - title>.flac
    Three names share what is left, so each gets a third.
    """
    if not IS_WINDOWS or long_paths_enabled():
        return 120                      # generous; only the OS cares beyond this
    fixed = len(str(Path(collection_root).resolve())) + len("/audio/") + \
        len(".flac") + reserve + PATH_SAFETY_MARGIN
    available = MAX_PATH - fixed
    return max(available // 3, 24)      # never so short as to be useless


# ------------------------------------------------------ cloud-synced folders

CLOUD_MARKERS = ("onedrive", "dropbox", "google drive", "googledrive",
                 "icloud", "box sync", "pcloud", "mega", "nextcloud",
                 "sync.com", "creative cloud files")


def cloud_service_for(path) -> Optional[str]:
    """Name of the sync service that appears to own *path*, if any."""
    text = str(Path(path).resolve()).replace("\\", "/").lower()
    for marker in CLOUD_MARKERS:
        if f"/{marker}" in text or text.startswith(marker):
            pretty = {"onedrive": "OneDrive", "dropbox": "Dropbox",
                      "google drive": "Google Drive",
                      "googledrive": "Google Drive",
                      "icloud": "iCloud Drive"}.get(marker, marker.title())
            return pretty
    return None


def cloud_warning(path) -> Optional[str]:
    """Explain why keeping a concert archive inside a synced folder hurts."""
    service = cloud_service_for(path)
    if not service:
        return None
    return (
        f"This folder is inside {service}, which will try to upload your whole "
        f"archive.\n\n"
        f"Concert downloads run to several gigabytes each, so this normally "
        f"means a full {service} storage quota, a saturated upload connection, "
        f"and files turned into online-only placeholders that ffmpeg then "
        f"cannot read.\n\n"
        f"Recommended: keep the app here if you like, but put the collection "
        f"itself somewhere outside {service} — for example "
        f"C:\\\\Users\\\\Owner\\\\Music\\\\LiveArchive — using the collection "
        f"folder setting on the Settings tab.\n\n"
        f"If you would rather keep it here, right-click the collection folder "
        f"in Explorer and choose \"Always keep on this device\" so files are "
        f"never turned into placeholders.")
