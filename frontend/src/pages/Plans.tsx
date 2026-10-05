/**
 * Plans.
 *
 * The reviewer surface. Shows the plan exactly as the backend assembles it for
 * review — score with its caveat, evidence status, network checksum agreement,
 * warnings and limitations — and puts the approval controls here rather than
 * only on the dashboard. Nothing on this page can approve itself.
 */

import { useMemo } from 'react'
import {
  AlertTriangle,
  FileCheck2,
  Fingerprint,
  Network,
  ScrollText,
  Target,
} from 'lucide-react'
import { ApprovalPanel } from '../components/approval/ApprovalPanel'
import { ProgressStrip } from '../components/layout/ProgressStrip'
import {
  DemoBadge,
  EmptyState,
  Metric,
  Panel,
  StatusBadge,
  Unavailable,
} from '../components/ui/primitives'
import { useApprovalActions } from '../hooks/useApprovalActions'
import { formatDateTime, formatDistance, formatPercent, formatSeconds } from '../lib/format'
import { planStatusLabel, planStatusTone } from '../lib/status'
import type { AppData } from '../hooks/useAppData'
import type { RouteId } from '../hooks/useHashRoute'

export function Plans({ data, navigate }: { data: AppData; navigate: (id: RouteId) => void }) {
  const actions = useApprovalActions()
  const review = data.review

  const alternatives = useMemo(() => review?.route.alternatives ?? [], [review])

  if (!review) {
    return (
      <div className="p-3">
        <Panel title="Plans" ariaLabel="Plan review">
          <EmptyState
            title="No review package available"
            detail={
              data.demoReason ??
              'The backend did not return a review package for this plan.'
            }
            icon={<FileCheck2 size={18} />}
          />
        </Panel>
      </div>
    )
  }

  const evidence = review.simulation_evidence
  const score = review.plan.score
  const coverage = review.plan.score_coverage

  return (
    <div className="flex flex-col gap-3 p-3">
      <header className="panel flex flex-wrap items-center justify-between gap-2 px-3 py-2.5">
        <div className="flex min-w-0 flex-col gap-0.5">
          <h1 className="text-sm font-semibold uppercase tracking-[0.1em] text-ink-100">
            Plans
          </h1>
          <p className="text-[11px] text-ink-400">
            Plan v{review.plan.version} · {review.plan.objective}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          {data.demo && <DemoBadge reason={data.demoReason ?? 'Backend unreachable.'} />}
          <StatusBadge
            label={planStatusLabel(review.approval.approval_state)}
            tone={planStatusTone(review.approval.approval_state)}
            live={review.approval.approval_state === 'EXECUTING'}
          />
          {review.plan.feasible === false && (
            <StatusBadge label="INFEASIBLE" tone="alert" />
          )}
        </div>
      </header>

      <div className="grid grid-cols-1 gap-3 xl:grid-cols-[minmax(0,1fr)_minmax(320px,400px)]">
        <div className="flex min-w-0 flex-col gap-3">
          <div className="grid grid-cols-2 gap-2.5 sm:grid-cols-4">
            <Metric
              label="Version"
              value={`v${review.plan.version}`}
              hint={formatDateTime(review.plan.created_at)}
            />
            <Metric
              label="Optimizer score"
              value={formatPercent(score === null ? null : score * 100, 1)}
              tone="intel"
              hint="Selection objective, not a confidence"
            />
            <Metric
              label="Score coverage"
              value={formatPercent(coverage === null ? null : coverage * 100, 0)}
              hint="Objective terms actually weighted"
            />
            <Metric
              label="Feasible"
              value={review.plan.feasible === null ? <Unavailable /> : review.plan.feasible ? 'yes' : 'no'}
              tone={review.plan.feasible ? 'go' : 'alert'}
              hint="Under hard constraints"
            />
          </div>

          {score !== null && (
            <p className="panel flex items-start gap-2 px-3 py-2 text-[10px] leading-relaxed text-ink-400">
              <Target size={12} className="mt-0.5 shrink-0 text-intel-400" />
              <span>{review.plan.score_note}</span>
            </p>
          )}

          <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
            <Panel title="Selection" ariaLabel="Plan selection">
              <dl className="flex flex-col gap-1.5 text-[11px]">
                <Row label="Route" value={review.route.route_id} mono />
                <Row label="Resilience role" value={review.route.resilience_role} />
                <Row label="Hospital" value={review.selected.hospital_id} mono />
                <Row label="Vehicle" value={review.selected.vehicle_id} mono />
                <Row label="Rank" value={review.route.route_rank} />
                <Row label="Road edges" value={review.route.canonical_road_edge_count} />
              </dl>
              <div className="mt-2.5 grid grid-cols-2 gap-2 border-t border-graphite-700 pt-2.5">
                <Metric
                  label="ETA"
                  value={formatSeconds(review.route.eta_seconds ?? null)}
                  tone="go"
                  size="sm"
                />
                <Metric
                  label="Distance"
                  value={formatDistance(review.route.distance_meters ?? null)}
                  size="sm"
                />
                <Metric
                  label="Failure risk"
                  value={numeric(review.route.failure_risk)}
                  size="sm"
                />
                <Metric
                  label="Diversity"
                  value={numeric(review.route.diversity_from_primary)}
                  size="sm"
                />
              </div>
              {review.plan.rationale !== null && (
                <p className="mt-2.5 border-t border-graphite-700 pt-2 text-[10px] leading-relaxed text-ink-300">
                  {review.plan.rationale}
                </p>
              )}
            </Panel>

            <div className="flex flex-col gap-3">
              <Panel title="Evidence" ariaLabel="Simulation evidence status">
                <dl className="flex flex-col gap-1.5 text-[11px]">
                  <Row label="Evidence status" value={evidence.evidence_status} />
                  <Row
                    label="Comparable"
                    value={evidence.comparable ? 'yes' : 'no'}
                  />
                  <Row
                    label="Improvement"
                    value={
                      evidence.travel_time_improvement_percent === null
                        ? null
                        : formatPercent(evidence.travel_time_improvement_percent, 2)
                    }
                  />
                  <Row label="Safety status" value={evidence.safety_status} />
                  <Row label="Execution mode" value={evidence.execution_mode} />
                </dl>
                <p className="mt-2 border-t border-graphite-700 pt-2 text-[10px] leading-relaxed text-ink-400">
                  {evidence.summary}
                  {evidence.reason !== undefined && evidence.reason !== evidence.summary && (
                    <> {evidence.reason}</>
                  )}
                </p>
              </Panel>

              <Panel title="Network Agreement" ariaLabel="Road network checksum agreement">
                <p className="flex items-start gap-2 text-[11px] leading-relaxed text-ink-300">
                  <Network size={12} className="mt-0.5 shrink-0 text-intel-400" />
                  <span>
                    {review.network.matches_plan
                      ? 'The road network still matches the checksum this plan was built against.'
                      : review.network.road_network_key === null
                        ? 'The backend reported no road network key for this plan, so checksum agreement could not be established.'
                        : 'The road network checksum does not match the one this plan was built against. Re-verify before approving.'}
                  </span>
                </p>
                <dl className="mt-2 flex flex-col gap-1 border-t border-graphite-700 pt-2 text-[11px]">
                  <Row label="Network key" value={review.network.road_network_key} mono />
                  <Row label="Stale" value={review.network.stale ? 'yes' : 'no'} />
                </dl>
              </Panel>
            </div>
          </div>

          <Panel title={`Route Alternatives (${alternatives.length})`} ariaLabel="Route alternatives">
            <div className="overflow-x-auto">
              <table className="w-full border-collapse text-left text-[11px]">
                <thead>
                  <tr className="border-b border-graphite-700 text-[10px] uppercase tracking-[0.08em] text-ink-400">
                    <th scope="col" className="py-1.5 pr-2 font-semibold">Role</th>
                    <th scope="col" className="py-1.5 pr-2 font-semibold">Time</th>
                    <th scope="col" className="py-1.5 pr-2 font-semibold">Distance</th>
                    <th scope="col" className="py-1.5 pr-2 font-semibold">Score</th>
                    <th scope="col" className="py-1.5 pr-2 font-semibold">Viable</th>
                    <th scope="col" className="py-1.5 font-semibold">Selected</th>
                  </tr>
                </thead>
                <tbody>
                  {alternatives.map((alt) => (
                    <tr key={alt.route_id} className="border-b border-graphite-800">
                      <th scope="row" className="py-1.5 pr-2 font-normal text-ink-200">
                        {alt.resilience_role ?? <Unavailable>candidate</Unavailable>}
                      </th>
                      <td className="stat-value py-1.5 pr-2 text-xs text-ink-100">
                        {formatSeconds(alt.estimated_duration_seconds)}
                      </td>
                      <td className="stat-value py-1.5 pr-2 text-xs text-ink-200">
                        {formatDistance(alt.distance_meters)}
                      </td>
                      <td className="py-1.5 pr-2 text-xs">
                        {alt.score === null ? (
                          <Unavailable />
                        ) : (
                          <span className="stat-value text-ink-200">{alt.score.toFixed(4)}</span>
                        )}
                      </td>
                      <td className="py-1.5 pr-2 text-xs text-ink-300">
                        {alt.backup_viable === null ? <Unavailable /> : alt.backup_viable ? 'yes' : 'no'}
                      </td>
                      <td className="py-1.5">
                        {alt.is_selected ? (
                          <StatusBadge label="SELECTED" tone="go" />
                        ) : (
                          <span className="text-[10px] text-ink-400">—</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Panel>

          <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
            <Panel title="Warnings" ariaLabel="Review warnings">
              {review.warnings.length === 0 ? (
                <p className="text-[11px] text-ink-400">No warnings were reported.</p>
              ) : (
                <ul className="flex flex-col gap-1.5">
                  {review.warnings.map((warning) => (
                    <li key={warning} className="flex gap-1.5 text-[10px] leading-relaxed text-ink-300">
                      <AlertTriangle size={11} className="mt-0.5 shrink-0 text-warn-400" />
                      <span>{warning}</span>
                    </li>
                  ))}
                </ul>
              )}
            </Panel>

            <Panel title="Known Limitations" ariaLabel="Known limitations">
              <ul className="flex flex-col gap-1.5">
                {review.known_limitations.map((line) => (
                  <li key={line} className="flex gap-1.5 text-[10px] leading-relaxed text-ink-400">
                    <ScrollText size={11} className="mt-0.5 shrink-0 text-ink-400" />
                    <span>{line}</span>
                  </li>
                ))}
              </ul>
            </Panel>
          </div>

          <p className="panel flex items-start gap-2 px-3 py-2 text-[10px] leading-relaxed text-ink-400">
            <Fingerprint size={12} className="mt-0.5 shrink-0 text-intel-400" />
            <span>
              Decisions are bound to plan v{review.plan.version} and the reviewer ID
              you supply. The backend refuses a decision against a stale version,
              which is what prevents an approval carrying over to a changed plan.
            </span>
          </p>
        </div>

        <div className="flex min-w-0 flex-col gap-3">
          <ApprovalPanel
            review={review}
            missionId={data.missionId}
            actions={actions}
            onChanged={data.refresh}
            onOpenPlans={() => navigate('plans')}
            demo={data.demo}
          />
        </div>
      </div>

      <ProgressStrip review={review} />
    </div>
  )
}

/** Renders a numeric plan field, treating the backend's "unavailable" string as absent. */
function numeric(value: number | string | null | undefined): string | null {
  return typeof value === 'number' && Number.isFinite(value) ? value.toFixed(3) : null
}

function Row({
  label,
  value,
  mono = false,
}: {
  label: string
  value: string | number | null | undefined
  mono?: boolean
}) {
  const missing = value === null || value === undefined || value === ''
  return (
    <div className="flex min-w-0 items-baseline justify-between gap-2">
      <dt className="shrink-0 text-[10px] uppercase tracking-[0.08em] text-ink-400">
        {label}
      </dt>
      <dd
        className={`truncate text-right ${mono ? 'font-mono text-[10px]' : 'text-[11px]'} ${
          missing ? 'italic text-ink-400' : 'text-ink-100'
        }`}
        title={missing ? undefined : String(value)}
      >
        {missing ? <Unavailable /> : String(value)}
      </dd>
    </div>
  )
}
