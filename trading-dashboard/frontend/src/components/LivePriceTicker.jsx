import { useState, useEffect, useRef } from 'react'

/**
 * LivePriceTicker — BTC/USDT price in the top bar with flash animation
 * on price update and directional arrow.
 *
 * Props:
 *   price     — current BTC price (number)
 *   symbol    — string (default 'BTC/USDT')
 *   connected — boolean (WebSocket connected)
 */
export default function LivePriceTicker({ price, symbol = 'BTC/USDT', connected }) {
  const [isFlashing, setIsFlashing] = useState(false)
  const [direction, setDirection] = useState(null) // 'up' | 'down' | null
  const prevPriceRef = useRef(price)

  useEffect(() => {
    if (price && prevPriceRef.current && price !== prevPriceRef.current) {
      // Determine direction
      setDirection(price > prevPriceRef.current ? 'up' : 'down')

      // Trigger flash
      setIsFlashing(true)
      const timer = setTimeout(() => setIsFlashing(false), 600)

      prevPriceRef.current = price
      return () => clearTimeout(timer)
    }
    prevPriceRef.current = price
  }, [price])

  const formattedPrice = price
    ? '$' + price.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
    : '---'

  return (
    <div className="price-ticker" style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
      {/* Connection status dot */}
      <span className={`ws-dot ${connected ? 'connected' : 'disconnected'}`} />

      {/* Symbol label */}
      <span className="label">{symbol}</span>

      {/* Price with flash */}
      <span className={isFlashing ? 'price-flash' : ''} key={price}>
        {formattedPrice}
      </span>

      {/* Direction arrow */}
      {direction && (
        <span className={`price-direction ${direction}`}>
          {direction === 'up' ? '▲' : '▼'}
        </span>
      )}

      {/* Reconnecting text */}
      {!connected && (
        <span style={{ fontSize: 10, color: 'var(--rose)', marginLeft: 4 }}>
          Reconnecting…
        </span>
      )}
    </div>
  )
}
