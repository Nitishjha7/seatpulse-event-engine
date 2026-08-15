import { useState } from 'react'

import { useAuth } from './AuthContext'

/** Google ka official "G" logo — inline SVG, koi external request nahi */
function GoogleIcon() {
  return (
    <svg className="h-4 w-4" viewBox="0 0 24 24" aria-hidden="true">
      <path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92a5.06 5.06 0 0 1-2.2 3.32v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.1z" />
      <path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.65l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84A11 11 0 0 0 12 23z" />
      <path fill="#FBBC05" d="M5.84 14.11a6.6 6.6 0 0 1 0-4.22V7.05H2.18a11 11 0 0 0 0 9.9l3.66-2.84z" />
      <path fill="#EA4335" d="M12 4.75c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 1.46 14.97.5 12 .5A11 11 0 0 0 2.18 7.05l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z" />
    </svg>
  )
}

export default function AuthPage() {
  const { login, register, googleLogin, googleEnabled } = useAuth()

  const [mode, setMode] = useState('login')   // login | signup
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [fullName, setFullName] = useState('')
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)

  const isSignup = mode === 'signup'

  // Google redirect fail hua to backend ?auth_error=... ke saath wapas bhejta hai
  const urlError = new URLSearchParams(window.location.search).get('auth_error')

  async function handleSubmit(e) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      if (isSignup) {
        await register(email, password, fullName)
      } else {
        await login(email, password)
      }
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-slate-950 p-6 text-slate-100">
      <div className="w-full max-w-sm">
        <div className="mb-8 text-center">
          <h1 className="text-3xl font-bold tracking-tight">🎟️ SeatPulse</h1>
          <p className="mt-1 text-sm text-slate-500">
            High-Concurrency Event Booking Engine
          </p>
        </div>

        <div className="rounded-xl border border-slate-800 bg-slate-900 p-6">
          <h2 className="text-lg font-semibold">
            {isSignup ? 'Account banao' : 'Login karo'}
          </h2>
          <p className="mt-1 text-sm text-slate-500">
            {isSignup ? 'Seats book karne ke liye' : 'Wapas aa gaye, badhiya'}
          </p>

          {googleEnabled && (
            <>
              <button
                onClick={googleLogin}
                type="button"
                className="mt-5 flex w-full items-center justify-center gap-2 rounded-lg
                           border border-slate-700 bg-slate-800 px-4 py-2.5 text-sm
                           font-medium transition hover:bg-slate-700"
              >
                <GoogleIcon />
                Continue with Google
              </button>

              <div className="my-5 flex items-center gap-3 text-xs text-slate-600">
                <span className="h-px flex-1 bg-slate-800" />
                ya
                <span className="h-px flex-1 bg-slate-800" />
              </div>
            </>
          )}

          <form onSubmit={handleSubmit} className={googleEnabled ? '' : 'mt-5'}>
            {isSignup && (
              <Field
                label="Naam"
                type="text"
                value={fullName}
                onChange={setFullName}
                placeholder="Nitish Jha"
                autoComplete="name"
              />
            )}

            <Field
              label="Email"
              type="email"
              value={email}
              onChange={setEmail}
              placeholder="you@example.com"
              autoComplete="email"
              required
            />

            <Field
              label="Password"
              type="password"
              value={password}
              onChange={setPassword}
              placeholder={isSignup ? 'kam se kam 8 characters' : '••••••••'}
              autoComplete={isSignup ? 'new-password' : 'current-password'}
              required
              minLength={isSignup ? 8 : undefined}
            />

            {(error || urlError) && (
              <p className="mt-3 rounded-lg bg-rose-950/40 px-3 py-2 text-sm text-rose-300">
                {error || `Google login fail: ${urlError}`}
              </p>
            )}

            <button
              type="submit"
              disabled={busy}
              className="mt-5 w-full rounded-lg bg-indigo-600 px-4 py-2.5 text-sm
                         font-medium transition hover:bg-indigo-500
                         disabled:cursor-not-allowed disabled:opacity-50"
            >
              {busy ? 'Ruko…' : isSignup ? 'Account banao' : 'Login'}
            </button>
          </form>

          <p className="mt-5 text-center text-sm text-slate-500">
            {isSignup ? 'Account pehle se hai?' : 'Naye ho?'}{' '}
            <button
              onClick={() => {
                setMode(isSignup ? 'login' : 'signup')
                setError(null)
              }}
              className="text-indigo-400 transition hover:text-indigo-300"
            >
              {isSignup ? 'Login karo' : 'Account banao'}
            </button>
          </p>
        </div>

        {/* Demo credentials — recruiter/interviewer ko turant andar jaane deta hai */}
        <div className="mt-4 rounded-lg border border-slate-800/60 bg-slate-900/40 p-3 text-center text-xs text-slate-500">
          Demo login —{' '}
          <button
            onClick={() => {
              setMode('login')
              setEmail('demo@seatpulse.dev')
              setPassword('demo1234')
            }}
            className="font-mono text-slate-400 underline decoration-slate-700 hover:text-slate-200"
          >
            demo@seatpulse.dev / demo1234
          </button>
        </div>
      </div>
    </div>
  )
}

function Field({ label, value, onChange, ...props }) {
  return (
    <label className="mt-4 block first:mt-0">
      <span className="mb-1.5 block text-xs font-medium text-slate-400">{label}</span>
      <input
        {...props}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="w-full rounded-lg border border-slate-800 bg-slate-950 px-3 py-2
                   text-sm text-slate-100 outline-none transition
                   placeholder:text-slate-700 focus:border-indigo-500"
      />
    </label>
  )
}
