import { useState } from 'react'
import { Outlet } from 'react-router-dom'

import { useBooking } from '../booking/BookingContext'
import Sidebar from './Sidebar'
import Topbar from './Topbar'

/**
 * Page ka frame — sidebar + topbar + content.
 *
 * `<Outlet />` me react-router current page render karta hai. Isse sidebar
 * aur topbar page badalne par re-mount nahi hote (aur WebSocket bhi bacha
 * rehta hai, kyunki wo aur upar BookingProvider me hai).
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

/** Spinner ki jagah skeleton — layout shift nahi hota aur tez lagta hai */
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
        Backend chal raha hai? <code className="text-slate-400">docker compose ps</code>
      </p>
    </div>
  )
}
