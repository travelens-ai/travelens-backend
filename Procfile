web: PYTHONPATH=src venv/bin/gunicorn --workers 1 --threads 8 --timeout 600 --graceful-timeout 600 --keep-alive 75 --bind 0.0.0.0:8000 src.app:app
