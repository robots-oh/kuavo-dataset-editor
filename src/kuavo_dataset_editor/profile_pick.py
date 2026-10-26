"""Pick a bag profile by file name: profiles/*.json with a matching "match" glob.

An unedited (default) profile from the UI means "choose by name", e.g. rwh_auto_*.bag -> rwh_auto.json.
A profile the user edited is used as-is.
"""
import json
from fnmatch import fnmatch
from pathlib import Path

PROFILES = Path(__file__).parent / "profiles"
DEFAULT = "kuavo_5w.json"


def load_all():
    return {p.stem: json.loads(p.read_text()) for p in sorted(PROFILES.glob("*.json"))}


def default_profile():
    return json.loads((PROFILES / DEFAULT).read_text())


def for_path(path, posted=None):
    base = default_profile()
    if posted and posted != base:
        return posted
    name = Path(str(path)).name
    for stem, profile in load_all().items():
        if any(fnmatch(name, pattern) for pattern in profile.get("match", [])):
            return profile
    return base
