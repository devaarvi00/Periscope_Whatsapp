"""Knowledge base: text extraction (uploads, external URLs) and retrieval.

Retrieval is a small in-process BM25 over passages of the active entries —
no vector DB. FAQ entries are one passage each; documents and external
sources are split into ~900-character passages. The index is rebuilt when
the set of active entries changes (or on an explicit re-index).
"""
from __future__ import annotations

import io
import logging
import math
import re
import zipfile
from collections import Counter
from html.parser import HTMLParser
from typing import Any
from xml.etree import ElementTree

import httpx
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.knowledge_item import KnowledgeItem, kb_type_of

logger = logging.getLogger(__name__)

MAX_DOC_CHARS = 200_000
MAX_FETCH_BYTES = 2_000_000
PASSAGE_CHARS = 900
UPLOAD_TYPES = (".txt", ".md", ".markdown", ".docx")  # .pdf omitted: no PDF text library installed

_STOPWORDS = {
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "do", "does", "did", "can", "could",
    "will", "would", "should", "how", "what", "when", "where", "who", "why", "which", "i", "me", "you",
    "my", "your", "we", "our", "us", "to", "of", "in", "on", "at", "for", "and", "or", "it", "its",
    "this", "that", "these", "those", "please", "hi", "hello", "hey", "with", "from", "by", "as", "about",
    "any", "have", "has", "had", "there", "their", "they", "them", "if", "so", "not", "no", "yes", "am",
    "get", "got", "want", "need", "tell", "know", "just", "also", "then", "than", "here",
}


def tokenize(text: str) -> list[str]:
    words = re.findall(r"\w+", (text or "").lower())
    out = []
    for w in words:
        if len(w) < 2 or w in _STOPWORDS:
            continue
        # crude plural / suffix folding so "prices" matches "price"
        if len(w) > 4 and w.endswith("ies"):
            w = w[:-3] + "y"
        elif len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
            w = w[:-1]
        out.append(w)
    return out


def chunk_text(text: str, size: int = PASSAGE_CHARS) -> list[str]:
    """Split on paragraphs, packing them into ~size-character passages."""
    text = re.sub(r"\r\n?", "\n", text or "").strip()
    if not text:
        return []
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: list[str] = []
    buf = ""
    for p in paras:
        while len(p) > size:  # very long paragraph: hard-split on sentence/space
            cut = max(p.rfind(". ", 0, size), p.rfind(" ", 0, size))
            cut = cut + 1 if cut > size // 2 else size
            piece, p = p[:cut].strip(), p[cut:].strip()
            if buf:
                chunks.append(buf)
                buf = ""
            chunks.append(piece)
        if buf and len(buf) + len(p) + 2 > size:
            chunks.append(buf)
            buf = p
        else:
            buf = f"{buf}\n\n{p}" if buf else p
    if buf:
        chunks.append(buf)
    return chunks


# ── BM25 index ───────────────────────────────────────────────────────────────
class _Index:
    def __init__(self, passages: list[dict]) -> None:
        self.passages = passages
        self.tfs = [Counter(tokenize(p["search"])) for p in passages]
        self.lens = [sum(tf.values()) for tf in self.tfs]
        self.avg = (sum(self.lens) / len(self.lens)) if self.lens else 0.0
        df: Counter = Counter()
        for tf in self.tfs:
            df.update(tf.keys())
        n = len(passages)
        self.idf = {t: math.log(1 + (n - d + 0.5) / (d + 0.5)) for t, d in df.items()}

    def search(self, query: str, k: int = 4, k1: float = 1.5, b: float = 0.75) -> list[tuple[float, dict]]:
        terms = set(tokenize(query))
        if not terms or not self.passages:
            return []
        scored = []
        for i, tf in enumerate(self.tfs):
            s = 0.0
            for t in terms:
                f = tf.get(t)
                if not f:
                    continue
                s += self.idf.get(t, 0.0) * f * (k1 + 1) / (f + k1 * (1 - b + b * self.lens[i] / (self.avg or 1)))
            if s > 0:
                scored.append((s, self.passages[i]))
        scored.sort(key=lambda x: x[0], reverse=True)
        return scored[:k]


_cache: dict[str, Any] = {"sig": None, "index": None}


def _signature(db: Session) -> tuple:
    row = db.query(func.count(KnowledgeItem.id), func.max(KnowledgeItem.updated_at), func.max(KnowledgeItem.id)) \
        .filter(KnowledgeItem.status == "active").one()
    return tuple(str(v) for v in row)


def build_index(items: list[KnowledgeItem]) -> _Index:
    passages = []
    for it in items:
        kind = kb_type_of(it)
        if kind in ("faq", "self_learned"):
            text = f"Q: {it.title}\nA: {it.content}"
            passages.append({"item_id": it.id, "title": it.title, "type": kind, "text": text, "search": text})
        else:
            for n, chunk in enumerate(chunk_text(it.content)):
                passages.append({"item_id": it.id, "title": it.title, "type": kind, "part": n + 1,
                                 "text": chunk, "search": f"{it.title}\n{chunk}"})
    return _Index(passages)


def get_index(db: Session, force: bool = False) -> _Index:
    sig = _signature(db)
    if force or _cache["sig"] != sig or _cache["index"] is None:
        items = db.query(KnowledgeItem).filter(KnowledgeItem.status == "active").all()
        _cache["index"] = build_index(items)
        _cache["sig"] = sig
    return _cache["index"]


def invalidate_index() -> None:
    _cache["sig"] = None
    _cache["index"] = None


def retrieve(db: Session, query: str, k: int = 4, max_chars: int = 6000) -> list[dict]:
    """Top-k passages for `query` from active entries (empty when nothing matches)."""
    hits = get_index(db).search(query, k=k)
    out, used = [], 0
    for score, p in hits:
        text = p["text"]
        if used + len(text) > max_chars:
            text = text[: max(0, max_chars - used)]
        if not text:
            break
        used += len(text)
        out.append({"item_id": p["item_id"], "title": p["title"], "type": p["type"],
                    "text": text, "score": round(score, 3)})
    return out


# ── Text extraction ──────────────────────────────────────────────────────────
class UnsupportedFile(ValueError):
    pass


def _docx_text(data: bytes) -> str:
    ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            info = z.getinfo("word/document.xml")
            if info.file_size > 20_000_000:
                raise UnsupportedFile("Document is too large")
            xml = z.read(info)
    except (zipfile.BadZipFile, KeyError) as exc:
        raise UnsupportedFile("Not a valid .docx file") from exc
    root = ElementTree.fromstring(xml)
    paras = []
    for p in root.iter(f"{ns}p"):
        texts = [t.text or "" for t in p.iter(f"{ns}t")]
        line = "".join(texts).strip()
        if line:
            paras.append(line)
    return "\n\n".join(paras)


def extract_upload_text(filename: str, data: bytes) -> str:
    name = (filename or "").lower()
    if name.endswith(".docx"):
        text = _docx_text(data)
    elif name.endswith((".txt", ".md", ".markdown")):
        text = data.decode("utf-8", errors="replace")
    else:
        raise UnsupportedFile("Supported files: .txt, .md, .docx")
    text = re.sub(r"[ \t]+\n", "\n", text).strip()
    if not text:
        raise UnsupportedFile("No text found in the file")
    return text[:MAX_DOC_CHARS]


class _HTMLText(HTMLParser):
    _SKIP = {"script", "style", "noscript", "svg", "template", "head"}
    _BLOCK = {"p", "div", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "section", "article",
              "header", "footer", "ul", "ol", "table", "blockquote", "pre"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip = 0
        self.title = ""
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag == "title":
            self._in_title = True
        if tag in self._SKIP:
            self.skip += 1
        elif tag in self._BLOCK:
            self.parts.append("\n\n")

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        if tag in self._SKIP and self.skip:
            self.skip -= 1
        elif tag in self._BLOCK:
            self.parts.append("\n\n")

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        if not self.skip:
            self.parts.append(data)


def html_to_text(html: str) -> tuple[str, str]:
    p = _HTMLText()
    try:
        p.feed(html)
    except Exception:
        pass
    text = "".join(p.parts)
    text = re.sub(r"[ \t\xa0]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return p.title.strip(), text


async def fetch_external(url: str) -> tuple[str, str]:
    """Fetch a public URL (SSRF-guarded, redirects re-validated, size-limited)
    and return (title, text)."""
    from app.services.url_safety import UnsafeURLError, assert_public_url

    current = url.strip()
    async with httpx.AsyncClient(timeout=15, follow_redirects=False,
                                 headers={"User-Agent": "HyperscopeKB/1.0"}) as client:
        for _ in range(4):
            await assert_public_url(current)
            async with client.stream("GET", current) as resp:
                if resp.is_redirect:
                    loc = resp.headers.get("location")
                    if not loc:
                        raise UnsafeURLError("Redirect without a location")
                    current = str(resp.url.join(loc))
                    continue
                if resp.status_code >= 400:
                    raise ValueError(f"The page returned HTTP {resp.status_code}")
                ctype = resp.headers.get("content-type", "").lower()
                if not any(t in ctype for t in ("text/html", "text/plain", "text/markdown", "application/xhtml")):
                    raise ValueError("Only HTML or plain-text pages can be imported")
                buf = bytearray()
                async for chunk in resp.aiter_bytes():
                    buf.extend(chunk)
                    if len(buf) > MAX_FETCH_BYTES:
                        break
                enc = resp.encoding or "utf-8"
                raw = bytes(buf[:MAX_FETCH_BYTES]).decode(enc, errors="replace")
                if "html" in ctype:
                    title, text = html_to_text(raw)
                else:
                    title, text = "", raw.strip()
                if not text:
                    raise ValueError("No readable text found on the page")
                return title[:300], text[:MAX_DOC_CHARS]
        raise ValueError("Too many redirects")
