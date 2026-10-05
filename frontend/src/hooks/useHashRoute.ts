/**
 * Hash-based routing.
 *
 * Deliberately hand-rolled rather than pulling in a router library: SENTINEL has
 * five flat screens and no nested or guarded routes, so a full router would be
 * the largest dependency in the app for the least benefit. The hash form also
 * means the UI works from any static host and from Vite dev without server
 * rewrite rules.
 */

import { useCallback, useEffect, useState } from 'react'

export const ROUTES = [
  { id: 'command', label: 'Command Center', path: '#/command' },
  { id: 'missions', label: 'Missions', path: '#/missions' },
  { id: 'routes', label: 'Routes', path: '#/routes' },
  { id: 'simulations', label: 'Simulations', path: '#/simulations' },
  { id: 'plans', label: 'Plans', path: '#/plans' },
] as const

export type RouteId = (typeof ROUTES)[number]['id']

const DEFAULT_ROUTE: RouteId = 'command'

function readHash(): RouteId {
  const raw = window.location.hash.replace(/^#\/?/, '').split('?')[0]
  const match = ROUTES.find((route) => route.id === raw)
  return match ? match.id : DEFAULT_ROUTE
}

export function useHashRoute(): { route: RouteId; navigate: (id: RouteId) => void } {
  const [route, setRoute] = useState<RouteId>(readHash)

  useEffect(() => {
    const onChange = () => setRoute(readHash())
    window.addEventListener('hashchange', onChange)
    // Normalise a missing or unknown hash so the address bar always reflects a
    // real screen. replace() rather than assignment: no extra history entry.
    if (!ROUTES.some((item) => window.location.hash === item.path)) {
      window.history.replaceState(null, '', ROUTES[0].path)
    }
    return () => window.removeEventListener('hashchange', onChange)
  }, [])

  const navigate = useCallback((id: RouteId) => {
    const target = ROUTES.find((item) => item.id === id)
    if (!target) return
    if (window.location.hash === target.path) return
    window.location.hash = target.path
  }, [])

  return { route, navigate }
}