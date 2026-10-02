import type { RouteOption } from '../types/route'

export const mockRoutes: RouteOption[] = [
  {
    id: 'ROUTE-001',
    name: 'RECOMMENDED',
    eta: 8,
    risk: 'LOW',
    confidence: 94,
    distance: 4.2,
    status: 'RECOMMENDED',
  },
  {
    id: 'ROUTE-002',
    name: 'BACKUP',
    eta: 10,
    risk: 'MEDIUM',
    confidence: 86,
    distance: 4.8,
    status: 'BACKUP',
  },
]