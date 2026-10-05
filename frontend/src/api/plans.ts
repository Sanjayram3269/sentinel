/** Plan, review, approval and authorization endpoints. */

import { api } from './client'
import type {
  ApprovalRead,
  ExecutionRead,
  GateDecisionRead,
  ModifyPlanRead,
  ObservationRead,
  PlanRead,
  PlanReviewRead,
  ReplanRead,
  SimulationPairEvidenceRead,
} from '../types/api'

export function getPlan(missionId: string, planId: string) {
  return api.get<PlanRead>(`/missions/${missionId}/plans/${planId}`)
}

export function getLatestPlan(missionId: string) {
  return api.get<PlanRead>(`/missions/${missionId}/plans/latest`)
}

/**
 * The reviewer-facing bundle: plan context plus Phase 7 simulation evidence,
 * assembled server-side so the UI never has to guess what "reviewed" means.
 */
export function getReviewPackage(missionId: string, planId: string, signal?: AbortSignal) {
  return api.get<PlanReviewRead>(`/missions/${missionId}/plans/${planId}/review`, {
    signal,
  })
}

/** Read-only gate verdict. Never mutates state. */
export function getAuthorization(missionId: string, planId: string) {
  return api.get<GateDecisionRead>(
    `/missions/${missionId}/plans/${planId}/authorization`,
  )
}

export function approvePlan(
  missionId: string,
  planId: string,
  payload: {
    plan_version: number
    reviewer_id: string
    comment?: string
    reviewer_role?: string
    evidence_reviewed?: boolean
  },
) {
  return api.post<ApprovalRead>(
    `/missions/${missionId}/plans/${planId}/approve`,
    payload,
  )
}

export function rejectPlan(
  missionId: string,
  planId: string,
  payload: {
    plan_version: number
    reviewer_id: string
    comment: string
    reviewer_role?: string
    evidence_reviewed?: boolean
  },
) {
  return api.post<ApprovalRead>(
    `/missions/${missionId}/plans/${planId}/reject`,
    payload,
  )
}

export function modifyPlan(
  missionId: string,
  planId: string,
  payload: {
    plan_version: number
    reviewer_id: string
    comment?: string
    modifications: Array<{
      field: 'ROUTE' | 'HOSPITAL' | 'RESOURCE'
      value?: string | null
      value_list?: string[] | null
      reason: string
    }>
  },
) {
  return api.post<ModifyPlanRead>(
    `/missions/${missionId}/plans/${planId}/modify`,
    payload,
  )
}

export function executePlan(missionId: string, planId: string) {
  return api.post<ExecutionRead>(`/missions/${missionId}/plans/${planId}/execute`)
}

export function observePlan(
  missionId: string,
  planId: string,
  payload: { max_speed_deviation_mps?: number } = {},
) {
  return api.post<ObservationRead>(
    `/missions/${missionId}/plans/${planId}/observe`,
    payload,
  )
}

export function replanMission(
  missionId: string,
  planId: string,
  payload: { reason: string; correlation_id?: string },
) {
  return api.post<ReplanRead>(`/missions/${missionId}/plans/${planId}/replan`, payload)
}

/**
 * Runs the baseline/CLEARPATH pair for a plan and returns the evidence.
 * The seed is caller-controlled and reproducible by design, so the same seed
 * and configuration must return the same measurement.
 */
export function simulatePlan(
  missionId: string,
  planId: string,
  payload: { seed?: number; max_simulation_seconds?: number } = {},
) {
  return api.post<SimulationPairEvidenceRead>(
    `/missions/${missionId}/plans/${planId}/simulate`,
    payload,
  )
}

export const DEFAULT_PLAN_ID = '79485ff9-fda4-4f3e-9576-257f50a44b6b'
