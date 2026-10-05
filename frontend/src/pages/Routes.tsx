/**
 * Routes.
 *
 * The resilience story is the point of this page: one primary plus ordered,
 * geometrically distinct fallbacks, with the backend's own explanation of why
 * they were chosen. The map is interactive here so a route can be inspected
 * before anything is approved.
 */

import { useMemo } from 'react'
import { GitBranch, ShieldCheck, Waypoints } from 'lucide-react'
import { RoutePanel } from '../components/routes/RoutePanel'
import { OperationalMap, type MapMarker } from '../components/map/OperationalMap'
import { IntelligencePanel } from '../components/intelligence/IntelligencePanel'
import { DemoBadge, Panel, StatusBadge, Unavailable } from '../components/ui/primitives'
import { formatDistance, formatPercent, formatRatio, formatSeconds } from '../lib/format'
import { resilienceTone, ROLE_LABEL } from '../lib/status'
import type { AppData } from '../hooks/useAppData'

export function Routes({ data }: { data: AppData }) {
  const { routes, resilience, roleByRouteId, selectedRouteId, state } = data
  const selected = routes.find((route) => route.id === selectedRouteId) ?? null

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
    return list
  }, [state])

  const unranked = routes.filter((route) => !roleByRouteId[route.id])

  return (
    <div className="flex flex-col gap-3 p-3">
      <header className="panel flex flex-wrap items-center justify-between gap-2 px-3 py-2.5">
        <div className="flex min-w-0 flex-col gap-0.5">
          <h1 className="text-sm font-semibold uppercase tracking-[0.1em] text-ink-100">
            Routes
          </h1>
          <p className="text-[11px] text-ink-400">
            {routes.length} candidate{routes.length === 1 ? '' : 's'} from the current
            planning cycle, resolved against the road network.
          </p>
        </div>
        <div className="flex items-center gap-1.5">
          {data.demo && <DemoBadge reason={data.demoReason ?? 'Backend unreachable.'} />}
          {resilience !== null && (
            <StatusBadge
              label={resilience.resilience_level.replace(/_/g, ' ')}
              tone={resilienceTone(resilience.resilience_level)}
            />
          )}
        </div>
      </header>

      <div className="grid grid-cols-1 gap-3 xl:grid-cols-[minmax(280px,340px)_minmax(0,1fr)]">
        <div className="flex min-w-0 flex-col gap-3">
          <RoutePanel
            routes={routes}
            resilience={resilience}
            roleByRouteId={roleByRouteId}
            selectedRouteId={selectedRouteId}
            onSelectRoute={data.selectRoute}
          />
          <IntelligencePanel review={data.review} selectedRoute={selected} modelBacked={false} />
        </div>

        <div className="flex min-w-0 flex-col gap-3">
          <Panel
            title="Route Geometry"
            ariaLabel="Selected route map"
            flush
            className="min-h-[420px]"
            actions={
              selected !== null ? (
                <StatusBadge
                  label={roleByRouteId[selected.id]
                    ? ROLE_LABEL[roleByRouteId[selected.id]]
                    : 'CANDIDATE'}
                  tone="intel"
                />
              ) : null
            }
          >
            {routes.length === 0 ? (
              <p className="p-4 text-[11px] text-ink-400">
                <Unavailable>no route geometry available</Unavailable>
              </p>
            ) : (
              <OperationalMap
                routes={routes}
                roleByRouteId={roleByRouteId}
                selectedRouteId={selectedRouteId}
                onSelectRoute={data.selectRoute}
                markers={markers}
                className="min-h-[420px] w-full"
              />
            )}
          </Panel>

          <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
            <Panel title="Candidate Comparison" ariaLabel="Route candidate comparison">
              <div className="overflow-x-auto">
                <table className="w-full border-collapse text-left text-[11px]">
                  <thead>
                    <tr className="border-b border-graphite-700 text-[10px] uppercase tracking-[0.08em] text-ink-400">
                      <th scope="col" className="py-1.5 pr-2 font-semibold">Route</th>
                      <th scope="col" className="py-1.5 pr-2 font-semibold">Time</th>
                      <th scope="col" className="py-1.5 pr-2 font-semibold">Distance</th>
                      <th scope="col" className="py-1.5 pr-2 font-semibold">Risk</th>
                      <th scope="col" className="py-1.5 font-semibold">Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {routes.map((route) => (
                      <tr
                        key={route.id}
                        className={`border-b border-graphite-800 transition-colors ${
                          route.id === selectedRouteId ? 'bg-intel-600/10' : ''
                        }`}
                      >
                        <th scope="row" className="py-1.5 pr-2 font-mono text-[10px] font-normal text-ink-300">
                          {roleByRouteId[route.id]
                            ? ROLE_LABEL[roleByRouteId[route.id]]
                            : route.id.slice(0, 8)}
                        </th>
                        <td className="stat-value py-1.5 pr-2 text-xs text-ink-100">
                          {formatSeconds(route.estimated_duration_seconds)}
                        </td>
                        <td className="stat-value py-1.5 pr-2 text-xs text-ink-200">
                          {formatDistance(route.distance_meters)}
                        </td>
                        <td className="py-1.5 pr-2 text-xs">
                          {route.risk_score === null ? (
                            <Unavailable />
                          ) : (
                            <span className="stat-value text-ink-200">
                              {formatRatio(route.risk_score)}
                            </span>
                          )}
                        </td>
                        <td className="py-1.5">
                          <StatusBadge label={route.status} tone="neutral" />
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Panel>

            <Panel title="Resilience Assessment" ariaLabel="Resilience assessment">
              {resilience === null ? (
                <p className="text-[11px] text-ink-400">
                  <Unavailable>no resilience assessment returned</Unavailable>
                </p>
              ) : (
                <div className="flex flex-col gap-2.5">
                  <dl className="grid grid-cols-2 gap-2 text-[11px]">
                    <Metric label="Resilience score" value={formatPercent(resilience.resilience_score, 1)} />
                    <Metric label="Route diversity" value={formatRatio(resilience.route_diversity)} />
                    <Metric label="Failure exposure" value={formatRatio(resilience.failure_exposure)} />
                    <Metric
                      label="Planning cycle"
                      value={resilience.planning_cycle_id?.slice(0, 8) ?? null}
                      mono
                    />
                  </dl>
                  <ul className="flex flex-col gap-1 border-t border-graphite-700 pt-2">
                    {resilience.explanation.map((line) => (
                      <li key={line} className="flex gap-1.5 text-[10px] leading-relaxed text-ink-400">
                        <ShieldCheck size={11} className="mt-0.5 shrink-0 text-intel-500" />
                        <span>{line}</span>
                      </li>
                    ))}
                  </ul>
                  {unranked.length > 0 && (
                    <p className="flex items-center gap-1.5 border-t border-graphite-700 pt-2 text-[10px] text-ink-400">
                      <GitBranch size={11} className="shrink-0 text-intel-400" />
                      {unranked.length} candidate{unranked.length === 1 ? '' : 's'} carried no
                      resilience role in this cycle.
                    </p>
                  )}
                </div>
              )}
            </Panel>
          </div>

          <p className="panel flex items-start gap-2 px-3 py-2 text-[10px] leading-relaxed text-ink-400">
            <Waypoints size={12} className="mt-0.5 shrink-0 text-intel-400" />
            <span>
              Route risk and diversity are reported as unavailable unless the backend
              supplies them. An unranked candidate is still selectable, but selecting
              one does not change the mission — activation is a reviewed action.
            </span>
          </p>
        </div>
      </div>
    </div>
  )
}

function Metric({
  label,
  value,
  mono = false,
}: {
  label: string
  value: string | null
  mono?: boolean
}) {
  return (
    <div className="flex flex-col gap-0.5">
      <dt className="label-caps">{label}</dt>
      <dd className={`stat-value text-sm ${mono ? 'text-[11px]' : ''} text-ink-100`}>
        {value === null ? <Unavailable /> : value}
      </dd>
    </div>
  )
}
