/**
 * CLEARPATH comparison normalisation.
 *
 * Lives in `lib/` rather than beside the panel because these are pure parsers,
 * not rendering concerns, and mixing them into a component module breaks
 * fast-refresh for every developer editing the panel.
 *
 * The rule the whole file obeys: a comparison is returned only when both arms
 * actually reported metrics. If they did not, it returns null so the caller
 * falls back to clearly-labelled reference figures rather than rendering a
 * measurement that never happened.
 */

import { DEMO_CLEARPATH } from './demoData'
import type { SimulationEvidenceRead, SimulationPairEvidenceRead } from '../types/api'

/** One side of a comparison, normalised from whichever shape supplied it. */
export interface ArmMetrics {
  travelTimeSeconds: number | null
  stoppedTimeSeconds: number | null
  stops: number | null
  routeCompleted: boolean | null
  /** `SimulationStatus` when the arm came from a full run record; null for the flattened review projection. */
  status: string | null
  errorCode: string | null
  errorMessage: string | null
}

export interface ClearpathComparison {
  baseline: ArmMetrics
  clearpath: ArmMetrics
  improvementPercent: number | null
  stoppedDelta: number | null
  stopsDelta: number | null
  seed: number | null
  network: string | null
  /** Where the numbers came from: a live measured pair, or the committed demo run. */
  provenance: 'live' | 'demo'
}

/**
 * Reads the review package's flattened evidence block. Returns null when the
 * pair is not comparable, so the caller can fall back rather than render a
 * number the backend never measured.
 */
export function comparisonFromReview(
  evidence: SimulationEvidenceRead | null | undefined,
): ClearpathComparison | null {
  if (!evidence || !evidence.comparable) return null
  const baseline = evidence.baseline_metrics
  const clearpath = evidence.clearpath_metrics
  if (!baseline || !clearpath) return null
  return {
    baseline: {
      travelTimeSeconds: baseline.emergency_vehicle_travel_time_seconds,
      stoppedTimeSeconds: baseline.stopped_time_seconds,
      stops: baseline.number_of_stops,
      routeCompleted: baseline.route_completed,
      status: null,
      errorCode: null,
      errorMessage: null,
    },
    clearpath: {
      travelTimeSeconds: clearpath.emergency_vehicle_travel_time_seconds,
      stoppedTimeSeconds: clearpath.stopped_time_seconds,
      stops: clearpath.number_of_stops,
      routeCompleted: clearpath.route_completed,
      status: null,
      errorCode: null,
      errorMessage: null,
    },
    improvementPercent: evidence.travel_time_improvement_percent,
    stoppedDelta: evidence.stopped_time_delta_seconds,
    stopsDelta: evidence.stops_delta,
    seed: evidence.seed,
    network: null,
    provenance: 'live',
  }
}

/** Reads the full `POST /simulate` evidence object. */
export function comparisonFromPair(
  pair: SimulationPairEvidenceRead | null | undefined,
): ClearpathComparison | null {
  if (!pair) return null
  const arm = (run: SimulationPairEvidenceRead['baseline']): ArmMetrics => ({
    travelTimeSeconds: run.metrics?.emergency_vehicle_travel_time_seconds ?? null,
    stoppedTimeSeconds: run.metrics?.stopped_time_seconds ?? null,
    stops: run.metrics?.number_of_stops ?? null,
    routeCompleted: run.metrics?.route_completed ?? null,
    status: run.status,
    errorCode: run.error_code,
    errorMessage: run.error_message,
  })
  const networkKey = pair.network?.['network_key']
  return {
    baseline: arm(pair.baseline),
    clearpath: arm(pair.clearpath),
    improvementPercent: pair.comparison.travel_time_improvement_percent,
    stoppedDelta: pair.comparison.stopped_time_delta_seconds,
    stopsDelta: pair.comparison.stops_delta,
    seed: pair.seed,
    network: typeof networkKey === 'string' ? networkKey : null,
    provenance: 'live',
  }
}

/**
 * The committed reference run. Recorded from a SUMO verification on a separate
 * synthetic network, so callers must label it as reference data, not a
 * measurement of the mission on screen.
 */
export function demoComparison(): ClearpathComparison {
  return {
    baseline: {
      ...DEMO_CLEARPATH.baseline,
      status: 'COMPLETED',
      errorCode: null,
      errorMessage: null,
    },
    clearpath: {
      ...DEMO_CLEARPATH.clearpath,
      status: 'COMPLETED',
      errorCode: null,
      errorMessage: null,
    },
    improvementPercent: DEMO_CLEARPATH.improvementPercent,
    stoppedDelta:
      DEMO_CLEARPATH.clearpath.stoppedTimeSeconds -
      DEMO_CLEARPATH.baseline.stoppedTimeSeconds,
    stopsDelta: DEMO_CLEARPATH.clearpath.stops - DEMO_CLEARPATH.baseline.stops,
    seed: 42,
    network: DEMO_CLEARPATH.networkLabel,
    provenance: 'demo',
  }
}

/** Signed delta for display, e.g. `-33.0s`. */
export function formatDelta(value: number, suffix = 's'): string {
  const sign = value > 0 ? '+' : ''
  return `${sign}${value.toFixed(1)}${suffix}`
}
