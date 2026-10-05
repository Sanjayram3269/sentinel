/**
 * Simulations.
 *
 * This is where the CLEARPATH boundary is most likely to be misread, so the page
 * leads with the comparison and follows with the raw run log. When the adapter
 * is not configured the failure is shown as a first-class operational fact —
 * that is the truth of a system whose evidence pipeline depends on optional
 * simulation infrastructure, and hiding it would be the dishonest choice.
 */

import { useMemo } from 'react'
import { Activity, FlaskConical, ShieldAlert } from 'lucide-react'
import { ClearpathPanel } from '../components/simulation/ClearpathPanel'
import { DemoBadge, EmptyState, Panel, StatusBadge, Unavailable } from '../components/ui/primitives'
import { useSimulations } from '../hooks/useSimulations'
import { comparisonFromReview } from '../lib/clearpath'
import { formatDateTime, formatSeconds } from '../lib/format'
import type { SimulationRunRead, SimulationStatus } from '../types/api'
import type { AppData } from '../hooks/useAppData'

const STATUS_TONE = (status: SimulationStatus) => {
  switch (status) {
    case 'COMPLETED':
      return 'go' as const
    case 'FAILED':
      return 'alert' as const
    case 'RUNNING':
    case 'PENDING':
      return 'warn' as const
    default:
      return 'muted' as const
  }
}

export function Simulations({ data }: { data: AppData }) {
  const { runs, loading, error, refresh } = useSimulations(data.missionId)

  const comparison = comparisonFromReview(data.review?.simulation_evidence)
  const failedRuns = runs.filter((run) => run.status === 'FAILED')
  const distinctErrors = useMemo(
    () =>
      Array.from(
        new Set(
          runs
            .map((run) => run.error_message)
            .filter((message): message is string => typeof message === 'string'),
        ),
      ),
    [runs],
  )

  return (
    <div className="flex flex-col gap-3 p-3">
      <header className="panel flex flex-wrap items-center justify-between gap-2 px-3 py-2.5">
        <div className="flex min-w-0 flex-col gap-0.5">
          <h1 className="text-sm font-semibold uppercase tracking-[0.1em] text-ink-100">
            Simulations
          </h1>
          <p className="text-[11px] text-ink-400">
            CLEARPATH evidence is produced inside a SUMO/TraCI digital twin. It is
            advisory and simulation-only.
          </p>
        </div>
        <div className="flex items-center gap-1.5">
          {data.demo && <DemoBadge reason={data.demoReason ?? 'Backend unreachable.'} />}
          <StatusBadge
            label={`${runs.length} run${runs.length === 1 ? '' : 's'}`}
            tone={failedRuns.length === runs.length && runs.length > 0 ? 'warn' : 'neutral'}
          />
        </div>
      </header>

      <div className="grid grid-cols-1 gap-3 xl:grid-cols-[minmax(300px,380px)_minmax(0,1fr)]">
        <div className="flex min-w-0 flex-col gap-3">
          <ClearpathPanel
            comparison={comparison}
            noEvidenceReason={
              data.review?.simulation_evidence.reason ??
              data.review?.simulation_evidence.summary ??
              null
            }
            evidenceStatus={data.review?.simulation_evidence.evidence_status ?? null}
            onOpenSimulation={() => undefined}
            demo={data.demo}
          />

          <Panel title="Scope Notice" ariaLabel="Simulation scope notice">
            <ul className="flex flex-col gap-1.5">
              {[
                'CLEARPATH requests signal priority changes inside the simulator only.',
                'No real traffic signal, junction or vehicle is actuated by SENTINEL.',
                'Results describe one seeded run; they are evidence, not a guarantee.',
                'A plan may still be approved without any measured evidence — the approval record says so.',
              ].map((line) => (
                <li key={line} className="flex gap-1.5 text-[10px] leading-relaxed text-ink-400">
                  <span aria-hidden="true" className="mt-1 h-1 w-1 shrink-0 rounded-full bg-intel-400" />
                  <span>{line}</span>
                </li>
              ))}
            </ul>
          </Panel>
        </div>

        <div className="flex min-w-0 flex-col gap-3">
          {distinctErrors.length > 0 && (
            <div className="panel border-alert-500/40 px-3 py-2.5">
              <p className="flex items-center gap-1.5 text-[11px] font-bold uppercase tracking-[0.08em] text-alert-400">
                <ShieldAlert size={13} />
                Simulator reports
              </p>
              <ul className="mt-1.5 flex flex-col gap-1">
                {distinctErrors.map((message) => (
                  <li key={message} className="text-[10px] leading-relaxed text-ink-300">
                    {message}
                  </li>
                ))}
              </ul>
              <p className="mt-1.5 text-[10px] leading-relaxed text-ink-400">
                These are backend-side configuration findings, not UI failures. The
                plan's review package reports no evidence as a result.
              </p>
            </div>
          )}

          <Panel
            title="Run History"
            ariaLabel="Simulation run history"
            actions={
              <button
                type="button"
                onClick={refresh}
                className="rounded border border-graphite-600 px-2 py-1 text-[10px] uppercase tracking-[0.08em] text-ink-300 transition-colors hover:border-intel-500 hover:text-intel-300"
              >
                Refresh
              </button>
            }
          >
            {loading ? (
              <EmptyState title="Loading runs…" icon={<Activity size={18} />} />
            ) : error !== null ? (
              <EmptyState
                title="Run history unavailable"
                detail={error}
                icon={<FlaskConical size={18} />}
              />
            ) : runs.length === 0 ? (
              <EmptyState
                title="No simulation runs"
                detail="No baseline or CLEARPATH pair has been executed for this mission."
                icon={<FlaskConical size={18} />}
              />
            ) : (
              <ul className="flex flex-col gap-1.5">
                {runs.map((run) => (
                  <RunRow key={run.simulation_id} run={run} />
                ))}
              </ul>
            )}
          </Panel>
        </div>
      </div>
    </div>
  )
}

function RunRow({ run }: { run: SimulationRunRead }) {
  const metrics = run.metrics
  return (
    <li className="rounded border border-graphite-700 bg-graphite-900/50 px-2.5 py-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="flex items-center gap-2">
          <span className="text-[11px] font-bold uppercase tracking-[0.1em] text-ink-100">
            {run.mode ?? 'UNKNOWN'}
          </span>
          <StatusBadge
            label={run.status}
            tone={STATUS_TONE(run.status)}
            live={run.status === 'RUNNING'}
          />
          {run.simulation_only && (
            <span className="text-[10px] uppercase tracking-[0.08em] text-warn-400">
              simulation only
            </span>
          )}
        </span>
        <time dateTime={run.created_at} className="font-mono text-[10px] text-ink-400">
          {formatDateTime(run.created_at)}
        </time>
      </div>

      {run.status === 'FAILED' ? (
        <p className="mt-1.5 flex items-start gap-1.5 text-[10px] leading-relaxed text-alert-400">
          <ShieldAlert size={11} className="mt-0.5 shrink-0" />
          <span>
            {run.error_message ?? 'The run failed without a message.'}
            {run.error_code !== null && (
              <span className="ml-1 font-mono text-ink-400">{run.error_code}</span>
            )}
          </span>
        </p>
      ) : (
        <dl className="mt-1.5 grid grid-cols-2 gap-x-3 gap-y-1 sm:grid-cols-4">
          <Metric
            label="travel time"
            value={formatSeconds(metrics?.emergency_vehicle_travel_time_seconds ?? null)}
          />
          <Metric
            label="stopped"
            value={formatSeconds(metrics?.stopped_time_seconds ?? null)}
          />
          <Metric
            label="stops"
            value={
              metrics?.number_of_stops === null || metrics?.number_of_stops === undefined
                ? null
                : String(metrics.number_of_stops)
            }
          />
          <Metric
            label="completed"
            value={
              metrics?.route_completed === null || metrics?.route_completed === undefined
                ? null
                : metrics.route_completed
                  ? 'yes'
                  : 'no'
            }
          />
        </dl>
      )}

      <p className="mt-1.5 flex flex-wrap gap-x-3 gap-y-0.5 text-[10px] text-ink-400">
        <span>simulator: {run.simulator}</span>
        <span>network: {run.network_id ?? <Unavailable />}</span>
        <span>seed: {run.seed ?? <Unavailable />}</span>
        <span className="font-mono">id {run.simulation_id.slice(0, 8)}</span>
      </p>
    </li>
  )
}

function Metric({ label, value }: { label: string; value: string | null }) {
  return (
    <div className="flex flex-col gap-0.5">
      <dt className="text-[10px] uppercase tracking-[0.06em] text-ink-400">{label}</dt>
      <dd className="stat-value text-xs text-ink-100">
        {value === null ? <Unavailable /> : value}
      </dd>
    </div>
  )
}
