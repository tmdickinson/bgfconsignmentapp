"""Sales slip PDFs. Large slips print 2 per page and small slips 6 per page.

The layout is provisional until the treasurer reviews it (OPEN_ITEMS.md).
Sellers print at 100% / actual size on plain paper and cut along the dashed lines.
"""

import io
from dataclasses import dataclass, field

from reportlab.graphics.barcode import code128
from reportlab.lib.colors import Color, black, white
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.lib.utils import simpleSplit
from reportlab.pdfgen import canvas

from apps.core.money import format_cents

from .models import Condition, SlipSize

RED = Color(0.8, 0.1, 0.1)  # Prints as grey on black-and-white printers.
PAGE_W, PAGE_H = letter
MARGIN = 0.35 * inch
LAYOUT = {
    SlipSize.LARGE: {"cols": 1, "rows": 2, "title": 18, "body": 11, "small": 9, "price": 16,
                     "bar_w": 1.3, "bar_h": 0.6 * inch, "pad": 14},
    SlipSize.SMALL: {"cols": 2, "rows": 3, "title": 12, "body": 8.5, "small": 7, "price": 11,
                     "bar_w": 1.05, "bar_h": 0.45 * inch, "pad": 8},
}


@dataclass
class SlipData:
    item_id: str
    seller_id: str
    event_name: str
    name: str
    publisher: str
    description: str
    condition: str
    condition_note: str = ""
    bundle_count: int | None = None
    size: str = SlipSize.LARGE
    donate: bool = False
    prices: list = field(default_factory=list)  # [(window name, cents)]

    @classmethod
    def from_slip(cls, slip):
        seller = slip.listing.seller
        return cls(
            item_id=slip.item_id, seller_id=seller.seller_id, event_name=seller.event.name,
            name=slip.name, publisher=slip.publisher, description=slip.description,
            condition=slip.condition, condition_note=slip.condition_note,
            bundle_count=slip.bundle_count, size=slip.slip_size, donate=slip.donate_if_unsold,
            prices=[(p.window.name, p.price_cents) for p in slip.prices.all()],
        )


def _lines(text, font, size, width, max_lines=None):
    lines = simpleSplit(text or "", font, size, width)
    if max_lines and len(lines) > max_lines:
        lines = lines[:max_lines]
        last = lines[-1]
        while last and simpleSplit(last + "…", font, size, width)[0] != last + "…":
            last = last[:-1]
        lines[-1] = last.rstrip() + "…"
    return lines


def _fit(text, font, start, minimum, width, height):
    """The largest font size from `start` down to `minimum` that fits; truncated if none does."""
    size = start
    while size >= minimum:
        lines = simpleSplit(text, font, size, width)
        if len(lines) * size * 1.2 <= height:
            return size, lines
        size -= 0.5
    return minimum, _lines(text, font, minimum, width, max(1, int(height // (minimum * 1.2))))


def _heart(c, x, y, s):
    """A small heart mark, `s` points wide, with its bottom point at (x, y)."""
    c.saveState()
    c.setFillColor(RED)
    p = c.beginPath()
    p.moveTo(x, y)
    p.curveTo(x - s * 0.55, y + s * 0.45, x - s * 0.5, y + s * 0.95, x, y + s * 0.7)
    p.curveTo(x + s * 0.5, y + s * 0.95, x + s * 0.55, y + s * 0.45, x, y)
    c.drawPath(p, stroke=0, fill=1)
    c.restoreState()


def draw_slip(c, slip, x, y, w, h):
    L = LAYOUT[slip.size]
    pad = L["pad"]
    left, right, top = x + pad, x + w - pad, y + h - pad
    inner = right - left

    # Bottom block: donate box, then the barcode above it.
    bottom = y + pad
    if slip.donate:
        box_h = L["title"] + 8
        c.setFillColor(RED)
        c.rect(left, bottom, inner, box_h, stroke=0, fill=1)
        c.setFillColor(white)
        c.setFont("Helvetica-Bold", L["title"])
        c.drawCentredString(x + w / 2, bottom + (box_h - L["title"] * 0.7) / 2, "DONATE IF UNSOLD")
        c.setFillColor(black)
        bottom += box_h + 4
    c.setFont("Courier-Bold", L["body"] + 1)
    c.drawCentredString(x + w / 2, bottom, slip.item_id)
    barcode = code128.Code128(slip.item_id, barHeight=L["bar_h"], barWidth=L["bar_w"])
    barcode.drawOn(c, x + (w - barcode.width) / 2, bottom + L["body"] + 3)
    body_bottom = bottom + L["body"] + 3 + L["bar_h"] + 6

    # Header: event name and Seller ID.
    c.setFont("Helvetica", L["small"])
    c.drawString(left, top - L["small"], slip.event_name[:48])
    c.setFont("Helvetica-Bold", L["small"] + 1)
    c.drawRightString(right, top - L["small"], f"Seller {slip.seller_id}")
    cursor = top - L["small"] - 6
    c.setLineWidth(0.5)
    c.line(left, cursor, right, cursor)
    cursor -= 4

    # Prices in a right-hand column.
    price_w = inner * (0.34 if slip.size == SlipSize.LARGE else 0.38)
    text_w = inner - price_w - pad / 2
    px = right - price_w
    py = cursor - L["small"]
    c.setFont("Helvetica", L["small"])
    c.drawString(px, py, "PRICE")
    if slip.donate:
        _heart(c, right - L["small"], py - 1, L["small"] * 1.4)
    for name, cents in slip.prices:
        py -= L["price"] * 1.35
        c.setFont("Helvetica", L["body"])
        c.drawString(px, py, _lines(name, "Helvetica", L["body"], price_w * 0.55, 1)[0])
        c.setFont("Helvetica-Bold", L["price"])
        c.drawRightString(right, py, format_cents(cents).removesuffix(".00"))

    # Text on the left.
    for line in _lines(slip.name, "Helvetica-Bold", L["title"], text_w, 2):
        cursor -= L["title"]
        c.setFont("Helvetica-Bold", L["title"])
        c.drawString(left, cursor, line)
    cursor -= L["body"] * 1.3
    c.setFont("Helvetica", L["body"])
    c.drawString(left, cursor, _lines(slip.publisher, "Helvetica", L["body"], text_w, 1)[0])
    condition = Condition(slip.condition).label
    if slip.condition_note:
        condition += f": {slip.condition_note}"
    for line in _lines("Condition: " + condition, "Helvetica-Bold", L["body"], text_w, 2):
        cursor -= L["body"] * 1.25
        c.setFont("Helvetica-Bold", L["body"])
        c.drawString(left, cursor, line)
    if slip.bundle_count:
        cursor -= L["body"] * 1.25
        c.drawString(left, cursor, f"Bundle of {slip.bundle_count} items")
    cursor -= 4
    size, lines = _fit(slip.description, "Helvetica", L["body"], 5.5, text_w,
                       cursor - body_bottom)
    c.setFont("Helvetica", size)
    for line in lines:
        cursor -= size * 1.2
        c.drawString(left, cursor, line)


def slips_pdf(slips):
    """A PDF of SlipData, with large slips first and then small ones, each on their own pages."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    c.setTitle("Sales slips")
    for size in (SlipSize.LARGE, SlipSize.SMALL):
        group = [s for s in slips if s.size == size]
        L = LAYOUT[size]
        per_page = L["cols"] * L["rows"]
        w = (PAGE_W - 2 * MARGIN) / L["cols"]
        h = (PAGE_H - 2 * MARGIN) / L["rows"]
        for start in range(0, len(group), per_page):
            for i, slip in enumerate(group[start:start + per_page]):
                col, row = i % L["cols"], i // L["cols"]
                x, y = MARGIN + col * w, PAGE_H - MARGIN - (row + 1) * h
                c.setDash(3, 3)
                c.setLineWidth(0.5)
                c.rect(x, y, w, h)
                c.setDash()
                draw_slip(c, slip, x, y, w, h)
            c.setFont("Helvetica", 7)
            c.drawCentredString(PAGE_W / 2, MARGIN / 2,
                                "Print at 100% / Actual size. Cut along the dashed lines.")
            c.showPage()
    c.save()
    return buf.getvalue()


def sample_slips(event):
    """Made-up slips for test prints with the real scanners. Not saved anywhere."""
    windows = list(event.windows.all()) or []
    prices = [(w.name, max(100, 3000 - 1000 * i)) for i, w in enumerate(windows)]
    base = dict(seller_id=f"{event.code}-999", event_name=event.name, prices=prices)
    long_text = ("A sample description to check how much text fits on a slip. " * 5).strip()
    return [
        SlipData(item_id=f"{event.code}-999-0001", name="Sample Large Game", publisher="Sample Games",
                 description="Includes all pieces. Sleeved cards.", condition=Condition.GOOD,
                 size=SlipSize.LARGE, **base),
        SlipData(item_id=f"{event.code}-999-0002", name="Sample Donated Bundle With a Long Name",
                 publisher="Sample Publisher", description=long_text, condition=Condition.FAIR,
                 condition_note="Missing two cards", bundle_count=3, size=SlipSize.LARGE,
                 donate=True, **base),
    ] + [
        SlipData(item_id=f"{event.code}-999-{n:04d}", name=f"Sample Small Game {n}",
                 publisher="Sample Games", description=long_text if n % 2 else "Like new.",
                 condition=Condition.NEW, size=SlipSize.SMALL, donate=n == 4, **base)
        for n in range(3, 9)
    ]
