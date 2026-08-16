import { useAuth } from '../auth/AuthContext'
import { useBooking } from '../booking/BookingContext'
import { Avatar } from '../layout/Topbar'

export default function Profile() {
  const { user, logout } = useAuth()
  const { health, wsStatus, bookings } = useBooking()

  const confirmed = bookings.filter((b) => b.status === 'confirmed').length

  return (
    <div className="animate-rise max-w-2xl space-y-5">
      <header>
        <h1 className="text-xl font-semibold text-slate-100">Profile</h1>
        <p className="mt-1 text-sm text-slate-500">Account aur session details</p>
      </header>

      <section className="rounded-2xl border border-[var(--border)] bg-[var(--panel)] p-5">
        <div className="flex items-center gap-4">
          <Avatar user={user} size="h-14 w-14 text-lg" />
          <div className="min-w-0">
            <p className="truncate text-lg font-semibold text-slate-100">
              {user.full_name || 'No name set'}
            </p>
            <p className="truncate text-sm text-slate-400">{user.email}</p>
            <div className="mt-1.5 flex flex-wrap gap-1.5">
              <span className="rounded bg-white/5 px-2 py-0.5 text-[11px] text-slate-400">
                {user.is_google_user ? 'Google account' : 'Email + password'}
              </span>
              <RoleBadge role={user.role} />
            </div>
          </div>
        </div>

        <dl className="mt-5 grid gap-3 border-t border-[var(--border)] pt-4 sm:grid-cols-2">
          <Field label="User ID" value={`#${user.id}`} />
          <Field label="Confirmed bookings" value={confirmed} />
        </dl>
      </section>

      <section className="rounded-2xl border border-[var(--border)] bg-[var(--panel)] p-5">
        <h2 className="text-sm font-medium text-slate-300">Session</h2>
        <p className="mt-1 text-xs leading-relaxed text-slate-500">
          Access token sirf memory me hai — localStorage me nahi, taki XSS use
          padh na sake. Refresh token ek httpOnly cookie me hai aur har refresh
          par rotate hota hai.
        </p>

        <button
          onClick={logout}
          className="mt-4 rounded-xl border border-rose-500/30 bg-rose-500/10 px-4 py-2
                     text-sm text-rose-300 transition hover:bg-rose-500/20"
        >
          Logout
        </button>
      </section>

      <section className="rounded-2xl border border-[var(--border)] bg-[var(--panel)] p-5">
        <h2 className="text-sm font-medium text-slate-300">System</h2>
        <dl className="mt-3 grid gap-3 sm:grid-cols-2">
          <Field label="API version" value={health?.version ?? '—'} />
          <Field label="Database" value={health?.database ?? '—'} />
          <Field label="Redis" value={health?.redis ?? '—'} />
          <Field label="WebSocket" value={wsStatus} />
        </dl>
      </section>
    </div>
  )
}

/** Role ka rang — admin sabse alag dikhna chahiye */
function RoleBadge({ role }) {
  const style =
    {
      admin: 'bg-rose-500/15 text-rose-300 ring-rose-500/25',
      organizer: 'bg-violet-500/15 text-violet-300 ring-violet-500/25',
    }[role] || 'bg-white/5 text-slate-400 ring-white/10'

  return (
    <span className={`rounded px-2 py-0.5 text-[11px] capitalize ring-1 ${style}`}>
      {role}
    </span>
  )
}

function Field({ label, value }) {
  return (
    <div className="rounded-xl bg-[var(--panel-2)] px-3 py-2.5">
      <dt className="text-[10px] uppercase tracking-wide text-slate-500">{label}</dt>
      <dd className="mt-0.5 text-sm text-slate-200">{value}</dd>
    </div>
  )
}
