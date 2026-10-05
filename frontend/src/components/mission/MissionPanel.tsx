/** Mission identity, incident severity, and the active unit. */

import type { ReactNode } from 'react'
import { Ambulance, Radio, TriangleAlert } from 'lucide-react'
import { Metric, Panel, StatusBadge } from '../ui/primitives'
import { formatTimeOfDay, severityLabel } from '../../lib/format'
import { missionStatusTone } from '../../lib/status'
import type { MissionStateRead } from '../../types/api'

interface MissionPanelProps {
  state: MissionStateRead | null
  demo: boolean
}

export function MissionPanel({ state, demo }: MissionPanelProps) {
  const incident = state?.incident ?? null
  const vehicle = state?.vehicles?.[0] ?? null
  const mission = state?.mission ?? null
  const status = mission?.status ?? state?.status ?? null

  return (
    <Panel
      title="Mission"
      ariaLabel="Mission details"
      actions={
        <StatusBadge
          label={status ?? 'UNKNOWN'}
          tone={missionStatusTone(status)}
          live={status === 'ACTIVE' || status === 'DISPATCHED'}
        />
      }
    >
      <div className="flex flex-col gap-3">
        <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
          <span className="font-mono text-base font-semibold tracking-tight text-ink-100">
            {vehicle?.call_sign ?? 'AMBULANCE'}
          </span>
          <span className="text-[10px] uppercase tracking-[0.1em] text-ink-400">
            {vehicle?.vehicle_type ?? '—'}
          </span>
        </div>

        <p className="line-clamp-2 text-[11px] leading-relaxed text-ink-400">
          {mission?.objective ?? 'No mission objective reported by the backend.'}
        </p>

        <div className="grid grid-cols-2 gap-2.5 border-t border-graphite-700 pt-2.5">
          <Metric
            label="Incident"
            size="sm"
            value={<span className="text-sm">{incident?.type ?? '—'}</span>}
            tone="alert"
          />
          <Metric
            label="Severity"
            size="sm"
            value={
              <span className="text-sm">{severityLabel(incident?.severity ?? null)}</span>
            }
            tone={incident && incident.severity >= 4 ? 'emergency' : 'warn'}
          />
        </div>

        <dl className="flex flex-col gap-1 border-t border-graphite-700 pt-2.5 text-[11px]">
          <Row
            icon={<Radio size={11} className="text-intel-400" />}
            label="Mission ID"
            value={mission?.id ?? 'unavailable'}
            mono
          />
          <Row
            icon={<Ambulance size={11} className="text-emergency-400" />}
            label="Unit status"
            value={vehicle?.status ?? 'unavailable'}
          />
          <Row
            icon={<TriangleAlert size={11} className="text-ink-400" />}
            label="Detected"
            value={formatTimeOfDay(incident?.occurred_at)}
          />
        </dl>

        {demo && (
          <p className="border-t border-graphite-700 pt-2 text-[10px] leading-relaxed text-ink-400">
            Offline snapshot. Recorded mission values, not a live feed.
          </p>
        )}
      </div>
    </Panel>
  )
}

function Row({
  icon,
  label,
  value,
  mono = false,
}: {
  icon: ReactNode
  label: string
  value: string
  mono?: boolean
}) {
  const isUnavailable = value === 'unavailable' || value === '—'
  return (
    <div className="flex items-center justify-between gap-2">
      <dt className="flex shrink-0 items-center gap-1.5 text-ink-400">
        {icon}
        <span className="text-[10px] uppercase tracking-[0.08em]">{label}</span>
      </dt>
      <dd
        className={`truncate text-right ${mono ? 'font-mono text-[10px]' : 'text-[11px]'} ${
          isUnavailable ? 'italic text-ink-400' : 'text-ink-200'
        }`}
        title={value}
      >
        {value}
      </dd>
    </div>
  )
}
