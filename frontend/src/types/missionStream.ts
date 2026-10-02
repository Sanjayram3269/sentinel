export interface MissionStreamUpdate {
  missionId: string
  status?: 'STANDBY' | 'ACTIVE' | 'COMPLETED'
  eta?: number
  risk?: 'LOW' | 'MEDIUM' | 'HIGH'
  confidence?: number
  vehicleId?: string
  latitude?: number
  longitude?: number
}