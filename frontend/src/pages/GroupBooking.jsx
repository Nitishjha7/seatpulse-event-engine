import { useCallback, useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'

import { cancelGroup, claimShare, getGroup, payShare } from '../api'
import { useAuth } from '../auth/AuthContext'

/** 1850 -> "30:50" */
function mmss(total) {
  if (total <= 0) return '0:00'
  const m = Math.floor(total / 60)
  const s = total % 60
  return `${m}:${String(s).padStart(2, '0')}`
}

/**
 * Group booking share page — displayed when the link is opened.
 *
 * This page answers one question: **who is holding up the process?**
 * Status and ownership are shown clearly at the top.
 */
export default function GroupBooking() {
  const { shareToken } = useParams()
  const { user } = useAuth()

  const [group, setGroup] = useState(null)
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(null)
  const [copied, setCopied] = useState(false)
  const [secondsLeft, setSecondsLeft] = useState(0)

  const load = useCallback(async () => {
    try {
      const data = await getGroup(shareToken)
      setGroup(data)
      setSecondsLeft(data.seconds_left)
      setError(null)
    } catch (err) {
      setError(err.message)
    }
  }, [shareToken])

  useEffect(() => {
    load()
  }, [load])

  // Others are paying in different browsers — this page should update
  // automatically when their payment arrives.
  //
  // Using polling here instead of WebSockets. The seat grid subscribes to
  // socket events; groups are separate and would require a new channel and
  // subscription lifecycle. 5-second polling is sufficient for this page —
  // people pay for groups in minutes, not milliseconds.
  useEffect(() => {
    if (!group || group.status !== 'collecting') return
    const id = setInterval(load, 5000)
    return () => clearInterval(id)
  }, [group, load])

  // The countdown is for display only. The actual expiry is handled by a
  // server cron job — nothing happens automatically when it reaches 0.
  useEffect(() => {
    if (!group || group.status !== 'collecting') return
    const id = setInterval(() => setSecondsLeft((s) => s - 1), 1000)
    return () => clearInterval(id)
  }, [group])

  async function act(fn, shareId) {
    setBusy(shareId)
    setError(null)
    try {
      await fn()
      await load()
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(null)
    }
  }

  async function handlePay(shareId) {
    setBusy(shareId)
    setError(null)
    try {
      const session = await payShare(shareToken, shareId)
      window.location.href = session.checkout_url
    } catch (err) {
      setError(err.message)
      setBusy(null)
    }
  }

  function copyLink() {
    navigator.clipboard?.writeText(window.location.href)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  if (error && !group) {
    return (
      <div className="animate-rise mx-auto max-w-lg rounded-2xl border border-[var(--border)]
                      bg-[var(--panel)] p-6 text-center">
        <p className="text-3xl">🔗</p>
        <p className="mt-2 text-sm text-rose-300">{error}</p>
      </div>
    )
  }

  if (!group) return <p className="text-sm text-slate-500">Loading…</p>

  const mine = group.shares.find((s) => s.claimed_by === user.id)
  const iAmIn = Boolean(mine)
  const total = group.shares.reduce((sum, s) => sum + s.amount, 0)

  return (
    <div className="animate-rise mx-auto max-w-2xl space-y-4">
      <header className="flex items-start justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-slate-100">Group booking</h1>
          <p className="mt-1 text-sm text-slate-500">
            {group.total_shares} seats · ₹{total} total
          </p>
        </div>

        {group.status === 'collecting' && (
          <span
            className={`rounded-lg px-3 py-1.5 font-mono text-sm tabular-nums ring-1 ${
              secondsLeft <= 120
                ? 'bg-rose-500/15 text-rose-300 ring-rose-500/30'
                : 'bg-amber-400/10 text-amber-300 ring-amber-400/20'
            }`}
          >
            {mmss(secondsLeft)}
          </span>
        )}
      </header>

      <StatusBanner group={group} />

      {group.status === 'collecting' && (
        <>
          {/* Progress — a direct answer to "who are we still waiting on" */}
          <div className="rounded-2xl border border-[var(--border)] bg-[var(--panel)] p-4">
            <div className="flex items-baseline justify-between text-sm">
              <span className="text-slate-300">
                {group.paid_shares} / {group.total_shares} paid
              </span>
              <span className="text-xs text-slate-500">
                Seats are confirmed only after everyone pays
              </span>
            </div>
            <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-white/5">
              <div
                className="h-full rounded-full bg-emerald-500 transition-[width] duration-500"
                style={{ width: `${(group.paid_shares / group.total_shares) * 100}%` }}
              />
            </div>
          </div>

          <button
            onClick={copyLink}
            className="w-full rounded-xl border border-[var(--border)] px-4 py-2.5 text-sm
                       text-slate-300 transition hover:bg-white/5"
          >
            {copied ? '✓ Link copied' : '🔗 Copy link and send to friends'}
          </button>
        </>
      )}

      <section className="space-y-2">
        {group.shares.map((share) => (
          <ShareRow
            key={share.id}
            share={share}
            isMine={share.claimed_by === user.id}
            canClaim={group.status === 'collecting' && !share.claimed_by && !iAmIn}
            busy={busy === share.id}
            onClaim={() => act(() => claimShare(shareToken, share.id), share.id)}
            onPay={() => handlePay(share.id)}
          />
        ))}
      </section>

      {error && (
        <p className="rounded-lg bg-rose-500/10 px-3 py-2 text-sm text-rose-300">{error}</p>
      )}

      {group.status === 'collecting' && (
        <button
          onClick={() => act(() => cancelGroup(shareToken), 'cancel')}
          className="w-full rounded-xl border border-[var(--border)] px-4 py-2.5 text-xs
                     text-slate-500 transition hover:bg-white/5 hover:text-rose-300"
        >
          Cancel group (payments received will be refunded)
        </button>
      )}
    </div>
  )
}

function StatusBanner({ group }) {
  if (group.status === 'collecting') return null

  const tone = {
    confirmed: {
      bg: 'bg-emerald-500/15 ring-emerald-500/30 text-emerald-300',
      icon: '✓',
      title: 'Confirmed!',
      body: 'Everyone has paid. All seats are booked and tickets are being generated.',
    },
    expired: {
      bg: 'bg-amber-500/15 ring-amber-500/30 text-amber-300',
      icon: '⏱',
      title: 'Time expired',
      body: 'Not everyone paid by the deadline, so all seats have been released. Refunds are being processed for those who paid.',
    },
    cancelled: {
      bg: 'bg-slate-500/15 ring-slate-500/30 text-slate-300',
      icon: '✕',
      title: 'Cancelled',
      body: 'The group organizer cancelled the booking. Payments are being refunded.',
    },
  }[group.status]

  if (!tone) return null

  return (
    <section className={`animate-pop-in rounded-2xl p-5 ring-1 ${tone.bg}`}>
      <div className="flex items-start gap-3">
        <span className="text-2xl">{tone.icon}</span>
        <div>
          <p className="font-semibold">{tone.title}</p>
          <p className="mt-1 text-sm opacity-80">{tone.body}</p>
        </div>
      </div>
    </section>
  )
}

function ShareRow({ share, isMine, canClaim, busy, onClaim, onPay }) {
  const paid = share.status === 'paid'
  const refunded = share.status === 'refunded'

  return (
    <div
      className={`flex items-center gap-3 rounded-xl border p-3.5 transition ${
        isMine
          ? 'border-violet-500/40 bg-violet-500/5'
          : 'border-[var(--border)] bg-[var(--panel)]'
      }`}
    >
      <span
        className={`flex h-10 w-12 shrink-0 items-center justify-center rounded-lg
                    text-sm font-bold ${
                      paid
                        ? 'bg-emerald-500/15 text-emerald-300'
                        : refunded
                          ? 'bg-slate-500/15 text-slate-400'
                          : 'bg-white/5 text-slate-400'
                    }`}
      >
        {share.seat_label}
      </span>

      <div className="min-w-0 flex-1">
        <p className="truncate text-sm text-slate-200">
          {share.claimed_by_name ?? <span className="text-slate-600">empty seat</span>}
          {isMine && <span className="ml-1.5 text-xs text-violet-400">(you)</span>}
        </p>
        <p className="text-xs text-slate-500">
          ₹{share.amount}
          {paid && <span className="ml-1.5 text-emerald-400">· paid</span>}
          {refunded && <span className="ml-1.5 text-slate-400">· refunded</span>}
        </p>
      </div>

      {canClaim && (
        <button
          onClick={onClaim}
          disabled={busy}
          className="shrink-0 rounded-lg bg-violet-600 px-3 py-1.5 text-xs font-medium
                     transition hover:bg-violet-500 disabled:opacity-40"
        >
          Claim seat
        </button>
      )}

      {isMine && !paid && !refunded && (
        <button
          onClick={onPay}
          disabled={busy}
          className="shrink-0 rounded-lg bg-emerald-600 px-3 py-1.5 text-xs font-semibold
                     transition hover:bg-emerald-500 disabled:opacity-40"
        >
          {busy ? '…' : `Pay ₹${share.amount}`}
        </button>
      )}
    </div>
  )
}
