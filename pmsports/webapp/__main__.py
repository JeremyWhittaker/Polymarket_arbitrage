"""Run the research server: .venv/bin/python -m pmsports.webapp [--port 8808]"""
from __future__ import annotations

import argparse
import logging

import uvicorn


def main() -> None:
    ap = argparse.ArgumentParser(prog="pmsports.webapp")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8808)
    ap.add_argument("--reload", action="store_true")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    uvicorn.run("pmsports.webapp.server:app", host=a.host, port=a.port, reload=a.reload, log_level="info")


if __name__ == "__main__":
    main()
