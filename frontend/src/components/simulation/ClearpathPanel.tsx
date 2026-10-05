/**
 * CLEARPATH simulation panel.
 *
 * This is the most visually load-bearing panel in the app, and the most easily
 * abused one. Two rules are enforced here at the component level rather than by
 * convention:
 *
 * 1. A measured baseline-vs-CLEARPATH comparison is only ever shown when the
 *    backend actually returned comparable evidence. When it did not, the panel
 *    says so in the backend's own words instead of borrowing numbers.
 * 2. Every figure is framed as simulation output inside a SUMO/TraCI digital
 *    twin. Nothing here controls, or authorises control of, any real traffic
 *    signal or any real infrastructure.
 *
 * The comparison parsers live in `lib/clearpath.ts`; this file only renders.
 */

import type { ReactNode } from 'react'
import { Beaker, FlaskConical, ShieldAlert, TrendingDown } from 'lucide-react'
import { Panel, StatusBadge, Unavailable } from '../ui/primitives'
import { demoComparison, formatDelta, type ClearpathComparison } from '../../lib/clearpath'
import { formatSeconds } from '../../lib/format'

interface ClearpathPanelProps {
  /** A live measured pair, when one exists for this plan. */
  comparison: ClearpathComparison | null
  /** Why there is no live measurement. Shown verbatim beside the fallback. */
  noEvidenceReason: string | null
  evidenceStatus: string | null
  onOpenSimulation?: () => void
  /** True when the whole dashboard is already demo state. */
  demo?: boolean
}

export function ClearpathPanel({
  comparison,
  noEvidenceReason,
  evidenceStatus,
  onOpenSimulation,
  demo = false,
}: ClearpathPanelProps) {
  // A live pair always wins. The reference figures only fill a genuine gap.
  const resolved = comparison ?? demoComparison()
  const isDemo = resolved.provenance === 'demo'
  const anyFailed =
    resolved.baseline.status === 'FAILED' || resolved.clearpath.status === 'FAILED'
  const failureMessage =
    resolved.baseline.errorMessage ?? resolved.clearpath.errorMessage
  const failureCode = resolved.baseline.errorCode ?? resolved.clearpath.errorCode

  return (
    <Panel
      title="CLEARPATH Simulation"
      ariaLabel="CLEARPATH simulation comparison"
      className="border-intel-600/60"
      actions={
        <>
          {isDemo && (
            <span
              title="These figures come from a committed verification run on a different, synthetic network. They are not a measurement of this mission."
              className="inline-flex items-center gap-1 rounded border border-warn-500/70 bg-warn-500/15 px-1.5 py-0.5 text-[10px] font-bold uppercase tracking-[0.1em] text-warn-400"
            >
              Demo data
            </span>
          )}
          <StatusBadge
            label={isDemo ? 'REFERENCE RUN' : (evidenceStatus ?? 'NO EVIDENCE')}
            tone={isDemo ? 'warn' : 'intel'}
            live={!isDemo}
          />
        </>
      }
    >
      <div className="flex flex-col gap-2.5">
        {anyFailed && (
          <div className="flex items-start gap-2 rounded border border-alert-500/50 bg-alert-500/10 px-2.5 py-2">
            <ShieldAlert size={13} className="mt-0.5 shrink-0 text-alert-400" />
            <div className="flex min-w-0 flex-col gap-0.5">
              <span className="text-[10px] font-bold uppercase tracking-[0.08em] text-alert-400">
                Simulator unavailable
              </span>
              <span className="text-[10px] leading-relaxed text-ink-300">
                {failureMessage ?? 'The SUMO adapter did not complete the pair.'}
              </span>
              {failureCode !== null && (
                <span className="font-mono text-[10px] text-ink-400">{failureCode}</span>
              )}
            </div>
          </div>
        )}

        <div className="grid grid-cols-2 gap-2">
          <Arm label="Baseline" arm={resolved.baseline} tone="muted" />
          <Arm label="CLEARPATH" arm={resolved.clearpath} tone="intel" />
        </div>

        <div className="flex items-center justify-between gap-3 rounded border border-intel-600/50 bg-intel-600/10 px-2.5 py-2">
          <span className="flex items-center gap-1.5">
            <TrendingDown size={13} className="text-intel-400" />
            <span className="label-caps">Result</span>
          </span>
          <span className="flex items-baseline gap-2">
            <span className="stat-value text-2xl text-intel-300">
              {resolved.improvementPercent === null ? (
                <Unavailable />
              ) : (
                `${resolved.improvementPercent.toFixed(2)}%`
              )}
            </span>
            <span className="text-[10px] text-ink-400">emergency travel time</span>
          </span>
        </div>

        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[10px] text-ink-400">
          <span className="inline-flex items-center gap-1">
            <FlaskConical size={10} /> seed {resolved.seed ?? <Unavailable />}
          </span>
          <span>network: {resolved.network ?? <Unavailable />}</span>
          {resolved.stoppedDelta !== null && (
            <span>stopped time {formatDelta(resolved.stoppedDelta)}</span>
          )}
          {resolved.stopsDelta !== null && (
            <span>stops {formatDelta(resolved.stopsDelta, '')}</span>
          )}
        </div>

        {noEvidenceReason !== null && (
          <p className="rounded border border-graphite-700 bg-graphite-900/60 px-2.5 py-2 text-[10px] leading-relaxed text-ink-300">
            <span className="font-semibold text-ink-200">
              No live measurement for this plan.{' '}
            </span>
            {noEvidenceReason}
          </p>
        )}

        <div className="flex flex-col gap-1.5 border-t border-graphite-700 pt-2">
          <span className="inline-flex items-center gap-1.5 text-[10px] font-bold uppercase tracking-[0.1em] text-warn-400">
            <Beaker size={11} />
            Simulation-only recommendation
          </span>
          <p className="text-[10px] leading-relaxed text-ink-400">
            CLEARPATH runs inside a SUMO/TraCI digital twin. It does not control
            a real traffic signal and authorises no physical action. Figures
            describe one simulated run under one seed, not a guarantee.
          </p>
          {onOpenSimulation !== undefined && (
            <div>
              <button
                type="button"
                onClick={onOpenSimulation}
                className="inline-flex items-center gap-1.5 rounded border border-intel-600 bg-intel-600/10 px-2.5 py-1.5 text-[11px] font-semibold uppercase tracking-[0.07em] text-intel-300 transition-colors hover:bg-intel-600/20"
              >
                <FlaskConical size={12} />
                View simulation detail
              </button>
            </div>
          )}
        </div>

        {demo && (
          <p className="border-t border-graphite-700 pt-2 text-[10px] leading-relaxed text-ink-400">
            Backend unreachable. Showing the recorded reference run, clearly
            separated from any live measurement.
          </p>
        )}
      </div>
    </Panel>
  )
}

function Arm({
  label,
  arm,
  tone,
}: {
  label: string
  arm: ClearpathComparison['baseline']
  tone: 'muted' | 'intel'
}) {
  const accent = tone === 'intel' ? 'border-intel-600/50' : 'border-graphite-700'
  const valueClass = tone === 'intel' ? 'text-intel-300' : 'text-ink-200'
  return (
    <div className={`rounded border ${accent} bg-graphite-900/60 px-2.5 py-2`}>
      <span className="label-caps">{label}</span>
      <dl className="mt-1.5 flex flex-col gap-1">
        <Line
          label="travel time"
          value={formatSeconds(arm.travelTimeSeconds)}
          valueClass={valueClass}
        />
        <Line
          label="stopped"
          value={formatSeconds(arm.stoppedTimeSeconds)}
          valueClass={valueClass}
        />
        <Line
          label="stops"
          value={arm.stops === null ? <Unavailable /> : String(arm.stops)}
          valueClass={valueClass}
        />
        <Line
          label="completed"
          value={
            arm.routeCompleted === null ? (
              <Unavailable />
            ) : arm.routeCompleted ? (
              'yes'
            ) : (
              'no'
            )
          }
          valueClass={valueClass}
        />
      </dl>
    </div>
  )
}

function Line({
  label,
  value,
  valueClass,
}: {
  label: string
  value: ReactNode
  valueClass: string
}) {
  return (
    <div className="flex items-baseline justify-between gap-2">
      <dt className="text-[10px] uppercase tracking-[0.06em] text-ink-400">{label}</dt>
      <dd className={`stat-value text-xs ${valueClass}`}>{value}</dd>
    </div>
  )
}
