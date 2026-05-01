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

  const connect = useCallback(() => {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const host = window.location.host
    const ws = new WebSocket(`${protocol}//${host}/ws`)
    wsRef.current = ws

    ws.onopen = () => {
      setConnected(true)
      retryRef.current = 1000  // Reset backoff
    }

    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data)

        switch (msg.type) {
          case 'snapshot':
            if (msg.data?.current_btc_price) setPrice(msg.data.current_btc_price)
            if (msg.data?.bot_status) setBotStatus(msg.data.bot_status)
            break
          case 'price':
            setPrice(msg.price)
            break
          case 'new_trade':
            setLastTrade(msg.trade)
            break
          case 'new_decision':
            setDecisions(prev => [msg.decision, ...prev].slice(0, 50))
            break
          case 'bot_status':
            setBotStatus(msg.status)
            break
          case 'ping':
            break
        }
      } catch {
        // Ignore malformed messages
      }
    }

    ws.onclose = () => {
      setConnected(false)
      // Exponential backoff reconnect
      const delay = Math.min(retryRef.current, 30000)
      retryRef.current *= 2
      setTimeout(connect, delay)
    }

    ws.onerror = () => {
      ws.close()
    }
  }, [])

  useEffect(() => {
    connect()
    return () => {
      if (wsRef.current) wsRef.current.close()
    }
  }, [connect])

  return { price, lastTrade, botStatus, connected, decisions }
}
