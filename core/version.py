#!/usr/bin/env python3
"""Pygmalion Studio - Version information (single source of truth)"""

__version__ = "0.10.12-beta"
__version_info__ = (0, 10, 12)
__release_date__ = "2026-11-21"
__codename__ = "Auto Update"

VERSION = __version__
VERSION_STRING = f"v{__version__}"
FULL_VERSION_STRING = f"Pygmalion Studio v{__version__} ({__codename__}, {__release_date__})"


def get_version() -> str:
    return __version__


def get_version_string() -> str:
    return FULL_VERSION_STRING


if __name__ == "__main__":
    print(FULL_VERSION_STRING)
