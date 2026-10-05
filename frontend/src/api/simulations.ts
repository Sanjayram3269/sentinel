/** Simulation run reads. */

import { api } from './client'
import type { SimulationCollectionRead, SimulationRunRead } from '../types/api'

export function getMissionSimulations(
  missionId: string,
  limit = 20,
  signal?: AbortSignal,
) {
  return api.get<SimulationCollectionRead>(
    `/missions/${missionId}/simulations?limit=${limit}`,
    { signal },
  )
}

export function getSimulation(missionId: string, simulationId: string) {
  return api.get<SimulationRunRead>(
    `/missions/${missionId}/simulations/${simulationId}`,
  )
}

/** Narrows an untyped metrics bag to the numbers the UI actually renders. */
export function readMetric(
  metrics: Record<string, unknown> | null | undefined,
  key: string,
): number | null {
  if (!metrics) return null
  const value = metrics[key]
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

export function readBoolMetric(
  metrics: Record<string, unknown> | null | undefined,
  key: string,
): boolean | null {
  if (!metrics) return null
  const value = metrics[key]
  return typeof value === 'boolean' ? value : null
}
