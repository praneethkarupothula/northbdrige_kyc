"""Generator for CLEARLY SYNTHETIC test documents.

Every document carries the banner "SYNTHETIC SAMPLE - NOT A REAL DOCUMENT". People,
employers and banks are fictional; identifiers are random values that only satisfy local
format/checksum rules. These files are for testing this software and nothing else.
"""

from __future__ import annotations

import io
import random
from dataclasses import dataclass, field

import pymupdf
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from app.pipeline.identifiers import verhoeff_check_digit

BANNER = "SYNTHETIC SAMPLE - NOT A REAL DOCUMENT"


def make_aadhaar_number(rng: random.Random) -> str:
    base = str(rng.randint(2, 9)) + "".join(str(rng.randint(0, 9)) for _ in range(10))
    full = base + verhoeff_check_digit(base)
    return f"{full[:4]} {full[4:8]} {full[8:]}"


def make_pan(rng: random.Random, surname: str) -> str:
    letters = "ABCDEFGHJKLMNPQRSTUVWXYZ"
    return "".join(rng.choice(letters) for _ in range(3)) + "P" + surname[0].upper() + \
        "".join(str(rng.randint(0, 9)) for _ in range(4)) + rng.choice(letters)


@dataclass
class Person:
    name: str
    dob: str
    aadhaar: str
    pan: str
    father: str
    employer: str = "Northwind Analytics Pvt Ltd"
    address: str = "12 Lake View Road, Pune 411001"


@dataclass
class SalaryInfo:
    month: str = "April"
    year: int = 2025
    basic: int = 30000
    hra: int = 12000
    special: int = 10000
    pf: int = 3600
    ptax: int = 200
    itax: int = 2950
    employee_id: str = "NW1043"

    @property
    def gross(self) -> int:
        return self.basic + self.hra + self.special

    @property
    def net(self) -> int:
        return self.gross - self.pf - self.ptax - self.itax


@dataclass
class SynthDoc:
    category: str
    lines: list[str]
    truth: dict = field(default_factory=dict)


def fmt_money(n: int) -> str:
    return f"{n:,}.00"


def aadhaar_doc(p: Person) -> SynthDoc:
    return SynthDoc("AADHAAR", [
        BANNER, "Government of India", "Unique Identification Authority of India",
        f"Name: {p.name}", f"DOB: {p.dob}", "Gender: Female", f"Aadhaar No: {p.aadhaar}", f"Address: {p.address}",
    ], {"document_type": "AADHAAR", "full_name": p.name, "date_of_birth": p.dob, "id_number": p.aadhaar})


def pan_doc(p: Person) -> SynthDoc:
    return SynthDoc("PAN", [
        BANNER, "Income Tax Department", "Govt of India", "Permanent Account Number Card",
        f"PAN: {p.pan}", f"Name: {p.name}", f"Father's Name: {p.father}", f"Date of Birth: {p.dob}",
    ], {"document_type": "PAN", "full_name": p.name, "date_of_birth": p.dob, "id_number": p.pan})


def salary_doc(p: Person, s: SalaryInfo | None = None, *, name_override: str | None = None) -> SynthDoc:
    s = s or SalaryInfo()
    name = name_override or p.name
    return SynthDoc("SALARY_SLIP", [
        BANNER, p.employer, f"Salary Slip for the month of {s.month} {s.year}", f"Pay Period: {s.month} {s.year}",
        f"Employee Name: {name}", f"PAN: {p.pan}", f"Employee ID: {s.employee_id}", "Designation: Data Analyst",
        f"Employer: {p.employer}",
        f"Basic: {fmt_money(s.basic)}", f"HRA: {fmt_money(s.hra)}", f"Special Allowance: {fmt_money(s.special)}",
        f"Gross Earnings: {fmt_money(s.gross)}",
        f"Provident Fund: {fmt_money(s.pf)}", f"Professional Tax: {fmt_money(s.ptax)}",
        f"Income Tax: {fmt_money(s.itax)}", f"Net Pay: Rs. {fmt_money(s.net)}", "UAN: 100234567890",
    ], {"document_type": "SALARY_SLIP", "full_name": name, "pan_number": p.pan, "employer_name": p.employer,
        "gross_salary": f"{s.gross}.00", "net_salary": f"{s.net}.00", "pay_period": (s.month, s.year)})


def bank_doc(p: Person) -> SynthDoc:
    return SynthDoc("BANK_STATEMENT", [
        BANNER, "Synthetic Bank", "Bank Statement - Statement of Account",
        f"Account Holder Name: {p.name}", "Account Number: 501002345678", "IFSC: SYNB0001234",
        "Statement Period: 01/04/2025 to 30/04/2025",
        "05/04/2025 Salary credit NEFT 45,250.00", "09/04/2025 UPI payment 1,200.00",
        "Closing Balance: 61,830.00",
    ], {"document_type": "BANK_STATEMENT", "full_name": p.name})


# ------------------------------------------------------------------------------ renderers
def to_pdf_text(doc: SynthDoc, *, extra_pages: list[list[str]] | None = None) -> bytes:
    pdf = pymupdf.open()
    for lines in [doc.lines] + (extra_pages or []):
        page = pdf.new_page(width=595, height=842)
        y = 72
        for i, ln in enumerate(lines):
            page.insert_text((60, y), ln, fontsize=13 if i else 10, fontname="helv")
            y += 26
    out = pdf.tobytes()
    pdf.close()
    return out


def to_image(doc: SynthDoc, *, blur: float = 0.0, rotate: float = 0.0, scale: float = 1.0) -> Image.Image:
    font = ImageFont.load_default(size=int(30 * scale))
    small = ImageFont.load_default(size=int(22 * scale))
    w, h = int(1400 * scale), int((140 + 56 * len(doc.lines)) * scale)
    img = Image.new("RGB", (w, h), "white")
    d = ImageDraw.Draw(img)
    y = int(50 * scale)
    for i, ln in enumerate(doc.lines):
        d.text((int(60 * scale), y), ln, fill=(15, 15, 15), font=font if i else small)
        y += int(56 * scale)
    if rotate:
        img = img.rotate(rotate, expand=True, fillcolor="white", resample=Image.BICUBIC)
    if blur:
        img = img.filter(ImageFilter.GaussianBlur(blur))
    return img


def to_png(doc: SynthDoc, **kw) -> bytes:
    buf = io.BytesIO()
    to_image(doc, **kw).save(buf, format="PNG", dpi=(200, 200))
    return buf.getvalue()


def to_jpeg(doc: SynthDoc, **kw) -> bytes:
    buf = io.BytesIO()
    to_image(doc, **kw).save(buf, format="JPEG", quality=92)
    return buf.getvalue()


def to_scanned_pdf(doc: SynthDoc) -> bytes:
    """Image-only PDF (no text layer) - forces the Tesseract path."""
    png = to_png(doc)
    pdf = pymupdf.open()
    img = Image.open(io.BytesIO(png))
    page = pdf.new_page(width=img.width * 72 / 200, height=img.height * 72 / 200)
    page.insert_image(page.rect, stream=png)
    out = pdf.tobytes()
    pdf.close()
    return out


RENDERERS = {"pdf": to_pdf_text, "png": to_png, "jpeg": to_jpeg, "scanned_pdf": to_scanned_pdf}

PEOPLE_SEED = [
    ("Asha Verma", "Ramesh Verma"), ("Rohan Iyer", "Suresh Iyer"), ("Meera Nair", "Gopal Nair"),
    ("Kabir Malhotra", "Anil Malhotra"), ("Priya Deshpande", "Vijay Deshpande"), ("Arjun Rao", "Mohan Rao"),
]


def person(i: int = 0, seed: int = 7) -> Person:
    rng = random.Random(seed * 1000 + i)
    name, father = PEOPLE_SEED[i % len(PEOPLE_SEED)]
    dob = f"{rng.randint(1, 28):02d}/{rng.randint(1, 12):02d}/{rng.randint(1975, 2000)}"
    return Person(name=name, dob=dob, aadhaar=make_aadhaar_number(rng), pan=make_pan(rng, name.split()[-1]),
                  father=father)


def encrypted_pdf() -> bytes:
    pdf = pymupdf.open()
    pdf.new_page().insert_text((72, 72), BANNER)
    out = pdf.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="owner-secret", user_pw="user-secret")
    pdf.close()
    return out
