import { useState } from 'react'
import { useNavigate } from 'react-router-dom'

import { createEvent } from '../../api'
import { IconClose, IconTicket } from '../../layout/icons'

const ROW_LABELS = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'

export default function CreateEvent() {
  const navigate = useNavigate()

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
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)

  const totalRows = tiers.reduce((sum, t) => sum + Number(t.rows || 0), 0)
  const totalSeats = totalRows * Number(form.seats_per_row || 0)

  // Backend me bhi yahi limits hain — yahan sirf user ko pehle bata rahe hain
  const tooManyRows = totalRows > 26
  const tooManySeats = totalSeats > 2000

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
      const created = await createEvent({
        ...form,
        seats_per_row: Number(form.seats_per_row),
        // datetime-local "2026-12-01T19:30" deta hai — backend ko ISO chahiye
        starts_at: new Date(form.starts_at).toISOString(),
        description: form.description || null,
        price_tiers: tiers.map((t) => ({
          rows: Number(t.rows),
          price: Number(t.price),
        })),
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
          Seats price tiers se apne aap ban jaayengi
        </p>
      </header>

      <form onSubmit={handleSubmit} className="space-y-5">
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
        </Card>

        {error && (
          <p className="rounded-xl bg-rose-500/10 px-4 py-3 text-sm text-rose-300">{error}</p>
        )}

        <div className="flex gap-3">
          <button
            type="submit"
            disabled={busy || tooManyRows || tooManySeats || totalSeats === 0}
            className="rounded-xl bg-violet-600 px-5 py-2.5 text-sm font-semibold transition
                       hover:bg-violet-500 disabled:cursor-not-allowed disabled:opacity-40"
          >
            {busy ? 'Creating…' : `Create event with ${totalSeats} seats`}
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
