/**
 * Missions.
 *
 * A detail view rather than a list: the backend contract has no mission
 * collection endpoint, so the honest thing to show is the selected mission in
 * full, plus its incident and unit records, and to say plainly that no mission
 * index is exposed.
 */

import { Ambulance, Info, Radio, Siren } from 'lucide-react'
import { MissionPanel } from '../components/mission/MissionPanel'
import { DemoBadge, Panel, StatusBadge, Unavailable } from '../components/ui/primitives'
import { EventTimeline } from '../components/timeline/EventTimeline'
import { entriesFromState } from '../lib/timeline'
import { formatDateTime, severityLabel } from '../lib/format'
import type { AppData } from '../hooks/useAppData'

interface PageProps {
  data: AppData
}

export function Missions({ data }: PageProps) {
  const state = data.state
  const mission = state?.mission ?? null
  const incident = state?.incident ?? state?.incidents?.[0] ?? null
  const vehicles = state?.vehicles ?? []

  return (
    <div className="flex flex-col gap-3 p-3">
      <header className="panel flex flex-wrap items-center justify-between gap-2 px-3 py-2.5">
        <div className="flex min-w-0 flex-col gap-0.5">
          <h1 className="text-sm font-semibold uppercase tracking-[0.1em] text-ink-100">
            Missions
          </h1>
          <p className="text-[11px] text-ink-400">
            {mission?.objective ?? 'No mission objective reported.'}
          </p>
        </div>
        <div className="flex items-center gap-1.5">
          {data.demo && <DemoBadge reason={data.demoReason ?? 'Backend unreachable.'} />}
          <button
            type="button"
            onClick={data.refresh}
            className="rounded border border-graphite-600 px-2 py-1 text-[10px] uppercase tracking-[0.08em] text-ink-300 transition-colors hover:border-intel-500 hover:text-intel-300"
          >
            Refresh
          </button>
        </div>
      </header>

      <div className="grid grid-cols-1 gap-3 lg:grid-cols-[minmax(260px,340px)_minmax(0,1fr)]">
        <div className="flex flex-col gap-3">
          <MissionPanel state={state} demo={data.demo} />

          <Panel title="Mission Record" ariaLabel="Mission record">
            <dl className="flex flex-col gap-1.5 text-[11px]">
              <Row label="Mission ID" value={mission?.id ?? null} mono />
              <Row label="Status" value={mission?.status ?? null} />
              <Row label="Priority" value={mission?.priority ?? null} />
              <Row label="Created" value={formatDateTime(mission?.created_at)} />
              <Row label="Started" value={formatDateTime(mission?.started_at)} />
              <Row label="Completed" value={formatDateTime(mission?.completed_at)} />
              <Row label="State updated" value={formatDateTime(state?.updated_at)} />
            </dl>
          </Panel>
        </div>

        <div className="flex min-w-0 flex-col gap-3">
          <Panel
            title="Incident"
            ariaLabel="Incident detail"
            actions={
              incident !== null ? (
                <StatusBadge
                  label={severityLabel(incident.severity)}
                  tone={incident.severity >= 4 ? 'emergency' : 'warn'}
                  live={incident.active}
                />
              ) : undefined
            }
          >
            {incident === null ? (
              <p className="text-[11px] text-ink-400">
                <Unavailable>no incident recorded</Unavailable>
              </p>
            ) : (
              <div className="flex flex-col gap-2.5">
                <div className="flex items-center gap-2">
                  <Siren size={14} className="text-alert-400" />
                  <span className="text-sm font-semibold text-ink-100">{incident.type}</span>
                </div>
                <p className="text-[11px] leading-relaxed text-ink-300">
                  {incident.description}
                </p>
                <dl className="grid grid-cols-2 gap-2 border-t border-graphite-700 pt-2 text-[11px]">
                  <Row label="Incident ID" value={incident.id} mono />
                  <Row label="Occurred" value={formatDateTime(incident.occurred_at)} />
                  <Row
                    label="Position"
                    value={
                      incident.location
                        ? `${incident.location.latitude.toFixed(6)}, ${incident.location.longitude.toFixed(6)}`
                        : null
                    }
                    mono
                  />
                  <Row label="Active" value={incident.active ? 'yes' : 'no'} />
                </dl>
              </div>
            )}
          </Panel>

          <Panel
            title={`Units (${vehicles.length})`}
            ariaLabel="Assigned vehicles"
          >
            {vehicles.length === 0 ? (
              <p className="text-[11px] text-ink-400">
                <Unavailable>no vehicles assigned</Unavailable>
              </p>
            ) : (
              <ul className="flex flex-col gap-2">
                {vehicles.map((vehicle) => (
                  <li key={vehicle.id} className="rounded border border-graphite-700 bg-graphite-900/50 px-2.5 py-2">
                    <div className="flex items-center justify-between gap-2">
                      <span className="flex items-center gap-1.5">
                        <Ambulance size={12} className="text-emergency-400" />
                        <span className="font-mono text-[12px] text-ink-100">
                          {vehicle.call_sign}
                        </span>
                      </span>
                      <StatusBadge
                        label={vehicle.status}
                        tone={vehicle.status === 'OFFLINE' ? 'muted' : 'intel'}
                        live={vehicle.status === 'EN_ROUTE'}
                      />
                    </div>
                    <dl className="mt-2 grid grid-cols-2 gap-2 text-[11px]">
                      <Row label="Type" value={vehicle.vehicle_type} />
                      <Row label="Vehicle ID" value={vehicle.id} mono />
                      <Row
                        label="Position"
                        value={
                          vehicle.current_location
                            ? `${vehicle.current_location.latitude.toFixed(6)}, ${vehicle.current_location.longitude.toFixed(6)}`
                            : null
                        }
                        mono
                      />
                      <Row
                        label="Speed"
                        value={vehicle.speed === null ? null : `${vehicle.speed.toFixed(2)} m/s`}
                      />
                    </dl>
                    {vehicle.capability !== null && (
                      <div className="mt-2 flex flex-wrap gap-1 border-t border-graphite-700 pt-2">
                        {Object.entries(vehicle.capability).map(([key, value]) => (
                          <span
                            key={key}
                            className="rounded border border-graphite-700 px-1.5 py-0.5 text-[10px] text-ink-300"
                          >
                            {key.replace(/_/g, ' ')}: {String(value)}
                          </span>
                        ))}
                      </div>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </Panel>

          <Panel title="Recent Activity" ariaLabel="Recent mission activity" flush={false}>
            <EventTimeline
              entries={entriesFromState(state?.latest_events ?? [])}
              limit={12}
              demo={data.demo}
            />
          </Panel>

          <p className="panel flex items-start gap-2 px-3 py-2 text-[10px] leading-relaxed text-ink-400">
            <Info size={12} className="mt-0.5 shrink-0 text-intel-400" />
            <span>
              <Radio size={10} className="mr-1 inline" />
              The contract exposes no mission collection endpoint, so this page
              shows the selected mission in full rather than an invented list.
              Switch missions by changing the development selection constant.
            </span>
          </p>
        </div>
      </div>
    </div>
  )
}

function Row({
  label,
  value,
  mono = false,
}: {
  label: string
  value: string | number | null | undefined
  mono?: boolean
}) {
  const missing = value === null || value === undefined
  return (
    <div className="flex min-w-0 items-baseline justify-between gap-2">
      <dt className="shrink-0 text-[10px] uppercase tracking-[0.08em] text-ink-400">
        {label}
      </dt>
      <dd
        className={`truncate text-right ${mono ? 'font-mono text-[10px]' : 'text-[11px]'} ${
          missing ? 'italic text-ink-400' : 'text-ink-100'
        }`}
        title={missing ? undefined : String(value)}
      >
        {missing ? <Unavailable /> : String(value)}
      </dd>
    </div>
  )
}
