import { Fragment } from 'react'

/**
 * Visual seat layout builder.
 *
 * ---- Ye kya hai, aur kya NAHI hai ----
 *
 * Ye ek FORM hai jiska live preview hai — drag-and-drop canvas nahi.
 *
 * Drag-and-drop pehle sochne me behtar lagta hai, par asli venue rows aur
 * sections me hi bane hote hain. "Row C me 12 seats, seat 4 ke baad aisle"
 * type karna maus se 12 boxes ghaseetne se tez bhi hai aur galti-proof
 * bhi. Aur wo pointer-events, undo/redo, snapping ka poora pahaad bhi
 * nahi laata.
 *
 * ⚠️ Yahan ka koi bhi check SECURITY nahi hai. Validation server par
 * `layout.py` me hoti hai — ye sirf usse pehle user ko batata hai ki
 * kya galat hai.
 */

const ROW_LETTERS = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'

/** Poore layout me abhi tak kaunse row labels use ho chuke hain. */
function usedLabels(sections) {
  return new Set(
    sections.flatMap((s) => s.rows.map((r) => r.label.trim().toUpperCase())),
  )
}

/** Agla khaali row letter dhoondo — A, B, C… phir A1, B1… */
function nextLabel(sections) {
  const used = usedLabels(sections)
  for (const ch of ROW_LETTERS) {
    if (!used.has(ch)) return ch
  }
  for (let i = 1; i < 10; i++) {
    for (const ch of ROW_LETTERS) {
      if (!used.has(`${ch}${i}`)) return `${ch}${i}`
    }
  }
  return 'Z9'
}

export function emptyLayout() {
  return {
    sections: [
      {
        name: 'Ground',
        price: 1500,
        rows: [
          { label: 'A', seats: 10, aisles_after: [5] },
          { label: 'B', seats: 10, aisles_after: [5] },
        ],
      },
    ],
  }
}

/**
 * Client-side validation.
 *
 * Server ke rules ki copy hai — aur ye duplication jaan-boojh ke hai.
 * Server par ye rules hone hi chahiye (koi bhi API ko seedha hit kar
 * sakta hai), par user ko submit dabane se PEHLE pata chalna chahiye ki
 * do rows ka label same hai. Wo round-trip bekaar hai.
 *
 * Server hi asli faisla karta hai; ye sirf jaldi feedback deta hai.
 */
export function validateLayout(layout) {
  const seen = new Map()
  let total = 0

  for (const section of layout.sections) {
    if (!section.name.trim()) return 'Har section ka naam chahiye'

    for (const row of section.rows) {
      const label = row.label.trim().toUpperCase()
      if (!label) return 'Har row ka label chahiye'
      if (seen.has(label)) {
        return `Row "${label}" do jagah hai (${seen.get(label)} aur ${section.name})`
      }
      seen.set(label, section.name)

      if (row.seats < 1) return `Row ${label} me kam se kam 1 seat`
      for (const a of row.aisles_after) {
        if (a >= row.seats) {
          return `Row ${label}: aisle seat ${a} ke baad nahi ho sakti (row me ${row.seats} seats hain)`
        }
      }
      total += row.seats
    }
  }

  if (total === 0) return 'Ek bhi seat nahi hai'
  if (total > 2000) return `Max 2000 seats — is layout me ${total} hain`
  return null
}

export function countSeats(layout) {
  return layout.sections.reduce(
    (sum, s) => sum + s.rows.reduce((n, r) => n + r.seats, 0),
    0,
  )
}

export default function LayoutBuilder({ layout, onChange }) {
  const error = validateLayout(layout)
  const total = countSeats(layout)

  function update(fn) {
    const next = structuredClone(layout)
    fn(next)
    onChange(next)
  }

  return (
    <div className="space-y-4">
      {layout.sections.map((section, si) => (
        <div
          key={si}
          className="rounded-xl border border-[var(--border)] bg-white/[0.02] p-4"
        >
          <div className="flex flex-wrap items-end gap-3">
            <label className="flex-1">
              <span className="block text-xs font-medium text-slate-400">Section</span>
              <input
                value={section.name}
                onChange={(e) => update((l) => { l.sections[si].name = e.target.value })}
                placeholder="Ground / Balcony"
                className="mt-1 w-full rounded-lg border border-[var(--border)] bg-[var(--bg)]
                           px-3 py-2 text-sm text-slate-100 outline-none focus:border-violet-500"
              />
            </label>

            <label className="w-32">
              <span className="block text-xs font-medium text-slate-400">Price (₹)</span>
              <input
                type="number"
                min="0"
                value={section.price}
                onChange={(e) =>
                  update((l) => { l.sections[si].price = Number(e.target.value) })
                }
                className="mt-1 w-full rounded-lg border border-[var(--border)] bg-[var(--bg)]
                           px-3 py-2 text-sm text-slate-100 outline-none focus:border-violet-500"
              />
            </label>

            {layout.sections.length > 1 && (
              <button
                type="button"
                onClick={() => update((l) => { l.sections.splice(si, 1) })}
                className="mb-1 text-slate-600 transition hover:text-rose-400"
                aria-label={`Remove section ${section.name}`}
              >
                ✕
              </button>
            )}
          </div>

          <div className="mt-3 space-y-2">
            {section.rows.map((row, ri) => (
              <RowEditor
                key={ri}
                row={row}
                canRemove={section.rows.length > 1}
                onChange={(fn) => update((l) => fn(l.sections[si].rows[ri]))}
                onRemove={() => update((l) => { l.sections[si].rows.splice(ri, 1) })}
              />
            ))}
          </div>

          <button
            type="button"
            onClick={() =>
              update((l) => {
                const last = l.sections[si].rows.at(-1)
                l.sections[si].rows.push({
                  label: nextLabel(l.sections),
                  // Pichli row ki shape copy — aksar rows ek jaisi hoti
                  // hain, to user ko har baar sab type na karna pade
                  seats: last?.seats ?? 10,
                  aisles_after: [...(last?.aisles_after ?? [])],
                })
              })
            }
            className="mt-2 rounded-lg border border-[var(--border)] px-3 py-1.5 text-xs
                       text-slate-400 transition hover:bg-white/5 hover:text-slate-200"
          >
            + Row
          </button>
        </div>
      ))}

      {layout.sections.length < 10 && (
        <button
          type="button"
          onClick={() =>
            update((l) => {
              l.sections.push({
                name: '',
                price: 500,
                rows: [{ label: nextLabel(l.sections), seats: 10, aisles_after: [] }],
              })
            })
          }
          className="rounded-lg border border-[var(--border)] px-3 py-1.5 text-xs
                     text-slate-400 transition hover:bg-white/5 hover:text-slate-200"
        >
          + Section
        </button>
      )}

      <LayoutPreview layout={layout} />

      <div className="flex flex-wrap items-center gap-3 border-t border-[var(--border)] pt-3">
        <span className="text-sm text-slate-300">
          {layout.sections.length} section{layout.sections.length > 1 ? 's' : ''} ·{' '}
          <strong className="text-violet-300">{total} seats</strong>
        </span>
        {error && (
          <span className="rounded-lg bg-amber-500/10 px-2.5 py-1 text-xs text-amber-300">
            {error}
          </span>
        )}
      </div>
    </div>
  )
}

function RowEditor({ row, canRemove, onChange, onRemove }) {
  const aisles = row.aisles_after.join(',')

  return (
    <div className="flex flex-wrap items-center gap-2 rounded-lg bg-black/20 p-2">
      <input
        value={row.label}
        onChange={(e) => onChange((r) => { r.label = e.target.value.toUpperCase() })}
        className="w-12 rounded border border-[var(--border)] bg-[var(--bg)] px-2 py-1.5
                   text-center text-sm font-semibold text-slate-100 outline-none
                   focus:border-violet-500"
      />

      <label className="flex items-center gap-1.5 text-xs text-slate-500">
        seats
        <input
          type="number"
          min="1"
          max="60"
          value={row.seats}
          onChange={(e) => onChange((r) => { r.seats = Number(e.target.value) })}
          className="w-16 rounded border border-[var(--border)] bg-[var(--bg)] px-2 py-1.5
                     text-sm text-slate-100 outline-none focus:border-violet-500"
        />
      </label>

      <label className="flex flex-1 items-center gap-1.5 text-xs text-slate-500">
        aisle after
        <input
          value={aisles}
          onChange={(e) =>
            onChange((r) => {
              // "4, 8" -> [4, 8]. Adhoora input ("4,") type karte waqt
              // NaN na aaye isliye filter — warna har keystroke pe
              // validation error flash karta hai.
              r.aisles_after = e.target.value
                .split(',')
                .map((x) => parseInt(x.trim(), 10))
                .filter((x) => Number.isInteger(x) && x > 0)
            })
          }
          placeholder="4, 8"
          className="w-full min-w-16 rounded border border-[var(--border)] bg-[var(--bg)]
                     px-2 py-1.5 text-sm text-slate-100 outline-none
                     placeholder:text-slate-700 focus:border-violet-500"
        />
      </label>

      {canRemove && (
        <button
          type="button"
          onClick={onRemove}
          className="text-slate-600 transition hover:text-rose-400"
          aria-label={`Remove row ${row.label}`}
        >
          ✕
        </button>
      )}
    </div>
  )
}

/**
 * Live preview — bilkul wahi shape jo attendee ko dikhega.
 *
 * Yahi is builder ka poora point hai: 40 seats ki row aur 4 aisles ko
 * numbers me sochna mushkil hai, dekh ke turant samajh aata hai.
 */
function LayoutPreview({ layout }) {
  return (
    <div className="rounded-xl border border-[var(--border)] bg-[var(--bg)] p-3">
      <p className="mb-2 text-center text-[10px] uppercase tracking-[0.25em] text-violet-300/70">
        Stage
      </p>

      <div className="space-y-2 overflow-x-auto">
        {layout.sections.map((section, si) => (
          <div key={si}>
            {layout.sections.length > 1 && (
              <p className="mb-1 text-[10px] uppercase tracking-widest text-slate-600">
                {section.name || '—'} · ₹{section.price}
              </p>
            )}
            {section.rows.map((row, ri) => (
              <div key={ri} className="mb-1 flex items-center gap-1">
                <span className="w-5 shrink-0 text-[10px] font-semibold text-slate-600">
                  {row.label}
                </span>
                {Array.from({ length: Math.min(row.seats, 60) }, (_, i) => (
                  <Fragment key={i}>
                    <span className="h-3.5 w-3.5 shrink-0 rounded-sm bg-emerald-500/50" />
                    {row.aisles_after.includes(i + 1) && (
                      <span className="w-3 shrink-0" aria-hidden="true" />
                    )}
                  </Fragment>
                ))}
              </div>
            ))}
          </div>
        ))}
      </div>
    </div>
  )
}
