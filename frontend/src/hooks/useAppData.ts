/**
 * App-level data composition.
 *
 * The command centre needs several reads at once. This hook owns the
 * orchestration so pages stay declarative, and it holds the two pieces of shared
 * state that genuinely belong above a single page: the selected route, and the
 * live/demo provenance that every panel must be able to render.
 *
 * Provenance is derived once, here, rather than in each panel. If it were
 * computed per panel, a panel could forget to check it and quietly render demo
 * data as if it were live.
 */

import { useCallback, useEffect, useMemo, useState } from 'react'
import { probeHealth } from '../api/client'
import { DEFAULT_MISSION_ID } from '../api/missions'
import { DEFAULT_PLAN_ID } from '../api/plans'
import { useMissionData, type MissionData } from './useMissionData'
import { useMissionState } from './useMissionState'
import type { MissionStateRead, ResilienceRole } from '../types/api'

export type ConnectionState = 'checking' | 'live' | 'offline'

export interface MissionStatusSummary {
  id: string
  status: string | null
  statusLabel: string
  objective: string
}

export interface AppData {
  missionId: string
  planId: string
  connection: ConnectionState
  mission: MissionStatusSummary
  demo: boolean
  demoReason: string | null
  loading: boolean
  routes: MissionData['routes']
  resilience: MissionData['resilience']
  review: MissionData['review']
  events: MissionData['events']
  state: MissionStateRead | null
  roleByRouteId: Record<string, ResilienceRole>
  selectedRouteId: string | null
  selectRoute: (routeId: string) => void
  refresh: () => void
}

export function useAppData(missionId: string = DEFAULT_MISSION_ID): AppData {
  const [connection, setConnection] = useState<ConnectionState>('checking')
  // The operator's explicit pick. `null` means "follow the planner", and the
  // effective selection below resolves that against what actually exists.
  const [overrideRouteId, setOverrideRouteId] = useState<string | null>(null)

  // Plan id is a development selection; the backend owns version bumping.
  const planId = DEFAULT_PLAN_ID

  const planning = useMissionData({ missionId, planId })
  const identity = useMissionState(missionId)

  const refresh = useCallback(() => {
    planning.refresh()
    identity.refresh()
  }, [planning, identity])

  // Health probe is independent of the data reads: a 200 here is what justifies
  // showing the "Live" indicator, so it must not be inferred from a cached read.
  useEffect(() => {
    let cancelled = false
    const probe = async () => {
      const ok = await probeHealth()
      if (!cancelled) setConnection(ok ? 'live' : 'offline')
    }
    const kickoff = setTimeout(() => void probe(), 0)
    const timer = setInterval(() => void probe(), 30_000)
    return () => {
      cancelled = true
      clearTimeout(kickoff)
      clearInterval(timer)
    }
  }, [refresh])

  const demo = planning.source === 'demo' || identity.source === 'demo'

  const roleByRouteId = useMemo(() => {
    const map: Record<string, ResilienceRole> = {}
    const resilience = planning.resilience
    if (resilience) {
      if (resilience.primary_route_id) map[resilience.primary_route_id] = 'PRIMARY'
      if (resilience.backup_route_id) map[resilience.backup_route_id] = 'BACKUP'
      if (resilience.contingency_route_id) map[resilience.contingency_route_id] = 'CONTINGENCY'
    }
    // The route payload also carries a role; prefer the resilience assignment
    // (authoritative for this cycle) but fall back to what the route claims.
    for (const route of planning.routes) {
      if (!map[route.id] && route.resilience_role) map[route.id] = route.resilience_role
    }
    return map
  }, [planning.resilience, planning.routes])

  // Derived rather than stored: the effective selection follows the planner
  // until the operator picks something, and recovers automatically if their
  // pick disappears from the next poll.
  const selectedRouteId = useMemo(() => {
    if (
      overrideRouteId !== null &&
      planning.routes.some((route) => route.id === overrideRouteId)
    ) {
      return overrideRouteId
    }
    return planning.resilience?.primary_route_id ?? planning.routes[0]?.id ?? null
  }, [overrideRouteId, planning.routes, planning.resilience])

  const mission = useMemo<MissionStatusSummary>(() => {
    const state = identity.state
    const status = state?.mission?.status ?? state?.status ?? null
    return {
      id: state?.mission?.id ?? missionId,
      status,
      statusLabel: status ?? 'UNKNOWN',
      objective:
        state?.mission?.objective ??
        planning.review?.mission.objective ??
        'No mission objective reported.',
    }
  }, [identity.state, missionId, planning.review])

  const demoReason = planning.sourceReason ?? identity.sourceReason

  return {
    missionId,
    planId,
    connection,
    mission,
    demo,
    demoReason,
    loading: planning.source === 'loading' && identity.source === 'loading',
    routes: planning.routes,
    resilience: planning.resilience,
    review: planning.review,
    events: planning.events,
    state: identity.state,
    roleByRouteId,
    selectedRouteId,
    selectRoute: setOverrideRouteId,
    refresh,
  }
}
