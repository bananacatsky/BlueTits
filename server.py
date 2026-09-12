"""Compatibility launcher for the BlueTits backend."""

from backend.server import app, main

__all__ = ["app"]


if __name__ == "__main__":
    raise SystemExit(main())
