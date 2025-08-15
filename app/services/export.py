# app/services/export.py
from __future__ import annotations
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.lib.units import cm
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer
from reportlab.lib import utils
import io, os, re
from typing import Dict, Any

def _cleanup(html: str) -> str:
    # очень простой санитайзер для PDF
    txt = re.sub(r"<[^>]+>", "", html)
    return txt

def create_brand_pdf(brand_name: str, caption_html: str, out_path: str) -> str:
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    doc = SimpleDocTemplate(out_path, pagesize=A4, leftMargin=2*cm, rightMargin=2*cm, topMargin=2*cm, bottomMargin=2*cm)
    styles = getSampleStyleSheet()
    flow = []
    flow.append(Paragraph(f"<b>{brand_name}</b>", styles['Title']))
    flow.append(Spacer(1, 0.5*cm))
    flow.append(Paragraph(_cleanup(caption_html).replace("\n","<br/>"), styles['Normal']))
    doc.build(flow)
    return out_path
