import { useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'

import { getPayment, simulatePayment } from '../api'
import { IconLock } from '../layout/icons'

/**
 * Mock gateway ka checkout page.
 *
 * ⚠️ Ye ASLI Stripe page ki jagah hai — sirf tab dikhta hai jab
 * STRIPE_SECRET_KEY set nahi hai. Isse koi bhi (interviewer bhi) poora
 * payment flow chala sakta hai bina Stripe account ke.
 *
 * Ye page kuch decide NAHI karta — sirf backend ka `simulate` endpoint
 * call karta hai, jo wahi `_fulfil`/`_fail` chalata hai jo asli webhook
 * chalata hai. Isliye mock aur real ka logic bilkul same rehta hai.
 */
export default function MockCheckout() {
  const { paymentId } = useParams()
  const navigate = useNavigate()

  const [payment, setPayment] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [secondsLeft, setSecondsLeft] = useState(0)

  useEffect(() => {
    getPayment(paymentId)
      .then((p) => {
        setPayment(p)
        setSecondsLeft(
          Math.max(0, Math.floor((new Date(p.expires_at) - Date.now()) / 1000)),
        )
      })
      .catch((err) => setError(err.message))
  }, [paymentId])

  // Countdown — payment window bhi TTL wala hai, seat hold ki tarah
  useEffect(() => {
    if (secondsLeft <= 0) return
    const id = setInterval(() => setSecondsLeft((s) => Math.max(0, s - 1)), 1000)
    return () => clearInterval(id)
  }, [secondsLeft])

  async function decide(outcome) {
    setBusy(true)
    setError(null)
    try {
      await simulatePayment(paymentId, outcome)
      navigate(`/payment/return?payment_id=${paymentId}`)
    } catch (err) {
      setError(err.message)
      setBusy(false)
    }
  }

  if (error && !payment) {
    return <Centered><p className="text-rose-300">{error}</p></Centered>
  }

  if (!payment) {
    return <Centered><p className="text-slate-500">Loading…</p></Centered>
  }

  if (payment.status !== 'pending') {
    return (
      <Centered>
        <p className="text-slate-300">Ye payment already {payment.status} hai.</p>
        <button
          onClick={() => navigate(`/payment/return?payment_id=${paymentId}`)}
          className="mt-4 rounded-xl bg-violet-600 px-4 py-2 text-sm font-medium"
        >
          Status dekho
        </button>
      </Centered>
    )
  }

  const mins = Math.floor(secondsLeft / 60)
  const secs = secondsLeft % 60

  return (
    <Centered>
      <div className="w-full max-w-sm">
        {/* Ye banner har waqt dikhna chahiye — koi galatfehmi na ho ki
            ye asli payment page hai */}
        <div className="mb-4 rounded-xl border border-amber-500/25 bg-amber-500/10 px-4 py-3 text-center">
          <p className="text-sm font-semibold text-amber-300">🧪 Simulated Checkout</p>
          <p className="mt-1 text-xs text-amber-200/70">
            Stripe keys configure nahi hain, isliye ye mock gateway chal raha
            hai. Koi asli paisa nahi katega.
          </p>
        </div>

        <div className="animate-pop-in rounded-2xl border border-[var(--border)] bg-[var(--panel)] p-6">
          <div className="flex items-center justify-between">
            <span className="flex items-center gap-2 text-sm text-slate-400">
              <IconLock width={15} height={15} />
              Secure checkout
            </span>
            <span className="rounded-lg bg-[var(--panel-2)] px-2 py-1 font-mono text-xs text-amber-300">
              {mins}:{String(secs).padStart(2, '0')}
            </span>
          </div>

          <p className="mt-6 text-center text-4xl font-bold text-white">
            ₹{payment.amount}
          </p>
          <p className="mt-1 text-center text-sm text-slate-500">
            Payment #{payment.id} · seat #{payment.seat_id}
          </p>

          {error && (
            <p className="mt-4 rounded-lg bg-rose-500/10 px-3 py-2 text-sm text-rose-300">
              {error}
            </p>
          )}

          <button
            onClick={() => decide('success')}
            disabled={busy || secondsLeft === 0}
            className="mt-6 w-full rounded-xl bg-emerald-600 px-4 py-3 text-sm font-semibold
                       transition hover:bg-emerald-500 disabled:cursor-not-allowed disabled:opacity-40"
          >
            {busy ? 'Processing…' : `Pay ₹${payment.amount}`}
          </button>

          <button
            onClick={() => decide('fail')}
            disabled={busy || secondsLeft === 0}
            className="mt-2 w-full rounded-xl border border-[var(--border)] px-4 py-3 text-sm
                       text-slate-400 transition hover:bg-white/5 disabled:opacity-40"
          >
            Simulate failure
          </button>

          {secondsLeft === 0 && (
            <p className="mt-3 text-center text-xs text-rose-300">
              Payment window khatam — seat wapas available ho gayi
            </p>
          )}
        </div>
      </div>
    </Centered>
  )
}

function Centered({ children }) {
  return (
    <div className="flex min-h-screen items-center justify-center p-6 text-slate-100">
      <div className="text-center">{children}</div>
    </div>
  )
}
