"""Entrypoint Vercel looks for at the repo root. The app itself lives in api/main.py."""

from api.main import app

__all__ = ["app"]
