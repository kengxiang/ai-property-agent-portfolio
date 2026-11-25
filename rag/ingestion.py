import hashlib
import json
from pathlib import Path

from pypdf import PdfReader


PROJECT_ROOT = Path(__file__).resolve().parents[1]

DOCUMENTS_DIR = PROJECT_ROOT / "data" / "sample_documents"


def chunk_text(text, max_words=90, overlap_words=15):
    """Split text into smaller overlapping chunks."""

    if max_words <= 0 or not 0 <= overlap_words < max_words:
        raise ValueError("Invalid chunk settings")

    words = text.split()

    if not words:
        return []

    chunks = []
    step = max_words - overlap_words

    for start in range(0, len(words), step):
        chunk_words = words[start:start + max_words]

        if chunk_words:
            chunks.append(" ".join(chunk_words))

        if start + max_words >= len(words):
            break

    return chunks


def load_document_chunks(documents_dir=DOCUMENTS_DIR):
    """Extract PDF text and attach source metadata."""

    documents_dir = Path(documents_dir)

    manifest_path = documents_dir / "manifest.json"

    manifest = json.loads(
        manifest_path.read_text(encoding="utf-8")
    )

    all_chunks = []
    seen_document_ids = set()

    for document in manifest:

        document_id = document["document_id"]
        filename = document["filename"]
        property_id = document["property_id"]

        if document_id in seen_document_ids:
            raise ValueError("Duplicate document_id")

        seen_document_ids.add(document_id)

        # Prevent paths escaping our document folder.
        if Path(filename).name != filename:
            raise ValueError("Invalid document filename")

        pdf_path = documents_dir / filename

        content_hash = hashlib.sha256(
            pdf_path.read_bytes()
        ).hexdigest()

        reader = PdfReader(pdf_path)

        for page_number, page in enumerate(
            reader.pages, start=1
        ):

            text = page.extract_text() or ""

            chunks = chunk_text(text)

            for chunk_number, content in enumerate(
                chunks, start=1
            ):

                all_chunks.append({
                    "chunk_id": (
                        f"{document_id}-p{page_number}"
                        f"-c{chunk_number}"
                    ),
                    "document_id": document_id,
                    "property_id": property_id,
                    "filename": filename,
                    "page": page_number,
                    "source_type": "PDF",
                    "content_hash": content_hash,
                    "version": 1,
                    "content": content,
                })

    return all_chunks
