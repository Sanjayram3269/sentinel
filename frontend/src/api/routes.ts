/** Route and road-network endpoints. */

import { api } from './client'
import type {
  ResilienceRead,
  ResilienceRole,
  RouteCollectionRead,
  RouteRead,
} from '../types/api'

export function getMissionRoutes(missionId: string, signal?: AbortSignal) {
  return api.get<RouteCollectionRead>(`/missions/${missionId}/routes`, { signal })
}

export function getResilience(missionId: string, signal?: AbortSignal) {
  return api.get<ResilienceRead>(`/missions/${missionId}/routes/resilience`, {
    signal,
  })
}

export function getRoute(missionId: string, routeId: string) {
  return api.get<RouteRead>(`/missions/${missionId}/routes/${routeId}`)
}

export interface RoadNetworkEdgesRead {
  network_key: string
  features: Array<{
    type: 'Feature'
    id?: string
    geometry: { type: 'LineString'; coordinates: [number, number][] }
    properties: Record<string, unknown>
  }>
}

export function getRoadNetworkEdges(networkKey: string, signal?: AbortSignal) {
  return api.get<RoadNetworkEdgesRead>(
    `/road-networks/${encodeURIComponent(networkKey)}/edges`,
    { signal },
  )
}

export const DEFAULT_NETWORK_KEY = 'osm_urban_v1'

export const DEFAULT_ROUTE_IDS: Record<ResilienceRole, string> = {
  PRIMARY: '35524ebd-8e4c-45a2-b500-31f75821a1d7',
  BACKUP: '0659fa7e-ec01-417a-b536-868cd247faf9',
  CONTINGENCY: 'd01c6039-dab7-4fbe-a80b-88d9697d9543',
}
