/**
 * Display formatting.
 *
 * The important rule here: a missing value formats as "unavailable", never as
 * "0". An operator reading "0 s stopped" would draw a conclusion the data does
 * not support, so `null` has to stay visibly distinct from a real zero.
 */

export const UNAVAILABLE = 'unavailable'

export function isAvailable(value: number | null | undefined): value is number {
  return typeof value === 'number' && Number.isFinite(value)
}

export function formatSeconds(value: number | null | undefined, suffix = 's'): string {
  if (!isAvailable(value)) return UNAVAILABLE
  if (value < 60) return `${value.toFixed(value % 1 === 0 ? 0 : 1)} ${suffix}`
  const minutes = Math.floor(value / 60)
  const seconds = Math.round(value % 60)
  return `${minutes}m ${String(seconds).padStart(2, '0')}s`
}

export function formatDuration(value: number | null | undefined): string {
  return formatSeconds(value)
}

export function formatDistance(meters: number | null | undefined): string {
  if (!isAvailable(meters)) return UNAVAILABLE
  if (meters < 1000) return `${meters.toFixed(0)} m`
  return `${(meters / 1000).toFixed(2)} km`
}

export function formatPercent(
  value: number | null | undefined,
  digits = 1,
): string {
  if (!isAvailable(value)) return UNAVAILABLE
  return `${value.toFixed(digits)}%`
}

/** 0..1 ratios (risk, resilience) shown as percentages with a % sign. */
export function formatRatio(value: number | null | undefined, digits = 2): string {
  if (!isAvailable(value)) return UNAVAILABLE
  return value.toFixed(digits)
}

export function formatDelta(value: number | null | undefined, suffix = 's'): string {
  if (!isAvailable(value)) return UNAVAILABLE
  const sign = value > 0 ? '+' : ''
  return `${sign}${value.toFixed(1)}${suffix}`
}

/** Whole seconds as `MM:SS`, for the mission clock. */
export function formatClock(date: Date): string {
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`
}

export function formatTimeOfDay(iso: string | null | undefined): string {
  if (!iso) return UNAVAILABLE
  const parsed = new Date(iso)
  if (Number.isNaN(parsed.getTime())) return UNAVAILABLE
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${pad(parsed.getHours())}:${pad(parsed.getMinutes())}:${pad(parsed.getSeconds())}`
}

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return UNAVAILABLE
  const parsed = new Date(iso)
  if (Number.isNaN(parsed.getTime())) return UNAVAILABLE
  return parsed.toLocaleString(undefined, {
    year: 'numeric',
    month: 'short',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  })
}

export function truncate(text: string, max = 96): string {
  return text.length <= max ? text : `${text.slice(0, max - 1)}…`
}

export function severityLabel(severity: number | null | undefined): string {
  if (!isAvailable(severity)) return UNAVAILABLE
  return `SEV-${severity}`
}

/** Low/medium/high banding used for risk and resilience readouts. */
export type Band = 'low' | 'medium' | 'high' | 'unknown'

export function riskBand(risk: number | null | undefined): Band {
  if (!isAvailable(risk)) return 'unknown'
  if (risk < 0.34) return 'low'
  if (risk < 0.67) return 'medium'
  return 'high'
}

export function scoreBand(score: number | null | undefined): Band {
  if (!isAvailable(score)) return 'unknown'
  if (score < 0.4) return 'low'
  if (score < 0.7) return 'medium'
  return 'high'
}
