"""Soulkiller runtime package."""
import sys as _sys
import pathlib as _pathlib

# Modules in this package use bare imports (e.g. `from soulkiller_db import ...`)
# that were originally written to run as standalone scripts from this directory.
# Adding the package directory to sys.path preserves that behaviour when the
# package is imported as `soulkiller.*`.
_pkg_dir = str(_pathlib.Path(__file__).parent)
if _pkg_dir not in _sys.path:
    _sys.path.insert(0, _pkg_dir)
