import { BrowserRouter, Routes, Route, NavLink, Navigate } from 'react-router-dom'
import { LayoutDashboard, ListOrdered, BarChart2, Settings, Wifi, WifiOff } from 'lucide-react'
import { useState, useEffect } from 'react'
import useWebSocket from './hooks/useWebSocket'
import LivePriceTicker from './components/LivePriceTicker'
import Dashboard from './pages/Dashboard'
import Orders from './pages/Orders'
import Analytics from './pages/Analytics'
import './index.css'

function App() {
  const [utcTime, setUtcTime] = useState('')
  const ws = useWebSocket()

  // Derive state from WebSocket (with REST fallback)
  const [fallbackPrice, setFallbackPrice] = useState(null)
  const btcPrice = ws.price || fallbackPrice
  const botStatus = ws.botStatus !== 'unknown' ? ws.botStatus : 'running'

  // Update UTC clock every second
  useEffect(() => {
    const tick = () => {
      const now = new Date()
      setUtcTime(now.toISOString().slice(11, 19) + ' UTC')
    }
    tick()
    const interval = setInterval(tick, 1000)
    return () => clearInterval(interval)
  }, [])

  // Fallback: fetch BTC price via REST if WebSocket is not connected
  useEffect(() => {
    if (ws.connected) return  // WebSocket handles it

    const fetchPrice = async () => {
      try {
        const resp = await fetch(
          'https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT'
        )
        if (resp.ok) {
          const data = await resp.json()
          setFallbackPrice(parseFloat(data.price))
        }
      } catch {
        // Silently fail
      }
    }
    fetchPrice()
    const interval = setInterval(fetchPrice, 10000)
    return () => clearInterval(interval)
  }, [ws.connected])

  return (
    <BrowserRouter>
      <div className="app-layout">
        {/* ── Sidebar ─────────────────────────── */}
        <nav className="sidebar">
          <div className="sidebar-logo">AT</div>

          <NavLink
            to="/dashboard"
            className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}
          >
            <LayoutDashboard size={20} />
            <span className="nav-label">Dashboard</span>
          </NavLink>

          <NavLink
            to="/orders"
            className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}
          >
            <ListOrdered size={20} />
            <span className="nav-label">Orders</span>
          </NavLink>

          <NavLink
            to="/analytics"
            className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}
          >
            <BarChart2 size={20} />
            <span className="nav-label">Analytics</span>
          </NavLink>

          <NavLink
            to="/settings"
            className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}
          >
            <Settings size={20} />
            <span className="nav-label">Settings</span>
          </NavLink>
        </nav>

        {/* ── Main Area ───────────────────────── */}
        <div className="main-content">
          {/* ── Top Bar ──────────────────────── */}
          <header className="top-bar">
            <div className="top-bar-left">
              <div className={`status-badge ${botStatus}`}>
                <span className="status-dot"></span>
                Bot {botStatus === 'running' ? 'Running' : botStatus === 'paused' ? 'Paused' : 'Error'}
              </div>
              <div className={`status-badge ${ws.connected ? '' : 'error'}`} style={{ fontSize: 11 }}>
                {ws.connected ? <Wifi size={12} /> : <WifiOff size={12} />}
                {ws.connected ? 'Live' : 'Reconnecting...'}
              </div>
            </div>
            <div className="top-bar-right">
              <LivePriceTicker
                price={btcPrice}
                symbol="BTC/USDT"
                connected={ws.connected}
              />
              <div className="utc-time">{utcTime}</div>
            </div>
          </header>

          {/* ── Routes ───────────────────────── */}
          <main className="page-content">
            <Routes>
              <Route path="/" element={<Navigate to="/dashboard" replace />} />
              <Route path="/dashboard" element={<Dashboard wsData={ws} />} />
              <Route path="/orders" element={<Orders />} />
              <Route path="/analytics" element={<Analytics />} />
              <Route path="/settings" element={<SettingsPlaceholder />} />
            </Routes>
          </main>
        </div>
      </div>
    </BrowserRouter>
  )
}

function SettingsPlaceholder() {
  return (
    <div>
      <h1 className="page-title">Settings</h1>
      <div className="card">
        <p style={{ color: 'var(--text-muted)' }}>
          Bot configuration settings will be added here.
        </p>
      </div>
    </div>
  )
}

export default App
