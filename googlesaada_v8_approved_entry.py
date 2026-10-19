"""GoogleSaada approved UI mount. Existing unified routes retained."""
from pathlib import Path
from fastapi.staticfiles import StaticFiles
from googlesaada_unified_app import app
ROOT=Path(__file__).resolve().parent
FRONTEND=ROOT/'frontend'
if not (FRONTEND/'index.html').is_file():
    raise RuntimeError('Approved GoogleSaada frontend missing')
if not hasattr(app,'mount'):
    raise RuntimeError('Unified application is not ASGI/FastAPI-mountable')
app.mount('/v8', StaticFiles(directory=str(FRONTEND),html=True),name='approved-googlesaada-v8')
