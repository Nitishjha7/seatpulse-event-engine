import { IconCalendar, IconPin, IconUsers } from '../layout/icons'

/**
 * Event ka banner.
 *
 * Koi photo use nahi ki — external image CSP/offline me toot jati hai aur
 * repo bhaari karti hai. Ye poora CSS gradient + inline SVG hai: stage
 * lights ka effect, self-contained.
 */
export default function EventHero({ event, totalSeats }) {
  if (!event) return null

  const starts = new Date(event.starts_at)

  return (
    <section className="relative overflow-hidden rounded-2xl border border-[var(--border)]">
      {/* Stage lights */}
      <div className="absolute inset-0 bg-gradient-to-br from-violet-900 via-indigo-950 to-slate-950" />
      <svg
        className="absolute inset-0 h-full w-full opacity-60"
        preserveAspectRatio="none"
        aria-hidden="true"
      >
        <defs>
          <linearGradient id="beam" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#a78bfa" stopOpacity="0.55" />
            <stop offset="100%" stopColor="#a78bfa" stopOpacity="0" />
          </linearGradient>
          <linearGradient id="beam2" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#60a5fa" stopOpacity="0.4" />
            <stop offset="100%" stopColor="#60a5fa" stopOpacity="0" />
          </linearGradient>
        </defs>
        <polygon points="60,0 130,0 230,260 0,260" fill="url(#beam)" />
        <polygon points="220,0 280,0 300,260 130,260" fill="url(#beam2)" />
        <polygon points="620,0 690,0 780,260 540,260" fill="url(#beam)" />
      </svg>
      {/* Crowd silhouette */}
      <div
        className="absolute inset-x-0 bottom-0 h-16 opacity-40"
        style={{
          backgroundImage:
            'radial-gradient(closest-side, #000 88%, transparent 90%), radial-gradient(closest-side, #000 88%, transparent 90%)',
          backgroundSize: '38px 38px, 26px 26px',
          backgroundPosition: '0 22px, 19px 30px',
          backgroundRepeat: 'repeat-x',
        }}
      />

      <div className="relative px-5 py-6 sm:px-7 sm:py-8">
        <span
          className="inline-flex items-center gap-1.5 rounded-full bg-violet-500/20 px-2.5 py-1
                     text-[11px] font-medium uppercase tracking-wide text-violet-200
                     ring-1 ring-violet-400/30"
        >
          ⚡ Live concert
        </span>

        <h1 className="mt-3 text-2xl font-bold tracking-tight text-white sm:text-3xl">
          {event.name}
        </h1>

        <div className="mt-3 space-y-1.5 text-sm text-slate-300">
          <p className="flex items-center gap-2">
            <IconPin width={15} height={15} className="text-slate-400" />
            {event.venue}
          </p>
          <p className="flex items-center gap-2">
            <IconCalendar width={15} height={15} className="text-slate-400" />
            {starts.toLocaleDateString(undefined, {
              weekday: 'short',
              day: 'numeric',
              month: 'short',
              year: 'numeric',
            })}
            {' · '}
            {starts.toLocaleTimeString(undefined, {
              hour: '2-digit',
              minute: '2-digit',
            })}
          </p>
        </div>

        <div className="mt-4 flex flex-wrap gap-2">
          <Chip>
            <IconUsers width={14} height={14} />
            {totalSeats ?? event.total_seats} seats
          </Chip>
          <Chip>🎵 Music</Chip>
        </div>
      </div>
    </section>
  )
}

function Chip({ children }) {
  return (
    <span
      className="flex items-center gap-1.5 rounded-lg border border-white/10 bg-black/30
                 px-2.5 py-1.5 text-xs text-slate-300 backdrop-blur"
    >
      {children}
    </span>
  )
}
