/**
 * Plan governance actions.
 *
 * Every mutation in SENTINEL is reviewer-scoped: the backend requires a named
 * `reviewer_id` and an exact `plan_version`, and it refuses a decision on a
 * version that is not current. This hook keeps that contract visible — the
 * caller always supplies the reviewer and the exact plan it is looking at.
 *
 * It never auto-approves. Nothing here fires without an explicit operator
 * action, and a second click while a call is in flight is ignored rather than
 * queued.
 */

import { useCallback, useRef, useState } from 'react'
import {
  approvePlan,
  executePlan,
  modifyPlan,
  observePlan,
  rejectPlan,
  replanMission,
} from '../api/plans'
import { isApiError } from '../api/client'
import type {
  ApprovalRead,
  ExecutionRead,
  ModifyPlanRead,
  ObservationRead,
  ReplanRead,
} from '../types/api'

export type ApprovalAction =
  | 'approve'
  | 'reject'
  | 'modify'
  | 'execute'
  | 'observe'
  | 'replan'

export interface ApprovalOutcome {
  action: ApprovalAction
  message: string
  detail: string | null
}

interface InternalState {
  pending: ApprovalAction | null
  error: string | null
  errorCode: string | null
  outcome: ApprovalOutcome | null
}

const INITIAL: InternalState = {
  pending: null,
  error: null,
  errorCode: null,
  outcome: null,
}

export interface PlanTarget {
  missionId: string
  planId: string
  planVersion: number
}

export interface ApproveInput extends PlanTarget {
  reviewerId: string
  comment?: string
  reviewerRole?: string
  evidenceReviewed: boolean
}

export interface RejectInput extends ApproveInput {
  comment: string
}

export interface ModifyInput extends PlanTarget {
  reviewerId: string
  comment?: string
  field: 'ROUTE' | 'HOSPITAL' | 'RESOURCE'
  value?: string | null
  reason: string
}

export interface UseApprovalActionsResult extends InternalState {
  approve: (input: ApproveInput) => Promise<ApprovalRead | null>
  reject: (input: RejectInput) => Promise<ApprovalRead | null>
  modify: (input: ModifyInput) => Promise<ModifyPlanRead | null>
  execute: (target: PlanTarget) => Promise<ExecutionRead | null>
  observe: (target: PlanTarget) => Promise<ObservationRead | null>
  requestReplan: (
    target: PlanTarget,
    reason: string,
  ) => Promise<ReplanRead | null>
  clearOutcome: () => void
}

function describe(error: unknown): { message: string; code: string | null } {
  if (isApiError(error)) {
    return {
      message: error.isOffline
        ? 'The SENTINEL backend is not reachable.'
        : error.detail,
      code: error.code,
    }
  }
  return { message: 'The request failed for an unknown reason.', code: null }
}

export function useApprovalActions(): UseApprovalActionsResult {
  const [state, setState] = useState<InternalState>(INITIAL)
  const inFlight = useRef(false)

  const run = useCallback(
    async <T,>(
      action: ApprovalAction,
      work: () => Promise<T>,
      describeResult: (result: T) => { message: string; detail: string | null },
    ): Promise<T | null> => {
      if (inFlight.current) return null
      inFlight.current = true
      setState({ ...INITIAL, pending: action })
      try {
        const result = await work()
        const described = describeResult(result)
        setState({
          pending: null,
          error: null,
          errorCode: null,
          outcome: { action, ...described },
        })
        return result
      } catch (error) {
        const { message, code } = describe(error)
        setState({ pending: null, error: message, errorCode: code, outcome: null })
        return null
      } finally {
        inFlight.current = false
      }
    },
    [],
  )

  const approve = useCallback(
    (input: ApproveInput) =>
      run(
        'approve',
        () =>
          approvePlan(input.missionId, input.planId, {
            plan_version: input.planVersion,
            reviewer_id: input.reviewerId,
            comment: input.comment,
            reviewer_role: input.reviewerRole,
            // Declared explicitly: the reviewer either reviewed the evidence or
            // did not, and the backend records which.
            evidence_reviewed: input.evidenceReviewed,
          }),
        (result) => ({
          message: `Plan v${result.plan_version} approved by ${result.approval.reviewer_id}.`,
          detail: result.execution_authorized
            ? 'Execution is authorised for this exact version.'
            : 'Approval recorded. Execution remains a separate gated step.',
        }),
      ),
    [run],
  )

  const reject = useCallback(
    (input: RejectInput) =>
      run(
        'reject',
        () =>
          rejectPlan(input.missionId, input.planId, {
            plan_version: input.planVersion,
            reviewer_id: input.reviewerId,
            comment: input.comment,
            reviewer_role: input.reviewerRole,
            evidence_reviewed: input.evidenceReviewed,
          }),
        (result) => ({
          message: `Plan v${result.plan_version} rejected by ${result.approval.reviewer_id}.`,
          detail: result.approval.comment,
        }),
      ),
    [run],
  )

  const modify = useCallback(
    (input: ModifyInput) =>
      run(
        'modify',
        () =>
          modifyPlan(input.missionId, input.planId, {
            plan_version: input.planVersion,
            reviewer_id: input.reviewerId,
            comment: input.comment,
            modifications: [
              { field: input.field, value: input.value ?? null, reason: input.reason },
            ],
          }),
        (result) => ({
          message: `Plan v${result.previous_plan_version} modified into v${result.new_plan_version}.`,
          detail: result.previous_approval_invalidated
            ? 'The previous approval was invalidated. The new version needs a fresh human approval.'
            : 'The new version requires human approval.',
        }),
      ),
    [run],
  )

  const execute = useCallback(
    (target: PlanTarget) =>
      run(
        'execute',
        () => executePlan(target.missionId, target.planId),
        (result) => ({
          message: `Plan v${result.plan_version} execution started.`,
          detail: result.notice,
        }),
      ),
    [run],
  )

  const observe = useCallback(
    (target: PlanTarget) =>
      run(
        'observe',
        () => observePlan(target.missionId, target.planId),
        (result) => ({
          message: result.deviation_detected
            ? `Deviation detected across ${result.observed_samples} samples.`
            : `No deviation across ${result.observed_samples} samples.`,
          detail: result.replan_required
            ? result.recommended_replan_reason
            : (result.reasons[0] ?? null),
        }),
      ),
    [run],
  )

  const requestReplan = useCallback(
    (target: PlanTarget, reason: string) =>
      run(
        'replan',
        () => replanMission(target.missionId, target.planId, { reason }),
        (result) => ({
          message: result.replan_requested
            ? `Replan requested. New plan ${
                result.new_plan_id ? result.new_plan_id.slice(0, 8) : 'pending'
              } at v${result.new_plan_version ?? '—'}.`
            : 'The backend did not accept the replan request.',
          detail: result.note,
        }),
      ),
    [run],
  )

  const clearOutcome = useCallback(
    () => setState((prev) => ({ ...prev, outcome: null, error: null, errorCode: null })),
    [],
  )

  return {
    ...state,
    approve,
    reject,
    modify,
    execute,
    observe,
    requestReplan,
    clearOutcome,
  }
}
