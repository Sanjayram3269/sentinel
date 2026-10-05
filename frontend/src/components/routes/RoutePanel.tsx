/**
 * Resilient route cards.
 *
 * The role ordering and visual weight are the point: an operator must see at a
 * glance which route is live, which are standing by, and how distinct they are
 * from one another.
 */

import { GitBranch, ShieldCheck } from 'lucide-react'
import { EmptyState, Panel, StatusBadge, Unavailable } from '../ui/primitives'
import { formatDistance, formatPercent, formatRatio, formatSeconds } from '../../lib/format'
import { resilienceTone, ROLE_LABEL, ROLE_TONE, routeStatusTone } from '../../lib/status'
import type { ResilienceRead, ResilienceRole, RouteRead } from '../../types/api'

interface RoutePanelProps {
  routes: RouteRead[]
  resilience: ResilienceRead | null
  roleByRouteId: Record<string, ResilienceRole>
  selectedRouteId: string | null
  onSelectRoute: (routeId: string) => void
}

const ROLE_ORDER: ResilienceRole[] = ['PRIMARY', 'BACKUP', 'CONTINGENCY']

export function RoutePanel({
  routes,
  resilience,
  roleByRouteId,
  selectedRouteId,
  onSelectRoute,
}: RoutePanelProps) {
  const roleRoutes = ROLE_ORDER.map((role) => ({
    role,
    route: routes.find((item) => roleByRouteId[item.id] === role) ?? null,
  }))

  const alternates = routes.filter(
    (item) => !roleByRouteId[item.id],
  )

  return (
    <Panel
      title="Routes"
      ariaLabel="Route candidates"
      actions={
        resilience ? (
          <StatusBadge
            label={resilience.resilience_level.replace(/_/g, ' ')}
            tone={resilienceTone(resilience.resilience_level)}
          />
        ) : undefined
      }
    >
      <div className="flex flex-col gap-2">
        {roleRoutes.map(({ role, route }) => (
          <RouteCard
            key={role}
            role={role}
            route={route}
            selected={route?.id === selectedRouteId}
            onSelect={onSelectRoute}
            diversity={role === 'PRIMARY' ? null : (resilience?.route_diversity ?? null)}
          />
        ))}

        {alternates.length > 0 && (
          <details className="group rounded border border-graphite-700 bg-graphite-900/50">
            <summary className="flex cursor-pointer list-none items-center justify-between px-2 py-1.5 text-[10px] uppercase tracking-[0.08em] text-ink-400 transition-colors hover:text-ink-200">
              <span className="flex items-center gap-1.5">
                <GitBranch size={11} /> {alternates.length} unranked candidate
                {alternates.length === 1 ? '' : 's'}
              </span>
              <span className="group-open:hidden">show</span>
              <span className="hidden group-open:inline">hide</span>
            </summary>
            <ul className="flex flex-col gap-1 border-t border-graphite-700 p-1.5">
              {alternates.map((route) => (
                <li key={route.id}>
                  <button
                    type="button"
                    onClick={() => onSelectRoute(route.id)}
                    className={`flex w-full items-center justify-between gap-2 rounded px-1.5 py-1 text-left text-[11px] transition-colors ${
                      route.id === selectedRouteId
                        ? 'bg-graphite-700 text-ink-100'
                        : 'text-ink-300 hover:bg-graphite-800'
                    }`}
                  >
                    <span className="truncate font-mono text-[10px]">{route.id.slice(0, 8)}</span>
                    <span className="shrink-0 font-mono text-[11px]">
                      {formatSeconds(route.estimated_duration_seconds)}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          </details>
        )}

        {roleRoutes.every((entry) => entry.route === null) && (
          <EmptyState
            title="No route candidates"
            detail="The backend reported no routed candidates for this mission."
            icon={<GitBranch size={18} />}
          />
        )}

        {resilience && (
          <div className="mt-0.5 grid grid-cols-2 gap-2 border-t border-graphite-700 pt-2 text-[11px]">
            <div className="flex flex-col">
              <span className="label-caps">Diversity</span>
              <span className="stat-value text-sm text-intel-300">
                {formatRatio(resilience.route_diversity)}
              </span>
            </div>
            <div className="flex flex-col">
              <span className="label-caps">Resilience</span>
              <span className="stat-value text-sm text-ink-100">
                {formatPercent(resilience.resilience_score, 1)}
              </span>
            </div>
          </div>
        )}

        {resilience && resilience.explanation.length > 0 && (
          <ul className="flex flex-col gap-1 border-t border-graphite-700 pt-2">
            {resilience.explanation.map((line) => (
              <li key={line} className="flex gap-1.5 text-[10px] leading-relaxed text-ink-400">
                <ShieldCheck size={11} className="mt-0.5 shrink-0 text-intel-500" />
                <span>{line}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </Panel>
  )
}

function RouteCard({
  role,
  route,
  selected,
  onSelect,
  diversity,
}: {
  role: ResilienceRole
  route: RouteRead | null
  selected: boolean
  onSelect: (routeId: string) => void
  diversity: number | null
}) {
  const tone = ROLE_TONE[role]
  const isReady = route !== null && route.status === 'CANDIDATE'
  const isLive = route !== null && route.status === 'ACTIVE'

  if (route === null) {
    return (
      <div className="rounded border border-dashed border-graphite-700 bg-graphite-900/40 px-2.5 py-2">
        <div className="flex items-center justify-between">
          <span className="text-[11px] font-semibold uppercase tracking-[0.1em] text-ink-400">
            {ROLE_LABEL[role]}
          </span>
          <Unavailable>no candidate</Unavailable>
        </div>
      </div>
    )
  }

  return (
    <button
      type="button"
      onClick={() => onSelect(route.id)}
      aria-pressed={selected}
      aria-label={`${ROLE_LABEL[role]} route, ${formatSeconds(route.estimated_duration_seconds)} travel time, ${formatDistance(route.distance_meters)}`}
      className={`panel-interactive w-full rounded border px-2.5 py-2 text-left ${
        selected ? 'border-intel-500 bg-intel-600/10' : 'border-graphite-700'
      }`}
    >
      <div className="flex items-center justify-between gap-2">
        <span className="flex items-center gap-1.5">
          <span
            aria-hidden="true"
            className={`h-1.5 w-1.5 rounded-full ${
              tone === 'go'
                ? 'bg-go-400'
                : tone === 'warn'
                  ? 'bg-warn-400'
                  : 'bg-intel-400'
            } ${isLive ? 'pulse-live' : ''}`}
          />
          <span className="text-[11px] font-bold uppercase tracking-[0.1em] text-ink-100">
            {ROLE_LABEL[role]}
          </span>
        </span>
        <StatusBadge
          label={isLive ? 'ACTIVE' : isReady ? 'READY' : route.status}
          tone={routeStatusTone(route.status)}
          live={isLive}
        />
      </div>

      <div className="mt-1.5 flex items-end justify-between gap-2">
        <span className="stat-value text-xl text-ink-100">
          {formatSeconds(route.estimated_duration_seconds)}
        </span>
        <span className="pb-0.5 font-mono text-[11px] text-ink-300">
          {formatDistance(route.distance_meters)}
        </span>
      </div>

      <div className="mt-1 flex items-center gap-2 text-[10px] text-ink-400">
        <span className="font-mono">{route.geometry.length} pts</span>
        <span aria-hidden="true">·</span>
        <span>risk {route.risk_score !== null ? formatRatio(route.risk_score) : <Unavailable />}</span>
        {diversity !== null && (
          <>
            <span aria-hidden="true">·</span>
            <span>div {formatRatio(diversity)}</span>
          </>
        )}
      </div>
    </button>
  )
}
