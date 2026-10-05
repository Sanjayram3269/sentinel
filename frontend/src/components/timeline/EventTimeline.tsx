/**
 * Operational event timeline.
 *
 * Renders normalised rows from `lib/timeline.ts`. Event types are mapped to
 * icons and tones in one place so the list stays legible: an operator scanning
 * it should spot a failure or a replan trigger without reading the text.
 */

import { EmptyState, Panel } from '../ui/primitives'
import { formatTimeOfDay } from '../../lib/format'
import {
  presentationFor,
  TONE_ICON_COLOR,
  type TimelineEntry,
} from '../../lib/timeline'

interface EventTimelineProps {
  entries: TimelineEntry[]
  /** Caps the visible list; the rest stay reachable by raising the limit. */
  limit?: number
  onShowAll?: () => void
  demo?: boolean
}

export function EventTimeline({
  entries,
  limit = 8,
  onShowAll,
  demo = false,
}: EventTimelineProps) {
  // Newest first: the thing that just happened is what an operator needs.
  const ordered = [...entries].sort((a, b) => Date.parse(b.at) - Date.parse(a.at))
  const visible = ordered.slice(0, limit)
  const hidden = ordered.length - visible.length

  return (
    <Panel
      title="Activity"
      ariaLabel="Mission activity timeline"
      actions={
        <span className="text-[10px] uppercase tracking-[0.08em] text-ink-400">
          {ordered.length} event{ordered.length === 1 ? '' : 's'}
        </span>
      }
    >
      {visible.length === 0 ? (
        <EmptyState
          title="No events recorded"
          detail="The backend has not reported any activity for this mission yet."
          icon={<PulseGlyph />}
        />
      ) : (
        <ol className="relative flex flex-col gap-0">
          {/* Connector rail, behind the dots. */}
          <span
            aria-hidden="true"
            className="absolute bottom-2 left-[9px] top-2 w-px bg-graphite-700"
          />
          {visible.map((entry) => {
            const presentation = presentationFor(entry.type)
            const { Icon } = presentation
            return (
              <li key={entry.id} className="relative flex gap-2.5 py-1.5">
                <span
                  className={`relative z-10 mt-0.5 flex h-[19px] w-[19px] shrink-0 items-center justify-center rounded-full border border-graphite-700 bg-graphite-900 ${TONE_ICON_COLOR[presentation.tone]}`}
                >
                  <Icon size={10} />
                </span>
                <div className="flex min-w-0 flex-1 flex-col">
                  <div className="flex items-baseline justify-between gap-2">
                    <span className="truncate text-[11px] font-medium text-ink-100">
                      {presentation.label}
                    </span>
                    <time
                      dateTime={entry.at}
                      className="shrink-0 font-mono text-[10px] text-ink-400"
                    >
                      {formatTimeOfDay(entry.at)}
                    </time>
                  </div>
                  {entry.detail !== null && (
                    <span className="truncate text-[10px] text-ink-400" title={entry.detail}>
                      {entry.detail}
                    </span>
                  )}
                </div>
              </li>
            )
          })}
        </ol>
      )}

      {(hidden > 0 || demo) && (
        <div className="mt-1 flex items-center justify-between gap-2 border-t border-graphite-700 pt-1.5">
          {hidden > 0 && onShowAll !== undefined ? (
            <button
              type="button"
              onClick={onShowAll}
              className="text-[10px] uppercase tracking-[0.08em] text-intel-300 transition-colors hover:text-intel-200"
            >
              Show {hidden} more
            </button>
          ) : hidden > 0 ? (
            <span className="text-[10px] uppercase tracking-[0.08em] text-ink-400">
              {hidden} older not shown
            </span>
          ) : (
            <span />
          )}
          {demo && (
            <span
              title="Recorded events shown because the backend is unreachable."
              className="text-[10px] font-bold uppercase tracking-[0.1em] text-warn-400"
            >
              Demo data
            </span>
          )}
        </div>
      )}
    </Panel>
  )
}

function PulseGlyph() {
  return (
    <svg width="18" height="18" viewBox="0 0 18 18" aria-hidden="true" fill="none">
      <path
        d="M1 9h4l2-5 3 10 2-5h5"
        stroke="currentColor"
        strokeWidth="1.4"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  )
}
