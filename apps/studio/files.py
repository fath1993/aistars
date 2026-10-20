"""
Files the AI reads: what a Telegram message carries, which files each
provider takes, how much room they take (for the hold), and their bytes for
the call.

OpenAI's Responses API reads PDFs (their text and page images, on models
that see images), Word and other documents, PowerPoint, spreadsheets (the
first 1,000 rows of each sheet), text and code; at most 50 MB a request.
Anthropic's models read PDFs and plain text. Photos go as images to both.
A bot downloads at most 20 MB a file from Telegram. Nothing is stored here:
files are fetched from Telegram by file_id whenever they are needed.
"""

import io
from pathlib import PurePosixPath

from apps.core.services import telegram

MAX_FILE = telegram.MAX_DOWNLOAD
MAX_REQUEST = 50 * 1024 * 1024
MAX_ITEMS = 20
# A photo whose size is unknown (an older one), for the 50 MB budget.
PHOTO_BYTES = 400 * 1024

PHOTO_TYPES = ("image/jpeg", "image/png", "image/webp", "image/gif")
PDF = "application/pdf"

# Read by OpenAI as text (spreadsheets as their first 1,000 rows a sheet).
OFFICE = {
    "doc", "docx", "dot", "odt", "rtf", "pages",
    "ppt", "pptx", "pps", "pot", "key",
    "xls", "xlsx", "xlsm", "xlt", "xla", "xlb", "xlc", "xlm", "xlw", "iif",
}
# Read by both providers as plain text.
TEXT = {
    "txt", "md", "markdown", "csv", "tsv", "json", "jsonl", "xml", "html", "htm", "css",
    "yaml", "yml", "toml", "ini", "cfg", "conf", "log", "sql", "srt", "vtt", "tex",
    "py", "js", "mjs", "ts", "tsx", "jsx", "java", "kt", "c", "h", "cpp", "hpp", "cc",
    "cs", "go", "rs", "rb", "php", "swift", "sh", "bash", "ps1", "bat", "r", "m", "scala",
    "lua", "pl", "dart", "vue", "svelte",
}
TEXT_TYPES = ("text/", "application/json", "application/xml", "application/x-yaml")

# Room an item takes in the request, in tokens, on the safe side: a photo at
# full detail; a PDF page's text and its image; text at three bytes a token
# (fewer bytes a token in most languages).
PHOTO_TOKENS = 1600
PDF_PAGE_TOKENS = 1800
BYTES_PER_TOKEN = 3


def extension(name) -> str:
    return PurePosixPath(name or "").suffix.lower().lstrip(".")


# --------------------------------------------------------------------------
# What a message carries
# --------------------------------------------------------------------------

def of_message(message) -> list:
    """
    Every file in one Telegram message, as items: photo, document (a file
    the AI reads, or not), voice and audio (turned into text), or other.
    """
    if message.get("photo"):
        largest = message["photo"][-1]
        return [{"kind": "photo", "file_id": largest["file_id"],
                 "size": largest.get("file_size") or 0}]
    if message.get("voice"):
        voice = message["voice"]
        return [{"kind": "voice", "file_id": voice["file_id"],
                 "seconds": voice.get("duration") or 0, "name": "voice.ogg",
                 "size": voice.get("file_size") or 0}]
    if message.get("audio"):
        audio = message["audio"]
        return [{"kind": "audio", "file_id": audio["file_id"],
                 "seconds": audio.get("duration") or 0,
                 "name": audio.get("file_name") or "audio.mp3",
                 "size": audio.get("file_size") or 0}]
    if message.get("document"):
        document = message["document"]
        mime = document.get("mime_type") or ""
        item = {"file_id": document["file_id"], "name": document.get("file_name") or "file",
                "mime": mime, "size": document.get("file_size") or 0}
        return [{**item, "kind": "photo" if mime in PHOTO_TYPES else "document"}]
    for key in ("video", "animation", "video_note", "sticker"):
        if message.get(key):
            return [{"kind": "other", "name": key}]
    return []


def sort(item) -> str:
    """A document's sort: "pdf", "office", "text", or "" when the AI cannot read it."""
    ext, mime = extension(item.get("name")), item.get("mime") or ""
    if mime == PDF or ext == "pdf":
        return "pdf"
    if ext in OFFICE:
        return "office"
    if ext in TEXT or mime.startswith(TEXT_TYPES):
        return "text"
    return ""


def media_type(item) -> str:
    if sort(item) == "pdf":
        return PDF
    if sort(item) == "text" and not (item.get("mime") or "").startswith(TEXT_TYPES):
        return "text/plain"
    return item.get("mime") or "application/octet-stream"


def problem(item, model) -> tuple:
    """(what, name): why `model` cannot read `item`; ("", "") when it can."""
    from apps.ai.models import ProviderKind

    name = item.get("name") or ""
    if item["kind"] == "other":
        return "unsupported", name
    if item["kind"] == "photo":
        return ("no_photos", "") if not model.sees_images else ("", "")
    if (item.get("size") or 0) > MAX_FILE:
        return "too_big", name
    kind = sort(item)
    if not kind:
        return "unsupported", name
    if kind == "pdf" and not model.sees_images:
        return "cannot_read", name
    if kind == "office" and model.provider.kind != ProviderKind.OPENAI:
        return "cannot_read", name
    return "", ""


def tokens(item) -> int:
    """The most room an item takes in a request, in tokens."""
    if item["kind"] == "photo":
        return PHOTO_TOKENS
    if sort(item) == "pdf":
        pages = item.get("pages") or 0
        if pages:
            return pages * PDF_PAGE_TOKENS
    return max((item.get("size") or 0) // BYTES_PER_TOKEN, 500)


def size(item) -> int:
    return item.get("size") or (PHOTO_BYTES if item["kind"] == "photo" else 0)


def count_pages(item):
    """A PDF's pages, read once when it arrives, so its hold covers them."""
    if item["kind"] != "document" or sort(item) != "pdf" or item.get("pages"):
        return item
    try:
        from pypdf import PdfReader

        item["pages"] = len(PdfReader(io.BytesIO(telegram.download_file(item["file_id"]))).pages)
    except Exception:  # unreadable or not downloadable: the size decides
        item["pages"] = 0
    return item


# --------------------------------------------------------------------------
# For the call
# --------------------------------------------------------------------------

def load_photo(file_id) -> bytes:
    """A photo as the AI sees it: a JPEG of at most 1568 px a side."""
    from apps.ai.clients import AIError
    from apps.safety import images

    try:
        return images.prepare(file_id)
    except ValueError as exc:
        raise AIError(f"the photo could not be read: {exc}") from None


def load_file(item) -> dict:
    """{"name", "media_type", "data"} for clients.chat."""
    from apps.ai.clients import AIError

    try:
        data = telegram.download_file(item["file_id"])
    except ValueError as exc:
        raise AIError(f"{item.get('name')} could not be had: {exc}") from None
    return {"name": item.get("name") or "file", "media_type": media_type(item), "data": data}


def marker(item) -> str:
    """An attachment no longer sent with the chat, as the AI sees it."""
    if item["kind"] == "photo":
        return "[photo]"
    return f"[file: {item.get('name') or 'file'}]"
