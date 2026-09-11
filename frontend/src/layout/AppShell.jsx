import { useState } from 'react'
import { Outlet } from 'react-router-dom'

import { useBooking } from '../booking/BookingContext'
import Sidebar from './Sidebar'
import Topbar from './Topbar'

/**
 * Main application layout containing sidebar, topbar, and content.
 *
 * Uses <Outlet /> for routing. This ensures the sidebar and topbar persist
 * across navigation, maintaining the WebSocket connection held in the
 * parent BookingProvider.
 */
export default function AppShell() {
  const [menuOpen, setMenuOpen] = useState(false)
  const { loading, fatalError } = useBooking()

  return (
    <div className="min-h-screen text-slate-100">
      <Sidebar open={menuOpen} onClose={() => setMenuOpen(false)} />

      <div className="lg:pl-64">
        <Topbar onMenu={() => setMenuOpen(true)} />

        <main className="mx-auto max-w-[1400px] px-4 py-5 lg:px-6">
          {loading ? (
            <SkeletonPage />
          ) : fatalError ? (
            <ErrorPanel message={fatalError} />
          ) : (
            <Outlet />
          )}
        </main>
      </div>
    </div>
  )
}

/** Uses skeleton screens instead of spinners to prevent layout shifts and improve perceived performance. */
function SkeletonPage() {
  return (
    <div className="grid animate-pulse gap-5 xl:grid-cols-[1fr_380px]">
      <div className="space-y-5">
        <div className="h-44 rounded-2xl bg-[var(--panel)]" />
        <div className="h-96 rounded-2xl bg-[var(--panel)]" />
      </div>
      <div className="space-y-5">
        <div className="h-52 rounded-2xl bg-[var(--panel)]" />
        <div className="h-64 rounded-2xl bg-[var(--panel)]" />
      </div>
    </div>
  )
}

function ErrorPanel({ message }) {
  return (
    <div className="mx-auto mt-10 max-w-md rounded-2xl border border-rose-900/50 bg-rose-950/25 p-6 text-center">
      <p className="text-rose-200">{message}</p>
      <p className="mt-3 text-xs text-slate-500">
        Is the backend running? <code className="text-slate-400">docker compose ps</code>
      </p>
    </div>
  )
}
