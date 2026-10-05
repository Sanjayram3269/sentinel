/**
 * Application shell.
 *
 * The sidebar collapses to a top rail below 1024px rather than disappearing:
 * navigation is how an operator moves between the command centre and the
 * supporting evidence, so it must survive a tablet viewport.
 */

import type { ReactNode } from 'react'
import { Link } from 'lucide-react'
import { StatusBadge } from '../ui/primitives'
import { ROUTES, type RouteId } from '../../hooks/useHashRoute'
import { formatClock } from '../../lib/format'
import { missionStatusTone } from '../../lib/status'
import type { ConnectionState, MissionStatusSummary } from '../../hooks/useAppData'

interface AppShellProps {
  route: RouteId
  now: Date
  connection: ConnectionState
  mission: MissionStatusSummary
  /** Rendered next to the mission status in the top bar. */
  actions?: ReactNode
  children: ReactNode
}

export function AppShell({
  route,
  now,
  connection,
  mission,
  actions,
  children,
}: AppShellProps) {
  return (
    <div className="flex h-dvh flex-col overflow-hidden bg-void">
      <header className="flex shrink-0 items-center gap-3 border-b border-graphite-700 bg-graphite-900/90 px-3 py-2 backdrop-blur lg:px-4">
        <div className="flex items-center gap-2">
          <Wordmark />
          <span className="hidden text-[10px] uppercase tracking-[0.14em] text-ink-400 xl:inline">
            Predictive Geoagentic Emergency Response
          </span>
        </div>

        <div className="ml-auto flex items-center gap-2 sm:gap-3">
          {actions}
          <StatusBadge
            label={
              connection === 'live'
                ? 'Live'
                : connection === 'checking'
                  ? 'Checking'
                  : 'Backend offline'
            }
            tone={connection === 'live' ? 'go' : connection === 'checking' ? 'warn' : 'alert'}
            live={connection === 'live'}
            title={
              connection === 'live'
                ? 'Connected to the SENTINEL backend.'
                : connection === 'checking'
                  ? 'Probing the backend health endpoint.'
                  : 'The SENTINEL backend is not responding. Displayed data is demo state.'
            }
          />
          <StatusBadge
            label={`Mission ${mission.statusLabel}`}
            tone={missionStatusTone(mission.status)}
            live={mission.status === 'ACTIVE' || mission.status === 'DISPATCHED'}
            title={mission.objective}
          />
          <time
            dateTime={now.toISOString()}
            className="stat-value hidden text-sm text-ink-100 sm:block"
            title="Local browser time"
          >
            {formatClock(now)} IST
          </time>
        </div>
      </header>

      <div className="flex min-h-0 flex-1 flex-col lg:flex-row">
        <nav
          aria-label="Primary"
          className="flex shrink-0 gap-1 overflow-x-auto border-b border-graphite-700 bg-graphite-900/60 px-2 py-1.5 lg:w-[188px] lg:flex-col lg:overflow-visible lg:border-b-0 lg:border-r lg:px-2 lg:py-3"
        >
          {ROUTES.map((item) => {
            const active = item.id === route
            return (
              <a
                key={item.id}
                href={item.path}
                aria-current={active ? 'page' : undefined}
                className={`flex shrink-0 items-center gap-2 rounded px-2.5 py-1.5 text-[11px] font-semibold uppercase tracking-[0.07em] transition-colors ${
                  active
                    ? 'border border-intel-600/70 bg-intel-600/15 text-intel-300'
                    : 'border border-transparent text-ink-400 hover:bg-graphite-800 hover:text-ink-100'
                }`}
              >
                <Link size={12} aria-hidden="true" />
                {item.label}
              </a>
            )
          })}

          <div className="mt-auto hidden flex-col gap-1 border-t border-graphite-700 pt-2 lg:flex">
            <span className="label-caps">Mission</span>
            <span className="truncate font-mono text-[10px] text-ink-300" title={mission.id}>
              {mission.id}
            </span>
            <span className="text-[10px] leading-relaxed text-ink-400">{mission.objective}</span>
            <span className="mt-1 text-[10px] leading-relaxed text-ink-400">
              Simulation-only. No real infrastructure is controlled.
            </span>
          </div>
        </nav>

        <main className="min-h-0 min-w-0 flex-1 overflow-y-auto">{children}</main>
      </div>
    </div>
  )
}

function Wordmark() {
  return (
    <div className="flex items-center gap-2">
      <span className="relative flex h-6 w-6 items-center justify-center rounded border border-emergency-500/70 bg-emergency-500/10">
        <span
          aria-hidden="true"
          className="pulse-live h-2 w-2 rounded-full bg-emergency-400"
        />
      </span>
      <span className="text-[13px] font-bold uppercase tracking-[0.22em] text-ink-100">
        Sentinel
      </span>
    </div>
  )
}
