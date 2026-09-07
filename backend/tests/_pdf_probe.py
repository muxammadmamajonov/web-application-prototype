"""Reading a rendered PDF back the way a person looks at it.

Asserting on the HTML that goes INTO WeasyPrint proves nothing about the
document that comes out: the reported defects — a label colliding with its
value, a footer sitting on top of a clause, a name running past its column —
are all products of layout, and every one of them is invisible in the template
source.

So these helpers walk WeasyPrint's laid-out box tree, which carries the final
text together with its position and size on the page. That is strictly better
than rasterising and OCR-ing: the geometry is exact rather than inferred, and a
failure names the two strings that overlap instead of pointing at a picture.

Not a test module — no `test_` prefix, so pytest imports it without collecting.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Span:
    """One run of text with its box on the page, in CSS pt."""

    text: str
    x: float
    y: float
    w: float
    h: float

    @property
    def right(self) -> float:
        return self.x + self.w

    @property
    def bottom(self) -> float:
        return self.y + self.h

    def __repr__(self) -> str:  # a failure should read like a bug report
        return f"{self.text!r}@({self.x:.0f},{self.y:.0f} {self.w:.0f}×{self.h:.0f})"


@dataclass(frozen=True)
class Page:
    spans: tuple[Span, ...]
    width: float
    height: float

    @property
    def text(self) -> str:
        return " ".join(s.text for s in self.spans)


def render_pages(html: str, *, base_url: str | None = None) -> list[Page]:
    """Lay `html` out and return one :class:`Page` per printed page."""
    from weasyprint import HTML
    from weasyprint.formatting_structure import boxes

    def collect(box, into: list[Span]) -> None:
        """Depth-first over the laid-out tree, gathering every text run.

        Takes its accumulator as an argument rather than closing over one: a
        closure defined inside the page loop would capture the loop variable,
        which is the classic way to end up with every page's spans landing in
        the last page's list.
        """
        if isinstance(box, boxes.TextBox):
            content = box.text
            if content and content.strip():
                into.append(Span(
                    text=content,
                    x=float(box.position_x), y=float(box.position_y),
                    w=float(box.width), h=float(box.height),
                ))
        for child in getattr(box, "children", None) or ():
            collect(child, into)

    doc = HTML(string=html, base_url=base_url).render()
    pages: list[Page] = []
    for page in doc.pages:
        spans: list[Span] = []
        collect(page._page_box, spans)
        pages.append(Page(tuple(spans), float(page.width), float(page.height)))
    return pages


def all_text(pages: list[Page]) -> str:
    return " ".join(p.text for p in pages)


#: How much two text boxes may share before it counts as a collision.
#:
#: Not zero. Adjacent lines legitimately share a point or two where a line box
#: is taller than its glyphs, and neighbouring words on one line abut exactly.
#: The defect being caught is a label sitting ON its value, which overlaps by
#: far more than this in both directions at once.
_OVERLAP_TOLERANCE_PT = 2.0


def collisions(page: Page, *, tolerance: float = _OVERLAP_TOLERANCE_PT) -> list[tuple[Span, Span]]:
    """Pairs of text boxes that overlap in BOTH axes — i.e. print on top of
    each other. Requiring both is what separates a real collision from the
    ordinary vertical sharing between consecutive lines."""
    found: list[tuple[Span, Span]] = []
    spans = page.spans
    for i, a in enumerate(spans):
        for b in spans[i + 1:]:
            dx = min(a.right, b.right) - max(a.x, b.x)
            dy = min(a.bottom, b.bottom) - max(a.y, b.y)
            if dx > tolerance and dy > tolerance:
                found.append((a, b))
    return found


def overflows(page: Page, *, margin: float = 0.0) -> list[Span]:
    """Text that runs off the page — the clipping case. A span starting beyond
    the page is as broken as one ending beyond it, so both are reported."""
    return [
        s for s in page.spans
        if s.right > page.width - margin + 0.5
        or s.bottom > page.height - margin + 0.5
        or s.x < margin - 0.5
        or s.y < margin - 0.5
    ]


def find(pages: list[Page], needle: str) -> list[Span]:
    return [s for p in pages for s in p.spans if needle in s.text]
