#!/usr/bin/env python3
"""Live2D Master Agent - Version information (single source of truth)"""

__version__ = "10.0.0"
__version_info__ = (10, 0, 0)
__release_date__ = "2026-07-22"
__codename__ = "Full Production Upgrade"

VERSION = __version__
VERSION_STRING = f"v{__version__}"
FULL_VERSION_STRING = f"Live2D Master Agent v{__version__} ({__codename__}, {__release_date__})"


def get_version() -> str:
    return __version__


def get_version_string() -> str:
    return FULL_VERSION_STRING


if __name__ == "__main__":
    print(FULL_VERSION_STRING)
