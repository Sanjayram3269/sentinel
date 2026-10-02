export interface RouteOption {
  id: string
  name: string
  eta: number
  risk: 'LOW' | 'MEDIUM' | 'HIGH'
  confidence: number
  distance: number
  status: 'RECOMMENDED' | 'BACKUP'
}