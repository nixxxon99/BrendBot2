from __future__ import annotations
from typing import List
import os, io, re
_USE_GCV = False
try:
    from google.cloud import vision  # type: ignore
    _USE_GCV = True
except Exception:
    _USE_GCV = False
_OCR_AVAILABLE = False
try:
    import pytesseract  # type: ignore
    from PIL import Image  # type: ignore
    _OCR_AVAILABLE = True
except Exception:
    _OCR_AVAILABLE = False
def _cleanup_tokens(text: str) -> List[str]:
    tokens = re.split(r"[^A-Za-zА-Яа-яЁё0-9+&'´`’.-]+", text.lower())
    tokens = [t.strip(" .,-_") for t in tokens if t and len(t) >= 2]
    return tokens
def _reconstruct_candidates(tokens: List[str]) -> List[str]:
    cands=set(); n=len(tokens)
    for k in (1,2,3):
        for i in range(n-k+1):
            piece=" ".join(tokens[i:i+k]).strip()
            if len(piece)>=3: cands.add(piece)
    return list(cands)
def _google_vision_extract_text(image_bytes: bytes) -> str:
    client = vision.ImageAnnotatorClient(); image = vision.Image(content=image_bytes)
    resp = client.text_detection(image=image)
    if resp.error and resp.error.message: raise RuntimeError(resp.error.message)
    return resp.full_text_annotation.text if resp.full_text_annotation and resp.full_text_annotation.text else ""
def _tesseract_extract_text(image_bytes: bytes) -> str:
    image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    try: return pytesseract.image_to_string(image, lang=os.getenv("TESS_LANGS", "eng+rus"))
    except Exception: return pytesseract.image_to_string(image) or ""
def recognize_brands_from_bytes(image_bytes: bytes) -> List[str]:
    text=""
    if _USE_GCV and os.getenv("GOOGLE_APPLICATION_CREDENTIALS"):
        try: text=_google_vision_extract_text(image_bytes)
        except Exception: text=""
    if not text and _OCR_AVAILABLE:
        try: text=_tesseract_extract_text(image_bytes)
        except Exception: text=""
    if not text: return []
    tokens=_cleanup_tokens(text)
    cands=_reconstruct_candidates(tokens)
    cands=[c for c in cands if re.search(r"[A-Za-zА-Яа-яЁё]", c)]
    return sorted(set(cands), key=lambda x:(-len(x),x))[:50]
