import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import { installDiagnostics } from './diagnostics'

// Catch failed requests and screen errors from the very first render.
installDiagnostics()

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
)
