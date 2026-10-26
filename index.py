"""Entry point for Vercel, which looks for a Flask object named `app` in index.py.

Run locally the usual way: python -m app.main
"""

from app.main import create_app

app = create_app()
