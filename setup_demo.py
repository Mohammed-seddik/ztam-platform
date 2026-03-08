#!/usr/bin/env python3
"""Backward-compatible wrapper for the demo bootstrap script."""

from pathlib import Path
import runpy


if __name__ == "__main__":
    runpy.run_path(
        str(Path(__file__).resolve().parent / "demo" / "setup_demo.py"),
        run_name="__main__",
    )
