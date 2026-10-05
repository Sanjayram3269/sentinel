/**
 * Application root.
 *
 * Deliberately thin: routing, the shared data hook, and the shell. Everything
 * operational lives in the page and component layers so the command centre can
 * be read without scrolling through app wiring.
 */

import { AppShell } from './components/layout/AppShell'
import { useAppData } from './hooks/useAppData'
import { useHashRoute, type RouteId } from './hooks/useHashRoute'
import { useNow } from './hooks/useNow'
import { CommandCenter } from './pages/CommandCenter'
import { Missions } from './pages/Missions'
import { Plans } from './pages/Plans'
import { Routes } from './pages/Routes'
import { Simulations } from './pages/Simulations'

export default function App() {
  const { route, navigate } = useHashRoute()
  const now = useNow()
  const data = useAppData()

  return (
    <AppShell
      route={route}
      now={now}
      connection={data.connection}
      mission={data.mission}
    >
      <Page route={route} data={data} navigate={navigate} />
    </AppShell>
  )
}

function Page({
  route,
  data,
  navigate,
}: {
  route: RouteId
  data: ReturnType<typeof useAppData>
  navigate: (id: RouteId) => void
}) {
  switch (route) {
    case 'missions':
      return <Missions data={data} />
    case 'routes':
      return <Routes data={data} />
    case 'simulations':
      return <Simulations data={data} />
    case 'plans':
      return <Plans data={data} navigate={navigate} />
    case 'command':
    default:
      return <CommandCenter data={data} navigate={navigate} />
  }
}
