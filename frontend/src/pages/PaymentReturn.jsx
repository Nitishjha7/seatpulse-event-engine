import { useEffect, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'

import { getPayment } from '../api'
import Confetti from '../components/Confetti'
import { bookingRef } from '../components/BookingConfirmedModal'

/**
 * Gateway se wapas aane par ye page khulta hai.
 *
 * ⚠️ SABSE ZAROORI BAAT: ye page kuch DECIDE nahi karta.
 *
 * Payment succeed hua ya nahi, wo backend webhook se tay hota hai. Ye page
 * sirf backend se POOCHTA hai ("payment kya hua?") aur jawab dikhata hai.
 *
 * Agar hum is redirect par bharosa karke booking bana dete, to koi bhi
 * seedha ye URL kholke bina paise ke ticket le leta.
 *
 * Aur ulta bhi: user pay karke tab band kar de to ye page kabhi khulta hi
 * nahi — par webhook phir bhi aayega aur booking ban jayegi. Isliye redirect
 * sirf UI hai, source of truth nahi.
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

        // Terminal state? Ruk jao.
        if (p.status !== 'pending') return

        // ⚠️ Webhook ko pahunchne me thoda time lagta hai — gateway se
        // redirect aksar webhook se PEHLE aa jata hai. Isliye poll karte
        // hain, ek baar poochh ke "failed" nahi bol dete.
        //
        // 20 attempts, 1.5s apart = ~30 second. Uske baad user ko bolte
        // hain ki bookings me check kar le — kyunki webhook baad me bhi
        // aayega aur booking khud ban jayegi.
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
          <p className="mt-1 text-sm text-slate-400">Tumhari seat book ho gayi.</p>

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
          {cancelled ? 'Payment cancelled' : 'Payment nahi hua'}
        </h1>
        <p className="mt-1 text-sm text-slate-400">
          {payment.failure_reason === 'expired_unpaid' || payment.status === 'expired'
            ? 'Payment window khatam ho gaya — seat wapas available hai.'
            : 'Koi paisa nahi kata. Seat wapas available hai.'}
        </p>
        <Link to="/" className={primaryBtn}>Dobara try karo</Link>
      </Shell>
    )
  }

  // ---- Still pending ----
  return (
    <Shell>
      <div className="mx-auto h-14 w-14 animate-pulse rounded-full bg-amber-500/20" />
      <h1 className="mt-4 text-xl font-semibold text-white">Payment confirm ho raha hai…</h1>
      <p className="mt-1 text-sm text-slate-400">
        Gateway se confirmation ka intezaar hai. Ye page apne aap update hoga.
      </p>

      {attempts >= 20 && (
        <p className="mt-4 rounded-xl bg-amber-500/10 px-4 py-3 text-xs leading-relaxed text-amber-200">
          Zyada time lag raha hai. Ghabrao mat — confirmation aane par booking
          apne aap ban jayegi, chahe tum ye page band kar do.
          <br />
          <Link to="/bookings" className="mt-2 inline-block underline">
            My Bookings me check karo
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
