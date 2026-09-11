import { Fragment } from 'react'

import { seatPrice } from '../booking/BookingContext'

/**
 * Seat grid — displays all event seats organized by row.
 *
 * WebSocket updates trigger re-renders only for the specific seat changed,
 * rather than replacing the entire array in state.
 */

// Centralized styles for seat statuses to ensure consistency between grid and legend.
const SEAT_STYLES = {
  available:
    'bg-emerald-500/85 text-emerald-950 hover:bg-emerald-400 hover:-translate-y-0.5 cursor-pointer',
  locked: 'bg-amber-400/80 text-amber-950 cursor-not-allowed',
  // Payment in progress — distinct color to signal the seat is being purchased, not just held.
  payment_pending: 'bg-orange-600/80 text-orange-50 cursor-not-allowed animate-pulse',
  booked: 'bg-rose-600/70 text-rose-100/70 cursor-not-allowed line-through',
  // Group booking hold — unavailable for individual purchase. Distinct color indicates
  // a long hold duration (up to 30 mins), signaling it will not be released soon.
  group_held: 'bg-sky-600/70 text-sky-50 cursor-not-allowed',
  // User's own hold — visually distinct from other users' holds (yellow).
  selected:
    'bg-violet-500 text-white ring-2 ring-violet-300 ring-offset-2 ring-offset-[var(--panel)] cursor-pointer',
}

function Seat({ seat, isSelected, isMine, onSelect, busy }) {
  // Seats are clickable if available or if currently held by the user (to deselect).
  const clickable = !busy && (seat.status === 'available' || isMine)
  const style = isSelected
    ? SEAT_STYLES.selected
    : SEAT_STYLES[seat.status] || SEAT_STYLES.available

  return (
    <button
      onClick={() => clickable && onSelect(seat)}
      disabled={!clickable}
      title={`${seat.row_label}-${seat.seat_number} · ₹${seatPrice(seat)} · ${seat.status}`}
      className={`h-8 w-8 shrink-0 rounded-md text-[11px] font-semibold transition-all
                  duration-150 sm:h-9 sm:w-9 sm:text-xs ${style} ${busy ? 'opacity-60' : ''}`}
    >
      {seat.seat_number}
    </button>
  )
}

/**
 * Creates a lookup map: row label -> set of seat numbers followed by an aisle.
 *
 * ⚠️ Layout is optional. Events created before Phase 18 have a NULL layout.
 * Fallbacks ensure backward compatibility, returning an empty Map for uniform rows.
 */
function aisleMap(layout) {
  const map = new Map()
  if (!layout?.sections) return map

  for (const section of layout.sections) {
    for (const row of section.rows ?? []) {
      if (row.aisles_after?.length) {
        map.set(row.label, new Set(row.aisles_after))
      }
    }
  }
  return map
}

export default function SeatGrid({
  seats,
  selectedSeat,
  onSelect,
  currentUserId,
  busy,
  layout,
}) {
  // Group flat list into rows. Backend provides sorted data.
  const rows = seats.reduce((acc, seat) => {
    ;(acc[seat.row_label] ||= []).push(seat)
    return acc
  }, {})

  const aisles = aisleMap(layout)

  // Only show section headings if multiple sections exist.
  // Single-section events don't require redundant labels.
  const sectionNames = [...new Set(seats.map((s) => s.section).filter(Boolean))]
  const showSections = sectionNames.length > 1

  return (
    <section className="rounded-2xl border border-[var(--border)] bg-[var(--panel)] p-4 sm:p-5">
      {/* Stage indicator for spatial orientation */}
      <div className="relative mx-auto mb-7 w-4/5 max-w-md">
        <div
          className="rounded-b-3xl border-b-2 border-violet-500/40 bg-gradient-to-b
                     from-violet-500/20 to-transparent py-2 text-center text-[11px]
                     font-semibold uppercase tracking-[0.25em] text-violet-300/80"
        >
          Stage
        </div>
      </div>

      <div className="-mx-1 overflow-x-auto px-1 pb-1">
        <div className="inline-block min-w-full space-y-2">
          {Object.entries(rows).map(([rowLabel, rowSeats], rowIndex, allRows) => {
            const gaps = aisles.get(rowLabel)
            const section = rowSeats[0]?.section
            // Show section heading if the section changes or at the start.
            const prevSection =
              rowIndex > 0 ? rows[allRows[rowIndex - 1][0]][0]?.section : null
            const newSection = showSections && section && section !== prevSection

            return (
              <div key={rowLabel}>
                {newSection && (
                  <p
                    className="mb-1.5 mt-4 text-[10px] font-semibold uppercase
                               tracking-[0.2em] text-slate-500 first:mt-0"
                  >
                    {section}
                  </p>
                )}

                <div className="flex items-center gap-1.5 sm:gap-2">
                  <span className="w-4 shrink-0 text-xs font-semibold text-slate-500">
                    {rowLabel}
                  </span>
                  {rowSeats.map((seat) => (
                    <Fragment key={seat.id}>
                      <Seat
                        seat={seat}
                        isSelected={selectedSeat?.id === seat.id}
                        isMine={seat.status === 'locked' && seat.locked_by === currentUserId}
                        onSelect={onSelect}
                        busy={busy}
                      />
                      {/* Aisle — visual gap to represent venue layout without affecting numbering */}
                      {gaps?.has(seat.seat_number) && (
                        <span className="w-4 shrink-0 sm:w-5" aria-hidden="true" />
                      )}
                    </Fragment>
                  ))}
                </div>
              </div>
            )
          })}
        </div>
      </div>

      <div className="mt-6 flex flex-wrap gap-x-5 gap-y-2 border-t border-[var(--border)] pt-4 text-xs text-slate-400">
        <Legend className="bg-emerald-500/85" label="Available" />
        <Legend className="bg-violet-500" label="Your hold" />
        <Legend className="bg-amber-400/80" label="Held by someone else" />
        <Legend className="bg-orange-600/80" label="Being purchased" />
        <Legend className="bg-sky-600/70" label="Group hold" />
        <Legend className="bg-rose-600/70" label="Booked" />
      </div>
    </section>
  )
}

function Legend({ className, label }) {
  return (
    <span className="flex items-center gap-1.5">
      <span className={`h-3 w-3 rounded ${className}`} />
      {label}
    </span>
  )
}
