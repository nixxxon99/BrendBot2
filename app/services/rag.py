# app/services/rag.py
from __future__ import annotations
from typing import List, Dict, Any, Tuple
from pathlib import Path
import json, os
_HAS_SBERT = False
try:
    from sentence_transformers import SentenceTransformer
    import numpy as _np
    _HAS_SBERT = True
except Exception:
    import numpy as _np
    _HAS_SBERT = False
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
import joblib
_DATA_DIR = Path("data")
_SBERT_MODEL_NAME = os.getenv("SBERT_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
_TFIDF_VECT_PATH = _DATA_DIR / "tfidf_vectorizer.joblib"
_TFIDF_MTX_PATH  = _DATA_DIR / "tfidf_matrix.joblib"
_SBERT_EMB_PATH  = _DATA_DIR / "sbert_embeddings.npy"
_DOCS_META_PATH  = _DATA_DIR / "docs_meta.json"
def _load_docs() -> List[Dict[str, Any]]:
    docs: List[Dict[str, Any]] = []
    for p in [Path("data/ingested_kb.json"), Path("data/brands_kb.json"), Path("data/catalog.json")]:
        if p.exists():
            try:
                obj = json.loads(p.read_text(encoding="utf-8"))
                if isinstance(obj, list):
                    docs.extend(obj)
                elif isinstance(obj, dict):
                    for k, v in obj.items():
                        item = dict(v) if isinstance(v, dict) else {"text": str(v)}
                        item.setdefault("brand", k)
                        docs.append(item)
            except Exception:
                pass
    return docs
def _as_text(d: Dict[str, Any]) -> str:
    parts = []
    for k, v in d.items():
        if isinstance(v, (str, int, float)):
            parts.append(f"{k}: {v}")
        elif isinstance(v, list):
            parts.append(f"{k}: " + ", ".join(str(x) for x in v[:10]))
    return "\\n".join(parts)
def _ensure_meta(docs: List[Dict[str, Any]]):
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    _DOCS_META_PATH.write_text(json.dumps(docs, ensure_ascii=False), encoding="utf-8")
def rebuild_index(prefer_sbert: bool | None = None) -> str:
    docs = _load_docs()
    texts = [_as_text(d) for d in docs]
    _ensure_meta(docs)
    use_sbert = (prefer_sbert is True) or (prefer_sbert is None and _HAS_SBERT)
    if use_sbert:
        try:
            model = SentenceTransformer(_SBERT_MODEL_NAME)
            emb = model.encode(texts, convert_to_numpy=True, show_progress_bar=False, normalize_embeddings=True)
            _np.save(_SBERT_EMB_PATH, emb)
            if _TFIDF_VECT_PATH.exists(): _TFIDF_VECT_PATH.unlink()
            if _TFIDF_MTX_PATH.exists(): _TFIDF_MTX_PATH.unlink()
            return f"SBERT index built: {len(texts)} docs @ {_SBERT_MODEL_NAME}"
        except Exception:
            use_sbert = False
    vect = TfidfVectorizer(min_df=1, max_df=0.9, ngram_range=(1,2))
    mtx = vect.fit_transform(texts)
    joblib.dump(vect, _TFIDF_VECT_PATH)
    joblib.dump(mtx, _TFIDF_MTX_PATH)
    if _SBERT_EMB_PATH.exists(): _SBERT_EMB_PATH.unlink()
    return f"TF‑IDF index built: {mtx.shape[0]} docs, {mtx.shape[1]} terms"
def _have_sbert_index() -> bool:
    return _SBERT_EMB_PATH.exists() and _DOCS_META_PATH.exists()
def _have_tfidf_index() -> bool:
    return _TFIDF_VECT_PATH.exists() and _TFIDF_MTX_PATH.exists() and _DOCS_META_PATH.exists()
def ensure_index():
    if _have_sbert_index() or _have_tfidf_index(): return
    rebuild_index()
def search_semantic(query: str, top_k: int = 5) -> List[Tuple[float, Dict[str, Any]]]:
    if not query or not query.strip(): return []
    ensure_index()
    docs = json.loads(_DOCS_META_PATH.read_text(encoding="utf-8"))
    if _have_sbert_index():
        try:
            model = SentenceTransformer(_SBERT_MODEL_NAME)
            q = model.encode([query], convert_to_numpy=True, show_progress_bar=False, normalize_embeddings=True)
            emb = _np.load(_SBERT_EMB_PATH)
            sims = (emb @ q.T).squeeze()
            order = sims.argsort()[::-1]
            return [(float(sims[idx]), docs[idx]) for idx in order[:top_k]]
        except Exception:
            pass
    vect: TfidfVectorizer = joblib.load(_TFIDF_VECT_PATH)
    mtx = joblib.load(_TFIDF_MTX_PATH)
    q_vec = vect.transform([query])
    sims = cosine_similarity(mtx, q_vec).ravel()
    order = sims.argsort()[::-1]
    return [(float(sims[idx]), docs[idx]) for idx in order[:top_k]]
