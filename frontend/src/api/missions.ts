/** Mission-scoped endpoints, transcribed from the backend OpenAPI document. */

import { api } from './client'
import type {
  IncidentRead,
  MissionEventRead,
  MissionRead,
  MissionStateRead,
  VehicleRead,
} from '../types/api'

export function getMission(missionId: string) {
  return api.get<MissionRead>(`/missions/${missionId}`)
}

/**
 * The single most useful read for the command centre: it returns mission,
 * incident, vehicles, routes, active plan and recent events in one round trip.
 * Using it avoids the N+1 fan-out a panel-by-panel approach would cause.
 */
export function getMissionState(missionId: string, signal?: AbortSignal) {
  return api.get<MissionStateRead>(`/missions/${missionId}/state`, { signal })
}

export function getIncident(incidentId: string) {
  return api.get<IncidentRead>(`/incidents/${incidentId}`)
}

export function getVehicle(vehicleId: string) {
  return api.get<VehicleRead>(`/vehicles/${vehicleId}`)
}

export function getMissionEvents(missionId: string, limit = 25) {
  return api.get<{ items: MissionEventRead[]; limit: number; has_more: boolean }>(
    `/missions/${missionId}/events?limit=${limit}`,
  )
}

/** Development selection defaults. Overridable; never treated as immutable. */
export const DEFAULT_MISSION_ID = '66b25ba2-fcdd-4bcd-b54a-59b0756c8518'
export const DEFAULT_INCIDENT_ID = '609d038e-46b8-47cc-8f72-9ff247be54ed'
export const DEFAULT_VEHICLE_ID = '8b67cb15-2af6-4383-b47b-f15d945dfb3d'
export const DEFAULT_HOSPITAL_ID = '33333333-3333-4333-8333-000000000001'
