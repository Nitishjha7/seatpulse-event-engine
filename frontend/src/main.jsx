import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'

import App from './App.jsx'
import { AuthProvider } from './auth/AuthContext'
import './index.css'

createRoot(document.getElementById('root')).render(
  <StrictMode>
    {/* AuthProvider sabse upar — App ko user ka pata isi se chalta hai */}
    <AuthProvider>
      <App />
    </AuthProvider>
  </StrictMode>,
)
