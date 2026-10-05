/**
 * Simulation run history.
 *
 * Kept out of `useMissionData` because the simulations endpoint is the one most
 * likely to be unavailable: the SUMO adapter is optional configuration, so a
 * failed simulator is an expected state to render, not an error to hide. The
 * run list is therefore always shown, with each run's own error surfaced.
 */

import { useCallback, useEffect, useMemo, useState } from 'react'
import { getMissionSimulations } from '../api/simulations'
import { isApiError } from '../api/client'
import { DEFAULT_MISSION_ID } from '../api/missions'
import type { SimulationRunRead } from '../types/api'

interface InternalState {
  runs: SimulationRunRead[]
  loading: boolean
  error: string | null
}

const INITIAL: InternalState = { runs: [], loading: true, error: null }

export function useSimulations(
  missionId: string = DEFAULT_MISSION_ID,
  pollMs = 20_000,
): InternalState & { refresh: () => void } {
  const [state, setState] = useState<InternalState>(INITIAL)

  const load = useCallback(async () => {
    try {
      const response = await getMissionSimulations(missionId, 25)
      setState({ runs: response.items, loading: false, error: null })
    } catch (error) {
      setState({
        runs: [],
        loading: false,
        error: isApiError(error)
          ? error.isOffline
            ? 'The SENTINEL backend is not reachable.'
            : error.detail
          : 'Simulation history could not be loaded.',
      })
    }
  }, [missionId])

  useEffect(() => {
    // Deferred so the effect body stays synchronous; see useMissionData.
    const kickoff = setTimeout(() => void load(), 0)
    const timer = setInterval(() => void load(), pollMs)
    return () => {
      clearTimeout(kickoff)
      clearInterval(timer)
    }
  }, [load, pollMs])

  return useMemo(() => ({ ...state, refresh: () => void load() }), [state, load])
}
