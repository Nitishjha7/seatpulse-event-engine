import { IconActivity, IconBolt, IconLock, IconShield } from '../layout/icons'

/**
 * Feature tiles.
 *
 * ⚠️ Yahan sirf wo cheezein hain jo ACTUALLY bani hui hain. "Multiple
 * payments — UPI, Cards, Wallets" jaisi tile daalna aasan tha, par payments
 * abhi hain hi nahi — aur UI me jhootha claim interview me sabse bada red
 * flag hota hai. Sach bolne wali tiles waise bhi zyada impressive hain.
 */
const FEATURES = [
  {
    Icon: IconLock,
    title: 'Redis Seat Locking',
    body: 'Atomic SET NX EX hold for 5 minutes, auto-released on TTL',
    tone: 'text-violet-300 bg-violet-500/10',
  },
  {
    Icon: IconBolt,
    title: 'Real-Time Updates',
    body: 'WebSocket push over Redis pub/sub — no refresh, no polling',
    tone: 'text-amber-300 bg-amber-400/10',
  },
  {
    Icon: IconShield,
    title: 'Zero Overselling',
    body: '200 concurrent users on one seat → exactly 1 booking',
    tone: 'text-emerald-300 bg-emerald-500/10',
  },
  {
    Icon: IconActivity,
    title: 'Verified by Load Tests',
    body: '8,154 requests, 0 failures, integrity checked in the database',
    tone: 'text-sky-300 bg-sky-500/10',
  },
]

export default function FeatureStrip() {
  return (
    <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
      {FEATURES.map(({ Icon, title, body, tone }) => (
        <div
          key={title}
          className="rounded-2xl border border-[var(--border)] bg-[var(--panel)] p-4
                     transition hover:border-violet-500/30"
        >
          <span className={`inline-flex rounded-lg p-2 ${tone}`}>
            <Icon width={18} height={18} />
          </span>
          <p className="mt-3 text-sm font-semibold text-slate-100">{title}</p>
          <p className="mt-1 text-xs leading-relaxed text-slate-500">{body}</p>
        </div>
      ))}
    </section>
  )
}
