import { useState, useEffect, useRef, useCallback } from 'react'

const API_BASE = '/api'

/**
 * Generic fetch hook with auto-refresh.
 */
function useApi(endpoint, refreshInterval = 30000) {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const fetchData = useCallback(async () => {
    try {
      const resp = await fetch(`${API_BASE}${endpoint}`)
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
      const json = await resp.json()
      setData(json)
      setError(null)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }, [endpoint])

  useEffect(() => {
    fetchData()
    if (refreshInterval > 0) {
      const interval = setInterval(fetchData, refreshInterval)
      return () => clearInterval(interval)
    }
  }, [fetchData, refreshInterval])

  return { data, loading, error, refresh: fetchData }
}

/** Orders with pagination */
export function useOrders(params = {}) {
  const query = new URLSearchParams(params).toString()
  return useApi(`/orders?${query}`)
}

/** Order summary stats */
export function useOrderSummary() {
  return useApi('/orders/summary', 30000)
}

/** Equity curve */
export function useEquityCurve(days = 30) {
  return useApi(`/performance/equity-curve?days=${days}`)
}

/** Hourly performance breakdown */
export function useHourlyBreakdown() {
  return useApi('/performance/by-hour')
}

/** Full dashboard metrics */
export function useDashboardMetrics() {
  return useApi('/performance/metrics', 15000)
}

/** Recent agent decisions */
export function useDecisions(limit = 20) {
  return useApi(`/decisions/recent?limit=${limit}`, 15000)
}

/** Win rate trend */
export function useWinRateTrend() {
  return useApi('/performance/win-rate-trend')
}

/** Sharpe ratio */
export function useSharpe() {
  return useApi('/performance/sharpe', 60000)
}

/** Max drawdown */
export function useDrawdown() {
  return useApi('/performance/drawdown', 60000)
}

/** Win/loss streaks */
export function useStreaks() {
  return useApi('/performance/streaks', 30000)
}

/** P&L distribution histogram */
export function usePnlDistribution() {
  return useApi('/performance/pnl-distribution', 60000)
}

export default useApi
