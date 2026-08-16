import { useEffect, useState } from 'react'

import { getAdminStats } from '../../api'

export default function AdminStats() {
  const [stats, setStats] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    let cancelled = false

    async function load() {
      try {
        const data = await getAdminStats()
        if (!cancelled) setStats(data)
      } catch (err) {
        if (!cancelled) setError(err.message)
      }
    }

    load()
    // Live-ish feel ke liye har 10 second refresh. WebSocket bhi laga sakte
    // the, par ye admin dashboard hai — 10 second ki taazgi kaafi hai, aur
    // ek aur socket kholne ka koi faayda nahi.
    const id = setInterval(load, 10_000)

    return () => {
      cancelled = true
      clearInterval(id)
    }
  }, [])

  if (error) {
    return (
      <div className="animate-rise rounded-2xl border border-rose-900/50 bg-rose-950/25 p-6 text-center">
        <p className="text-rose-200">{error}</p>
      </div>
    )
  }

  if (!stats) {
    return <div className="h-64 animate-pulse rounded-2xl bg-[var(--panel)]" />
  }

  return (
    <div className="animate-rise space-y-5">
      <header>
        <h1 className="text-xl font-semibold text-slate-100">Platform Stats</h1>
        <p className="mt-1 text-sm text-slate-500">Har 10 second refresh hota hai</p>
      </header>

      <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Stat value={stats.users} label="Users" hint={`${stats.organizers} organizers`} />
        <Stat value={stats.events} label="Events" hint={`${stats.seats} seats`} />
        <Stat
          value={stats.bookings_confirmed}
          label="Confirmed bookings"
          hint={`${stats.bookings_cancelled} cancelled`}
          className="text-emerald-400"
        />
        <Stat
          value={`₹${stats.revenue.toLocaleString('en-IN')}`}
          label="Revenue"
          className="text-violet-300"
        />
      </section>

      <section className="rounded-2xl border border-[var(--border)] bg-[var(--panel)] p-5">
        <h2 className="text-sm font-medium text-slate-300">Live right now</h2>

        <div className="mt-4 grid gap-3 sm:grid-cols-2">
          <Live
            value={stats.active_locks}
            label="Seats on hold"
            hint="Redis me abhi kitne locks hain"
            className="text-amber-400"
          />
          <Live
            value={stats.live_connections}
            label="WebSocket clients"
            hint="⚠️ Sirf IS worker ka count"
            className="text-emerald-400"
          />
        </div>

        <p className="mt-4 text-xs leading-relaxed text-slate-600">
          Data teen jagah se aata hai — Postgres (users, events, bookings),
          Redis (active locks), aur is worker ki memory (WebSocket clients).
          Multi-worker deployment me connection count bhi Redis me rakhna
          padega; abhi wo zaroorat nahi hai.
        </p>
      </section>
    </div>
  )
}

function Stat({ value, label, hint, className = 'text-slate-100' }) {
  return (
    <div className="rounded-2xl border border-[var(--border)] bg-[var(--panel)] p-4">
      <p className={`text-2xl font-bold tabular-nums ${className}`}>{value}</p>
      <p className="mt-0.5 text-xs text-slate-400">{label}</p>
      {hint && <p className="mt-1 text-[11px] text-slate-600">{hint}</p>}
    </div>
  )
}

function Live({ value, label, hint, className }) {
  return (
    <div className="rounded-xl bg-[var(--panel-2)] p-4">
      <p className={`flex items-center gap-2 text-2xl font-bold tabular-nums ${className}`}>
        <span className={`h-2 w-2 rounded-full ${value > 0 ? 'animate-pulse bg-current' : 'bg-slate-700'}`} />
        {value}
      </p>
      <p className="mt-0.5 text-xs text-slate-400">{label}</p>
      <p className="mt-1 text-[11px] text-slate-600">{hint}</p>
    </div>
  )
}
