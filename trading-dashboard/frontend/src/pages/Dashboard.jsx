import { useState, useEffect } from 'react'
import { TrendingUp, TrendingDown, DollarSign, BarChart2, Target } from 'lucide-react'
import { useOrderSummary, useEquityCurve, useDecisions, useOrders } from '../hooks/useApi'
import MetricCard from '../components/MetricCard'
import EquityChart from '../components/EquityChart'
import OrdersTable from '../components/OrdersTable'
import AgentDecisionsLog from '../components/AgentDecisionsLog'
import OpenPositions from '../components/OpenPositions'

export default function Dashboard({ wsData = {} }) {
  const { data: summary, loading: summaryLoading } = useOrderSummary()
  const { data: equityCurve, loading: curveLoading } = useEquityCurve(30)
  const { data: decisions, loading: decisionsLoading } = useDecisions(10)
  const { data: ordersData, loading: ordersLoading } = useOrders({ limit: 15, page: 1 })

  // Get positions from the REST API
  const { data: positions, loading: positionsLoading } = usePositions()

  return (
    <div>
      <h1 className="page-title">Dashboard</h1>

      {/* ── Row 1: Metric Cards ──────────────── */}
      <div className="metrics-grid">
        <MetricCard
          title="Total P&L"
          value={summary ? `$${summary.total_pnl?.toFixed(2)}` : undefined}
          subtitle={summary ? `${summary.total_trades} trades` : ''}
          valueColor={summary?.total_pnl > 0 ? 'positive' : summary?.total_pnl < 0 ? 'negative' : 'neutral'}
          icon={<DollarSign size={16} />}
        />
        <MetricCard
          title="Win Rate"
          value={summary ? `${summary.win_rate?.toFixed(1)}%` : undefined}
          subtitle={summary ? `${summary.winning_trades}W / ${summary.losing_trades}L` : ''}
          valueColor={summary?.win_rate > 50 ? 'positive' : 'negative'}
          icon={<Target size={16} />}
        />
        <MetricCard
          title="Active Positions"
          value={positions ? String(positions.length) : undefined}
          subtitle={positions && positions.length > 0
            ? `$${positions.reduce((s, p) => s + (p.unrealized_pnl || 0), 0).toFixed(2)} unrealized`
            : 'No open trades'
          }
          valueColor={positions?.length > 0 ? 'neutral' : 'neutral'}
          icon={<BarChart2 size={16} />}
        />
        <MetricCard
          title="Today's P&L"
          value={summary ? `$${summary.today_pnl?.toFixed(2)}` : undefined}
          subtitle={summary ? `${summary.today_trades} trades today` : ''}
          valueColor={summary?.today_pnl > 0 ? 'positive' : summary?.today_pnl < 0 ? 'negative' : 'neutral'}
          icon={summary?.today_pnl >= 0 ? <TrendingUp size={16} /> : <TrendingDown size={16} />}
        />
      </div>

      {/* ── Row 2: Equity Chart + Open Positions ── */}
      <div className="split-layout">
        <div className="card">
          <div className="card-title">Equity Curve (30 days)</div>
          <EquityChart data={equityCurve || []} loading={curveLoading} />
        </div>
        <div className="card">
          <OpenPositions
            positions={positions || []}
            loading={positionsLoading}
            currentPrice={wsData.price}
          />
        </div>
      </div>

      {/* ── Row 3: Recent Orders + Agent Decisions ── */}
      <div className="split-layout">
        <div className="card">
          <div className="card-title">Recent Trades</div>
          <OrdersTable
            trades={ordersData?.trades || []}
            loading={ordersLoading}
            compact
          />
        </div>
        <div className="card">
          <AgentDecisionsLog
            decisions={decisions || []}
            loading={decisionsLoading}
          />
        </div>
      </div>
    </div>
  )
}

/** Hook for fetching open positions */
function usePositions() {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    const fetchPositions = async () => {
      try {
        const resp = await fetch('/api/positions/open')
        if (resp.ok) {
          const json = await resp.json()
          setData(json)
        }
      } catch {
        // Silently fail
      } finally {
        setLoading(false)
      }
    }

    fetchPositions()
    const interval = setInterval(fetchPositions, 15000)
    return () => clearInterval(interval)
  }, [])

  return { data, loading }
}
