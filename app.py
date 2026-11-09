"""Smart T&C Summary - Flask website. Run:  python app.py  ->  http://127.0.0.1:5000
Nothing uploaded is ever saved to disk; everything is processed in memory."""
import io, os, re, zipfile
import xml.etree.ElementTree as ET
from flask import Flask, jsonify, request, send_from_directory
from PIL import Image, ImageOps

try:
    import pytesseract
    for p in (r"C:\Program Files\Tesseract-OCR\tesseract.exe",
              r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"):
        if os.path.exists(p):
            pytesseract.pytesseract.tesseract_cmd = p
    pytesseract.get_tesseract_version()
    OCR_OK = True
except Exception:
    OCR_OK = False

app = Flask(__name__, static_folder="static", static_url_path="/static")
app.config["MAX_CONTENT_LENGTH"] = 25 * 1024 * 1024
IMG = {"png", "jpg", "jpeg", "webp", "bmp", "gif", "tif", "tiff"}

# (category, risk, regex, plain-language summary, why it matters)
RULES = [
 ("Payments", "High", r"auto(?:matic(?:ally)?)?[\s-]*renew\w*|renew\w* automatically|recurring (?:charge|billing|payment)",
  "Your subscription renews automatically and you will be charged again.", "You can be billed without a new confirmation if you forget to cancel."),
 ("Payments", "Medium", r"free trial[^.]{0,120}(?:charge|bill|convert|paid)|(?:charge|bill)[^.]{0,80}after (?:the|your) (?:free )?trial",
  "A free trial turns into a paid plan unless you cancel.", "Set a reminder before the trial ends."),
 ("Payments", "Medium", r"(?:change|increase|adjust)[^.]{0,40}(?:price|fee|pricing)s?",
  "Prices or fees may change.", "Your future cost may differ from what you see today."),
 ("Cancellation", "High", r"non-?refundable|no refunds?|not (?:be )?(?:entitled to )?(?:a )?refund",
  "Payments are generally not refundable.", "Money already paid may not come back, even if you stop using the service."),
 ("Cancellation", "Medium", r"cancel\w*[^.]{0,100}(?:\d+\s*(?:days?|hours?)|notice|before)",
  "Cancelling has conditions such as notice periods or deadlines.", "Missing the deadline can mean another charge."),
 ("Privacy", "Medium", r"(?:collect|track|access|use)[^.]{0,60}\blocation\b|\bgps\b",
  "The app may collect your location.", "Location reveals where you live, work and travel."),
 ("Privacy", "Low", r"collect\w*[^.]{0,80}(?:personal|usage|device|browsing|contact|account) (?:data|information)|cookies?",
  "Personal, device or usage data may be collected.", "Check what is collected and why."),
 ("Privacy", "Medium", r"retain\w*|retention|store[^.]{0,40}(?:after|even if)",
  "Your data may be kept even after you stop using the app.", "Deleting your account may not erase everything."),
 ("Data Sharing", "High", r"\bsell\w*[^.]{0,60}(?:data|information)|sale of[^.]{0,40}(?:data|information)",
  "Your data may be sold.", "Sold data can reach companies you have never heard of."),
 ("Data Sharing", "Medium", r"shar\w+[^.]{0,80}(?:third[\s-]part\w+|partners?|affiliates?|advertisers?|service providers?)",
  "Some data may be shared with other companies.", "Others get access to information about you."),
 ("Permissions", "Medium", r"(?:access|use)[^.]{0,50}(?:camera|microphone|contacts|photos|sms|storage|calendar)",
  "The app may ask to use your camera, microphone, contacts or files.", "Only allow what the app really needs."),
 ("Legal Rights", "High", r"arbitration|class[\s-]action|waive\w*[^.]{0,40}(?:right|jury)",
  "You may give up the right to sue in court or join a class action.", "Disputes would go to private arbitration."),
 ("Legal Rights", "High", r"(?:perpetual|irrevocable|royalty-free|worldwide)[^.]{0,80}licen[cs]e",
  "You give the company wide, long-lasting rights to use what you post.", "Your photos and text can be reused, even after you leave."),
 ("Legal Rights", "Medium", r"\bas[\s-]is\b|not (?:be )?liable|limit\w* (?:our )?liability|indemnif\w+",
  "The company limits its responsibility and may make you cover its losses.", "If something goes wrong, you may have little recourse."),
 ("Account Rules", "Medium", r"(?:terminate|suspend|disable)[^.]{0,80}(?:any time|at any time|without (?:prior )?notice|sole discretion)",
  "Your account can be suspended or closed at any time.", "You could lose access and data without warning."),
 ("Account Rules", "Medium", r"(?:modify|change|update|amend)[^.]{0,60}(?:terms|agreement|policy)[^.]{0,80}(?:without|at any time|sole discretion)",
  "The terms can change, sometimes without notice.", "You may be bound by rules you never saw."),
 ("Account Rules", "Low", r"(?:at least|over|under|minimum age)[^.]{0,20}\b1[3-8]\b|\b(?:13|16|18) years",
  "There is a minimum age to use the service.", "Check that you meet the age requirement."),
]
ICONS = {"Privacy": "🔐", "Payments": "💳", "Data Sharing": "📤", "Permissions": "📷",
         "Cancellation": "⚠️", "Legal Rights": "⚖️", "Account Rules": "👤"}
WEIGHT = {"High": 3, "Medium": 2, "Low": 1}

def split_clauses(text):
    text = text.replace("\r", "")
    parts = re.split(r"\n\s*\n|\n(?=\s*(?:\d+[.)]|[A-Z][A-Za-z &]{2,40}:?\s*\n))", text)
    parts = [re.sub(r"\s+", " ", p).strip() for p in parts]
    parts = [p for p in parts if len(p) > 3]
    if len(parts) < 3:  # one big block: split into sentence groups
        s = re.split(r"(?<=[.!?])\s+", re.sub(r"\s+", " ", text).strip())
        parts = [" ".join(s[i:i + 2]) for i in range(0, len(s), 2) if s[i:i + 2]]
    return parts

def analyze(text):
    clauses = split_clauses(text)
    found = {}
    for idx, c in enumerate(clauses):
        for r, (cat, risk, pat, summ, why) in enumerate(RULES):
            m = re.search(pat, c, re.I)
            if not m:
                continue
            if r in found:
                found[r]["also_in"].append(idx + 1)
            else:
                found[r] = {"category": cat, "icon": ICONS[cat], "risk": risk, "summary": summ,
                            "why": why, "clause_no": idx + 1, "clause": c,
                            "start": m.start(), "end": m.end(), "also_in": []}
    findings = sorted(found.values(), key=lambda f: (-WEIGHT[f["risk"]], f["clause_no"]))
    score = sum(WEIGHT[f["risk"]] for f in findings)
    highs = sum(f["risk"] == "High" for f in findings)
    level = "High" if highs >= 3 or score >= 14 else "Medium" if score >= 5 else "Low"
    words = len(text.split())
    cats = {}
    for f in findings:
        cats[f["category"]] = cats.get(f["category"], 0) + 1
    return {"findings": findings, "clauses": clauses, "overall": level, "categories": cats,
            "words": words, "read_minutes": max(1, round(words / 200)),
            "counts": {k: sum(f["risk"] == k for f in findings) for k in WEIGHT}}

def ocr_image(data):
    img = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("L")
    if max(img.size) < 1600:
        img = img.resize((img.width * 2, img.height * 2), Image.LANCZOS)
    img = ImageOps.autocontrast(img)
    return pytesseract.image_to_string(img, timeout=90)

def read_docx(data):
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        root = ET.fromstring(z.read("word/document.xml"))
    ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    return "\n\n".join("".join(t.text or "" for t in p.iter(ns + "t")) for p in root.iter(ns + "p"))

def read_pdf(data):
    from pypdf import PdfReader
    return "\n\n".join((pg.extract_text() or "") for pg in PdfReader(io.BytesIO(data)).pages)

@app.get("/")
def home():
    return send_from_directory("static", "index.html")

@app.get("/api/sample")
def sample():
    return jsonify(text=SAMPLE)

@app.post("/api/extract")
def extract():
    texts, warnings, pending = [], [], []
    for f in request.files.getlist("files"):
        name = f.filename or "file"
        ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        try:
            data = f.read()
            if ext in IMG:
                if OCR_OK:
                    t = ocr_image(data)
                else:
                    pending.append(name); continue
            elif ext == "pdf":
                t = read_pdf(data)
            elif ext == "docx":
                t = read_docx(data)
            elif ext in ("txt", "md", "text", ""):
                t = data.decode("utf-8", errors="ignore")
            else:
                warnings.append(f"{name}: unsupported file type."); continue
            if t.strip():
                texts.append(t.strip())
            else:
                warnings.append(f"{name}: no readable text found (try a clearer, well-lit photo).")
        except Exception:
            warnings.append(f"{name}: could not be read.")
    return jsonify(ok=bool(texts) or bool(pending), text="\n\n".join(texts), warnings=warnings,
                   ocr_missing=bool(pending), pending=pending,
                   error=None if (texts or pending) else (warnings[0] if warnings else "No files received."))

@app.post("/api/analyze")
def analyze_api():
    text = ((request.get_json(silent=True) or {}).get("text") or "").strip()
    if len(text) < 40:
        return jsonify(ok=False, error="Please provide at least a few sentences of Terms & Conditions."), 400
    return jsonify(ok=True, **analyze(text[:400000]))

@app.errorhandler(413)
def too_big(_):
    return jsonify(ok=False, error="Upload too large (max 25 MB)."), 413

@app.errorhandler(Exception)
def any_error(e):
    code = getattr(e, "code", 500)
    return jsonify(ok=False, error="Something went wrong. Please try again."), code if isinstance(code, int) else 500

SAMPLE = """Terms of Service - ExampleApp

1. Eligibility. You must be at least 13 years old to use ExampleApp.

2. Information We Collect. We collect personal information, device data and your precise location while you use the app. We use cookies to track usage.

3. Data Sharing. We may share your information with third-party partners, advertisers and service providers. We may also sell aggregated data to other businesses.

4. Permissions. The app may access your camera, microphone and contacts to provide features.

5. Subscriptions. Your free trial will convert to a paid plan and your subscription renews automatically each month until cancelled. We may change prices at any time.

6. Cancellation and Refunds. You may cancel at least 48 hours before the renewal date. All fees are non-refundable.

7. Your Content. You grant us a perpetual, worldwide, royalty-free license to use content you upload.

8. Data Retention. We retain your data for up to 5 years even after you delete your account.

9. Disputes. Any dispute will be settled by binding arbitration and you waive the right to join a class action.

10. Liability. The service is provided as is and we are not liable for any losses.

11. Termination and Changes. We may suspend your account at any time without notice and modify these terms at any time."""

if __name__ == "__main__":
    print("OCR (Tesseract) available:", OCR_OK)
    app.run(host="127.0.0.1", port=5000, debug=False)
