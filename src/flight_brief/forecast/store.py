"""Where saved tiles live.

The design leaves the long-term home open (object storage is likely once the
page reads tiles). Until then tiles are GitHub release assets: one release per
month, one asset per run, written with the workflow's own token. Both stores
have the same three methods, so moving is a copy.
"""
from __future__ import annotations

import json
import pathlib
import subprocess
import tempfile


class LocalStore:
    def __init__(self, root: str | pathlib.Path):
        self.root = pathlib.Path(root)

    def names(self, group: str) -> set[str]:
        d = self.root / group
        return {p.name for p in d.iterdir()} if d.is_dir() else set()

    def put(self, group: str, name: str, data: bytes) -> None:
        (self.root / group).mkdir(parents=True, exist_ok=True)
        (self.root / group / name).write_bytes(data)

    def get(self, group: str, name: str) -> bytes:
        return (self.root / group / name).read_bytes()


class ReleaseStore:
    """Assets on GitHub releases of the current repository, through the ``gh`` CLI.

    ``group`` is the release tag. Needs ``GH_TOKEN`` with contents: write.
    """

    def _gh(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["gh", *args], capture_output=True, text=True)

    def names(self, group: str) -> set[str]:
        r = self._gh("release", "view", group, "--json", "assets")
        if r.returncode != 0:
            if "release not found" in r.stderr:
                return set()
            raise RuntimeError(r.stderr.strip())
        return {a["name"] for a in json.loads(r.stdout)["assets"]}

    def put(self, group: str, name: str, data: bytes) -> None:
        if self._gh("release", "view", group, "--json", "tagName").returncode != 0:
            r = self._gh("release", "create", group, "--title", group, "--latest=false", "--notes",
                         "Saved NOAA GTG turbulence tiles, one file per run. "
                         "Format: src/flight_brief/forecast/tiles.py.")
            if r.returncode != 0:
                raise RuntimeError(r.stderr.strip())
        with tempfile.TemporaryDirectory() as d:
            path = pathlib.Path(d) / name
            path.write_bytes(data)
            r = self._gh("release", "upload", group, str(path))
            if r.returncode != 0:
                raise RuntimeError(r.stderr.strip())

    def get(self, group: str, name: str) -> bytes:
        with tempfile.TemporaryDirectory() as d:
            r = self._gh("release", "download", group, "--pattern", name, "--dir", d)
            if r.returncode != 0:
                raise RuntimeError(r.stderr.strip())
            return (pathlib.Path(d) / name).read_bytes()


def open_store(spec: str):
    """``releases`` or ``dir:<path>``."""
    if spec == "releases":
        return ReleaseStore()
    if spec.startswith("dir:"):
        return LocalStore(spec[4:])
    raise ValueError(f"unknown store {spec!r}")
