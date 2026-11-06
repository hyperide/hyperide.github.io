"""HTML helpers shared by the report contract tests (check-own-results.py, check-product-report.py)."""

import html
import re
from html.parser import HTMLParser


class Links(HTMLParser):
    """Every <a href> of a page, in document order."""

    def __init__(self, text):
        super().__init__()
        self.urls = []
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self.urls.append(dict(attrs).get("href", ""))


class VisibleText(HTMLParser):
    """Text a reader sees without scripts and styles; `raw` keeps case, `text` is casefolded."""

    def __init__(self, text):
        super().__init__()
        self.parts, self.skip = [], 0
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skip += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.skip -= 1

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)

    @property
    def raw(self):
        return " ".join(" ".join(self.parts).split())

    @property
    def text(self):
        return self.raw.casefold()


def strip(fragment):
    """Text of an HTML fragment with tags removed, entities decoded and whitespace collapsed."""
    return " ".join(html.unescape(re.sub(r"<[^>]+>", "", fragment)).split())
