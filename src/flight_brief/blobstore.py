"""Kept files on Vercel Blob, behind the few `pathlib.Path` operations the app uses.

A hosted copy of the page has no disk that lasts between requests. Saved briefs and the
schedule's kept answers and unit counter are small JSON files, written rarely; `BlobPath`
keeps them in a private Vercel Blob store and looks enough like a `Path` that `web.save`,
`web`'s replay and `schedule.Schedule` use it unchanged.

The store's credentials come from the environment (`BLOB_READ_WRITE_TOKEN`, which Vercel sets
on a project the store is connected to). Nothing here is imported unless a hosted copy asks
for it, so a local copy needs neither the package nor a token.

Every failure of the service reaches the caller as an `OSError`, as a failing disk would.
"""
from __future__ import annotations

import fnmatch
from datetime import datetime
from typing import Any


def _client() -> Any:
    from vercel.blob import sync as blob
    return blob


class BlobPath:
    """One pathname in the store. `modified` is set on paths that came from a listing."""

    def __init__(self, pathname: str, client: Any = None, modified: datetime | None = None):
        self.pathname = pathname.strip("/")
        self._blob = client
        self.modified = modified

    @property
    def blob(self) -> Any:
        if self._blob is None:
            self._blob = _client()
        return self._blob

    def __truediv__(self, name: str) -> BlobPath:
        return BlobPath(f"{self.pathname}/{name}", self._blob)

    def __repr__(self) -> str:
        return f"BlobPath({self.pathname!r})"

    @property
    def name(self) -> str:
        return self.pathname.rsplit("/", 1)[-1]

    @property
    def parent(self) -> BlobPath:
        return BlobPath(self.pathname.rsplit("/", 1)[0] if "/" in self.pathname else "", self._blob)

    def mkdir(self, *args: Any, **kwargs: Any) -> None:
        """A blob store has no folders to make."""

    def with_suffix(self, suffix: str) -> _Part:
        """The app writes a file as a part beside it and then moves the part into place. A
        blob is written in one request, so the part only holds the text until `replace`."""
        return _Part()

    def _fail(self, exc: Exception) -> OSError:
        return exc if isinstance(exc, OSError) else OSError(f"blob store: {exc}")

    def exists(self) -> bool:
        try:
            self.blob.head_blob(self.pathname)
            return True
        except FileNotFoundError:
            return False
        except Exception as exc:  # noqa: BLE001 - the service's errors become a failing disk
            raise self._fail(exc) from exc

    def read_text(self, encoding: str = "utf-8") -> str:
        try:
            return self.blob.get_blob(self.pathname, access="private").body.decode(encoding)
        except Exception as exc:  # noqa: BLE001
            raise self._fail(exc) from exc

    def write_text(self, text: str, encoding: str = "utf-8") -> int:
        data = text.encode(encoding)
        try:
            self.blob.put_blob(self.pathname, data, access="private", allow_overwrite=True,
                               content_type="application/json")
        except Exception as exc:  # noqa: BLE001
            raise self._fail(exc) from exc
        return len(data)

    def unlink(self, missing_ok: bool = False) -> None:
        try:
            self.blob.delete_blob(self.pathname)
        except Exception as exc:  # noqa: BLE001
            raise self._fail(exc) from exc

    def glob(self, pattern: str) -> list[BlobPath]:
        """The files directly under this path whose name fits `pattern`."""
        prefix = self.pathname + "/"
        try:
            found = list(self.blob.query_blobs(prefix=prefix))
        except Exception as exc:  # noqa: BLE001
            raise self._fail(exc) from exc
        return [BlobPath(b.pathname, self._blob, modified=b.uploaded_at) for b in found
                if "/" not in b.pathname[len(prefix):]
                and fnmatch.fnmatch(b.pathname[len(prefix):], pattern)]


class _Part:
    def __init__(self) -> None:
        self.text = ""

    def write_text(self, text: str, encoding: str = "utf-8") -> int:
        self.text = text
        return len(text)

    def replace(self, target: BlobPath) -> BlobPath:
        target.write_text(self.text)
        return target
