import json
import re
import subprocess
from urllib.request import urlopen
from pathlib import Path

def is_pdf_url(url: str) -> bool:
    """Check if URL points to a PDF file."""
    path = urlparse(url).path.lower()
    if path.endswith(".pdf"):
        return True
    # Some URLs serve PDF without .pdf extension but with content-type
    return False

def fetch_pdf_via_marker(url: str, output_dir: Path = None) -> dict:
    """Download PDF and convert to markdown using Marker (preserves tables, images, equations)."""
    try:
        from marker.converters.pdf import PdfConverter
        from marker.models import create_model_dict
        from marker.output import text_from_rendered
    except ImportError:
        return {"error": "marker-pdf not installed", "content": "", "title": "", "method": "failed"}

    # Download PDF to temp file
    import tempfile as _tf
    tmp_f = _tf.NamedTemporaryFile(suffix=".pdf", delete=False)
    tmp = tmp_f.name
    tmp_f.close()
    try:
        resp = urlopen(url, timeout=60)
        with open(tmp, "wb") as f:
            f.write(resp.read())
    except Exception as e:
        return {"error": f"PDF download failed: {e}", "content": "", "title": "", "method": "failed"}

    try:
        converter = PdfConverter(artifact_dict=create_model_dict())
        rendered = converter(tmp)
        text, metadata, images = text_from_rendered(rendered)

        # Extract title from content: first meaningful heading
        title = ""
        for line in text.splitlines():
            stripped = line.lstrip("# ").strip()
            if stripped and len(stripped) > 5 and not stripped.lower().startswith("chapter"):
                title = stripped
                break
        # Fallback: combine "Chapter" heading with next real title
        if not title:
            lines = text.splitlines()
            for i, line in enumerate(lines):
                if line.strip().startswith("#") and "Chapter" in line:
                    for j in range(i + 1, min(i + 5, len(lines))):
                        candidate = lines[j].strip()
                        if candidate and len(candidate) > 3 and not candidate.startswith("#"):
                            title = candidate
                            break
                    break
        if not title:
            title = urlparse(url).path.split("/")[-1].replace(".pdf", "").replace("-", " ").strip()

        # Save extracted images to clippings/images/ directory
        if images and output_dir:
            img_dir = output_dir / "images"
            img_dir.mkdir(parents=True, exist_ok=True)
            date_prefix = datetime.now().strftime("%Y%m%d")
            for img_name, img_data in images.items():
                safe_name = re.sub(r'[^\w.]', '_', img_name)
                if not safe_name.startswith(date_prefix):
                    safe_name = f"{date_prefix}-{safe_name}"
                img_path = img_dir / safe_name
                try:
                    if hasattr(img_data, 'save'):
                        img_data.save(str(img_path))
                    elif isinstance(img_data, bytes):
                        with open(img_path, 'wb') as f:
                            f.write(img_data)
                    text = text.replace(img_name, f"images/{safe_name}")
                except Exception as e:
                    print(f"Warning: could not save image {img_name}: {e}")

        return {
            "title": title,
            "content": text,
            "method": "marker",
            "domain_hint": "general",
        }
    except Exception as e:
        return {"error": f"Marker conversion failed: {e}", "content": "", "title": "", "method": "failed"}
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)

