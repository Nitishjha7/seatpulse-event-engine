import { useEffect, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'

import { getPayment } from '../api'
import Confetti from '../components/Confetti'
import { bookingRef } from '../components/BookingConfirmedModal'

/**
 * This page opens upon returning from the payment gateway.
 *
 * ⚠️ IMPORTANT: This page does not make any decisions.
 *
 * Payment success is determined by the backend webhook. This page only queries
 * the backend for the status and displays the result.
 *
 * Relying on this redirect to create a booking would allow users to bypass
 * payment by accessing this URL directly.
 *
 * Conversely, if a user closes the tab after paying, this page never loads,
 * but the webhook will still process the booking. Thus, the redirect is
 * UI only, not the source of truth.
 */
export default function PaymentReturn() {
  const [params] = useSearchParams()
  const paymentId = params.get('payment_id')
  const cancelled = params.get('cancelled')

  const [payment, setPayment] = useState(null)
  const [error, setError] = useState(null)
  const [attempts, setAttempts] = useState(0)

  const timer = useRef(null)

  useEffect(() => {
    if (!paymentId) {
      setError('payment_id missing')
      return
    }

    let cancelledEffect = false

    async function poll(n) {
      try {
        const p = await getPayment(paymentId)
        if (cancelledEffect) return

        setPayment(p)
        setAttempts(n)

        // Terminal state? Stop.
        if (p.status !== 'pending') return

        // ⚠️ Webhooks can take time — the redirect often arrives before the
        // webhook. We poll rather than assuming failure after one check.
        //
        // 20 attempts, 1.5s apart = ~30 seconds. After that, we advise the
        // user to check their bookings, as the webhook will eventually
        // process the booking.
        if (n < 20) {
          timer.current = setTimeout(() => poll(n + 1), 1500)
        }
      } catch (err) {
        if (!cancelledEffect) setError(err.message)
      }
    }

    poll(0)

    return () => {
      cancelledEffect = true
      clearTimeout(timer.current)
    }
  }, [paymentId])

  if (error) return <Shell><p className="text-rose-300">{error}</p></Shell>
  if (!payment) return <Shell><p className="text-slate-500">Checking payment…</p></Shell>

  // ---- Success ----
  if (payment.status === 'succeeded') {
    return (
      <Shell>
        <div className="relative">
          <Confetti />
          <Tick ok />
          <h1 className="mt-4 text-2xl font-bold text-white">Payment successful</h1>
          <p className="mt-1 text-sm text-slate-400">Your seat has been booked.</p>

          <dl className="mt-5 grid grid-cols-2 gap-2 text-left">
            <Field label="Amount" value={`₹${payment.amount}`} />
            <Field label="Booking ID" value={payment.booking_id ? bookingRef(payment.booking_id) : '—'} mono />
          </dl>

          <Link to="/bookings" className={primaryBtn}>View My Bookings</Link>
          <Link to="/" className={secondaryBtn}>Back to seat map</Link>
        </div>
      </Shell>
    )
  }

  // ---- Failed / expired ----
  if (payment.status === 'failed' || payment.status === 'expired') {
    return (
      <Shell>
        <Tick ok={false} />
        <h1 className="mt-4 text-2xl font-bold text-white">
          {cancelled ? 'Payment cancelled' : 'Payment failed'}
        </h1>
        <p className="mt-1 text-sm text-slate-400">
          {payment.failure_reason === 'expired_unpaid' || payment.status === 'expired'
            ? 'Payment window expired — seat is now available again.'
            : 'No funds were deducted. Seat is now available again.'}
        </p>
        <Link to="/" className={primaryBtn}>Try again</Link>
      </Shell>
    )
  }

  // ---- Still pending ----
  return (
    <Shell>
      <div className="mx-auto h-14 w-14 animate-pulse rounded-full bg-amber-500/20" />
      <h1 className="mt-4 text-xl font-semibold text-white">Confirming payment…</h1>
      <p className="mt-1 text-sm text-slate-400">
        Waiting for gateway confirmation. This page will update automatically.
      </p>

      {attempts >= 20 && (
        <p className="mt-4 rounded-xl bg-amber-500/10 px-4 py-3 text-xs leading-relaxed text-amber-200">
          This is taking longer than expected. Don't worry — the booking will be
          created automatically once confirmed, even if you close this page.
          <br />
          <Link to="/bookings" className="mt-2 inline-block underline">
            Check My Bookings
          </Link>
        </p>
      )}
    </Shell>
  )
}

const primaryBtn =
  'mt-6 block rounded-xl bg-violet-600 px-4 py-2.5 text-sm font-semibold transition hover:bg-violet-500'
const secondaryBtn =
  'mt-2 block rounded-xl border border-[var(--border)] px-4 py-2.5 text-sm text-slate-400 transition hover:bg-white/5'

function Shell({ children }) {
  return (
    <div className="flex min-h-screen items-center justify-center p-6 text-slate-100">
      <div className="w-full max-w-sm rounded-2xl border border-[var(--border)] bg-[var(--panel)] p-6 text-center">
        {children}
      </div>
    </div>
  )
}

function Tick({ ok }) {
  return (
    <div className="relative mx-auto flex h-14 w-14 items-center justify-center">
      <span className={`absolute inset-0 rounded-full blur-md ${ok ? 'bg-emerald-500/20' : 'bg-rose-500/20'}`} />
      <span className={`relative flex h-14 w-14 items-center justify-center rounded-full ${ok ? 'bg-emerald-500' : 'bg-rose-500'}`}>
        <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="3" strokeLinecap="round">
          {ok ? <path d="m5 13 4 4L19 7" /> : <path d="M6 6l12 12M18 6L6 18" />}
        </svg>
      </span>
    </div>
  )
}

function Field({ label, value, mono }) {
  return (
    <div className="rounded-lg bg-[var(--panel-2)] px-3 py-2">
      <dt className="text-[10px] uppercase tracking-wide text-slate-500">{label}</dt>
      <dd className={`mt-0.5 text-sm font-semibold text-slate-100 ${mono ? 'font-mono text-xs' : ''}`}>
        {value}
      </dd>
    </div>
  )
}
