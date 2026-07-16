"""
Extract and cache embedded JPEG previews from RAW files.
Most DSLR RAW formats (CR2, NEF, ARW, DNG, etc.) embed a full-res JPEG inside.
"""
from pathlib import Path
from typing import Optional
import hashlib
import io

from PIL import Image


def extract_embedded_jpeg(raw_path: Path) -> Optional[bytes]:
    """
    Find the largest embedded JPEG in a RAW file by scanning for JPEG SOI/EOI markers.
    Returns raw JPEG bytes, or None if no preview is found.
    """
    try:
        with open(raw_path, 'rb') as f:
            data = f.read()

        jpegs = []
        pos = 0
        while True:
            start = data.find(b'\xff\xd8\xff', pos)
            if start == -1:
                break
            end = data.find(b'\xff\xd9', start)
            if end == -1:
                break
            jpegs.append(data[start:end + 2])
            pos = end + 2

        return max(jpegs, key=len) if jpegs else None
    except Exception:
        return None


def get_raw_preview(raw_path: Path, cache_dir: Path) -> Optional[bytes]:
    """Return the full embedded JPEG at its native resolution, cached to disk."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    try:
        mtime = raw_path.stat().st_mtime
    except FileNotFoundError:
        return None
    key = hashlib.md5(f"{raw_path}:{mtime}:raw".encode()).hexdigest()
    cache_file = cache_dir / f"{key}.jpg"
    if cache_file.exists():
        return cache_file.read_bytes()
    jpeg = extract_embedded_jpeg(raw_path)
    if not jpeg:
        return None
    cache_file.write_bytes(jpeg)
    return jpeg


def get_thumbnail(raw_path: Path, cache_dir: Path, max_px: int = 640) -> Optional[bytes]:
    """
    Return a resized JPEG thumbnail for a RAW file, served from disk cache.
    Cache key includes the file's mtime so stale entries are auto-invalidated.
    First call reads the RAW (slow). Subsequent calls hit the cache (fast).
    """
    cache_dir.mkdir(parents=True, exist_ok=True)

    try:
        mtime = raw_path.stat().st_mtime
    except FileNotFoundError:
        return None

    key = hashlib.md5(f"{raw_path}:{mtime}:{max_px}".encode()).hexdigest()
    cache_file = cache_dir / f"{key}.jpg"

    if cache_file.exists():
        return cache_file.read_bytes()

    jpeg = extract_embedded_jpeg(raw_path)
    if not jpeg:
        return None

    try:
        img = Image.open(io.BytesIO(jpeg))
        img.thumbnail((max_px, max_px), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format='JPEG', quality=82, optimize=True)
        thumb = buf.getvalue()
        cache_file.write_bytes(thumb)
        return thumb
    except Exception:
        return jpeg  # fallback: return unresized original
