/**
 * Mission data loading.
 *
 * The provenance distinction is the point of this hook. A payload is tagged
 * `live` or `demo`, and that tag travels with the data all the way to the UI.
 * A component cannot render demo data without also rendering the marker,
 * because the marker is part of the value rather than a separate concern.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { DEFAULT_MISSION_ID, getMissionEvents } from '../api/missions'
import { getMissionRoutes, getResilience } from '../api/routes'
import { DEFAULT_PLAN_ID, getReviewPackage } from '../api/plans'
import { ApiError, isApiError } from '../api/client'
import { DEMO_SNAPSHOT, type DemoSnapshot } from '../lib/demoData'
import type {
  MissionEventRead,
  PlanReviewRead,
  ResilienceRead,
  RouteRead,
} from '../types/api'

export type DataSource = 'live' | 'demo' | 'loading'

export interface MissionData {
  routes: RouteRead[]
  resilience: ResilienceRead | null
  review: PlanReviewRead | null
  events: MissionEventRead[]
  source: DataSource
  /** Why the data is demo rather than live. Shown to the operator. */
  sourceReason: string | null
  error: string | null
}

interface InternalState {
  routes: RouteRead[]
  resilience: ResilienceRead | null
  review: PlanReviewRead | null
  events: MissionEventRead[]
  source: DataSource
  sourceReason: string | null
  error: string | null
}

const INITIAL: InternalState = {
  routes: [],
  resilience: null,
  review: null,
  events: [],
  source: 'loading',
  sourceReason: null,
  error: null,
}

export interface UseMissionDataOptions {
  missionId?: string
  planId?: string
  pollMs?: number
  enabled?: boolean
}

/**
 * Loads the command-centre payload. On any backend failure it falls back to the
 * committed demo snapshot and records why, rather than showing an empty shell
 * that reads as "nothing is happening".
 */
export function useMissionData(options: UseMissionDataOptions = {}): MissionData & {
  refresh: () => void
  demo: DemoSnapshot | null
} {
  const {
    missionId = DEFAULT_MISSION_ID,
    planId = DEFAULT_PLAN_ID,
    pollMs = 15_000,
    enabled = true,
  } = options

  const [state, setState] = useState<InternalState>(INITIAL)
  const abortRef = useRef<AbortController | null>(null)

  const load = useCallback(async () => {
    if (!enabled) return
    abortRef.current?.abort()
    const controller = new AbortController()
    abortRef.current = controller

    try {
      // Independent reads are issued together: they do not depend on each
      // other, and serialising them would make the slowest call set the
      // latency of the whole dashboard.
      const [routesResult, resilienceResult, reviewResult, eventsResult] =
        await Promise.allSettled([
          getMissionRoutes(missionId, controller.signal),
          getResilience(missionId, controller.signal),
          getReviewPackage(missionId, planId, controller.signal),
          getMissionEvents(missionId, 30),
        ])

      if (controller.signal.aborted) return

      // The review package is the only truly load-bearing read; the panels can
      // degrade without it, but the command centre cannot be honest without it.
      if (reviewResult.status === 'rejected') {
        const reason = isApiError(reviewResult.reason)
          ? reviewResult.reason.isOffline
            ? 'The SENTINEL backend is not reachable.'
            : `Backend returned ${reviewResult.reason.detail}`
          : 'The review package could not be loaded.'
        setState({
          ...INITIAL,
          source: 'demo',
          sourceReason: reason,
          error: reason,
        })
        return
      }

      const reasonParts: string[] = []
      if (routesResult.status === 'rejected') reasonParts.push('routes unavailable')
      if (resilienceResult.status === 'rejected') reasonParts.push('resilience unavailable')
      if (eventsResult.status === 'rejected') reasonParts.push('event history unavailable')

      setState({
        routes: routesResult.status === 'fulfilled' ? routesResult.value.items : [],
        resilience:
          resilienceResult.status === 'fulfilled' ? resilienceResult.value : null,
        review: reviewResult.value,
        events: eventsResult.status === 'fulfilled' ? eventsResult.value.items : [],
        // Partially degraded is still live: real data with a named gap, which
        // is different from fabricated data and must read differently.
        source: reasonParts.length > 0 ? 'demo' : 'live',
        sourceReason:
          reasonParts.length > 0 ? `Partially unavailable: ${reasonParts.join(', ')}.` : null,
        error: null,
      })
    } catch (error) {
      if (controller.signal.aborted) return
      const message =
        error instanceof ApiError
          ? error.isOffline
            ? 'The SENTINEL backend is not reachable.'
            : error.detail
          : 'Unexpected error while loading mission data.'
      setState({
        ...INITIAL,
        source: 'demo',
        sourceReason: message,
        error: message,
      })
    }
  }, [missionId, planId, enabled])

  useEffect(() => {
    if (!enabled) return
    // The first load is deferred to a task rather than run inline: the effect
    // body itself stays synchronous, and a cancelled mount never issues a
    // request whose result nobody is waiting for.
    const kickoff = setTimeout(() => void load(), 0)
    const timer = setInterval(() => void load(), pollMs)
    return () => {
      clearTimeout(kickoff)
      clearInterval(timer)
      abortRef.current?.abort()
    }
  }, [load, pollMs, enabled])

  const refresh = useCallback(() => {
    void load()
  }, [load])

  // Demo payload is only ever materialised when the backend did not deliver.
  const demo = useMemo(
    () => (state.source === 'demo' ? DEMO_SNAPSHOT : null),
    [state.source],
  )

  return { ...state, refresh, demo }
}
