import io, re, sys

p = "/frontend/src/booking/BookingContext.jsx"
s = io.open(p, encoding="utf-8").read()

s = s.replace("  createBooking,\n  getAccessToken,",
              "  createBooking,\n  getAccessToken,\n  startCheckout,")

new_fn = '''  /**
   * Checkout shuru karo — user ko gateway pe bhejo.
   *
   * ⚠️ Yahan booking NAHI banti. Booking tabhi banti hai jab payment
   * confirm ho — aur wo confirmation webhook se aati hai, is redirect se
   * nahi. Ye function sirf session bana ke user ko bhej deta hai.
   */
  async function payForSeat() {
    if (!selectedSeat) return

    setBooking(true)
    setMessage(null)
    try {
      const session = await startCheckout(selectedSeat.id)
      // Full page redirect, SPA navigation nahi — Stripe ke case me ye
      // unka domain hai, mock me hamara apna /pay/:id page.
      window.location.href = session.checkout_url
    } catch (err) {
      setMessage({ type: 'error', text: errorText(err) })
      setBooking(false)
      await refresh(event.id)
    }
  }

  async function confirmBooking() {'''

s = s.replace("  async function confirmBooking() {", new_fn, 1)
s = s.replace("    confirmBooking,\n    cancel,", "    confirmBooking,\n    payForSeat,\n    cancel,")

io.open(p, "w", encoding="utf-8").write(s)
print("patched")
