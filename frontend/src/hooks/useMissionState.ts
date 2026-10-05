/**
 * Mission state read.
 *
 * Separate from `useMissionData` because the endpoints fail for different
 * reasons and degrade differently: `/state` carries identity (incident,
 * vehicle, mission, active plan), while the routes/resilience/review reads
 * carry the planning picture. Keeping them apart means a resilience outage
 * does not blank the mission panel.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { getMissionState, DEFAULT_MISSION_ID } from '../api/missions'
import { isApiError } from '../api/client'
import { DEMO_MISSION_STATE } from '../lib/demoData'
import type { MissionStateRead } from '../types/api'
import type { DataSource } from './useMissionData'

interface InternalState {
  state: MissionStateRead | null
  source: DataSource
  sourceReason: string | null
}

export interface UseMissionStateResult extends InternalState {
  refresh: () => void
}

export function useMissionState(
  missionId: string = DEFAULT_MISSION_ID,
  pollMs = 15_000,
): UseMissionStateResult {
  const [internal, setInternal] = useState<InternalState>({
    state: null,
    source: 'loading',
    sourceReason: null,
  })
  const abortRef = useRef<AbortController | null>(null)

  const load = useCallback(async () => {
    abortRef.current?.abort()
    const controller = new AbortController()
    abortRef.current = controller
    try {
      const state = await getMissionState(missionId, controller.signal)
      if (controller.signal.aborted) return
      setInternal({ state, source: 'live', sourceReason: null })
    } catch (error) {
      if (controller.signal.aborted) return
      const reason = isApiError(error)
        ? error.isOffline
          ? 'The SENTINEL backend is not reachable.'
          : error.detail
        : 'Mission state could not be loaded.'
      setInternal({ state: null, source: 'demo', sourceReason: reason })
    }
  }, [missionId])

  useEffect(() => {
    // Deferred so the effect body stays synchronous; see useMissionData.
    const kickoff = setTimeout(() => void load(), 0)
    const timer = setInterval(() => void load(), pollMs)
    return () => {
      clearTimeout(kickoff)
      clearInterval(timer)
      abortRef.current?.abort()
    }
  }, [load, pollMs])

  const refresh = useCallback(() => void load(), [load])

  // Demo identity is only materialised when the backend did not deliver.
  const state = useMemo(
    () => internal.state ?? (internal.source === 'demo' ? DEMO_MISSION_STATE : null),
    [internal.state, internal.source],
  )

  return { ...internal, state, refresh }
}