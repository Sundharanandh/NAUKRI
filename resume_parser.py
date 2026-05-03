"""
resume_parser.py — Extracts text from a PDF resume
"""

import logging
import os

logger = logging.getLogger(__name__)


def extract_resume_text(pdf_path: str) -> str:
    if not os.path.exists(pdf_path):
        raise FileNotFoundError(
            f"Resume PDF not found at: {pdf_path}\n"
            f"Make sure your resume PDF is in the project folder and "
            f"'resume_path' in config.json matches the filename."
        )

    logger.info(f"📄 Reading resume from: {pdf_path}")

    # Try pdfplumber first
    try:
        import pdfplumber
        text = ""
        with pdfplumber.open(pdf_path) as pdf:
            logger.info(f"   Pages found: {len(pdf.pages)}")
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    text += page_text + "\n"
        if text.strip():
            logger.info(f"✅ Resume extracted ({len(text)} characters)")
            return text.strip()
    except Exception as e:
        logger.warning(f"⚠️  pdfplumber failed ({e}) — trying pypdf...")

    # Fallback: pypdf
    try:
        from pypdf import PdfReader
        reader = PdfReader(pdf_path)
        text = ""
        for page in reader.pages:
            page_text = page.extract_text()
            if page_text:
                text += page_text + "\n"
        if text.strip():
            logger.info(f"✅ Resume extracted via pypdf ({len(text)} characters)")
            return text.strip()
    except Exception as e:
        raise RuntimeError(f"Could not extract text from resume PDF: {e}")

    raise RuntimeError("Resume PDF appears to be empty or scanned image.")
