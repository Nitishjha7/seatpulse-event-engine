import { useState } from 'react'
import { useNavigate } from 'react-router-dom'

import { createEvent } from '../../api'
import AiDraft from '../../components/AiDraft'
import LayoutBuilder, { emptyLayout, validateLayout } from '../../components/LayoutBuilder'
import { IconClose, IconTicket } from '../../layout/icons'

const ROW_LABELS = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'

export default function CreateEvent() {
  const navigate = useNavigate()

  // NOTE: `seats_per_row` yahan hai kyunki wo sirf tiers mode me chahiye.
  // Layout mode me ye bheja hi nahi jata (neeche handleSubmit dekho).
  const [form, setForm] = useState({
    name: '',
    venue: '',
    starts_at: '',
    category: 'Music',
    description: '',
    seats_per_row: 10,
  })
  // Tiers upar se neeche lagte hain — pehla tier row A se shuru
  const [tiers, setTiers] = useState([
    { rows: 2, price: 2500 },
    { rows: 3, price: 1200 },
    { rows: 5, price: 800 },
  ])
  // Dynamic pricing DEFAULT OFF. Organizer jaan-boojh ke on kare —
  // surge har event ke liye theek nahi (free meetup pe ye bhaddha lagta).
  const [surge, setSurge] = useState({ on: false, demand_factor: 0.5, max_surge: 2.0 })

  // 'tiers'  = purana simple raasta (N rows x M seats, ek price per tier)
  // 'layout' = poora naksha — sections, alag-alag row sizes, aisles
  //
  // Default 'tiers' hai jaan-boojh ke. Zyadatar events ko naksha chahiye
  // hi nahi, aur simple form 20 second me bhar jata hai. Layout builder
  // tab hai jab sach me zaroorat ho.
  const [mode, setMode] = useState('tiers')
  const [layout, setLayout] = useState(emptyLayout)

  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)

  const totalRows = tiers.reduce((sum, t) => sum + Number(t.rows || 0), 0)
  const totalSeats = totalRows * Number(form.seats_per_row || 0)

  // Backend me bhi yahi limits hain — yahan sirf user ko pehle bata rahe hain
  const tooManyRows = totalRows > 26
  const tooManySeats = totalSeats > 2000

  // Layout mode me seat count aur validity dono LayoutBuilder se aate hain,
  // tiers wale hisaab se nahi.
  const layoutError = mode === 'layout' ? validateLayout(layout) : null
  const layoutSeats =
    mode === 'layout'
      ? layout.sections.reduce(
          (sum, sec) => sum + sec.rows.reduce((n, r) => n + Number(r.seats || 0), 0),
          0,
        )
      : 0

  const seatCount = mode === 'layout' ? layoutSeats : totalSeats
  const blocked =
    mode === 'layout' ? Boolean(layoutError) : tooManyRows || tooManySeats || totalSeats === 0

  function setField(key, value) {
    setForm((f) => ({ ...f, [key]: value }))
  }

  function setTier(index, key, value) {
    setTiers((t) => t.map((tier, i) => (i === index ? { ...tier, [key]: value } : tier)))
  }

  async function handleSubmit(e) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      // Sirf chuna hua raasta bhejte hain. Dono bhejte to server ko
      // guess karna padta ki user ka matlab kya tha — aur wo guess
      // kabhi na kabhi galat hoti.
      const seatPlan =
        mode === 'layout'
          ? {
              layout: {
                sections: layout.sections.map((sec) => ({
                  name: sec.name.trim(),
                  price: Number(sec.price),
                  rows: sec.rows.map((r) => ({
                    label: r.label.trim().toUpperCase(),
                    seats: Number(r.seats),
                    aisles_after: r.aisles_after,
                  })),
                })),
              },
            }
          : {
              seats_per_row: Number(form.seats_per_row),
              price_tiers: tiers.map((t) => ({
                rows: Number(t.rows),
                price: Number(t.price),
              })),
            }

      const created = await createEvent({
        ...form,
        ...seatPlan,
        // datetime-local "2026-12-01T19:30" deta hai — backend ko ISO chahiye
        starts_at: new Date(form.starts_at).toISOString(),
        description: form.description || null,
        dynamic_pricing: surge.on,
        demand_factor: Number(surge.demand_factor),
        max_surge: Number(surge.max_surge),
      })
      navigate('/organizer/events', { state: { created: created.name } })
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="animate-rise max-w-3xl space-y-5">
      <header>
        <h1 className="text-xl font-semibold text-slate-100">Create Event</h1>
        <p className="mt-1 text-sm text-slate-500">
          {mode === 'layout'
            ? 'Sections, row sizes aur aisles — jaisa asli venue hai'
            : 'Seats price tiers se apne aap ban jaayengi'}
        </p>
      </header>

      <form onSubmit={handleSubmit} className="space-y-5">
        <AiDraft
          onDraft={(d) =>
            setForm((f) => ({
              ...f,
              name: d.name,
              description: d.description,
              category: d.category,
            }))
          }
        />

        <Card title="Event details">
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Name" required>
              <input
                required
                minLength={3}
                value={form.name}
                onChange={(e) => setField('name', e.target.value)}
                placeholder="Arijit Singh Live"
                className={inputCls}
              />
            </Field>

            <Field label="Category">
              <select
                value={form.category}
                onChange={(e) => setField('category', e.target.value)}
                className={inputCls}
              >
                {['Music', 'Comedy', 'Sports', 'Theatre', 'Conference'].map((c) => (
                  <option key={c} value={c}>{c}</option>
                ))}
              </select>
            </Field>

            <Field label="Venue" required>
              <input
                required
                minLength={3}
                value={form.venue}
                onChange={(e) => setField('venue', e.target.value)}
                placeholder="DY Patil Stadium, Mumbai"
                className={inputCls}
              />
            </Field>

            <Field label="Starts at" required>
              <input
                required
                type="datetime-local"
                value={form.starts_at}
                onChange={(e) => setField('starts_at', e.target.value)}
                className={inputCls}
              />
            </Field>
          </div>

          <Field label="Description" className="mt-4">
            <textarea
              rows={4}
              value={form.description}
              onChange={(e) => setField('description', e.target.value)}
              placeholder="Do paragraph alag karne ke liye ek khaali line chhodo"
              className={`${inputCls} resize-y`}
            />
          </Field>
        </Card>

        <Card title="Seat layout">
          {/* Mode toggle */}
          <div className="mb-4 inline-flex rounded-xl border border-[var(--border)] p-0.5">
            {[
              ['tiers', 'Simple', 'Barabar rows, tier-wise price'],
              ['layout', 'Layout builder', 'Sections, aisles, alag row sizes'],
            ].map(([value, label, hint]) => (
              <button
                key={value}
                type="button"
                onClick={() => setMode(value)}
                title={hint}
                className={`rounded-lg px-3.5 py-1.5 text-xs font-medium transition ${
                  mode === value
                    ? 'bg-violet-600 text-white'
                    : 'text-slate-400 hover:text-slate-200'
                }`}
              >
                {label}
              </button>
            ))}
          </div>

          {mode === 'layout' ? (
            <LayoutBuilder layout={layout} onChange={setLayout} />
          ) : (
          <>
          <Field label="Seats per row" className="max-w-[10rem]">
            <input
              required
              type="number"
              min={1}
              max={50}
              value={form.seats_per_row}
              onChange={(e) => setField('seats_per_row', e.target.value)}
              className={inputCls}
            />
          </Field>

          <p className="mt-5 text-xs font-medium uppercase tracking-wide text-slate-500">
            Price tiers
          </p>
          <p className="mt-1 text-xs text-slate-600">
            Upar se neeche lagte hain — pehla tier row A se shuru hota hai
          </p>

          <div className="mt-3 space-y-2">
            {tiers.map((tier, i) => {
              // Is tier ki rows kaunsi hongi (A-B, C-E...)
              const start = tiers.slice(0, i).reduce((s, t) => s + Number(t.rows || 0), 0)
              const end = start + Number(tier.rows || 0) - 1
              const label =
                end < start || end >= 26
                  ? '—'
                  : start === end
                    ? ROW_LABELS[start]
                    : `${ROW_LABELS[start]}–${ROW_LABELS[end]}`

              return (
                <div key={i} className="flex items-end gap-2 rounded-xl bg-[var(--panel-2)] p-3">
                  <span className="flex h-9 w-14 shrink-0 items-center justify-center rounded-lg bg-violet-500/15 font-mono text-xs font-semibold text-violet-300">
                    {label}
                  </span>

                  <Field label="Rows" className="w-24">
                    <input
                      required
                      type="number"
                      min={1}
                      max={26}
                      value={tier.rows}
                      onChange={(e) => setTier(i, 'rows', e.target.value)}
                      className={inputCls}
                    />
                  </Field>

                  <Field label="Price (₹)" className="flex-1">
                    <input
                      required
                      type="number"
                      min={0}
                      value={tier.price}
                      onChange={(e) => setTier(i, 'price', e.target.value)}
                      className={inputCls}
                    />
                  </Field>

                  {tiers.length > 1 && (
                    <button
                      type="button"
                      onClick={() => setTiers((t) => t.filter((_, idx) => idx !== i))}
                      className="mb-1.5 text-slate-600 transition hover:text-rose-400"
                      aria-label="Remove tier"
                    >
                      <IconClose />
                    </button>
                  )}
                </div>
              )
            })}
          </div>

          {tiers.length < 10 && (
            <button
              type="button"
              onClick={() => setTiers((t) => [...t, { rows: 1, price: 500 }])}
              className="mt-2 rounded-lg border border-[var(--border)] px-3 py-1.5 text-xs
                         text-slate-400 transition hover:bg-white/5 hover:text-slate-200"
            >
              + Add tier
            </button>
          )}

          <div className="mt-5 flex flex-wrap items-center gap-3 border-t border-[var(--border)] pt-4">
            <span className="flex items-center gap-2 text-sm text-slate-300">
              <IconTicket width={16} height={16} className="text-slate-500" />
              {totalRows} rows × {form.seats_per_row || 0} ={' '}
              <strong className="text-violet-300">{totalSeats} seats</strong>
            </span>

            {tooManyRows && <Warn>Max 26 rows (A–Z)</Warn>}
            {tooManySeats && <Warn>Max 2000 seats</Warn>}
          </div>
          </>
          )}

          {/* ---- Dynamic pricing ---- */}
          <div className="mt-5 border-t border-[var(--border)] pt-4">
            <label className="flex cursor-pointer items-start gap-3">
              <input
                type="checkbox"
                checked={surge.on}
                onChange={(e) => setSurge((s) => ({ ...s, on: e.target.checked }))}
                className="mt-0.5 h-4 w-4 accent-violet-500"
              />
              <span>
                <span className="text-sm font-medium text-slate-200">
                  Demand-based pricing
                </span>
                <span className="mt-0.5 block text-xs leading-relaxed text-slate-500">
                  Seats bikne ke saath price apne aap badhta hai. Upar ka price
                  BASE hai — surge usi par lagta hai.
                </span>
              </span>
            </label>

            {surge.on && (
              <div className="mt-3 space-y-3 rounded-xl bg-white/[0.03] p-3.5">
                <div>
                  <div className="flex items-center justify-between text-xs">
                    <span className="text-slate-400">Kitna aggressive</span>
                    <span className="font-mono text-violet-300">
                      sold out par +{Math.round(surge.demand_factor * 100)}%
                    </span>
                  </div>
                  {/* Slider isliye ki 0.5 ka matlab pehli nazar me samajh nahi
                      aata — par "sold out par +50%" turant samajh aata hai */}
                  <input
                    type="range"
                    min="0"
                    max="1"
                    step="0.05"
                    value={surge.demand_factor}
                    onChange={(e) =>
                      setSurge((s) => ({ ...s, demand_factor: e.target.value }))
                    }
                    className="mt-1.5 w-full accent-violet-500"
                  />
                </div>

                <p className="text-[11px] leading-relaxed text-slate-500">
                  ₹{tiers[0]?.price || 0} wali seat sold-out ke waqt tak
                  <strong className="text-slate-300">
                    {' '}
                    ₹
                    {Math.round(
                      ((tiers[0]?.price || 0) * (1 + Number(surge.demand_factor))) / 10,
                    ) * 10}
                  </strong>{' '}
                  tak jayegi. Jisne pehle hold kar liya, uska price locked
                  rehta hai.
                </p>
              </div>
            )}
          </div>
        </Card>

        {error && (
          <p className="rounded-xl bg-rose-500/10 px-4 py-3 text-sm text-rose-300">{error}</p>
        )}

        <div className="flex gap-3">
          <button
            type="submit"
            disabled={busy || blocked}
            className="rounded-xl bg-violet-600 px-5 py-2.5 text-sm font-semibold transition
                       hover:bg-violet-500 disabled:cursor-not-allowed disabled:opacity-40"
          >
            {busy ? 'Creating…' : `Create event with ${seatCount} seats`}
          </button>

          <button
            type="button"
            onClick={() => navigate('/organizer/events')}
            className="rounded-xl border border-[var(--border)] px-5 py-2.5 text-sm
                       text-slate-400 transition hover:bg-white/5"
          >
            Cancel
          </button>
        </div>
      </form>
    </div>
  )
}

const inputCls =
  'w-full rounded-lg border border-[var(--border)] bg-[var(--bg)] px-3 py-2 text-sm ' +
  'text-slate-100 outline-none transition placeholder:text-slate-700 focus:border-violet-500'

function Card({ title, children }) {
  return (
    <section className="rounded-2xl border border-[var(--border)] bg-[var(--panel)] p-5">
      <h2 className="mb-4 text-sm font-medium text-slate-300">{title}</h2>
      {children}
    </section>
  )
}

function Field({ label, children, className = '', required }) {
  return (
    <label className={`block ${className}`}>
      <span className="mb-1.5 block text-xs font-medium text-slate-400">
        {label}
        {required && <span className="ml-0.5 text-rose-400">*</span>}
      </span>
      {children}
    </label>
  )
}

function Warn({ children }) {
  return (
    <span className="rounded-lg bg-rose-500/10 px-2 py-1 text-xs text-rose-300">{children}</span>
  )
}
