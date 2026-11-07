"""Printable admin badges: a card-sized label with a Code 128 barcode of the Admin ID."""

import io

from django.conf import settings
from reportlab.graphics.barcode import code128
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.pdfgen import canvas

CARD_W, CARD_H = 3.375 * inch, 2.125 * inch


def badge_pdf(profile):
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    c.setTitle(f"Admin badge {profile.admin_id}")
    x, y = (letter[0] - CARD_W) / 2, letter[1] - inch - CARD_H
    c.setDash(3, 3)
    c.rect(x, y, CARD_W, CARD_H)
    c.setDash()
    c.setFont("Helvetica", 9)
    c.drawString(x + 12, y + CARD_H - 18, settings.BRAND["convention"])
    c.setFont("Helvetica-Bold", 12)
    c.drawRightString(x + CARD_W - 12, y + CARD_H - 19, "ADMIN")
    c.setFont("Helvetica-Bold", 14)
    c.drawString(x + 12, y + CARD_H - 40, profile.name[:32])
    barcode = code128.Code128(profile.admin_id, barHeight=0.55 * inch, barWidth=1.1)
    barcode.drawOn(c, x + (CARD_W - barcode.width) / 2, y + 30)
    c.setFont("Courier-Bold", 12)
    c.drawCentredString(x + CARD_W / 2, y + 14, profile.admin_id)
    c.setFont("Helvetica", 8)
    c.drawString(x, y - 14, "Print at 100% / actual size. Cut along the dashed line. "
                            "The badge is for overrides only and doesn't sign you in.")
    c.showPage()
    c.save()
    return buf.getvalue()
