#!/usr/bin/env python3.12
"""Convert XPeng G6 PDF manual to Spanish MkDocs site."""

import fitz  # PyMuPDF
import json
import re
import subprocess
import sys
from dataclasses import dataclass, asdict
from pathlib import Path

PDF_PATH = Path("/Users/german/Downloads/G6 2025-LHD User Manual.pdf")
PROJECT_ROOT = Path(__file__).parent.parent
DOCS_DIR = PROJECT_ROOT / "docs"
IMAGES_DIR = DOCS_DIR / "images"
PROGRESS_FILE = Path(__file__).parent / ".progress.json"
CHUNK_SIZE = 3000  # chars per translation chunk

CHAPTER_SLUGS = [
    "bienvenida",
    "perfil-vehiculo",
    "xos",
    "asistencia-realidad-virtual",
    "imagen-inteligente",
    "asistencia-conduccion",
    "estacionamiento-asistido",
    "seguridad-activa",
    "viaje-seguro",
    "entrada-salida",
    "operacion-conduccion",
    "configuracion-confort",
    "instrucciones-carga",
    "mantenimiento-diario",
    "rescate-emergencia",
    "informacion-vehiculo",
]


@dataclass
class Chapter:
    num: int        # 1-based
    title: str      # English title from TOC
    slug: str       # Spanish slug
    start_page: int # 0-based (PyMuPDF)
    end_page: int   # 0-based inclusive


def get_chapter_ranges(pdf_path: Path) -> list[Chapter]:
    """Extract level-1 TOC entries as chapter boundaries."""
    doc = fitz.open(str(pdf_path))
    toc = doc.get_toc()
    doc.close()

    level1 = [(title, page - 1) for level, title, page in toc if level == 1]
    chapters = []
    for i, (title, start) in enumerate(level1):
        end = level1[i + 1][1] - 1 if i + 1 < len(level1) else 366  # 0-based last page
        slug = CHAPTER_SLUGS[i] if i < len(CHAPTER_SLUGS) else f"capitulo-{i+1:02d}"
        chapters.append(Chapter(
            num=i + 1,
            title=title,
            slug=slug,
            start_page=start,
            end_page=end,
        ))
    return chapters


def extract_chapter_content(doc: fitz.Document, chapter: Chapter, images_dir: Path) -> str:
    """Extract text and images for a chapter. Images saved to images_dir."""
    parts = []
    image_count = 0

    for page_num in range(chapter.start_page, chapter.end_page + 1):
        page = doc[page_num]

        # Extract text blocks and image blocks in reading order
        blocks = page.get_text("blocks")
        blocks.sort(key=lambda b: (round(b[1] / 20), b[0]))  # sort by row then x

        for block in blocks:
            block_type = block[6]
            if block_type == 0:  # text
                text = block[4].strip()
                if text and len(text) > 2:
                    parts.append(text)
            elif block_type == 1:  # image placeholder in block list
                # Images are extracted separately below
                pass

        # Extract images for this page
        for img_info in page.get_images(full=False):
            xref = img_info[0]
            image_count += 1
            img_name = f"cap{chapter.num:02d}-img{image_count:02d}.png"
            img_path = images_dir / img_name
            if not img_path.exists():
                pix = fitz.Pixmap(doc, xref)
                if pix.n > 4:  # CMYK → RGB
                    pix = fitz.Pixmap(fitz.csRGB, pix)
                pix.save(str(img_path))
            parts.append(f"![Imagen {image_count}](images/{img_name})")

    return "\n\n".join(parts)


def translate_text(text: str) -> str:
    """Translate a single chunk of English text to Spanish using claude -p."""
    prompt = (
        "Sos un traductor técnico. Traducí el siguiente texto de un manual de auto "
        "del inglés al español. Conservá todo el formato Markdown, las listas, los "
        "encabezados y las referencias a imágenes exactamente como están. "
        "Devolvé únicamente el texto traducido, sin explicaciones ni comentarios.\n\n"
        + text
    )
    result = subprocess.run(
        ["claude", "-p"],
        input=prompt,
        capture_output=True,
        text=True,
        timeout=300,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Translation failed (exit {result.returncode}): {result.stderr[:200]}")
    return result.stdout.strip()


def translate_chapter_content(content: str) -> str:
    """Split content into CHUNK_SIZE chunks, translate each, reassemble."""
    if len(content) <= CHUNK_SIZE:
        return translate_text(content)

    # Split at paragraph boundaries
    paragraphs = content.split("\n\n")
    chunks: list[str] = []
    current = ""
    for para in paragraphs:
        if len(current) + len(para) > CHUNK_SIZE and current:
            chunks.append(current.strip())
            current = para
        else:
            current = current + "\n\n" + para if current else para
    if current.strip():
        chunks.append(current.strip())

    translated_parts = []
    for i, chunk in enumerate(chunks, 1):
        print(f"    chunk {i}/{len(chunks)} ({len(chunk)} chars)...", end=" ", flush=True)
        translated_parts.append(translate_text(chunk))
        print("ok")

    return "\n\n".join(translated_parts)


def write_chapter_md(chapter: Chapter, translated_title: str, content: str, docs_dir: Path) -> Path:
    """Write a translated chapter to docs/capXX-slug.md."""
    filename = f"cap{chapter.num:02d}-{chapter.slug}.md"
    path = docs_dir / filename
    header = f"# {translated_title}\n\n"
    path.write_text(header + content, encoding="utf-8")
    return path


def generate_index_md(chapters: list[Chapter], translated_titles: dict[int, str], docs_dir: Path) -> None:
    """Write docs/index.md with chapter list and short descriptions."""
    lines = [
        "# Manual de Usuario — XPeng G6\n",
        "> Manual traducido al español. Versión del sistema: V5.8.0.\n",
        "## Capítulos\n",
    ]
    for ch in chapters:
        title = translated_titles.get(ch.num, ch.title)
        filename = f"cap{ch.num:02d}-{ch.slug}.md"
        lines.append(f"- [{ch.num}. {title}]({filename})")
    (docs_dir / "index.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_mkdocs_yml(chapters: list[Chapter], translated_titles: dict[int, str]) -> None:
    """Write mkdocs.yml with full nav."""
    nav_entries = ["  - Índice: index.md"]
    for ch in chapters:
        title = translated_titles.get(ch.num, ch.title)
        filename = f"cap{ch.num:02d}-{ch.slug}.md"
        nav_entries.append(f"  - '{ch.num}. {title}': {filename}")

    content = f"""site_name: XPeng G6 — Manual de Usuario
site_url: https://germanpereyra.github.io/xpeng-g6-manual/  # replace with your GitHub username

theme:
  name: material
  language: es
  features:
    - navigation.instant
    - navigation.top
    - search.highlight

plugins:
  - search:
      lang: es

nav:
{chr(10).join(nav_entries)}
"""
    (PROJECT_ROOT / "mkdocs.yml").write_text(content, encoding="utf-8")


def load_progress() -> dict:
    if PROGRESS_FILE.exists():
        return json.loads(PROGRESS_FILE.read_text())
    return {"done": [], "translated_titles": {}}


def save_progress(done: list[int], translated_titles: dict[str, str]) -> None:
    PROGRESS_FILE.write_text(json.dumps(
        {"done": done, "translated_titles": translated_titles}, indent=2
    ))


def main() -> None:
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    DOCS_DIR.mkdir(parents=True, exist_ok=True)

    progress = load_progress()
    done: list[int] = progress["done"]
    translated_titles: dict[str, str] = progress["translated_titles"]

    doc = fitz.open(str(PDF_PATH))
    chapters = get_chapter_ranges(PDF_PATH)

    print(f"Found {len(chapters)} chapters. Already done: {done}")

    for chapter in chapters:
        if chapter.num in done:
            print(f"  [{chapter.num:02d}] {chapter.title} — skipped (already done)")
            continue

        print(f"  [{chapter.num:02d}] {chapter.title} (pp{chapter.start_page+1}-{chapter.end_page+1})")

        print("    Extracting...", end=" ", flush=True)
        content = extract_chapter_content(doc, chapter, IMAGES_DIR)
        print(f"ok ({len(content)} chars)")

        print("    Translating title...", end=" ", flush=True)
        translated_title = translate_text(chapter.title)
        translated_titles[str(chapter.num)] = translated_title
        print(f"ok → {translated_title!r}")

        print("    Translating content...")
        translated_content = translate_chapter_content(content)

        write_chapter_md(chapter, translated_title, translated_content, DOCS_DIR)
        done.append(chapter.num)
        save_progress(done, translated_titles)
        print(f"    Saved cap{chapter.num:02d}-{chapter.slug}.md")

    doc.close()

    # Convert string keys back to int for index/nav functions
    int_titles = {int(k): v for k, v in translated_titles.items()}
    generate_index_md(chapters, int_titles, DOCS_DIR)
    write_mkdocs_yml(chapters, int_titles)
    print("\nDone! Run: python3.12 -m mkdocs serve")


if __name__ == "__main__":
    main()
