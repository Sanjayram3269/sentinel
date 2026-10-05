/**
 * Command Center — the primary screen.
 *
 * Layout intent: the mission on the left, the operational picture in the
 * centre, the derived readouts on the right, and the decision surface across
 * the bottom. The operator's eye should land on the map, then travel down to
 * the recommendation and the approval controls, without scrolling at desktop
 * widths.
 */

import { useMemo, useState } from 'react'
import { Sparkles, TriangleAlert } from 'lucide-react'
import { MissionPanel } from '../components/mission/MissionPanel'
import { RoutePanel } from '../components/routes/RoutePanel'
import { IntelligencePanel } from '../components/intelligence/IntelligencePanel'
import { OperationalMap, type MapMarker } from '../components/map/OperationalMap'
import { ClearpathPanel } from '../components/simulation/ClearpathPanel'
import { ApprovalPanel } from '../components/approval/ApprovalPanel'
import { EventTimeline } from '../components/timeline/EventTimeline'
import { ProgressStrip } from '../components/layout/ProgressStrip'
import { DemoBadge, EmptyState, Panel } from '../components/ui/primitives'
import { useApprovalActions } from '../hooks/useApprovalActions'
import type { ClearpathComparison } from '../lib/clearpath'
import type { RouteId } from '../hooks/useHashRoute'
import type { AppData } from '../hooks/useAppData'
import { comparisonFromReview } from '../lib/clearpath'
import { entriesFromEvents } from '../lib/timeline'
import { DEMO_HOSPITAL_ID } from '../lib/demoData'

interface CommandCenterProps {
  data: AppData
  navigate: (route: RouteId) => void
}

export function CommandCenter({ data, navigate }: CommandCenterProps) {
  const approvalActions = useApprovalActions()
  const [timelineLimit, setTimelineLimit] = useState(8)

  const { review, routes, resilience, roleByRouteId, selectedRouteId, state } = data
  const selectedRoute = routes.find((route) => route.id === selectedRouteId) ?? null

  const markers = useMemo<MapMarker[]>(() => {
    const list: MapMarker[] = []
    const incident = state?.incident ?? null
    if (incident?.location) {
      list.push({
        id: incident.id,
        kind: 'incident',
        longitude: incident.location.longitude,
        latitude: incident.location.latitude,
        label: `SEV-${incident.severity} ${incident.type}`,
        detail: incident.description,
        live: incident.active,
      })
    }
    const vehicle = state?.vehicles?.[0] ?? null
    if (vehicle?.current_location) {
      list.push({
        id: vehicle.id,
        kind: 'vehicle',
        longitude: vehicle.current_location.longitude,
        latitude: vehicle.current_location.latitude,
        label: vehicle.call_sign,
        detail: vehicle.status,
        live: vehicle.status !== 'OFFLINE',
      })
    }
    // The hospital has no coordinates endpoint in the contract, so it is
    // anchored at the end of the primary route: honest, and clearly labelled.
    const primary = routes.find((route) => route.id === resilience?.primary_route_id)
    const tail = primary?.geometry.at(-1)
    if (tail) {
      list.push({
        id: DEMO_HOSPITAL_ID,
        kind: 'hospital',
        longitude: tail.longitude,
        latitude: tail.latitude,
        label: 'Receiving hospital',
        detail: review?.selected.hospital_id ?? 'No hospital selected',
      })
    }
    return list
  }, [state, routes, resilience, review])

  const comparison = comparisonFromReview(review?.simulation_evidence)
  const evidenceStatus = review?.simulation_evidence?.evidence_status ?? null
  const noEvidenceReason = review?.simulation_evidence?.reason ?? review?.simulation_evidence?.summary ?? null

  const timelineEntries = useMemo(() => entriesFromEvents(data.events), [data.events])

  return (
    <div className="flex min-h-full flex-col gap-2.5 p-2.5 lg:h-full lg:gap-3 lg:p-3">
      {data.demo && (
        <div className="panel flex flex-wrap items-center gap-2 border-warn-500/40 px-3 py-2">
          <DemoBadge reason={data.demoReason ?? 'Backend unreachable.'} />
          <p className="min-w-0 flex-1 text-[11px] leading-relaxed text-ink-300">
            {data.demoReason ??
              'The SENTINEL backend is not reachable.'}{' '}
            Everything below is a recorded snapshot, clearly marked. No action can
            be taken against demo state.
          </p>
        </div>
      )}

      <div className="grid min-h-0 flex-1 grid-cols-1 gap-2.5 xl:grid-cols-[minmax(230px,270px)_minmax(0,1fr)_minmax(250px,300px)] xl:gap-3">
        {/* Left column: identity and routing. */}
        <div className="flex min-h-0 flex-col gap-2.5 xl:overflow-y-auto">
          <MissionPanel state={state} demo={data.demo} />
          <RoutePanel
            routes={routes}
            resilience={resilience}
            roleByRouteId={roleByRouteId}
            selectedRouteId={selectedRouteId}
            onSelectRoute={data.selectRoute}
          />
          <div className="xl:hidden">
            <IntelligencePanel
              review={review}
              selectedRoute={selectedRoute}
              modelBacked={false}
            />
          </div>
        </div>

        {/* Centre column: the operational picture. */}
        <div className="flex min-h-[420px] flex-col gap-2.5 xl:min-h-0">
          <Panel
            title="Live Map"
            ariaLabel="Operational map"
            flush
            className="min-h-[380px] flex-1"
            actions={
              selectedRoute !== null ? (
                <span className="font-mono text-[10px] text-ink-400">
                  {selectedRoute.name}
                </span>
              ) : null
            }
          >
            {routes.length === 0 && markers.length === 0 ? (
              <EmptyState
                title="No geometry to display"
                detail="The backend returned no route geometry for this mission."
              />
            ) : (
              <OperationalMap
                routes={routes}
                roleByRouteId={roleByRouteId}
                selectedRouteId={selectedRouteId}
                onSelectRoute={data.selectRoute}
                markers={markers}
                className="w-full"
              />
            )}
          </Panel>

          <div className="xl:hidden">
            <RecommendationBar
              comparison={comparison}
              evidenceStatus={evidenceStatus}
              onViewSimulation={() => navigate('simulations')}
              onApprove={() => navigate('plans')}
            />
          </div>
        </div>

        {/* Right column: derived readouts and the simulation result. */}
        <div className="flex min-h-0 flex-col gap-2.5 xl:overflow-y-auto">
          <div className="hidden xl:block">
            <IntelligencePanel
              review={review}
              selectedRoute={selectedRoute}
              modelBacked={false}
            />
          </div>
          <ClearpathPanel
            comparison={comparison}
            noEvidenceReason={noEvidenceReason}
            evidenceStatus={evidenceStatus}
            onOpenSimulation={() => navigate('simulations')}
            demo={data.demo}
          />
          <EventTimeline
            entries={timelineEntries}
            limit={timelineLimit}
            onShowAll={() => setTimelineLimit((limit) => limit + 20)}
            demo={data.demo}
          />
        </div>
      </div>

      <RecommendationBar
        comparison={comparison}
        evidenceStatus={evidenceStatus}
        onViewSimulation={() => navigate('simulations')}
        onApprove={() => navigate('plans')}
      />

      <div className="grid grid-cols-1 gap-2.5 lg:grid-cols-[minmax(0,1fr)_minmax(320px,420px)] xl:hidden">
        <ApprovalPanel
          review={review}
          missionId={data.missionId}
          actions={approvalActions}
          onChanged={data.refresh}
          onOpenPlans={() => navigate('plans')}
          demo={data.demo}
        />
        <KnowledgePanel review={review} />
      </div>

      <ProgressStrip review={review} />
    </div>
  )
}

function RecommendationBar({
  comparison,
  evidenceStatus,
  onViewSimulation,
  onApprove,
}: {
  comparison: ClearpathComparison | null
  evidenceStatus: string | null
  onViewSimulation: () => void
  onApprove: () => void
}) {
  const live = comparison !== null
  const headline = live
    ? `CLEARPATH simulation indicates ${comparison.improvementPercent?.toFixed(2) ?? '—'}% lower emergency travel time on the selected route.`
    : 'CLEARPATH has no measured result for this plan yet. Approval would proceed without a simulated benefit figure.'

  return (
    <Panel
      title="AI Recommendation"
      ariaLabel="Recommendation and plan approval"
      className="border-intel-600/50 bg-intel-600/5"
      actions={
        <span
          title="A generated planning recommendation. It is advisory: a named human approves or rejects it."
          className="inline-flex items-center gap-1 text-[10px] uppercase tracking-[0.08em] text-intel-300"
        >
          <Sparkles size={11} />
          Advisory
        </span>
      }
    >
      <div className="flex flex-col gap-2.5 lg:flex-row lg:items-center lg:justify-between">
        <div className="flex min-w-0 items-start gap-2">
          {!live && (
            <TriangleAlert size={14} className="mt-0.5 shrink-0 text-warn-400" />
          )}
          <div className="flex min-w-0 flex-col gap-0.5">
            <p className="text-[11px] leading-relaxed text-ink-100">{headline}</p>
            <p className="text-[10px] leading-relaxed text-ink-400">
              {live
                ? 'Measured inside the SUMO/TraCI digital twin. Simulation-only; it does not control any real traffic signal.'
                : `Backend evidence status: ${evidenceStatus ?? 'unavailable'}. Configure SUMO for this road network to produce a measured pair.`}
            </p>
          </div>
        </div>
        <div className="flex shrink-0 flex-wrap items-center gap-1.5">
          <button
            type="button"
            onClick={onViewSimulation}
            className="inline-flex items-center gap-1.5 rounded border border-graphite-600 bg-graphite-800 px-3 py-1.5 text-[11px] font-semibold uppercase tracking-[0.07em] text-ink-100 transition-colors hover:border-intel-500 hover:text-intel-300"
          >
            View simulation
          </button>
          <button
            type="button"
            onClick={onApprove}
            className="inline-flex items-center gap-1.5 rounded border border-go-500 bg-go-500 px-3 py-1.5 text-[11px] font-bold uppercase tracking-[0.07em] text-void transition-colors hover:bg-go-400"
          >
            Approve plan
          </button>
        </div>
      </div>
    </Panel>
  )
}

function KnowledgePanel({ review }: { review: AppData['review'] }) {
  if (!review) return null
  return (
    <Panel title="Known Limitations" ariaLabel="Backend-reported limitations">
      <ul className="flex flex-col gap-1.5">
        {review.known_limitations.map((line) => (
          <li key={line} className="flex gap-1.5 text-[10px] leading-relaxed text-ink-400">
            <span aria-hidden="true" className="mt-1 h-1 w-1 shrink-0 rounded-full bg-warn-400" />
            <span>{line}</span>
          </li>
        ))}
      </ul>
    </Panel>
  )
}
