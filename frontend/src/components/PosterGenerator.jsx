import { useEffect, useState } from 'react'

import { generatePoster } from '../api'
import { useAuth } from '../auth/AuthContext'

/**
 * Generates a poster image from the same brief used for the AI draft.
 *
 * Purely a suggestion — the organizer can download it or ignore it, it's
 * never attached to the event automatically.
 */
export default function PosterGenerator({ brief }) {
  const { posterEnabled } = useAuth()

  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [posterUrl, setPosterUrl] = useState(null)

  // Blob URLs need to be released explicitly, or they leak for the life
  // of the page.
  useEffect(() => {
    return () => {
      if (posterUrl) URL.revokeObjectURL(posterUrl)
    }
  }, [posterUrl])

  if (!posterEnabled) return null

  async function run() {
    if (!brief.trim() || busy) return
    setBusy(true)
    setError(null)
    try {
      const url = await generatePoster(brief.trim())
      setPosterUrl((prev) => {
        if (prev) URL.revokeObjectURL(prev)
        return url
      })
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="rounded-2xl border border-violet-500/25 bg-violet-500/[0.04] p-4">
      <h2 className="text-sm font-medium text-violet-200">Generate poster with AI</h2>
      <p className="mt-0.5 text-xs text-slate-500">
        Uses the same brief above to generate a poster image.
      </p>

      <button
        type="button"
        onClick={run}
        disabled={busy || !brief.trim()}
        className="mt-2.5 rounded-lg bg-violet-600 px-4 py-2 text-sm font-medium transition
                   hover:bg-violet-500 disabled:opacity-40"
      >
        {busy ? 'Generating… (can take up to a minute)' : 'Generate poster'}
      </button>

      {error && (
        <p className="mt-2 rounded-lg bg-rose-500/10 px-3 py-2 text-xs text-rose-300">
          {error}
        </p>
      )}

      {posterUrl && !error && (
        <div className="mt-3">
          <img
            src={posterUrl}
            alt="Generated event poster"
            className="w-full max-w-xs rounded-lg border border-[var(--border)]"
          />
          <a
            href={posterUrl}
            download="poster.jpg"
            className="mt-2 inline-block text-xs text-violet-300 underline decoration-violet-500/40"
          >
            Download
          </a>
        </div>
      )}
    </section>
  )
}
