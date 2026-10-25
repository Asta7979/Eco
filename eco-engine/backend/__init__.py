"""Project Eco - EcoRetrofit AI Engine Backend Package."""
__version__ = "1.0.0"

# Load eco-engine/.env (if present) before any module reads its settings.
# Real environment variables always win over values in the file.
try:
    from pathlib import Path as _Path
    from dotenv import load_dotenv as _load_dotenv

    _load_dotenv(_Path(__file__).resolve().parent.parent / ".env", override=False)
except ImportError:
    pass
