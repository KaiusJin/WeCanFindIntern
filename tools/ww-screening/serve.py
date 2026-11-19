"""Run with .venv/bin/python tools/ww-screening/serve.py."""
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from wecanfindintern.api.routes.ww_screening import router

app = FastAPI(title="WaterlooWorks CS/CE screening")
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])
app.include_router(router)
app.mount("/", StaticFiles(directory=Path(__file__).parent, html=True), name="screening")

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8766)
