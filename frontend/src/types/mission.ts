export interface Mission {
  id: string
  status: 'STANDBY' | 'ACTIVE' | 'COMPLETED'

  origin: string
  destination: string
  vehicle: string

  eta: number | null
  risk: 'LOW' | 'MEDIUM' | 'HIGH' | null
  confidence: number | null
}