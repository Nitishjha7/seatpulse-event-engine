import { NavLink } from 'react-router-dom'

import { API_URL } from '../api'
import {
  IconCalendar,
  IconClose,
  IconCode,
  IconHome,
  IconTicket,
  IconUser,
} from './icons'

/**
 * Left navigation.
 *
 * Sirf wo pages hain jo ACTUALLY bane hue hain. Jo abhi nahi bane
 * (Reports, Settings) wo `soon: true` ke saath disabled dikhte hain —
 * taki shell ready dikhe par koi tootа hua link na ho.
 */
const NAV = [
  { to: '/', label: 'Dashboard', Icon: IconHome, end: true },
  { to: '/events', label: 'Events', Icon: IconCalendar },
  { to: '/bookings', label: 'My Bookings', Icon: IconTicket },
  { to: '/profile', label: 'Profile', Icon: IconUser },
]

const SOON = [
  { label: 'Reports', Icon: IconCalendar },
  { label: 'Settings', Icon: IconUser },
]

export default function Sidebar({ open, onClose }) {
  return (
    <>
      {/* Mobile pe sidebar khulne par background dim */}
      {open && (
        <div
          onClick={onClose}
          className="fixed inset-0 z-30 bg-black/60 backdrop-blur-sm lg:hidden"
        />
      )}

      <aside
        className={`fixed inset-y-0 left-0 z-40 flex w-64 flex-col border-r
                    border-[var(--border)] bg-[var(--panel)] transition-transform
                    lg:translate-x-0 ${open ? 'translate-x-0' : '-translate-x-full'}`}
      >
        <div className="flex items-center gap-2.5 px-5 py-5">
          <Logo />
          <div className="min-w-0">
            <p className="text-[15px] font-semibold leading-tight text-slate-100">
              SeatPulse
            </p>
            <p className="truncate text-[10px] leading-tight text-slate-500">
              High-Concurrency Booking Engine
            </p>
          </div>

          <button
            onClick={onClose}
            className="ml-auto text-slate-500 hover:text-slate-200 lg:hidden"
            aria-label="Close menu"
          >
            <IconClose />
          </button>
        </div>

        <nav className="flex-1 space-y-1 overflow-y-auto px-3">
          {NAV.map(({ to, label, Icon, end }) => (
            <NavLink
              key={to}
              to={to}
              end={end}
              onClick={onClose}
              className={({ isActive }) =>
                `flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm transition ${
                  isActive
                    ? 'bg-violet-600/15 font-medium text-violet-200 ring-1 ring-violet-500/30'
                    : 'text-slate-400 hover:bg-white/5 hover:text-slate-200'
                }`
              }
            >
              <Icon />
              {label}
            </NavLink>
          ))}

          <a
            href={`${API_URL}/docs`}
            target="_blank"
            rel="noreferrer"
            className="flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm
                       text-slate-400 transition hover:bg-white/5 hover:text-slate-200"
          >
            <IconCode />
            API Docs
          </a>

          <p className="px-3 pb-1 pt-5 text-[10px] font-medium uppercase tracking-wider text-slate-600">
            Coming soon
          </p>
          {SOON.map(({ label, Icon }) => (
            <span
              key={label}
              className="flex cursor-not-allowed items-center gap-3 rounded-lg px-3 py-2.5
                         text-sm text-slate-700"
            >
              <Icon />
              {label}
            </span>
          ))}
        </nav>

        {/* Neeche ka card — is project ka asli USP, marketing fluff nahi */}
        <div className="m-3 rounded-xl border border-violet-500/20 bg-violet-600/10 p-4">
          <p className="text-sm font-semibold text-slate-100">Zero overselling</p>
          <p className="mt-1 text-xs leading-relaxed text-slate-400">
            200 concurrent users on one seat → exactly 1 booking. Verified with
            Locust.
          </p>
          <div className="mt-3 flex gap-1.5">
            <Bar className="bg-emerald-400" />
            <Bar className="bg-amber-400" />
            <Bar className="bg-rose-400" />
          </div>
        </div>
      </aside>
    </>
  )
}

function Bar({ className }) {
  return <span className={`h-1 flex-1 rounded-full ${className}`} />
}

function Logo() {
  return (
    <span
      className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg
                 bg-gradient-to-br from-violet-500 to-indigo-600 text-base"
    >
      🎟️
    </span>
  )
}
