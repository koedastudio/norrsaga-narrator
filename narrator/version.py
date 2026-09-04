"""Service version, read from pyproject.toml so it is declared in exactly one place.

The project is not installed as a package (the image runs it from source with
``--no-install-project``), so package metadata is unavailable; the pyproject
file ships in the image instead.
"""

import tomllib
from pathlib import Path

_PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"


def _read() -> str:
    with _PYPROJECT.open("rb") as f:
        return tomllib.load(f)["project"]["version"]


__version__ = _read()
