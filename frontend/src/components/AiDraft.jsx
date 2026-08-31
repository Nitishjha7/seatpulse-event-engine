import { useState } from 'react'

import { draftEvent } from '../api'
import { useAuth } from '../auth/AuthContext'

/**
 * Chhote brief se event listing ka draft.
 *
 * ---- Sabse zaroori design faisla: ye kuch SAVE nahi karta ----
 *
 * Draft seedha form ke fields me bhar jata hai, aur organizer usse edit
 * karke khud publish karta hai. AI ko publish button tak pahunchne hi
 * nahi dete.
 *
 * Wajah cosmetic nahi hai: event ka description ticket kharidne wale ke
 * liye ek **waada** hai. Model "featuring special guests" gadh de aur wo
 * bina padhe publish ho jaye — to jhooth attendee tak pahunch jata hai,
 * aur uska zimmedar organizer hai, AI nahi.
 *
 * Isliye do jagah guard hai:
 *   1. Prompt me model ko facts gadhne se saaf mana kiya gaya hai
 *   2. Yahan — insaan ke haath se guzre bina kuch publish nahi hota
 */
export default function AiDraft({ onDraft }) {
  const { aiSearchEnabled } = useAuth()

  const [brief, setBrief] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [filled, setFilled] = useState(false)

  // Key nahi hai to ye box dikhta hi nahi — form haath se bharna waise
  // bhi poori tarah chalta hai.
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
        ✨ AI se draft banao
      </h2>
      <p className="mt-0.5 text-xs text-slate-500">
        Ek line likho — naam, description aur category apne aap bhar jaayenge
      </p>

      <div className="mt-2.5 flex gap-2">
        <input
          value={brief}
          onChange={(e) => setBrief(e.target.value)}
          maxLength={200}
          placeholder="Arijit Singh concert, DY Patil Mumbai, December"
          // Enter par form submit ho jata (ye ek form ke andar hai), isliye
          // rok ke draft chalate hain
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
          {busy ? 'Likh raha hai…' : 'Draft'}
        </button>
      </div>

      {error && (
        <p className="mt-2 rounded-lg bg-rose-500/10 px-3 py-2 text-xs text-rose-300">
          {error}
        </p>
      )}

      {filled && !error && (
        // Ye line zaroori hai. Organizer ko pata hona chahiye ki jo neeche
        // bhara hai wo ek MASHIN ne likha hai aur uski zimmedari uski hai.
        <p className="mt-2 rounded-lg bg-amber-500/10 px-3 py-2 text-xs leading-relaxed text-amber-200">
          Draft neeche bhar diya hai — <strong>publish se pehle padh lo</strong>.
          Jo likha hai wo tumhare naam se attendees tak jayega.
        </p>
      )}
    </section>
  )
}
