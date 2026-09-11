import { useState } from 'react'

import { draftEvent } from '../api'
import { useAuth } from '../auth/AuthContext'

/**
 * Generates an event draft from a brief description.
 *
 * ---- Critical Design Decision: This does not persist data ----
 *
 * The draft populates form fields directly, requiring the organizer to
 * review and publish manually. The AI is strictly prohibited from
 * triggering a publish action.
 *
 * Rationale: An event description is a binding promise to the attendee.
 * If the model hallucinates details (e.g., "featuring special guests"),
 * the organizer—not the AI—is responsible for the misinformation.
 *
 * Safeguards:
 *   1. System prompt explicitly forbids hallucination.
 *   2. Human-in-the-loop requirement ensures no content is published
 *      without manual verification.
 */
export default function AiDraft({ onDraft }) {
  const { aiSearchEnabled } = useAuth()

  const [brief, setBrief] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [filled, setFilled] = useState(false)

  // If the feature is disabled, hide the component. Manual entry remains available.
  if (!aiSearchEnabled) return null

  async function run() {
    if (!brief.trim() || busy) return
    setBusy(true)
    setError(null)
    try {
      onDraft(await draftEvent(brief.trim()))
      setFilled(true)
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <section
      className="rounded-2xl border border-violet-500/25 bg-violet-500/[0.04] p-4"
    >
      <h2 className="text-sm font-medium text-violet-200">
        ✨ Generate draft with AI
      </h2>
      <p className="mt-0.5 text-xs text-slate-500">
        Enter a brief description to auto-populate the name, details, and category.
      </p>

      <div className="mt-2.5 flex gap-2">
        <input
          value={brief}
          onChange={(e) => setBrief(e.target.value)}
          maxLength={200}
          placeholder="Arijit Singh concert, DY Patil Mumbai, December"
          // Prevent default form submission on Enter to trigger the draft process instead.
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              e.preventDefault()
              run()
            }
          }}
          className="flex-1 rounded-lg border border-[var(--border)] bg-[var(--bg)] px-3 py-2
                     text-sm text-slate-100 outline-none transition
                     placeholder:text-slate-700 focus:border-violet-500"
        />
        <button
          type="button"
          onClick={run}
          disabled={busy || !brief.trim()}
          className="rounded-lg bg-violet-600 px-4 text-sm font-medium transition
                     hover:bg-violet-500 disabled:opacity-40"
        >
          {busy ? 'Generating…' : 'Draft'}
        </button>
      </div>

      {error && (
        <p className="mt-2 rounded-lg bg-rose-500/10 px-3 py-2 text-xs text-rose-300">
          {error}
        </p>
      )}

      {filled && !error && (
        // Disclaimer: Remind the organizer that AI-generated content requires manual review.
        <p className="mt-2 rounded-lg bg-amber-500/10 px-3 py-2 text-xs leading-relaxed text-amber-200">
          Draft populated — <strong>please review before publishing</strong>.
          All content will be attributed to your event.
        </p>
      )}
    </section>
  )
}
