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
    // Refresh every 10s for near-real-time updates. Polling is sufficient
    // for this admin dashboard; WebSockets are unnecessary overhead.
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
        <p className="mt-1 text-sm text-slate-500">Refreshes every 10 seconds</p>
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
            hint="Current Redis lock count"
            className="text-amber-400"
          />
          <Live
            value={stats.live_connections}
            label="WebSocket clients"
            hint="⚠️ Count for this worker only"
            className="text-emerald-400"
          />
        </div>

        <p className="mt-4 text-xs leading-relaxed text-slate-600">
          Data aggregated from Postgres (users, events, bookings), Redis (active locks),
          and local worker memory (WebSocket clients). For multi-worker deployments,
          connection counts should be moved to Redis.
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
