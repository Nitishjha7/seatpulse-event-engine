import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'

import { useAuth } from '../auth/AuthContext'
import { useBooking } from '../booking/BookingContext'
import { IconChevron, IconLogout, IconMenu, IconUser } from './icons'

export default function Topbar({ onMenu }) {
  const { user, logout } = useAuth()
  const { health, wsStatus } = useBooking()

  return (
    <header
      className="sticky top-0 z-20 flex items-center gap-3 border-b border-[var(--border)]
                 bg-[var(--bg)]/85 px-4 py-3 backdrop-blur lg:px-6"
    >
      <button
        onClick={onMenu}
        className="text-slate-400 transition hover:text-slate-100 lg:hidden"
        aria-label="Open menu"
      >
        <IconMenu />
      </button>

      <div className="ml-auto flex items-center gap-2 sm:gap-3">
        {/* Live health pills. sm se neeche sirf dots dikhte hain — space bachta hai */}
        {health && (
          <div className="flex items-center gap-1.5 sm:gap-2">
            <Pill ok={health.database === 'connected'} label="DB" />
            <Pill ok={health.redis === 'connected'} label="Redis" />
            <Pill
              ok={wsStatus === 'open'}
              pending={wsStatus === 'connecting'}
              label={wsStatus === 'open' ? 'Live' : 'Offline'}
              pulse={wsStatus === 'open'}
            />
          </div>
        )}

        <UserMenu user={user} onLogout={logout} />
      </div>
    </header>
  )
}

function Pill({ ok, pending, label, pulse }) {
  const dot = pending ? 'bg-amber-400' : ok ? 'bg-emerald-400' : 'bg-rose-500'

  return (
    <span
      className="flex items-center gap-1.5 rounded-full border border-[var(--border)]
                 bg-[var(--panel)] px-2 py-1 sm:px-2.5 sm:py-1.5"
      title={label}
    >
      <span className={`h-2 w-2 rounded-full ${dot} ${pulse ? 'animate-pulse' : ''}`} />
      <span className="hidden text-xs text-slate-400 sm:inline">{label}</span>
    </span>
  )
}

function UserMenu({ user, onLogout }) {
  const [open, setOpen] = useState(false)
  const ref = useRef(null)

  // Bahar click karo to menu band — har dropdown me ye chahiye hota hai
  useEffect(() => {
    if (!open) return
    const handler = (e) => {
      if (ref.current && !ref.current.contains(e.target)) setOpen(false)
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [open])

  if (!user) return null

  const name = user.full_name || user.email

  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-2 rounded-full border border-[var(--border)]
                   bg-[var(--panel)] py-1 pl-1 pr-2 transition hover:bg-white/5"
      >
        <Avatar user={user} />
        <span className="hidden max-w-[9rem] truncate text-sm text-slate-300 sm:inline">
          {name}
        </span>
        <span className={`text-slate-500 transition ${open ? 'rotate-90' : ''}`}>
          <IconChevron width={14} height={14} />
        </span>
      </button>

      {open && (
        <div
          className="absolute right-0 mt-2 w-56 overflow-hidden rounded-xl border
                     border-[var(--border)] bg-[var(--panel)] shadow-xl shadow-black/40"
        >
          <div className="border-b border-[var(--border)] px-4 py-3">
            <p className="truncate text-sm font-medium text-slate-200">{name}</p>
            <p className="truncate text-xs text-slate-500">{user.email}</p>
            {user.is_google_user && (
              <span className="mt-1.5 inline-block rounded bg-white/5 px-1.5 py-0.5 text-[10px] text-slate-400">
                Google account
              </span>
            )}
          </div>

          <Link
            to="/profile"
            onClick={() => setOpen(false)}
            className="flex items-center gap-2.5 px-4 py-2.5 text-sm text-slate-300 hover:bg-white/5"
          >
            <IconUser width={16} height={16} />
            Profile
          </Link>

          <button
            onClick={onLogout}
            className="flex w-full items-center gap-2.5 px-4 py-2.5 text-left text-sm
                       text-rose-300 hover:bg-rose-500/10"
          >
            <IconLogout width={16} height={16} />
            Logout
          </button>
        </div>
      )}
    </div>
  )
}

export function Avatar({ user, size = 'h-7 w-7' }) {
  if (user.avatar_url) {
    return (
      <img
        src={user.avatar_url}
        alt=""
        className={`${size} rounded-full object-cover`}
        // Google avatars referrer header ke saath 403 dete hain
        referrerPolicy="no-referrer"
      />
    )
  }

  return (
    <span
      className={`${size} flex items-center justify-center rounded-full
                  bg-gradient-to-br from-violet-500 to-indigo-600 text-xs
                  font-semibold uppercase text-white`}
    >
      {(user.full_name || user.email)[0]}
    </span>
  )
}
