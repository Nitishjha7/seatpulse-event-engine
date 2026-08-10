import { useEffect, useState } from 'react'
import { getHealth, API_URL } from './api'

function App() {
  // Teen states: checking (abhi poochh rahe hain), online, offline
  const [status, setStatus] = useState('checking')
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)

  // Backend se health poochho
  async function checkBackend() {
    setStatus('checking')
    setError(null)
    try {
      const json = await getHealth()
      setData(json)
      setStatus('online')
    } catch (err) {
      setError(err.message)
      setStatus('offline')
    }
  }

  // Page load hote hi ek baar check karo
  useEffect(() => {
    checkBackend()
  }, [])

  // Har status ka apna color aur text
  const styles = {
    checking: { dot: 'bg-amber-400', text: 'text-amber-300', label: 'Checking…' },
    online: { dot: 'bg-emerald-400', text: 'text-emerald-300', label: 'Online' },
    offline: { dot: 'bg-rose-500', text: 'text-rose-300', label: 'Offline' },
  }[status]

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100 flex items-center justify-center p-6">
      <div className="w-full max-w-md">
        <header className="mb-8 text-center">
          <h1 className="text-3xl font-bold tracking-tight">🎟️ SeatPulse</h1>
          <p className="mt-1 text-sm text-slate-400">
            High-Concurrency Event Booking Engine
          </p>
        </header>

        <div className="rounded-xl border border-slate-800 bg-slate-900 p-6 shadow-lg">
          <div className="flex items-center justify-between">
            <span className="text-sm font-medium text-slate-400">Backend</span>
            <span className="flex items-center gap-2">
              {/* animate-pulse sirf tab jab check chal raha ho */}
              <span
                className={`h-2.5 w-2.5 rounded-full ${styles.dot} ${
                  status === 'checking' ? 'animate-pulse' : ''
                }`}
              />
              <span className={`text-sm font-semibold ${styles.text}`}>
                {styles.label}
              </span>
            </span>
          </div>

          {/* Backend chal raha hai to uska response dikhao */}
          {status === 'online' && data && (
            <dl className="mt-5 space-y-2 border-t border-slate-800 pt-4 text-sm">
              <div className="flex justify-between">
                <dt className="text-slate-500">Service</dt>
                <dd className="text-slate-300">{data.service}</dd>
              </div>
              <div className="flex justify-between">
                <dt className="text-slate-500">Version</dt>
                <dd className="text-slate-300">{data.version}</dd>
              </div>
              <div className="flex justify-between">
                <dt className="text-slate-500">Database</dt>
                <dd
                  className={
                    data.database === 'connected'
                      ? 'text-emerald-300'
                      : 'text-rose-300'
                  }
                >
                  {data.database}
                </dd>
              </div>
              <div className="flex justify-between">
                <dt className="text-slate-500">Server time</dt>
                <dd className="text-slate-300">
                  {new Date(data.time).toLocaleTimeString()}
                </dd>
              </div>
            </dl>
          )}

          {/* Nahi chal raha to error aur wajah batao */}
          {status === 'offline' && (
            <div className="mt-5 border-t border-slate-800 pt-4">
              <p className="text-sm text-rose-300">{error}</p>
              <p className="mt-2 text-xs text-slate-500">
                Backend <code className="text-slate-400">{API_URL}</code> pe chal
                raha hai? Check karo:{' '}
                <code className="text-slate-400">docker compose ps</code>
              </p>
            </div>
          )}

          <button
            onClick={checkBackend}
            disabled={status === 'checking'}
            className="mt-6 w-full rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium
                       transition hover:bg-indigo-500 disabled:opacity-50
                       disabled:cursor-not-allowed"
          >
            {status === 'checking' ? 'Checking…' : 'Recheck'}
          </button>
        </div>

        <p className="mt-6 text-center text-xs text-slate-600">
          API docs:{' '}
          <a
            href={`${API_URL}/docs`}
            target="_blank"
            rel="noreferrer"
            className="text-indigo-400 hover:text-indigo-300"
          >
            {API_URL}/docs
          </a>
        </p>
      </div>
    </div>
  )
}

export default App
