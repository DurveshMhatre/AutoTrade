import { useState, useEffect, useRef, useCallback } from 'react'

/**
 * WebSocket hook for real-time price, trade, and bot status updates.
 * Auto-reconnects with exponential backoff.
 */
export default function useWebSocket() {
  const [price, setPrice] = useState(null)
  const [lastTrade, setLastTrade] = useState(null)
  const [botStatus, setBotStatus] = useState('unknown')
  const [connected, setConnected] = useState(false)
  const [decisions, setDecisions] = useState([])
  const wsRef = useRef(null)
  const retryRef = useRef(1000)
  const timerRef = useRef(null)
  const stoppedRef = useRef(false)

  const connect = useCallback(() => {
    if (stoppedRef.current) return
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const host = window.location.host
    const wsUrl = import.meta.env.VITE_WS_URL || `${protocol}//${host}/ws`
    let ws
    try {
      ws = new WebSocket(wsUrl)
    } catch (e) {
      console.warn('WebSocket connection error:', e)
      return
    }
    wsRef.current = ws

    ws.onopen = () => {
      setConnected(true)
      retryRef.current = 1000  // Reset backoff
    }

    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data)
        switch (msg.type) {
          case 'heartbeat':
            setBotStatus(msg.status)
            break
          case 'price':
            setPrice(msg.data)
            break
          case 'trade':
            setLastTrade(msg.data)
            break
          case 'decision':
            setDecisions((prev) => [msg.data, ...prev.slice(0, 49)])
            break
          default:
            break
        }
      } catch (err) {
        console.error('WS parse error:', err)
      }
    }

    ws.onerror = () => {
      // Handled by onclose
    }

    ws.onclose = () => {
      setConnected(false)
      wsRef.current = null
      if (!stoppedRef.current) {
        // Exponential backoff up to 30s
        const delay = Math.min(retryRef.current, 30000)
        timerRef.current = setTimeout(connect, delay)
        retryRef.current = delay * 1.5
      }
    }
  }, [])

  useEffect(() => {
    stoppedRef.current = false
    connect()
    return () => {
      stoppedRef.current = true
      if (timerRef.current) clearTimeout(timerRef.current)
      if (wsRef.current) wsRef.current.close()
    }
  }, [connect])

  return { price, lastTrade, botStatus, connected, decisions }
}
