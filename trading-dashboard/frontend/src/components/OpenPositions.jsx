import { formatDistanceToNow } from 'date-fns'
import { AlertTriangle, Eye } from 'lucide-react'

/**
 * OpenPositions — Active trades with live unrealized P&L,
 * SL/TP progress bar, and warning border when price nears SL.
 *
 * Props:
 *   positions    — array of position objects
 *   loading      — boolean
 *   currentPrice — live BTC price from WebSocket (optional override)
 */
export default function OpenPositions({ positions = [], loading = false, currentPrice }) {
  // Format price
  const fmtPrice = (p) => {
    if (!p && p !== 0) return '—'
    return '$' + Number(p).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
  }

  // Format time opened
  const formatTime = (ts) => {
    if (!ts) return '—'
    try {
      const date = typeof ts === 'number'
        ? new Date(ts > 1e12 ? ts : ts * 1000)
        : new Date(ts)
      return formatDistanceToNow(date, { addSuffix: false }) + ' ago'
    } catch {
      return String(ts)
    }
  }

  // Compute SL/TP marker position (0-100%)
  const computeMarkerPos = (entry, current, sl, tp) => {
    if (!sl || !tp || !entry || !current) return 50
    const range = tp - sl
    if (range <= 0) return 50
    const pos = ((current - sl) / range) * 100
    return Math.max(0, Math.min(100, pos))
  }

  // Check if price is within 1% of SL
  const isNearSL = (entry, current, sl) => {
    if (!sl || !current || !entry) return false
    const diff = Math.abs(current - sl) / entry
    return diff < 0.01
  }

  // Skeleton
  if (loading) {
    return (
      <div>
        <div className="card-title">Open Positions</div>
        {Array.from({ length: 2 }).map((_, i) => (
          <div key={i} className="position-card">
            <div className="skeleton skeleton-text" style={{ width: '60%' }}></div>
            <div className="skeleton skeleton-value" style={{ width: '40%', marginTop: 8 }}></div>
          </div>
        ))}
      </div>
    )
  }

  // Empty state
  if (!positions || positions.length === 0) {
    return (
      <div>
        <div className="card-title">Open Positions</div>
        <div className="empty-state">
          <Eye size={32} strokeWidth={1.5} />
          <p>No open positions</p>
          <p style={{ fontSize: 11, marginTop: 4 }}>Bot is watching the market…</p>
        </div>
      </div>
    )
  }

  return (
    <div>
      <div className="card-title">Open Positions ({positions.length})</div>

      {positions.map((pos, i) => {
        const cp = currentPrice || pos.current_price || pos.entry_price
        const nearSL = isNearSL(pos.entry_price, cp, pos.stop_loss)
        const markerPos = computeMarkerPos(pos.entry_price, cp, pos.stop_loss, pos.take_profit)

        // Recalculate unrealized P&L with live price if available
        let unrealized = pos.unrealized_pnl || 0
        let unrealizedPct = pos.unrealized_pnl_pct || 0
        if (currentPrice && pos.entry_price && pos.quantity) {
          if (pos.side?.toLowerCase() === 'buy') {
            unrealized = (currentPrice - pos.entry_price) * pos.quantity
          } else {
            unrealized = (pos.entry_price - currentPrice) * pos.quantity
          }
          const cost = pos.entry_price * pos.quantity
          unrealizedPct = cost > 0 ? (unrealized / cost) * 100 : 0
        }

        const pnlColor = unrealized >= 0 ? 'var(--emerald)' : 'var(--rose)'

        return (
          <div key={i} className={`position-card ${nearSL ? 'warning' : ''}`}>
            {/* Header */}
            <div className="position-header">
              <div className="position-header-left">
                <span>{pos.symbol || 'BTC/USDT'}</span>
                <span className={`badge ${pos.side?.toLowerCase()}`}>
                  {pos.side?.toUpperCase()}
                </span>
                {nearSL && (
                  <AlertTriangle size={14} style={{ color: 'var(--amber)' }} />
                )}
              </div>
              <div className="position-header-right">
                {formatTime(pos.entry_time)}
              </div>
            </div>

            {/* Price Grid */}
            <div className="position-prices">
              <div>
                <div className="price-label">Entry</div>
                <div className="price-value">{fmtPrice(pos.entry_price)}</div>
              </div>
              <div>
                <div className="price-label">Current</div>
                <div className="price-value" style={{ color: pnlColor }}>
                  {fmtPrice(cp)}
                </div>
              </div>
            </div>

            {/* Unrealized P&L */}
            <div className="position-pnl" style={{ color: pnlColor }}>
              {unrealized >= 0 ? '+' : ''}${unrealized.toFixed(2)}
              <span className="position-pnl-pct" style={{ color: pnlColor }}>
                ({unrealizedPct >= 0 ? '+' : ''}{unrealizedPct.toFixed(2)}%)
              </span>
            </div>

            {/* Qty */}
            <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 4 }}>
              Qty: <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--text-secondary)' }}>
                {Number(pos.quantity).toFixed(6)} BTC
              </span>
            </div>

            {/* SL/TP Progress Bar */}
            {(pos.stop_loss || pos.take_profit) && (
              <>
                <div className="sltp-bar">
                  <div
                    className="sltp-marker"
                    style={{ left: `${markerPos}%` }}
                  />
                </div>
                <div className="sltp-labels">
                  <span style={{ color: 'var(--rose)' }}>
                    SL: {pos.stop_loss ? fmtPrice(pos.stop_loss) : '—'}
                  </span>
                  <span style={{ color: 'var(--emerald)' }}>
                    TP: {pos.take_profit ? fmtPrice(pos.take_profit) : '—'}
                  </span>
                </div>
              </>
            )}
          </div>
        )
      })}
    </div>
  )
}
