import { useState, useMemo } from 'react'
import { formatDistanceToNow } from 'date-fns'
import { TrendingUp, TrendingDown, Minus, Package } from 'lucide-react'

/**
 * OrdersTable — Sortable trade history with compact mode.
 *
 * Props:
 *   trades   — array of trade objects
 *   loading  — boolean
 *   compact  — boolean (hides Reason column, shrinks rows)
 */
export default function OrdersTable({ trades = [], loading = false, compact = false }) {
  const [sortKey, setSortKey] = useState('timestamp')
  const [sortDir, setSortDir] = useState('desc')

  // Sort handler
  const handleSort = (key) => {
    if (sortKey === key) {
      setSortDir(prev => prev === 'desc' ? 'asc' : 'desc')
    } else {
      setSortKey(key)
      setSortDir('desc')
    }
  }

  // Sorted data
  const sortedTrades = useMemo(() => {
    if (!trades || trades.length === 0) return []
    return [...trades].sort((a, b) => {
      let aVal = a[sortKey]
      let bVal = b[sortKey]
      // Numeric comparison for known numeric fields
      if (['price', 'quantity', 'pnl', 'id', 'timestamp'].includes(sortKey)) {
        aVal = Number(aVal) || 0
        bVal = Number(bVal) || 0
      }
      if (aVal < bVal) return sortDir === 'asc' ? -1 : 1
      if (aVal > bVal) return sortDir === 'asc' ? 1 : -1
      return 0
    })
  }, [trades, sortKey, sortDir])

  // Format timestamp to relative time
  const formatTime = (ts) => {
    if (!ts) return '—'
    try {
      // Handle both unix timestamps and ISO strings
      const date = typeof ts === 'number'
        ? new Date(ts > 1e12 ? ts : ts * 1000)
        : new Date(ts)
      return formatDistanceToNow(date, { addSuffix: true })
    } catch {
      return String(ts)
    }
  }

  // Sort indicator
  const SortIcon = ({ field }) => {
    const isActive = sortKey === field
    return (
      <span className="sort-icon">
        {isActive ? (sortDir === 'asc' ? '▲' : '▼') : '⇕'}
      </span>
    )
  }

  // Skeleton loading
  if (loading) {
    return (
      <table className={`data-table ${compact ? 'compact' : ''}`}>
        <thead>
          <tr>
            <th>Time</th><th>Symbol</th><th>Side</th>
            <th>Price</th><th>Qty</th><th>P&L</th><th>Status</th>
            {!compact && <th>Reason</th>}
          </tr>
        </thead>
        <tbody>
          {Array.from({ length: 5 }).map((_, i) => (
            <tr key={i}>
              {Array.from({ length: compact ? 7 : 8 }).map((_, j) => (
                <td key={j}>
                  <div className="skeleton skeleton-text" style={{ width: `${50 + Math.random() * 40}%` }}></div>
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    )
  }

  // Empty state
  if (!sortedTrades || sortedTrades.length === 0) {
    return (
      <div className="empty-state">
        <Package size={36} strokeWidth={1.5} />
        <p>No trades recorded yet</p>
        <p style={{ fontSize: 11, marginTop: 4 }}>The bot is watching the market...</p>
      </div>
    )
  }

  return (
    <table className={`data-table ${compact ? 'compact' : ''}`}>
      <thead>
        <tr>
          <th
            className={`sortable ${sortKey === 'timestamp' ? 'sorted' : ''}`}
            onClick={() => handleSort('timestamp')}
          >
            Time <SortIcon field="timestamp" />
          </th>
          <th>Symbol</th>
          <th
            className={`sortable ${sortKey === 'side' ? 'sorted' : ''}`}
            onClick={() => handleSort('side')}
          >
            Side <SortIcon field="side" />
          </th>
          <th
            className={`sortable ${sortKey === 'price' ? 'sorted' : ''}`}
            onClick={() => handleSort('price')}
          >
            Price <SortIcon field="price" />
          </th>
          <th>Qty</th>
          <th
            className={`sortable ${sortKey === 'pnl' ? 'sorted' : ''}`}
            onClick={() => handleSort('pnl')}
          >
            P&L <SortIcon field="pnl" />
          </th>
          <th>Status</th>
          {!compact && (
            <th
              className={`sortable ${sortKey === 'reason' ? 'sorted' : ''}`}
              onClick={() => handleSort('reason')}
            >
              Reason <SortIcon field="reason" />
            </th>
          )}
        </tr>
      </thead>
      <tbody>
        {sortedTrades.map((t, idx) => (
          <tr key={t.id || idx}>
            <td className="time-ago">{formatTime(t.timestamp)}</td>
            <td>{t.symbol || 'BTC/USDT'}</td>
            <td>
              <span className={`badge ${t.side?.toLowerCase()}`}>
                {t.side?.toUpperCase()}
              </span>
            </td>
            <td style={{ fontFamily: 'var(--font-mono)' }}>
              ${Number(t.price).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
            </td>
            <td style={{ fontFamily: 'var(--font-mono)', fontSize: compact ? 11 : 13 }}>
              {Number(t.quantity).toFixed(6)}
            </td>
            <td style={{ fontFamily: 'var(--font-mono)', fontWeight: 600 }}>
              {t.pnl != null ? (
                <span className={t.pnl > 0 ? 'pnl-positive' : t.pnl < 0 ? 'pnl-negative' : ''}>
                  {t.pnl > 0 && <TrendingUp size={12} style={{ marginRight: 3, verticalAlign: -1 }} />}
                  {t.pnl < 0 && <TrendingDown size={12} style={{ marginRight: 3, verticalAlign: -1 }} />}
                  ${Number(t.pnl).toFixed(2)}
                </span>
              ) : (
                <Minus size={14} style={{ color: 'var(--text-muted)' }} />
              )}
            </td>
            <td>
              <span style={{ fontSize: 11, color: t.status === 'filled' ? 'var(--emerald)' : 'var(--text-muted)' }}>
                {t.status || '—'}
              </span>
            </td>
            {!compact && (
              <td>
                {t.reason ? (
                  <span
                    className="tooltip-text"
                    data-tooltip={t.reason}
                    style={{ maxWidth: 150, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', display: 'inline-block' }}
                  >
                    {t.reason.length > 30 ? t.reason.slice(0, 30) + '…' : t.reason}
                  </span>
                ) : '—'}
              </td>
            )}
          </tr>
        ))}
      </tbody>
    </table>
  )
}
