/**
 * Timeline normalisation.
 *
 * The backend exposes two event projections with different key names — `/events`
 * returns `event_id`/`timestamp`, `/state` returns `id`/`occurred_at`. Both are
 * accepted and folded into one row shape so the timeline component stays purely
 * presentational, and so the two endpoints can feed the same list without a
 * lossy intermediate copy.
 */

import type { ComponentType } from 'react'
import {
  Ambulance,
  CircleAlert,
  CircleCheck,
  CloudLightning,
  FileClock,
  GitBranch,
  Radio,
  RefreshCw,
  Route,
  ShieldCheck,
  Siren,
  Waypoints,
} from 'lucide-react'
import type { Tone } from './status'
import type { EventState, MissionEventRead } from '../types/api'

/** One timeline row, normalised from either event projection. */
export interface TimelineEntry {
  id: string
  type: string
  at: string
  source: string
  detail: string | null
}

export function entriesFromEvents(events: MissionEventRead[]): TimelineEntry[] {
  return events.map((event) => ({
    id: event.event_id,
    type: event.event_type,
    at: event.timestamp,
    source: event.source,
    detail: summarise(event.payload),
  }))
}

export function entriesFromState(events: EventState[]): TimelineEntry[] {
  return events.map((event) => ({
    id: event.id,
    type: event.event_type,
    at: event.occurred_at,
    source: event.source,
    detail: summarise(event.payload),
  }))
}

/**
 * Pulls the most operationally meaningful field out of a free-form payload.
 * Inventing a sentence here would be worse than showing nothing, so an
 * unrecognised payload simply renders without a detail line.
 */
function summarise(payload: Record<string, unknown> | undefined): string | null {
  if (!payload) return null
  for (const key of ['message', 'reason', 'error_code', 'objective', 'status', 'action']) {
    const value = payload[key]
    if (typeof value === 'string' && value.trim() !== '') return value.slice(0, 160)
  }
  return null
}

export interface EventPresentation {
  label: string
  tone: Tone
  Icon: ComponentType<{ size?: number; className?: string }>
}

const PRESENTATION: Record<string, EventPresentation> = {
  MISSION_CREATED: { label: 'Mission created', tone: 'info', Icon: Radio },
  MISSION_STATUS_CHANGED: { label: 'Mission status changed', tone: 'intel', Icon: Radio },
  INCIDENT_CREATED: { label: 'Incident detected', tone: 'alert', Icon: CircleAlert },
  INCIDENT_UPDATED: { label: 'Incident updated', tone: 'warn', Icon: CircleAlert },
  ACCIDENT_DETECTED: { label: 'Accident detected', tone: 'alert', Icon: Siren },
  HAZARD_UPDATED: { label: 'Hazard reported', tone: 'warn', Icon: CloudLightning },
  ROAD_CLOSURE: { label: 'Road closure', tone: 'warn', Icon: Route },
  CONGESTION_CHANGED: { label: 'Congestion changed', tone: 'muted', Icon: Waypoints },
  VEHICLE_STATUS_CHANGED: { label: 'Unit status changed', tone: 'intel', Icon: Ambulance },
  VEHICLE_POSITION_UPDATE: { label: 'Unit position', tone: 'muted', Icon: Ambulance },
  ROUTE_ASSIGNED: { label: 'Route assigned', tone: 'go', Icon: Route },
  ROUTE_UPDATED: { label: 'Route updated', tone: 'intel', Icon: Route },
  ROUTE_FAILED: { label: 'Route failed', tone: 'alert', Icon: CircleAlert },
  ROUTE_DEVIATION: { label: 'Route deviation', tone: 'warn', Icon: GitBranch },
  BACKUP_ROUTE_DEGRADED: { label: 'Backup degraded', tone: 'warn', Icon: GitBranch },
  REPLAN_TRIGGERED: { label: 'Replan triggered', tone: 'emergency', Icon: RefreshCw },
  PREDICTION_UPDATED: { label: 'Prediction updated', tone: 'intel', Icon: CloudLightning },
  HOSPITAL_CAPACITY_CHANGED: { label: 'Hospital capacity', tone: 'intel', Icon: ShieldCheck },
  CLEARPATH_REQUESTED: { label: 'CLEARPATH requested', tone: 'intel', Icon: CloudLightning },
  CLEARPATH_UPDATED: { label: 'CLEARPATH updated', tone: 'intel', Icon: CloudLightning },
  PLAN_CREATED: { label: 'Plan drafted', tone: 'intel', Icon: FileClock },
  PLAN_READY_FOR_REVIEW: { label: 'Human approval required', tone: 'warn', Icon: ShieldCheck },
  PLAN_APPROVED: { label: 'Plan approved', tone: 'go', Icon: CircleCheck },
  PLAN_REJECTED: { label: 'Plan rejected', tone: 'alert', Icon: CircleAlert },
  PLAN_MODIFIED: { label: 'Plan modified', tone: 'warn', Icon: GitBranch },
  PLAN_SUPERSEDED: { label: 'Plan superseded', tone: 'muted', Icon: FileClock },
  PLAN_REQUIRES_REAPPROVAL: { label: 'Re-approval required', tone: 'emergency', Icon: ShieldCheck },
  EXECUTION_AUTHORIZED: { label: 'Execution authorised', tone: 'emergency', Icon: CircleCheck },
  MISSION_EXECUTION_STARTED: { label: 'Execution started', tone: 'emergency', Icon: CircleCheck },
  MISSION_EXECUTION_COMPLETED: { label: 'Execution completed', tone: 'go', Icon: CircleCheck },
  SIMULATION_STARTED: { label: 'Simulation started', tone: 'intel', Icon: CloudLightning },
  SIMULATION_COMPLETED: { label: 'Simulation completed', tone: 'go', Icon: CircleCheck },
  SIMULATION_FAILED: { label: 'Simulation failed', tone: 'alert', Icon: CircleAlert },
  TELEMETRY_RECEIVED: { label: 'Telemetry received', tone: 'muted', Icon: Radio },
}

/** Icon colour per tone. Centralised so a failure never looks like a success. */
export const TONE_ICON_COLOR: Record<Tone, string> = {
  neutral: 'text-ink-300',
  info: 'text-ink-300',
  intel: 'text-intel-300',
  warn: 'text-warn-400',
  emergency: 'text-emergency-400',
  alert: 'text-alert-400',
  go: 'text-go-400',
  muted: 'text-ink-400',
}

export function presentationFor(type: string): EventPresentation {
  return (
    PRESENTATION[type] ?? {
      label: type.replace(/_/g, ' ').toLowerCase(),
      tone: 'neutral',
      Icon: CircleAlert,
    }
  )
}
