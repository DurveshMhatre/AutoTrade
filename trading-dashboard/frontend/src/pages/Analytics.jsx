import { TrendingUp, TrendingDown, Activity, Zap, AlertTriangle } from 'lucide-react'
import {
  useOrderSummary, useEquityCurve, useHourlyBreakdown,
  useWinRateTrend, useSharpe, useDrawdown, useStreaks, usePnlDistribution,
} from '../hooks/useApi'
import MetricCard from '../components/MetricCard'
import EquityChart from '../components/EquityChart'
import {
  BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, Cell,
  LineChart, Line, ReferenceLine, PieChart, Pie,
} from 'recharts'

export default function Analytics() {
  const { data: summary } = useOrderSummary()
  const { data: equityCurve, loading: curveLoading } = useEquityCurve(90)
  const { data: hourly } = useHourlyBreakdown()
  const { data: winTrend } = useWinRateTrend()
  const { data: sharpeData } = useSharpe()
  const { data: drawdownData } = useDrawdown()
  const { data: streakData } = useStreaks()
  const { data: pnlDist } = usePnlDistribution()

  const sharpe = sharpeData?.sharpe_ratio ?? null
  const maxDd = drawdownData?.max_drawdown_dollars ?? null
  const maxDdPct = drawdownData?.max_drawdown_pct ?? null

  // Streak display
  const streakLabel = streakData
    ? `${streakData.current_streak} ${streakData.current_type}${streakData.current_streak !== 1 ? 's' : ''}`
    : null

  return (
    <div>
      <h1 className="page-title">Analytics</h1>

      {/* ── Section 1: Performance Summary ─── */}
      <div className="metrics-grid" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))' }}>
        <MetricCard
          title="Sharpe Ratio"
          value={sharpe !== null ? sharpe.toFixed(2) : undefined}
          subtitle={sharpe !== null ? (sharpe > 1 ? '> 1.0 is good' : 'Below 1.0') : ''}
          valueColor={sharpe > 1 ? 'positive' : sharpe > 0 ? 'neutral' : 'negative'}
          icon={<Activity size={16} />}
        />
        <MetricCard
          title="Max Drawdown"
          value={maxDd !== null ? `$${maxDd.toFixed(2)}` : undefined}
          subtitle={maxDdPct !== null ? `${maxDdPct.toFixed(1)}% of capital` : ''}
          valueColor="negative"
          icon={<TrendingDown size={16} />}
        />
        <MetricCard
          title="Profit Factor"
          value={summary ? summary.profit_factor?.toFixed(2) : undefined}
          subtitle={
            summary?.profit_factor > 1.5 ? 'Strong edge' :
            summary?.profit_factor > 1.0 ? 'Marginal edge' : 'No edge'
          }
          valueColor={
            summary?.profit_factor > 1.5 ? 'positive' :
            summary?.profit_factor > 1.0 ? 'neutral' : 'negative'
          }
          icon={<Zap size={16} />}
        />
        <MetricCard
          title="Best Trade"
          value={summary ? `$${summary.best_trade?.toFixed(2)}` : undefined}
          valueColor="positive"
          icon={<TrendingUp size={16} />}
        />
        <MetricCard
          title="Worst Trade"
          value={summary ? `$${summary.worst_trade?.toFixed(2)}` : undefined}
          valueColor="negative"
          icon={<TrendingDown size={16} />}
        />
        <MetricCard
          title="Win/Loss Streak"
          value={streakLabel}
          subtitle={streakData ? `Max win: ${streakData.max_win_streak} | Max loss: ${streakData.max_loss_streak}` : ''}
          valueColor={streakData?.current_type === 'win' ? 'positive' : streakData?.current_type === 'loss' ? 'negative' : 'neutral'}
          icon={<AlertTriangle size={16} />}
        />
      </div>

      {/* ── Section 2: Equity Curve (larger) ── */}
      <div className="card" style={{ marginBottom: 24 }}>
        <div className="card-title">Equity Curve (90 days)</div>
        <EquityChart data={equityCurve || []} loading={curveLoading} height={360} />
      </div>

      {/* ── Section 3: Hourly + P&L Distribution ─ */}
      <div className="split-layout">
        <div className="card">
          <div className="card-title">Which Hours Make Money? (UTC)</div>
          {hourly && hourly.some(h => h.trade_count > 0) ? (
            <ResponsiveContainer width="100%" height={240}>
              <BarChart data={hourly} margin={{ top: 8, right: 8, left: 8, bottom: 0 }}>
                <XAxis
                  dataKey="hour"
                  tick={{ fill: '#6b7280', fontSize: 10 }}
                  tickFormatter={(v) => `${v}h`}
                />
                <YAxis
                  tick={{ fill: '#6b7280', fontSize: 10 }}
                  tickFormatter={(v) => `$${v}`}
                  width={50}
                />
                <Tooltip
                  contentStyle={{
                    background: '#1a1d2b', border: '1px solid #2a2d3a',
                    borderRadius: 8, fontSize: 12, color: '#e8eaed',
                  }}
                  formatter={(v, name) => [`$${Number(v).toFixed(2)}`, 'Avg P&L']}
                  labelFormatter={(v) => `${v}:00 UTC`}
                />
                <Bar dataKey="avg_pnl" radius={[3, 3, 0, 0]}>
                  {hourly.map((entry, i) => (
                    <Cell
                      key={i}
                      fill={entry.avg_pnl >= 0 ? '#10b981' : '#f43f5e'}
                      fillOpacity={0.8}
                    />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          ) : (
            <div className="empty-state" style={{ height: 240, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
              <p>No hourly data yet</p>
            </div>
          )}
        </div>

        <div className="card">
          <div className="card-title">P&L Distribution</div>
          {pnlDist && pnlDist.length > 0 ? (
            <ResponsiveContainer width="100%" height={240}>
              <BarChart
                data={pnlDist}
                margin={{ top: 8, right: 8, left: 8, bottom: 0 }}
              >
                <XAxis
                  dataKey="bucket_start"
                  tick={{ fill: '#6b7280', fontSize: 10 }}
                  tickFormatter={(v) => `$${v}`}
                />
                <YAxis
                  tick={{ fill: '#6b7280', fontSize: 10 }}
                  width={30}
                />
                <Tooltip
                  contentStyle={{
                    background: '#1a1d2b', border: '1px solid #2a2d3a',
                    borderRadius: 8, fontSize: 12, color: '#e8eaed',
                  }}
                  formatter={(v) => [v, 'Trades']}
                  labelFormatter={(v) => `$${v} to $${v + 5}`}
                />
                <ReferenceLine x={0} stroke="#6b7280" strokeDasharray="4 4" />
                <Bar dataKey="count" radius={[3, 3, 0, 0]}>
                  {pnlDist.map((entry, i) => (
                    <Cell
                      key={i}
                      fill={entry.is_positive ? '#10b981' : '#f43f5e'}
                      fillOpacity={0.8}
                    />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          ) : (
            <div className="empty-state" style={{ height: 240, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
              <p>No P&L distribution data yet</p>
            </div>
          )}
        </div>
      </div>

      {/* ── Section 4: Win Rate Trend ─────── */}
      <div className="card" style={{ marginTop: 24 }}>
        <div className="card-title">Win Rate Trend (30 days)</div>
        {winTrend && winTrend.length > 0 ? (
          <ResponsiveContainer width="100%" height={220}>
            <LineChart data={[...winTrend].reverse()} margin={{ top: 8, right: 8, left: 8, bottom: 0 }}>
              <XAxis
                dataKey="date"
                tick={{ fill: '#6b7280', fontSize: 10 }}
                interval="preserveStartEnd"
              />
              <YAxis
                tick={{ fill: '#6b7280', fontSize: 10 }}
                domain={[0, 100]}
                tickFormatter={(v) => `${v}%`}
                width={45}
              />
              <Tooltip
                contentStyle={{
                  background: '#1a1d2b', border: '1px solid #2a2d3a',
                  borderRadius: 8, fontSize: 12, color: '#e8eaed',
                }}
                formatter={(v) => [`${Number(v).toFixed(1)}%`, 'Win Rate']}
              />
              <ReferenceLine y={50} stroke="#f59e0b" strokeDasharray="4 4" label={{ value: '50%', fill: '#f59e0b', fontSize: 10 }} />
              <Line
                type="monotone"
                dataKey="win_rate"
                stroke="#38bdf8"
                strokeWidth={2}
                dot={{ r: 3, fill: '#38bdf8' }}
                activeDot={{ r: 5 }}
              />
            </LineChart>
          </ResponsiveContainer>
        ) : (
          <div className="empty-state" style={{ height: 220, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            <p>No win rate data yet</p>
          </div>
        )}
      </div>
    </div>
  )
}
