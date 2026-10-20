"""AI answers in Markdown become Telegram HTML that is always balanced and short enough."""
from html.parser import HTMLParser

from django.test import SimpleTestCase

from apps.studio import formatting


class Balance(HTMLParser):
    ALLOWED = {"b", "i", "code", "pre", "a"}

    def __init__(self):
        super().__init__()
        self.open, self.bad = [], []

    def handle_starttag(self, tag, attrs):
        if tag not in self.ALLOWED:
            self.bad.append(tag)
        self.open.append(tag)

    def handle_endtag(self, tag):
        if not self.open or self.open.pop() != tag:
            self.bad.append(f"/{tag}")


def balanced(html_text) -> bool:
    parser = Balance()
    parser.feed(html_text)
    return not parser.bad and not parser.open


class InlineTests(SimpleTestCase):
    def html(self, text):
        return formatting.messages(text)[0][0]

    def test_bold_italic_code_and_links(self):
        self.assertEqual(self.html("**bold** and *italic* and `x < y`"),
                         "<b>bold</b> and <i>italic</i> and <code>x &lt; y</code>")
        self.assertEqual(self.html("[site](https://a.example/?a=1&b=2)"),
                         '<a href="https://a.example/?a=1&amp;b=2">site</a>')

    def test_headings_and_bullets(self):
        self.assertEqual(self.html("## Steps\n- one\n* two"), "<b>Steps</b>\n• one\n• two")

    def test_what_is_not_markdown_stays(self):
        self.assertEqual(self.html("5 * 3 = 15, snake_case_name, a*b*c"),
                         "5 * 3 = 15, snake_case_name, a*b*c")
        self.assertEqual(self.html("<script>alert(1)</script> & co"),
                         "&lt;script&gt;alert(1)&lt;/script&gt; &amp; co")

    def test_code_blocks_are_kept_as_written(self):
        html_text, plain = formatting.messages("Look:\n```python\nif a < b and **c**:\n    pass\n```")[0]
        self.assertEqual(html_text, "Look:\n\n<pre>if a &lt; b and **c**:\n    pass</pre>")
        self.assertIn("if a < b and **c**:", plain)

    def test_an_unclosed_code_block_is_still_code(self):
        self.assertEqual(self.html("```\nprint(1)"), "<pre>print(1)</pre>")

    def test_the_plain_version_drops_the_marks(self):
        self.assertEqual(formatting.plain("**Hi** see [x](https://e.example)"),
                         "Hi see x (https://e.example)")


class SplitTests(SimpleTestCase):
    def test_long_answers_split_under_the_limit_and_stay_balanced(self):
        text = "\n\n".join(f"**Part {n}.** " + "word " * 300 for n in range(10))
        text += "\n\n```\n" + "\n".join(f"line {n} <tag>" for n in range(600)) + "\n```"
        parts = formatting.messages(text)
        self.assertGreater(len(parts), 3)
        for html_text, plain in parts:
            self.assertLessEqual(len(html_text), formatting.LIMIT)
            self.assertTrue(balanced(html_text), html_text[:80])

    def test_one_enormous_line_is_cut_at_spaces(self):
        parts = formatting.messages("word " * 4000)
        self.assertTrue(all(not html_text.startswith("ord") for html_text, _ in parts))

    def test_an_empty_answer_is_one_empty_message(self):
        self.assertEqual(formatting.messages(""), [("", "")])

    def test_the_preview_is_cut_at_a_word(self):
        self.assertEqual(formatting.preview("aaa bbb ccc", limit=6), "aaa …")

    def test_plain_transcripts_split_at_spaces(self):
        parts = formatting.split_plain("abc " * 2000, limit=1000)
        self.assertTrue(all(len(part) <= 1000 for part in parts))
        self.assertEqual(" ".join(parts).split(), ["abc"] * 2000)
        self.assertEqual(formatting.split_plain(""), [""])
