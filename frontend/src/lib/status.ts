/**
 * Status-to-tone mapping.
 *
 * Centralised so a status looks identical everywhere it appears, and so the
 * colour choices stay in one reviewable place rather than scattered as Tailwind
 * class strings through components.
 */

import type { PlanStatus, ResilienceRole, RouteStatus } from '../types/api'

export type Tone =
  | 'neutral'
  | 'info'
  | 'intel'
  | 'warn'
  | 'emergency'
  | 'alert'
  | 'go'
  | 'muted'

interface ToneClasses {
  text: string
  border: string
  bg: string
  dot: string
}

export const TONES: Record<Tone, ToneClasses> = {
  neutral: {
    text: 'text-ink-200',
    border: 'border-graphite-600',
    bg: 'bg-graphite-800',
    dot: 'bg-ink-400',
  },
  info: {
    text: 'text-ink-200',
    border: 'border-graphite-600',
    bg: 'bg-graphite-850',
    dot: 'bg-ink-300',
  },
  intel: {
    text: 'text-intel-300',
    border: 'border-intel-600',
    bg: 'bg-intel-600/10',
    dot: 'bg-intel-400',
  },
  warn: {
    text: 'text-warn-400',
    border: 'border-warn-500/60',
    bg: 'bg-warn-500/10',
    dot: 'bg-warn-400',
  },
  emergency: {
    text: 'text-emergency-400',
    border: 'border-emergency-500/60',
    bg: 'bg-emergency-500/10',
    dot: 'bg-emergency-400',
  },
  alert: {
    text: 'text-alert-400',
    border: 'border-alert-500/60',
    bg: 'bg-alert-500/10',
    dot: 'bg-alert-400',
  },
  go: {
    text: 'text-go-400',
    border: 'border-go-500/60',
    bg: 'bg-go-500/10',
    dot: 'bg-go-400',
  },
  muted: {
    text: 'text-ink-400',
    border: 'border-graphite-700',
    bg: 'bg-graphite-900',
    dot: 'bg-graphite-500',
  },
}

export function planStatusTone(status: PlanStatus | string | null): Tone {
  switch (status) {
    case 'DRAFT':
      return 'muted'
    case 'SUBMITTED':
    case 'READY_FOR_REVIEW':
      return 'warn'
    case 'APPROVED':
      return 'go'
    case 'REJECTED':
      return 'alert'
    case 'SUPERSEDED':
      return 'neutral'
    case 'EXECUTION_AUTHORIZED':
      return 'intel'
    case 'EXECUTING':
      return 'emergency'
    case 'REPLAN_REQUIRED':
      return 'emergency'
    default:
      return 'muted'
  }
}

/** Human-facing label for a plan state, including the review-required callout. */
export function planStatusLabel(status: PlanStatus | string | null): string {
  switch (status) {
    case 'DRAFT':
      return 'DRAFT'
    case 'SUBMITTED':
    case 'READY_FOR_REVIEW':
      return 'PENDING REVIEW'
    case 'APPROVED':
      return 'APPROVED'
    case 'REJECTED':
      return 'REJECTED'
    case 'SUPERSEDED':
      return 'SUPERSEDED'
    case 'EXECUTION_AUTHORIZED':
      return 'AUTHORIZED'
    case 'EXECUTING':
      return 'EXECUTING'
    case 'REPLAN_REQUIRED':
      return 'REPLAN REQUIRED'
    default:
      return String(status ?? 'UNKNOWN').toUpperCase()
  }
}

export function routeStatusTone(status: RouteStatus | string | null): Tone {
  switch (status) {
    case 'ACTIVE':
      return 'go'
    case 'CANDIDATE':
      return 'info'
    case 'DEGRADED':
      return 'warn'
    case 'FAILED':
      return 'alert'
    case 'ABORTED':
      return 'alert'
    case 'COMPLETED':
      return 'neutral'
    default:
      return 'muted'
  }
}

export function resilienceTone(level: string | null | undefined): Tone {
  if (!level) return 'muted'
  const upper = level.toUpperCase()
  if (upper.includes('HIGH')) return 'go'
  if (upper.includes('MEDIUM')) return 'warn'
  if (upper.includes('LOW')) return 'emergency'
  return 'muted'
}

export function missionStatusTone(status: string | null | undefined): Tone {
  switch (status) {
    case 'ACTIVE':
      return 'emergency'
    case 'DISPATCHED':
      return 'warn'
    case 'COMPLETED':
      return 'go'
    case 'CANCELLED':
      return 'alert'
    case 'PAUSED':
      return 'warn'
    default:
      return 'info'
  }
}

/** Distinct, ordered visual hierarchy for the three resilience roles. */
export const ROLE_TONE: Record<ResilienceRole, Tone> = {
  PRIMARY: 'go',
  BACKUP: 'warn',
  CONTINGENCY: 'intel',
}

export const ROLE_LABEL: Record<ResilienceRole, string> = {
  PRIMARY: 'PRIMARY',
  BACKUP: 'BACKUP',
  CONTINGENCY: 'CONTINGENCY',
}

/**
 * Map line styling. Primary is solid and heaviest, backups progressively
 * quieter. `dash` is a mutable number[] because MapLibre's paint specification
 * types it as mutable, so a readonly tuple would not assign.
 */
export interface LineStyle {
  color: string
  width: number
  opacity: number
  dash: number[] | null
}

export type LineRole = 'PRIMARY' | 'BACKUP' | 'CONTINGENCY' | 'ALTERNATE'

export const ROLE_LINE: Record<LineRole, LineStyle> = {
  PRIMARY: { color: '#22c55e', width: 5, opacity: 0.95, dash: null },
  BACKUP: { color: '#eab308', width: 3, opacity: 0.7, dash: [1.5, 1.5] },
  CONTINGENCY: { color: '#22d3ee', width: 2, opacity: 0.55, dash: [0.6, 1.6] },
  ALTERNATE: { color: '#3b4855', width: 2, opacity: 0.45, dash: [0.4, 2] },
}
