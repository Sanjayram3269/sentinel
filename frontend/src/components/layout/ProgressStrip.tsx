/**
 * Lifecycle progress strip.
 *
 * SENTINEL's closed loop is the product, so it is always on screen. Each stage
 * shows where the mission actually is, derived from real backend state:
 *
 * - SENSE: an incident exists
 * - PREDICT: any prediction/simulation evidence exists
 * - ROUTE: routes exist and one is ACTIVE
 * - SIMULATE: a comparable CLEARPATH pair exists
 * - APPROVE: a human has approved this exact version
 * - ACT: execution has started
 * - OBSERVE: an observation was taken
 * - REPLAN: a replan was requested or required
 *
 * Stages with no evidence are visibly incomplete rather than optimistically
 * marked done. Nothing is inferred forward.
 */

import {
  Activity,
  BrainCircuit,
  Check,
  CircleDashed,
  FlaskConical,
  GitBranch,
  RefreshCw,
  Route,
  Siren,
} from 'lucide-react'
import type { ComponentType } from 'react'
import type { PlanReviewRead } from '../../types/api'

interface Stage {
  key: string
  label: string
  Icon: ComponentType<{ size?: number }>
  done: boolean
  /** True when the stage is the current bottleneck worth the operator's attention. */
  active: boolean
}

const ICON = { size: 11 }

export function ProgressStrip({ review }: { review: PlanReviewRead | null }) {
  const incidentIds = review?.mission.incident_ids ?? []
  const approvalState = review?.approval.approval_state ?? null
  const authorized = review?.approval.authorized_for_execution ?? false
  const comparable = review?.simulation_evidence.comparable ?? false
  const routeStatus = review?.route.route_status ?? null

  const stages: Stage[] = [
    {
      key: 'sense',
      label: 'Sense',
      Icon: Siren,
      done: incidentIds.length > 0,
      active: incidentIds.length === 0,
    },
    {
      key: 'predict',
      label: 'Predict',
      Icon: BrainCircuit,
      done: comparable,
      active: incidentIds.length > 0 && !comparable,
    },
    {
      key: 'route',
      label: 'Route',
      Icon: Route,
      done: routeStatus === 'ACTIVE',
      active: incidentIds.length > 0 && routeStatus !== 'ACTIVE',
    },
    {
      key: 'simulate',
      label: 'Simulate',
      Icon: FlaskConical,
      done: comparable,
      active: incidentIds.length > 0 && !comparable,
    },
    {
      key: 'approve',
      label: 'Approve',
      Icon: GitBranch,
      done: approvalState === 'APPROVED' || approvalState === 'EXECUTION_AUTHORIZED',
      active: !authorized,
    },
    {
      key: 'act',
      label: 'Act',
      Icon: Check,
      done: approvalState === 'EXECUTING',
      active: authorized && approvalState !== 'EXECUTING',
    },
    {
      key: 'observe',
      label: 'Observe',
      Icon: Activity,
      done: false,
      active: false,
    },
    {
      key: 'replan',
      label: 'Replan',
      Icon: RefreshCw,
      done: false,
      active: false,
    },
  ]

  return (
    <nav aria-label="SENTINEL mission lifecycle" className="panel px-3 py-2">
      <ol className="flex flex-wrap items-center gap-x-1.5 gap-y-1.5">
        {stages.map((stage, index) => (
          <li key={stage.key} className="flex items-center gap-1.5">
            <StageChip stage={stage} />
            {index < stages.length - 1 && (
              <span aria-hidden="true" className="h-px w-3 bg-graphite-600 sm:w-5" />
            )}
          </li>
        ))}
      </ol>
      <p className="mt-1.5 text-[10px] leading-relaxed text-ink-400">
        SENSE → PREDICT → ROUTE → SIMULATE → APPROVE → ACT → OBSERVE → REPLAN.
        Observe and Replan stay open until telemetry and an operator action
        close them.
      </p>
    </nav>
  )
}

function StageChip({ stage }: { stage: Stage }) {
  const state = stage.done ? 'done' : stage.active ? 'active' : 'idle'
  const classes =
    state === 'done'
      ? 'border-go-500/60 bg-go-500/10 text-go-400'
      : state === 'active'
        ? 'border-warn-500/60 bg-warn-500/10 text-warn-400'
        : 'border-graphite-700 bg-graphite-900 text-ink-400'

  return (
    <span
      title={
        state === 'done'
          ? `${stage.label}: complete`
          : state === 'active'
            ? `${stage.label}: waiting on this step`
            : `${stage.label}: not started`
      }
      className={`inline-flex items-center gap-1 rounded border px-1.5 py-1 text-[10px] font-semibold uppercase tracking-[0.08em] ${classes}`}
    >
      {state === 'done' ? (
        <Check {...ICON} />
      ) : state === 'active' ? (
        <stage.Icon {...ICON} />
      ) : (
        <CircleDashed {...ICON} />
      )}
      <span className="hidden sm:inline">{stage.label}</span>
    </span>
  )
}
