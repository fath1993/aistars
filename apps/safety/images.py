"""
The user's photo, as the checks see it: downloaded from Telegram into memory,
shrunk to a JPEG of at most 1568 pixels a side (what vision models read at
full detail), never written to disk.
"""

import io

from apps.core.services import telegram

MAX_SIDE = 1568


def prepare(file_id) -> bytes:
    """Raises ValueError when the photo cannot be had or read."""
    return shrink(telegram.download_file(file_id))


def shrink(data) -> bytes:
    from PIL import Image, ImageOps

    try:
        with Image.open(io.BytesIO(data)) as image:
            image = ImageOps.exif_transpose(image).convert("RGB")
            image.thumbnail((MAX_SIDE, MAX_SIDE))
            out = io.BytesIO()
            image.save(out, "JPEG", quality=85)
    except Exception as exc:  # Pillow raises many kinds for a bad file
        raise ValueError(f"not a readable image ({type(exc).__name__})") from None
    return out.getvalue()
