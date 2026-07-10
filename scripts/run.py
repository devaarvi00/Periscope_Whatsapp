#!/usr/bin/env python3
import os
import sys

# Ensure the project root (one level up from scripts/) is on sys.path
# so that `import app` works from any working directory.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# Change cwd to project root so .env and alembic.ini are found
os.chdir(ROOT)

import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        reload_dirs=[ROOT],
        log_level="info",
    )
