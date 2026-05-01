"""Vercel entry point. Re-exports the FastAPI app from app/main.py."""
import sys
from pathlib import Path

# Add project root so `import app.main` resolves on Vercel
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.main import app  # noqa: E402,F401
