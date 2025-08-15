# app/services/vision.py
from __future__ import annotations
from typing import List
# lightweight stub; pluggable with Google Vision or CLIP later

def recognize_brands_from_bytes(image_bytes: bytes) -> List[str]:
    """Return list of probable brand names from an image. Stub implementation.
    TODO: plug real OCR/detector (google-cloud-vision or CLIP)."""
    # For now, return empty list to avoid false positives
    return []
