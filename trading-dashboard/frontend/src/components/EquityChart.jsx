import {
  AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer, ReferenceLine
} from 'recharts'

/**
 * EquityChart — 30/90-day equity curve using Recharts.
 *
 * Props:
 *   data     — array of { timestamp, equity, pnl_cumulative }
 *   loading  — boolean
 *   height   — chart height in px (default 240)
 */
export default function EquityChart({ data = [], loading = false, height = 240 }) {
  if (loading) {
    return (
      <div className="skeleton" style={{ height, borderRadius: 'var(--radius-md)' }}></div>
    )
  }

  if (!data || data.length === 0) {
    return (
      <div className="empty-state" style={{ height, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        <p>No equity data yet — trades will appear here.</p>
      </div>
    )
  }

  const lastEquity = data[data.length - 1]?.equity || 1000
  const fillColor = lastEquity >= 1000 ? '#10b981' : '#f43f5e'
  const strokeColor = lastEquity >= 1000 ? '#10b981' : '#f43f5e'

  return (
    <ResponsiveContainer width="100%" height={height}>
      <AreaChart data={data} margin={{ top: 8, right: 8, left: 8, bottom: 0 }}>
        <defs>
          <linearGradient id="equityGradient" x1="0" y1="0" x2="0" y2="1">
            <stop offset="5%" stopColor={fillColor} stopOpacity={0.25} />
            <stop offset="95%" stopColor={fillColor} stopOpacity={0.02} />
          </linearGradient>
        </defs>
        <XAxis
          dataKey="timestamp"
          tick={{ fill: '#6b7280', fontSize: 11 }}
          tickLine={false}
          axisLine={{ stroke: '#2a2d3a' }}
          interval="preserveStartEnd"
        />
        <YAxis
          tick={{ fill: '#6b7280', fontSize: 11 }}
          tickLine={false}
          axisLine={false}
          tickFormatter={(v) => `$${v.toLocaleString()}`}
          width={70}
        />
        <Tooltip
          contentStyle={{
            background: '#1a1d2b',
            border: '1px solid #2a2d3a',
            borderRadius: 8,
            fontSize: 12,
            color: '#e8eaed',
          }}
          labelStyle={{ color: '#9ca3af' }}
          formatter={(value) => [`$${Number(value).toFixed(2)}`, 'Equity']}
        />
        <ReferenceLine
          y={1000}
          stroke="#6b7280"
          strokeDasharray="4 4"
          label={{ value: '$1,000', fill: '#6b7280', fontSize: 10, position: 'left' }}
        />
        <Area
          type="monotone"
          dataKey="equity"
          stroke={strokeColor}
          strokeWidth={2}
          fill="url(#equityGradient)"
          animationDuration={800}
        />
      </AreaChart>
    </ResponsiveContainer>
  )
}
