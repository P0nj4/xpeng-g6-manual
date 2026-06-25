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
