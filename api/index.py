"""Vercel's entry point: every address is rewritten here (vercel.json)."""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

from flight_brief.hosted import app  # noqa: E402, F401
