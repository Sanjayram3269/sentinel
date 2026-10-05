/**
 * Intelligence readouts.
 *
 * Deliberately conservative. SENTINEL's Phase 4 models may not be trained and
 * Phase 5 signals may be unavailable, so any value the backend did not supply
 * renders as "unavailable" rather than being backfilled with a plausible
 * number. A confident-looking 0.42 risk that came from nowhere would be worse
 * than an honest blank.
 */

import type { ReactNode } from 'react'
import { Brain, Gauge, Timer, TriangleAlert } from 'lucide-react'
import { Panel, StatusBadge, Unavailable } from '../ui/primitives'
import { formatRatio, formatSeconds, riskBand } from '../../lib/format'
import type { Band } from '../../lib/format'
import type { PlanReviewRead, RouteRead } from '../../types/api'

interface IntelligencePanelProps {
  review: PlanReviewRead | null
  selectedRoute: RouteRead | null
  /** True when the AI predictor actually produced a model-backed value. */
  modelBacked: boolean
}

export function IntelligencePanel({
  review,
  selectedRoute,
  modelBacked,
}: IntelligencePanelProps) {
  const eta = review?.route?.eta_seconds ?? selectedRoute?.estimated_duration_seconds ?? null
  // The backend sends the literal string "unavailable" where a number would go,
  // so only a real finite number counts as a measurement.
  const risk =
    toNumber(review?.route?.failure_risk) ?? selectedRoute?.risk_score ?? null
  const band = riskBand(risk)

  return (
    <Panel
      title="Intelligence"
      ariaLabel="Predictive intelligence readouts"
      actions={
        <StatusBadge
          label={modelBacked ? 'Model' : 'Baseline'}
          tone={modelBacked ? 'intel' : 'muted'}
          title={
            modelBacked
              ? 'A trained model produced these signals.'
              : 'No trained model backs these values; deterministic fallbacks are in use.'
          }
        />
      }
    >
      <div className="flex flex-col gap-2.5">
        <Readout
          icon={<Timer size={12} />}
          label="ETA"
          value={formatSeconds(eta)}
          tone="go"
          hint="Modelled travel time, not a guarantee"
        />
        <Readout
          icon={<Gauge size={12} />}
          label="Route risk"
          value={risk === null ? <Unavailable /> : bandLabel(band, risk)}
          tone={bandTone(band)}
          hint={
            risk === null
              ? 'No failure-risk estimate available for this route'
              : `risk index ${formatRatio(risk)}`
          }
        />
        <Readout
          icon={<Gauge size={12} />}
          label="Congestion"
          value={<Unavailable />}
          tone="muted"
          hint="No congestion signal available for this network"
        />
        <Readout
          icon={<TriangleAlert size={12} />}
          label="Hazard impact"
          value={<Unavailable />}
          tone="muted"
          hint="No hazard exposure recorded for this corridor"
        />
        <Readout
          icon={<Brain size={12} />}
          label="CLEARPATH"
          value={
            review?.simulation_evidence?.comparable
              ? `${(review.simulation_evidence.travel_time_improvement_percent ?? 0).toFixed(1)}%`
              : <Unavailable />
          }
          tone={review?.simulation_evidence?.comparable ? 'intel' : 'muted'}
          hint={
            review?.simulation_evidence?.comparable
              ? 'Measured in simulation'
              : 'No measured comparison for this route'
          }
        />

        <p className="border-t border-graphite-700 pt-2 text-[10px] leading-relaxed text-ink-400">
          Values shown are model outputs or deterministic fallbacks. SENTINEL
          does not report a prediction accuracy it has not measured.
        </p>
      </div>
    </Panel>
  )
}

function Readout({
  icon,
  label,
  value,
  tone,
  hint,
}: {
  icon: ReactNode
  label: string
  value: ReactNode
  tone: 'go' | 'intel' | 'muted' | 'warn' | 'emergency'
  hint: string
}) {
  const text =
    tone === 'go'
      ? 'text-go-400'
      : tone === 'intel'
        ? 'text-intel-300'
        : tone === 'warn'
          ? 'text-warn-400'
          : tone === 'emergency'
            ? 'text-emergency-400'
            : 'text-ink-400'

  return (
    <div className="flex items-center justify-between gap-3">
      <span className="flex items-center gap-1.5 text-[10px] uppercase tracking-[0.08em] text-ink-400">
        <span className={tone === 'muted' ? 'text-ink-400' : text}>{icon}</span>
        {label}
      </span>
      <span className="flex min-w-0 flex-col items-end">
        <span className={`stat-value text-sm ${text}`}>{value}</span>
        <span className="max-w-[18ch] truncate text-[10px] text-ink-400" title={hint}>
          {hint}
        </span>
      </span>
    </div>
  )
}

function bandLabel(band: Band, value: number): string {
  return `${band.toUpperCase()} ${value.toFixed(2)}`
}

function bandTone(band: Band): 'go' | 'warn' | 'emergency' | 'muted' {
  if (band === 'low') return 'go'
  if (band === 'medium') return 'warn'
  if (band === 'high') return 'emergency'
  return 'muted'
}

function toNumber(value: number | string | null | undefined): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}
