import { useCallback, useEffect, useRef, useState } from 'react'

import { checkIn, getCheckinStats } from '../../api'
import { useBooking } from '../../booking/BookingContext'

/**
 * Gate check-in portal.
 *
 * Scans QR codes via camera, or allows manual entry if no camera is available.
 *
 * ⚠️ No external library used for QR scanning. We use the native browser `BarcodeDetector` (available in Chrome/Edge/Android). It is not supported in Firefox/Safari, which fall back to manual entry.
 *
 * Libraries like html5-qrcode or jsQR add ~200KB. For a gate portal often running on specific devices, this is too heavy—and manual entry is required anyway (for damaged QRs or dead batteries).
 */
export default function GatePortal() {
  const { event } = useBooking()

  const [result, setResult] = useState(null)
  const [manual, setManual] = useState('')
  const [scanning, setScanning] = useState(false)
  const [cameraError, setCameraError] = useState(null)
  const [stats, setStats] = useState(null)
  const [busy, setBusy] = useState(false)

  const videoRef = useRef(null)
  const streamRef = useRef(null)
  const loopRef = useRef(null)
  // A QR code remains visible for multiple frames; this guard prevents
  // triggering 20 requests for a single scan.
  const lastScanned = useRef({ token: null, at: 0 })

  const supported = typeof window !== 'undefined' && 'BarcodeDetector' in window

  const submit = useCallback(async (token) => {
    if (!token || busy) return
    setBusy(true)
    try {
      const res = await checkIn(token)
      setResult(res)
      // Refresh counter after each scan
      if (event) getCheckinStats(event.id).then(setStats).catch(() => {})
    } catch (err) {
      setResult({ ok: false, reason: 'error', error: err.message })
    } finally {
      setBusy(false)
    }
  }, [busy, event])

  // Live counter
  useEffect(() => {
    if (!event) return
    getCheckinStats(event.id).then(setStats).catch(() => {})
  }, [event])

  async function startCamera() {
    setCameraError(null)
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        // Use rear camera for gate scanning
        video: { facingMode: 'environment' },
      })
      streamRef.current = stream
      videoRef.current.srcObject = stream
      await videoRef.current.play()
      setScanning(true)

      const detector = new window.BarcodeDetector({ formats: ['qr_code'] })

      const tick = async () => {
        if (!videoRef.current) return
        try {
          const codes = await detector.detect(videoRef.current)
          if (codes.length) {
            const token = codes[0].rawValue
            const now = Date.now()
            // Do not re-process the same QR for 3 seconds
            if (token !== lastScanned.current.token || now - lastScanned.current.at > 3000) {
              lastScanned.current = { token, at: now }
              submit(token)
            }
          }
        } catch {
          /* frame decode failed — the next frame will retry */
        }
        loopRef.current = requestAnimationFrame(tick)
      }
      tick()
    } catch (err) {
      setCameraError(err.message)
      setScanning(false)
    }
  }

  function stopCamera() {
    cancelAnimationFrame(loopRef.current)
    streamRef.current?.getTracks().forEach((t) => t.stop())
    streamRef.current = null
    setScanning(false)
  }

  // Stop camera when leaving the page to prevent the flashlight from staying on.
  useEffect(() => () => stopCamera(), [])

  return (
    <div className="animate-rise mx-auto max-w-lg space-y-4">
      <header className="flex items-start justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-slate-100">Gate Check-in</h1>
          <p className="mt-1 text-sm text-slate-500">
            {event?.name ?? 'Scan ticket QR'}
          </p>
        </div>

        {stats && (
          <div className="rounded-xl border border-[var(--border)] bg-[var(--panel)] px-3 py-2 text-right">
            <p className="text-lg font-bold tabular-nums text-emerald-400">
              {stats.checked_in}
              <span className="text-sm text-slate-600"> / {stats.tickets_sold}</span>
            </p>
            <p className="text-[10px] uppercase tracking-wide text-slate-500">Checked in</p>
          </div>
        )}
      </header>

      {/* Result — displayed prominently as it is the primary focus at the gate */}
      {result && <ResultCard result={result} onDismiss={() => setResult(null)} />}

      <section className="rounded-2xl border border-[var(--border)] bg-[var(--panel)] p-5">
        {supported ? (
          <>
            <div className="relative overflow-hidden rounded-xl bg-black">
              <video
                ref={videoRef}
                className={`w-full ${scanning ? 'block' : 'hidden'}`}
                playsInline
                muted
              />
              {scanning && (
                // Aiming frame — shows the user where to hold the QR code
                <div className="pointer-events-none absolute inset-0 flex items-center justify-center">
                  <div className="h-40 w-40 rounded-2xl border-2 border-violet-400/70" />
                </div>
              )}
              {!scanning && (
                <div className="flex h-48 items-center justify-center text-sm text-slate-600">
                  Camera is off
                </div>
              )}
            </div>

            <button
              onClick={scanning ? stopCamera : startCamera}
              className={`mt-3 w-full rounded-xl px-4 py-2.5 text-sm font-semibold transition ${
                scanning
                  ? 'border border-[var(--border)] text-slate-400 hover:bg-white/5'
                  : 'bg-violet-600 hover:bg-violet-500'
              }`}
            >
              {scanning ? 'Stop camera' : '📷 Start scanning'}
            </button>

            {cameraError && (
              <p className="mt-2 rounded-lg bg-rose-500/10 px-3 py-2 text-xs text-rose-300">
                Camera failed to open: {cameraError}. Please use manual entry below.
              </p>
            )}
          </>
        ) : (
          <p className="rounded-lg bg-amber-500/10 px-3 py-2.5 text-xs leading-relaxed text-amber-200">
            This browser does not support QR scanning (<code>BarcodeDetector</code>). Please use Chrome or Edge, or use manual entry in the meantime.
          </p>
        )}

        {/* Manual entry is always available for damaged QRs, dead batteries, or lack of browser support */}
        <form
          onSubmit={(e) => {
            e.preventDefault()
            submit(manual.trim())
            setManual('')
          }}
          className="mt-4 border-t border-[var(--border)] pt-4"
        >
          <label className="block text-xs font-medium text-slate-400">
            Or enter token manually
          </label>
          <div className="mt-1.5 flex gap-2">
            <input
              value={manual}
              onChange={(e) => setManual(e.target.value)}
              placeholder="Token printed below the QR"
              className="flex-1 rounded-lg border border-[var(--border)] bg-[var(--bg)] px-3 py-2
                         font-mono text-sm text-slate-100 outline-none transition
                         placeholder:font-sans placeholder:text-slate-700 focus:border-violet-500"
            />
            <button
              type="submit"
              disabled={busy || !manual.trim()}
              className="rounded-lg bg-violet-600 px-4 text-sm font-medium transition
                         hover:bg-violet-500 disabled:opacity-40"
            >
              Check
            </button>
          </div>
        </form>
      </section>
    </div>
  )
}

/** Gate staff need to read this in 2 seconds — keep it large and color-coded. */
function ResultCard({ result, onDismiss }) {
  const tone = result.ok
    ? { bg: 'bg-emerald-500/15 ring-emerald-500/30', text: 'text-emerald-300', icon: '✓' }
    : result.reason === 'already_checked_in'
      ? { bg: 'bg-amber-500/15 ring-amber-500/30', text: 'text-amber-300', icon: '!' }
      : { bg: 'bg-rose-500/15 ring-rose-500/30', text: 'text-rose-300', icon: '✕' }

  const headline = {
    checked_in: 'Admit',
    already_checked_in: 'Already used',
    invalid_ticket: 'Invalid ticket',
    booking_cancelled: 'Booking cancelled',
    ticket_not_issued: 'Ticket not issued',
    error: 'Something went wrong',
  }[result.reason] ?? result.reason

  return (
    <section className={`animate-pop-in rounded-2xl p-5 ring-1 ${tone.bg}`}>
      <div className="flex items-start gap-4">
        <span
          className={`flex h-12 w-12 shrink-0 items-center justify-center rounded-full
                      text-2xl font-bold ${tone.bg} ${tone.text} ring-1 ring-inherit`}
        >
          {tone.icon}
        </span>

        <div className="min-w-0 flex-1">
          <p className={`text-lg font-bold ${tone.text}`}>{headline}</p>

          {result.seat_label && (
            <p className="mt-1 text-2xl font-bold text-white">{result.seat_label}</p>
          )}

          {result.attendee_name && (
            <p className="mt-0.5 text-sm text-slate-300">{result.attendee_name}</p>
          )}
          {result.booking_ref && (
            <p className="font-mono text-xs text-slate-500">{result.booking_ref}</p>
          )}

          {/* For duplicates, show "when and by whom" — common questions at the gate */}
          {result.already_checked_in && result.checked_in_at && (
            <p className="mt-2 rounded-lg bg-black/20 px-3 py-2 text-xs text-amber-200">
              Checked in at {new Date(result.checked_in_at).toLocaleTimeString()}
              {result.scanned_by && ` — scanned by ${result.scanned_by}`}
            </p>
          )}

          {result.error && <p className="mt-2 text-sm text-rose-300">{result.error}</p>}
        </div>

        <button onClick={onDismiss} className="shrink-0 text-slate-500 hover:text-slate-200">
          ✕
        </button>
      </div>
    </section>
  )
}
