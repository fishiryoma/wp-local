"""
compress-images.py
Compresses images in wp-content/uploads/ in-place.

- JPEG : quality 85, progressive encoding
- PNG  : lossless optimize
- WebP : quality 85
- Skips files < 50 KB (already small)
- Skips files where compression would make them larger
- Incremental: only processes new or changed files since last run

Usage:
    python compress-images.py          # incremental (only new/changed)
    python compress-images.py --all    # reprocess everything

Requirements:
    python -m pip install Pillow
"""

import sys
import time
import json
import hashlib
from pathlib import Path

try:
    from PIL import Image, ImageOps
except ImportError:
    print("ERROR: Pillow not installed. Run: python -m pip install Pillow")
    sys.exit(1)

# ── Config ──────────────────────────────────────────────────────
UPLOADS_DIR  = Path(r"C:\Users\User\Local Sites\tesstaiwan-local\app\public\wp-content\uploads")
CACHE_FILE   = Path(r"C:\Users\User\Local Sites\tesstaiwan-local\.compress-cache.json")
MIN_SIZE_KB  = 50       # skip files smaller than this
JPEG_QUALITY = 85
WEBP_QUALITY = 85
EXTENSIONS   = {".jpg", ".jpeg", ".png", ".webp"}
# ────────────────────────────────────────────────────────────────


def file_hash(path):
    """MD5 of file for change detection."""
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def load_cache():
    if CACHE_FILE.exists():
        try:
            return json.loads(CACHE_FILE.read_text())
        except Exception:
            pass
    return {}


def save_cache(cache):
    CACHE_FILE.write_text(json.dumps(cache, indent=2))


def compress(path):
    """Compress one image in-place. Returns (saved_bytes, skipped_reason)."""
    original_size = path.stat().st_size

    if original_size < MIN_SIZE_KB * 1024:
        return 0, f"too small ({original_size // 1024} KB)"

    ext = path.suffix.lower()

    try:
        with Image.open(path) as img:
            # Preserve EXIF and fix orientation
            img = ImageOps.exif_transpose(img)

            # Convert RGBA PNG to RGB only if saving as JPEG
            fmt = img.format or "JPEG"

            import io
            buf = io.BytesIO()

            if ext in (".jpg", ".jpeg"):
                if img.mode in ("RGBA", "P"):
                    img = img.convert("RGB")
                img.save(buf, format="JPEG", quality=JPEG_QUALITY,
                         optimize=True, progressive=True)
            elif ext == ".png":
                img.save(buf, format="PNG", optimize=True)
            elif ext == ".webp":
                img.save(buf, format="WEBP", quality=WEBP_QUALITY, method=6)
            else:
                return 0, "unsupported"

            compressed = buf.getvalue()
            if len(compressed) >= original_size:
                return 0, "already optimal"

            path.write_bytes(compressed)
            return original_size - len(compressed), None

    except Exception as e:
        return 0, f"error: {e}"


def main():
    force_all = "--all" in sys.argv
    start = time.time()

    print("=" * 50)
    print("  Image Compressor")
    print(f"  Mode: {'full' if force_all else 'incremental'}")
    print("=" * 50)

    cache = {} if force_all else load_cache()
    files = [f for f in UPLOADS_DIR.rglob("*")
             if f.is_file() and f.suffix.lower() in EXTENSIONS]

    print(f"\nFound {len(files):,} image files")

    processed = skipped = errors = 0
    total_saved = 0

    for i, path in enumerate(files, 1):
        key = str(path)
        current_hash = file_hash(path)

        if not force_all and cache.get(key) == current_hash:
            continue  # unchanged since last run

        saved, reason = compress(path)

        if reason and reason != "already optimal":
            if reason.startswith("error"):
                errors += 1
                print(f"  [{i}] FAIL {path.name}  ({reason})")
            else:
                skipped += 1
            # still mark as processed so we don't retry next time
        else:
            processed += 1
            total_saved += saved
            if saved > 0:
                print(f"  [{i}] OK   {path.name}  (-{saved // 1024} KB)")

        # update cache with new hash (after compression)
        cache[key] = file_hash(path)

    save_cache(cache)

    elapsed = time.time() - start
    mins, secs = divmod(int(elapsed), 60)
    saved_mb = total_saved / 1_048_576

    print("\n" + "=" * 50)
    print(f"  Processed : {processed} files")
    print(f"  Skipped   : {skipped} files (too small / already optimal)")
    print(f"  Errors    : {errors}")
    print(f"  Saved     : {saved_mb:.1f} MB")
    print(f"  Time      : {mins}m {secs}s")
    print("=" * 50)
    print("\nNext: sync to R2")
    print("  aws s3 sync ... s3://tesstaiwan-uploads ...")


if __name__ == "__main__":
    main()
