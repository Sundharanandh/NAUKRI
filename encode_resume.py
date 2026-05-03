"""
encode_resume.py — Run ONCE to encode your PDF for GitHub Secrets
Usage: python encode_resume.py
"""

import base64, sys, os

def encode(pdf_path=None):
    if not pdf_path:
        for f in os.listdir("."):
            if f.endswith(".pdf"):
                pdf_path = f
                break
    if not pdf_path or not os.path.exists(pdf_path):
        print("❌ No PDF found. Usage: python encode_resume.py yourresume.pdf")
        sys.exit(1)

    with open(pdf_path, "rb") as f:
        encoded = base64.b64encode(f.read()).decode("utf-8")

    out = "resume_base64.txt"
    with open(out, "w") as f:
        f.write(encoded)

    print(f"\n✅ Encoded: {pdf_path}")
    print(f"💾 Saved to: {out}")
    print(f"\n➡️  Add contents of '{out}' as GitHub Secret: RESUME_PDF_BASE64")

if __name__ == "__main__":
    encode(sys.argv[1] if len(sys.argv) > 1 else None)
