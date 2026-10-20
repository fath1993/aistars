"""
AI answers, written in Markdown, as Telegram messages.

Telegram's HTML knows <b>, <i>, <code>, <pre> and <a>, and refuses a whole
message over one unbalanced tag, or one longer than 4096 characters. So the
answer is cut into blocks (paragraphs and code blocks), each converted on its
own and always balanced, then packed into messages under the limit. Every
message also comes as plain text, sent when Telegram refuses the HTML
(telegram.send_formatted): the answer is never lost to a formatting mistake.
"""

import html
import re

LIMIT = 3800          # room left for the footer and the length note
FENCE = re.compile(r"^```[\w+-]*\s*$")

INLINE_CODE = re.compile(r"`([^`\n]+)`")
BOLD = re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*|__(?=\S)(.+?)(?<=\S)__")
ITALIC = re.compile(r"(?<![\w*])\*(?=\S)([^*\n]+?)(?<=\S)\*(?![\w*])"
                    r"|(?<![\w_])_(?=\S)([^_\n]+?)(?<=\S)_(?![\w_])")
LINK = re.compile(r"\[([^\]\n]+)\]\((https?://[^\s)]+)\)")
HEADING = re.compile(r"^#{1,6}\s+(.+?)\s*#*$")
BULLET = re.compile(r"^(\s*)[-*+]\s+")


def esc(text) -> str:
    return html.escape(str(text), quote=False)


def blocks(text) -> list:
    """[(kind, text)]: "code" blocks whole, "text" paragraphs, in order."""
    result, paragraph, code, in_code = [], [], [], False
    for line in (text or "").replace("\r\n", "\n").split("\n"):
        if FENCE.match(line.strip()):
            if in_code:
                result.append(("code", "\n".join(code)))
                code, in_code = [], False
            else:
                if paragraph:
                    result.append(("text", "\n".join(paragraph)))
                    paragraph = []
                in_code = True
            continue
        if in_code:
            code.append(line)
        elif line.strip():
            paragraph.append(line)
        elif paragraph:
            result.append(("text", "\n".join(paragraph)))
            paragraph = []
    if in_code and code:        # an unclosed block: still code
        result.append(("code", "\n".join(code)))
    if paragraph:
        result.append(("text", "\n".join(paragraph)))
    return result


def inline(text) -> str:
    """One paragraph's Markdown as balanced Telegram HTML."""
    pieces, last = [], 0
    for match in INLINE_CODE.finditer(text):
        pieces.append(_styled(text[last:match.start()]))
        pieces.append(f"<code>{esc(match.group(1))}</code>")
        last = match.end()
    pieces.append(_styled(text[last:]))
    return "".join(pieces)


def _styled(text) -> str:
    """Escaped, then links, bold and italic; nothing here can leave a tag open."""
    lines = []
    for line in text.split("\n"):
        heading = HEADING.match(line)
        if heading:
            lines.append(f"<b>{_marks(esc(heading.group(1)))}</b>")
            continue
        line = BULLET.sub(lambda m: m.group(1) + "• ", line)
        lines.append(_marks(esc(line)))
    return "\n".join(lines)


def _marks(escaped) -> str:
    links = []

    def keep_link(match):
        # The line is escaped already: the address is unescaped, then quoted.
        url = html.escape(html.unescape(match.group(2)), quote=True)
        links.append(f'<a href="{url}">{match.group(1)}</a>')
        return f"\x00{len(links) - 1}\x00"

    escaped = LINK.sub(keep_link, escaped)
    escaped = BOLD.sub(lambda m: f"<b>{m.group(1) or m.group(2)}</b>", escaped)
    escaped = ITALIC.sub(lambda m: f"<i>{m.group(1) or m.group(2)}</i>", escaped)
    return re.sub(r"\x00(\d+)\x00", lambda m: links[int(m.group(1))], escaped)


def plain(text) -> str:
    """The answer as Telegram shows it without HTML: Markdown marks dropped."""
    out = []
    for kind, body in blocks(text):
        if kind == "code":
            out.append(body)
            continue
        body = INLINE_CODE.sub(r"\1", body)
        body = LINK.sub(r"\1 (\2)", body)
        body = BOLD.sub(lambda m: m.group(1) or m.group(2), body)
        body = "\n".join(HEADING.sub(r"\1", BULLET.sub(lambda m: m.group(1) + "• ", line))
                         for line in body.split("\n"))
        out.append(body)
    return "\n\n".join(out)


def _rendered(kind, body) -> list:
    """[(html, plain)] for one block, split when it is longer than a message."""
    if kind == "code":
        parts, current = [], []
        for line in body.split("\n"):
            if current and len(esc("\n".join(current + [line]))) > LIMIT - 30:
                parts.append("\n".join(current))
                current = []
            current.append(line[:LIMIT - 30])
        parts.append("\n".join(current))
        return [(f"<pre>{esc(part)}</pre>", part) for part in parts]
    if len(inline(body)) <= LIMIT:
        return [(inline(body), plain(body))]
    parts, current = [], ""
    for line in body.split("\n"):
        while len(line) > LIMIT // 2:              # one enormous line, cut at a space
            cut = line.rfind(" ", 0, LIMIT // 2)
            cut = cut if cut > 0 else LIMIT // 2
            parts.append(line[:cut])
            line = line[cut:].lstrip()
        if current and len(inline(current + "\n" + line)) > LIMIT:
            parts.append(current)
            current = line
        else:
            current = f"{current}\n{line}" if current else line
    if current:
        parts.append(current)
    return [(inline(part), plain(part)) for part in parts]


def messages(text) -> list:
    """
    The answer as [(html, plain)] messages, each under Telegram's limit with
    room for a footer. Never empty: an empty answer is one empty message.
    """
    out, html_parts, plain_parts, size = [], [], [], 0
    for kind, body in blocks(text):
        for html_text, plain_text in _rendered(kind, body):
            if html_parts and size + len(html_text) + 2 > LIMIT:
                out.append(("\n\n".join(html_parts), "\n\n".join(plain_parts)))
                html_parts, plain_parts, size = [], [], 0
            html_parts.append(html_text)
            plain_parts.append(plain_text)
            size += len(html_text) + 2
    if html_parts:
        out.append(("\n\n".join(html_parts), "\n\n".join(plain_parts)))
    return out or [("", "")]


def preview(text, limit=3900) -> str:
    """While an answer is being written: plain text, the end cut if too long."""
    text = text or ""
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0] + " …"


def split_plain(text, limit=3500) -> list:
    """Plain text (a transcript) in parts under `limit`, cut at spaces."""
    parts, text = [], (text or "").strip()
    while len(text) > limit:
        cut = text.rfind(" ", 0, limit)
        cut = cut if cut > 0 else limit
        parts.append(text[:cut])
        text = text[cut:].lstrip()
    return parts + [text] if text or not parts else parts
