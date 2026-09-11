"""
Ticket generation — QR code, PDF, and email.

⭐ These tasks are intentionally performed outside the request cycle.

Reason: QR generation, PDF rendering, and email dispatch take 2-3 seconds.
Performing these during checkout would make the payment appear to hang,
even after the transaction is successful and the booking is created.

The API now returns "confirmed" immediately, and tickets are generated
in the background. The user sees a "ticket generating" status, which
accurately reflects the process.
"""

import io
import logging
import secrets
from datetime import datetime
from pathlib import Path

import qrcode
from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A5, landscape
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

logger = logging.getLogger(__name__)

# Storage location for PDFs and outgoing emails.
# Configured as a Docker volume so both the API and worker can access it.
TICKET_DIR = Path("/app/tickets")
OUTBOX_DIR = Path("/app/tickets/outbox")


def new_qr_token() -> str:
    """
    Generates a random token for the QR code.

    ⚠️ Do not use the booking ID, as it is sequential. A user could guess
    subsequent IDs to access other tickets. `token_urlsafe(24)` provides
    32 characters, making it practically impossible to guess.
    """
    return secrets.token_urlsafe(24)


def make_qr_png(token: str) -> bytes:
    """Generates a QR code for the token as PNG bytes."""
    qr = qrcode.QRCode(
        version=None,                       # Size determined by content
        error_correction=qrcode.constants.ERROR_CORRECT_M,   # 15% damage tolerance
        box_size=8,
        border=2,
    )
    qr.add_data(token)
    qr.make(fit=True)

    buf = io.BytesIO()
    qr.make_image(fill_color="black", back_color="white").save(buf, format="PNG")
    return buf.getvalue()


def make_ticket_pdf(
    *,
    token: str,
    booking_ref: str,
    event_name: str,
    venue: str,
    starts_at: datetime,
    seat_label: str,
    amount: float,
    attendee: str,
) -> bytes:
    """
    Generates a single-page PDF ticket.

    Uses landscape A5 format, which is standard for physical tickets
    and optimized for mobile screen display.
    """
    buf = io.BytesIO()
    width, height = landscape(A5)
    c = canvas.Canvas(buf, pagesize=landscape(A5))

    violet = HexColor("#7c3aed")
    dark = HexColor("#0f0f18")
    grey = HexColor("#6b7280")

    # Header band
    c.setFillColor(dark)
    c.rect(0, height - 28 * mm, width, 28 * mm, fill=1, stroke=0)

    c.setFillColor(violet)
    c.setFont("Helvetica-Bold", 20)
    c.drawString(15 * mm, height - 18 * mm, "SeatPulse")

    c.setFillColor(HexColor("#9ca3af"))
    c.setFont("Helvetica", 8)
    c.drawString(15 * mm, height - 23 * mm, "E-TICKET")

    c.setFont("Helvetica-Bold", 10)
    c.setFillColor(HexColor("#e5e7eb"))
    c.drawRightString(width - 15 * mm, height - 18 * mm, booking_ref)

    # Event
    c.setFillColor(HexColor("#111827"))
    c.setFont("Helvetica-Bold", 16)
    c.drawString(15 * mm, height - 42 * mm, event_name[:44])

    c.setFillColor(grey)
    c.setFont("Helvetica", 9)
    c.drawString(15 * mm, height - 49 * mm, venue[:60])
    c.drawString(
        15 * mm,
        height - 55 * mm,
        starts_at.strftime("%a, %d %b %Y  ·  %I:%M %p"),
    )

    # Detail boxes
    def box(x, label, value, big=False):
        c.setFillColor(HexColor("#f3f4f6"))
        c.roundRect(x, height - 82 * mm, 38 * mm, 20 * mm, 2 * mm, fill=1, stroke=0)
        c.setFillColor(grey)
        c.setFont("Helvetica", 7)
        c.drawString(x + 4 * mm, height - 68 * mm, label.upper())
        c.setFillColor(HexColor("#111827"))
        c.setFont("Helvetica-Bold", 16 if big else 11)
        c.drawString(x + 4 * mm, height - 77 * mm, value)

    box(15 * mm, "Seat", seat_label, big=True)
    box(57 * mm, "Price", f"Rs {amount:.0f}")
    box(99 * mm, "Attendee", attendee[:14])

    # QR — right side
    qr_size = 42 * mm
    qr_reader = io.BytesIO(make_qr_png(token))
    from reportlab.lib.utils import ImageReader

    c.drawImage(
        ImageReader(qr_reader),
        width - qr_size - 15 * mm,
        height - 88 * mm,
        qr_size,
        qr_size,
    )

    c.setFillColor(grey)
    c.setFont("Helvetica", 6)
    c.drawCentredString(
        width - qr_size / 2 - 15 * mm, height - 92 * mm, "Scan at the gate"
    )

    # Footer — perforated line feel
    c.setStrokeColor(HexColor("#d1d5db"))
    c.setDash(2, 3)
    c.line(15 * mm, 18 * mm, width - 15 * mm, 18 * mm)
    c.setDash()

    c.setFillColor(grey)
    c.setFont("Helvetica", 7)
    c.drawString(15 * mm, 12 * mm, "Gates open 90 minutes before showtime.")
    c.drawRightString(
        width - 15 * mm, 12 * mm, "One entry only. Do not share this QR."
    )

    c.showPage()
    c.save()
    return buf.getvalue()


def save_ticket(booking_id: int, pdf: bytes) -> Path:
    TICKET_DIR.mkdir(parents=True, exist_ok=True)
    path = TICKET_DIR / f"ticket-{booking_id}.pdf"
    path.write_bytes(pdf)
    return path


def ticket_path(booking_id: int) -> Path:
    return TICKET_DIR / f"ticket-{booking_id}.pdf"


def send_ticket_email(*, to: str, subject: str, body: str, pdf: bytes, booking_id: int) -> None:
    """
    Sends the ticket email.

    ⚠️ No actual SMTP integration is implemented here.
    Uses an **outbox** pattern: emails are written to disk for processing.
    This mimics Django's console/file email backend used in development.

    To implement real SMTP, only this function needs modification; the
    surrounding flow (queue, retry, status) remains unchanged.
    """
    OUTBOX_DIR.mkdir(parents=True, exist_ok=True)

    eml = OUTBOX_DIR / f"booking-{booking_id}.eml"
    eml.write_text(
        f"To: {to}\n"
        f"Subject: {subject}\n"
        f"X-Attachment: ticket-{booking_id}.pdf ({len(pdf)} bytes)\n"
        f"\n{body}\n",
        encoding="utf-8",
    )

    logger.info("📧 Ticket email queued for %s (outbox: %s)", to, eml.name)
