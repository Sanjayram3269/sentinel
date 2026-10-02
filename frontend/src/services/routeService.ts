import { getMissionRoutes } from './api'
import type { RouteOption } from '../types/route'

export async function loadRoutes(
  missionId: string
): Promise<RouteOption[]> {
  return getMissionRoutes(missionId)
}