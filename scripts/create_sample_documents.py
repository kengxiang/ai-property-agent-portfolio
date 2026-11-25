import json
import textwrap
from pathlib import Path

from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4


OUTPUT = Path("data/sample_documents")
OUTPUT.mkdir(parents=True, exist_ok=True)


DOCUMENTS = [
    {
        "document_id": "DOC001",
        "property_id": "P001",
        "filename": "P001_brochure.pdf",
        "text": [
            "Property P001: KL Central Residence",
            "Price: RM550000.",
            "Bedrooms: 3. Bathrooms: 2.",
            "MRT distance: 500 metres.",
            "Facilities: swimming pool, gym and security.",
        ],
    },
    {
        "document_id": "DOC002",
        "property_id": "P001",
        "filename": "P001_house_rules.pdf",
        "text": [
            "Property P001: House Rules",
            "Pets are allowed with written management approval.",
            "Swimming pool closes at 10 PM.",
            "Residents must register visitors.",
        ],
    },
    {
        "document_id": "DOC003",
        "property_id": "P002",
        "filename": "P002_house_rules.pdf",
        "text": [
            "Property P002: House Rules",
            "Pets are not permitted in this property.",
            "Residents must follow parking regulations.",
        ],
    },
    {
        "document_id": "DOC004",
        "property_id": "P003",
        "filename": "P003_brochure.pdf",
        "text": [
            "Property P003: Metro Garden Condo",
            "Price: RM600000.",
            "Bedrooms: 2. Bathrooms: 2.",
            "MRT distance: 800 metres.",
            "Facilities: gym, playground and function room.",
        ],
    },
    {
        "document_id": "DOC005",
        "property_id": "P010",
        "filename": "P010_house_rules.pdf",
        "text": [
            "Property P010: House Rules",
            "Each unit has one allocated parking space.",
            "Visitor parking requires registration.",
            "Quiet hours begin at 11 PM.",
        ],
    },
    {
        "document_id": "DOC006",
        "property_id": None,
        "filename": "general_faq.pdf",
        "text": [
            "General Property FAQ",
            "Property prices are listed in Malaysian Ringgit.",
            "Availability must be checked using property records.",
            "Property-specific rules override general information.",
        ],
    },
]


def create_pdf(document):
    path = OUTPUT / document["filename"]

    pdf = canvas.Canvas(str(path), pagesize=A4)
    pdf.setTitle(document["filename"])

    width, height = A4

    pdf.setFont("Helvetica-Bold", 15)
    pdf.drawString(50, height - 60, document["filename"])

    pdf.setFont("Helvetica", 11)
    y = height - 100

    for paragraph in document["text"]:
        for line in textwrap.wrap(paragraph, width=85):
            pdf.drawString(50, y, line)
            y -= 18

        y -= 12

    pdf.save()


for document in DOCUMENTS:
    create_pdf(document)

manifest = [
    {
        "document_id": d["document_id"],
        "property_id": d["property_id"],
        "filename": d["filename"],
    }
    for d in DOCUMENTS
]

(OUTPUT / "manifest.json").write_text(
    json.dumps(manifest, indent=2),
    encoding="utf-8",
)

print(f"Created {len(DOCUMENTS)} synthetic PDF documents.")
