import { useState, useEffect, useCallback } from 'react'
import { Download, Filter } from 'lucide-react'
import OrdersTable from '../components/OrdersTable'

export default function Orders() {
  const [page, setPage] = useState(1)
  const [sideFilter, setSideFilter] = useState('all')
  const [rangeFilter, setRangeFilter] = useState('all')
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const limit = 50

  // Fetch orders with current filters
  const fetchOrders = useCallback(async () => {
    setLoading(true)
    try {
      const params = new URLSearchParams({ limit: String(limit), page: String(page) })
      if (sideFilter !== 'all') params.set('side', sideFilter)

      const resp = await fetch(`/api/orders?${params}`)
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
      const json = await resp.json()
      setData(json)
      setError(null)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }, [page, sideFilter])

  // Re-fetch when page or side filter changes
  useEffect(() => {
    fetchOrders()
  }, [fetchOrders])

  // CSV Export
  const exportCSV = () => {
    if (!data?.trades?.length) return
    const headers = ['ID', 'Timestamp', 'Symbol', 'Side', 'Price', 'Quantity', 'PnL', 'Status', 'Reason']
    const rows = data.trades.map(t => [
      t.id, t.timestamp, t.symbol, t.side,
      t.price, t.quantity, t.pnl ?? '', t.status, t.reason || ''
    ])

    const csv = [
      headers.join(','),
      ...rows.map(r => r.map(cell => `"${String(cell).replace(/"/g, '""')}"`).join(','))
    ].join('\n')

    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `trades_export_${new Date().toISOString().slice(0, 10)}.csv`
    a.click()
    URL.revokeObjectURL(url)
  }

  // Compute filtered P&L sum
  const filteredPnl = data?.trades?.reduce((sum, t) => sum + (t.pnl || 0), 0) || 0

  const totalPages = data?.pages || 1

  return (
    <div>
      <h1 className="page-title">Order History</h1>

      {/* ── Filter Bar ──────────────────────── */}
      <div className="filter-bar">
        <div className="filter-group">
          <Filter size={14} style={{ color: 'var(--text-muted)' }} />
          <span className="filter-label">Side</span>
          {['all', 'buy', 'sell'].map(s => (
            <button
              key={s}
              className={`filter-pill ${sideFilter === s ? 'active' : ''}`}
              onClick={() => { setSideFilter(s); setPage(1) }}
            >
              {s === 'all' ? 'All' : s.toUpperCase()}
            </button>
          ))}
        </div>

        <div className="filter-group">
          <span className="filter-label">Period</span>
          {[
            { key: 'all', label: 'All Time' },
            { key: '7d', label: '7 Days' },
            { key: '30d', label: '30 Days' },
            { key: '90d', label: '90 Days' },
          ].map(({ key, label }) => (
            <button
              key={key}
              className={`filter-pill ${rangeFilter === key ? 'active' : ''}`}
              onClick={() => setRangeFilter(key)}
            >
              {label}
            </button>
          ))}
        </div>

        <div style={{ marginLeft: 'auto' }}>
          <button className="btn" onClick={exportCSV} disabled={!data?.trades?.length}>
            <Download size={14} />
            Export CSV
          </button>
        </div>
      </div>

      {/* ── Summary Bar ─────────────────────── */}
      <div className="summary-bar">
        <div className="summary-stat">
          Showing <strong>{data?.trades?.length || 0}</strong> of <strong>{data?.total || 0}</strong> trades
        </div>
        <div className="summary-stat">
          Filtered P&L:&nbsp;
          <strong className={filteredPnl > 0 ? 'pnl-positive' : filteredPnl < 0 ? 'pnl-negative' : ''}>
            ${filteredPnl.toFixed(2)}
          </strong>
        </div>
      </div>

      {/* ── Table ───────────────────────────── */}
      <div className="card">
        {error ? (
          <div className="empty-state"><p>Failed to load: {error}</p></div>
        ) : (
          <OrdersTable
            trades={data?.trades || []}
            loading={loading}
            compact={false}
          />
        )}
      </div>

      {/* ── Pagination ──────────────────────── */}
      {totalPages > 1 && (
        <div className="pagination">
          <button
            className="page-btn"
            onClick={() => setPage(p => Math.max(1, p - 1))}
            disabled={page <= 1}
          >
            ‹
          </button>

          {Array.from({ length: Math.min(totalPages, 7) }).map((_, i) => {
            let pageNum
            if (totalPages <= 7) {
              pageNum = i + 1
            } else if (i < 3) {
              pageNum = i + 1
            } else if (i === 3) {
              pageNum = Math.max(4, Math.min(page, totalPages - 3))
            } else {
              pageNum = totalPages - (6 - i)
            }

            return (
              <button
                key={pageNum}
                className={`page-btn ${page === pageNum ? 'active' : ''}`}
                onClick={() => setPage(pageNum)}
              >
                {pageNum}
              </button>
            )
          })}

          <span className="pagination-info">
            Page {page} of {totalPages}
          </span>

          <button
            className="page-btn"
            onClick={() => setPage(p => Math.min(totalPages, p + 1))}
            disabled={page >= totalPages}
          >
            ›
          </button>
        </div>
      )}
    </div>
  )
}
