import type { Mission } from '../types/mission'
import type { RouteOption } from '../types/route'

const API_BASE_URL = 'http://localhost:8000'

export async function getMission(missionId: string): Promise<Mission> {
  const response = await fetch(`${API_BASE_URL}/missions/${missionId}`)

  if (!response.ok) {
    throw new Error('Failed to fetch mission')
  }

  return response.json()
}

export async function getMissionRoutes(
  missionId: string
): Promise<RouteOption[]> {
  const response = await fetch(`${API_BASE_URL}/missions/${missionId}/routes`)

  if (!response.ok) {
    throw new Error('Failed to fetch mission routes')
  }

  return response.json()
}