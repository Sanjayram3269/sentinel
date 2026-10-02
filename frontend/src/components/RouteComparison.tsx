import type { RouteOption } from '../types/route'

interface RouteComparisonProps {
  routes: RouteOption[]
}

function RouteComparison({ routes }: RouteComparisonProps) {
  return (
    <div className="route-comparison">
      <div className="route-comparison-header">
        <div>
          <span>ROUTE OPTIONS</span>
          <small>AI-OPTIMIZED PATHS</small>
        </div>

        <span>{routes.length} AVAILABLE</span>
      </div>

      <div className="route-list">
        {routes.map((route) => (
          <div
            className={`route-card ${
              route.status === 'RECOMMENDED' ? 'recommended-route' : ''
            }`}
            key={route.id}
          >
            <div className="route-name">
              <span>{route.status}</span>
              <strong>{route.name}</strong>
              <small>{route.distance} km</small>
            </div>

            <div>
              <span>ETA</span>
              <strong>{route.eta} min</strong>
            </div>

            <div>
              <span>RISK</span>
              <strong className={`risk-${route.risk.toLowerCase()}`}>
                {route.risk}
              </strong>
            </div>

            <div>
              <span>CONFIDENCE</span>
              <strong>{route.confidence}%</strong>
            </div>

            <div className="route-action">
              {route.status === 'RECOMMENDED'
                ? 'SELECTED'
                : 'BACKUP'}
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

export default RouteComparison