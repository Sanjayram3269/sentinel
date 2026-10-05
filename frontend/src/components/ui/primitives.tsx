/**
 * Shared UI primitives.
 *
 * Small and deliberately dumb: each renders one thing, takes explicit props, and
 * owns its own accessibility. Buttons are real `<button>` elements so keyboard
 * activation, focus rings and disabled semantics come for free.
 */

import type { ReactNode } from 'react'
import { TONES, type Tone } from '../../lib/status'

interface StatusBadgeProps {
  label: string
  tone?: Tone
  /** Adds the pulsing live dot. Reserve for genuinely live indicators. */
  live?: boolean
  size?: 'sm' | 'md'
  title?: string
}

const BADGE_SIZE = {
  sm: 'text-[10px] px-1.5 py-0.5 gap-1',
  md: 'text-[11px] px-2 py-0.5 gap-1.5',
}

export function StatusBadge({
  label,
  tone = 'neutral',
  live = false,
  size = 'sm',
  title,
}: StatusBadgeProps) {
  const classes = TONES[tone]
  return (
    <span
      title={title}
      className={`inline-flex items-center rounded border font-semibold uppercase tracking-[0.08em] ${BADGE_SIZE[size]} ${classes.text} ${classes.border} ${classes.bg}`}
    >
      {live ? (
        <span
          aria-hidden="true"
          className={`pulse-live h-1.5 w-1.5 rounded-full ${classes.dot}`}
        />
      ) : (
        <span aria-hidden="true" className={`h-1.5 w-1.5 rounded-full ${classes.dot}`} />
      )}
      {label}
    </span>
  )
}

interface PanelProps {
  title?: ReactNode
  actions?: ReactNode
  children: ReactNode
  className?: string
  bodyClassName?: string
  /** Removes the body padding, for a full-bleed map or list. */
  flush?: boolean
  ariaLabel?: string
}

export function Panel({
  title,
  actions,
  children,
  className = '',
  bodyClassName = 'p-3',
  flush = false,
  ariaLabel,
}: PanelProps) {
  return (
    <section
      aria-label={ariaLabel}
      className={`panel flex min-h-0 flex-col ${className}`}
    >
      {title !== undefined && (
        <header className="panel-header shrink-0">
          <h2 className="panel-title">{title}</h2>
          {actions !== undefined && <div className="flex items-center gap-1.5">{actions}</div>}
        </header>
      )}
      {/* A flush body is a definite-height flex column: percentage heights on
          children of an auto-sized panel resolve to zero. */}
      <div
        className={
          flush
            ? 'relative flex min-h-0 flex-1 flex-col'
            : `min-h-0 flex-1 ${bodyClassName}`
        }
      >
        {children}
      </div>
    </section>
  )
}

interface MetricProps {
  label: string
  value: ReactNode
  hint?: ReactNode
  tone?: Tone
  size?: 'sm' | 'md' | 'lg'
}

const METRIC_SIZE = {
  sm: 'text-base',
  md: 'text-xl',
  lg: 'text-2xl',
}

export function Metric({ label, value, hint, tone = 'neutral', size = 'md' }: MetricProps) {
  const classes = TONES[tone]
  return (
    <div className="flex min-w-0 flex-col gap-0.5">
      <span className="label-caps">{label}</span>
      <span className={`stat-value ${METRIC_SIZE[size]} ${classes.text}`}>{value}</span>
      {hint !== undefined && (
        <span className="truncate text-[10px] text-ink-400" title={typeof hint === 'string' ? hint : undefined}>
          {hint}
        </span>
      )}
    </div>
  )
}

interface ButtonProps {
  children: ReactNode
  onClick?: () => void
  variant?: 'primary' | 'secondary' | 'ghost' | 'danger' | 'approve'
  disabled?: boolean
  loading?: boolean
  type?: 'button' | 'submit'
  title?: string
  ariaLabel?: string
  className?: string
}

const BUTTON_VARIANT = {
  primary:
    'bg-intel-600 text-white border-intel-500 hover:bg-intel-500 disabled:bg-graphite-700 disabled:text-ink-400 disabled:border-graphite-600',
  secondary:
    'bg-graphite-800 text-ink-100 border-graphite-600 hover:bg-graphite-700 hover:border-graphite-500',
  ghost:
    'bg-transparent text-ink-300 border-transparent hover:bg-graphite-800 hover:text-ink-100',
  danger:
    'bg-alert-600 text-white border-alert-500 hover:bg-alert-500 disabled:bg-graphite-700 disabled:text-ink-400',
  approve:
    'bg-go-500 text-void border-go-400 font-bold hover:bg-go-400 disabled:bg-graphite-700 disabled:text-ink-400 disabled:font-normal',
} as const

export function Button({
  children,
  onClick,
  variant = 'secondary',
  disabled = false,
  loading = false,
  type = 'button',
  title,
  ariaLabel,
  className = '',
}: ButtonProps) {
  const inactive = disabled || loading
  return (
    <button
      type={type}
      onClick={onClick}
      disabled={inactive}
      title={title}
      aria-label={ariaLabel}
      aria-busy={loading || undefined}
      className={`inline-flex items-center justify-center gap-1.5 rounded border px-3 py-1.5 text-[11px] font-semibold uppercase tracking-[0.07em] transition-colors duration-150 disabled:cursor-not-allowed ${BUTTON_VARIANT[variant]} ${className}`}
    >
      {loading && (
        <span
          aria-hidden="true"
          className="h-2.5 w-2.5 animate-spin rounded-full border border-current border-t-transparent"
        />
      )}
      {children}
    </button>
  )
}

/** Renders "unavailable" in a visibly muted way instead of faking a number. */
export function Unavailable({ children = 'unavailable' }: { children?: ReactNode }) {
  return (
    <span className="text-ink-400 italic" title="The backend did not provide this value.">
      {children}
    </span>
  )
}

interface EmptyStateProps {
  title: string
  detail?: string
  icon?: ReactNode
}

export function EmptyState({ title, detail, icon }: EmptyStateProps) {
  return (
    <div className="flex h-full min-h-[80px] flex-col items-center justify-center gap-1.5 px-4 py-6 text-center">
      {icon !== undefined && <div className="text-ink-400">{icon}</div>}
      <p className="text-xs font-semibold text-ink-300">{title}</p>
      {detail !== undefined && (
        <p className="max-w-[36ch] text-[11px] leading-relaxed text-ink-400">{detail}</p>
      )}
    </div>
  )
}

/**
 * The prominent DEMO DATA marker.
 *
 * Used only when the backend could not be reached or the value is a committed
 * demo fixture. It is loud on purpose: the whole point is that a viewer can
 * never mistake demo state for live state.
 */
export function DemoBadge({ reason }: { reason: string }) {
  return (
    <span
      title={reason}
      className="inline-flex items-center gap-1 rounded border border-warn-500/70 bg-warn-500/15 px-1.5 py-0.5 text-[10px] font-bold uppercase tracking-[0.1em] text-warn-400"
    >
      Demo data
    </span>
  )
}
