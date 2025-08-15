# tools/build_semantic_index.py
from __future__ import annotations
import argparse
from app.services import rag

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Rebuild semantic index (SBERT or TF‑IDF).")
    ap.add_argument("--sbert", action="store_true", help="Force SBERT (requires sentence-transformers)")
    args = ap.parse_args()
    msg = rag.rebuild_index(prefer_sbert=True if args.sbert else None)
    print(msg)
