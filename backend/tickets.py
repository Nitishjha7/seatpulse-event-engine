"""
Ticket generation — QR code, PDF, aur email.

⭐ Ye teeno kaam JAAN-BOOJH KE request ke bahar hote hain.

Kyu: QR banana + PDF render karna + email bhejna mila ke 2-3 second lagta
hai. Wo checkout request ke andar karte to user ko lagta ki payment atak
gaya — jabki uska paisa kat chuka hota aur booking ban chuki hoti.

Ab API turant "confirmed" bolti hai, aur ticket background me banti hai.
User ko "ticket ban raha hai" dikhta hai, jo sach bhi hai.
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

# PDF aur "bheje gaye" emails kahan jaate hain.
# Docker me ye ek volume hai, to worker aur API dono dekh sakte hain.
TICKET_DIR = Path("/app/tickets")
OUTBOX_DIR = Path("/app/tickets/outbox")


def new_qr_token() -> str:
    """
    QR ke liye random token.

    ⚠️ Booking id NAHI use karte — wo sequential hai. Koi bhi 1, 2, 3...
    ka QR bana ke gate pe chala jata. `token_urlsafe(24)` se 32 characters
    aate hain, guess karna practically namumkin.
    """
    return secrets.token_urlsafe(24)


def make_qr_png(token: str) -> bytes:
    """Token ka QR code, PNG bytes me."""
    qr = qrcode.QRCode(
        version=None,                       # size khud tay karo content ke hisaab se
        error_correction=qrcode.constants.ERROR_CORRECT_M,   # 15% damage tolerate
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
    Ek page ka PDF ticket.

    Landscape A5 — asli tickets isi shape ke hote hain, aur phone pe
    dikhane layak bhi rehta hai.
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

    # Footer — perforated line ka feel
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
    "Email bhejo."

    ⚠️ Yahan asli SMTP nahi hai — koi credentials nahi hain, aur ek portfolio
    project se asli emails bhejna waise bhi galat hai.

    Iski jagah **outbox** pattern: email disk par likh dete hain aur log
    karte hain. Django ka console/file email backend bilkul aisa hi karta
    hai development me.

    Asli SMTP lagana ho to sirf ye function badalna hai — baaki poora flow
    (queue, retry, status) waisa ka waisa rahega. Yahi wajah hai ki isse
    alag function rakha hai.
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
