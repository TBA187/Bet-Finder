"""Entry point: `uvicorn main:app --host 0.0.0.0 --port 8000`"""
from app.main import create_app

app = create_app()
