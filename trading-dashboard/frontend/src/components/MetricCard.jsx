/**
 * MetricCard — displays a single KPI value with label and optional subtitle.
 *
 * Props:
 *   title       — small muted label
 *   value       — large number (rendered as-is, pass formatted string)
 *   subtitle    — secondary text below value
 *   valueColor  — 'positive' | 'negative' | 'neutral'
 *   icon        — optional Lucide icon element
 */
export default function MetricCard({ title, value, subtitle, valueColor = 'neutral', icon }) {
  if (value === undefined || value === null) {
    // Skeleton loading
    return (
      <div className="metric-card">
        <div className="metric-label">{title}</div>
        <div className="skeleton skeleton-value" style={{ width: '60%' }}></div>
        <div className="skeleton skeleton-text" style={{ width: '40%', marginTop: 8 }}></div>
      </div>
    )
  }

  return (
    <div className="metric-card">
      <div className="metric-label">
        {icon}
        {title}
      </div>
      <div className={`metric-value ${valueColor}`}>
        {value}
      </div>
      {subtitle && (
        <div className="metric-subtitle">{subtitle}</div>
      )}
    </div>
  )
}
