/**
 * Typed API contracts, transcribed from the backend's live OpenAPI document.
 *
 * Every type here corresponds to a schema the backend actually serves. Fields
 * are nullable where the backend may legitimately omit them, because the UI
 * has to represent "unavailable" honestly rather than coerce it to zero.
 */

// ---------------------------------------------------------------- primitives

export interface GeoPoint {
  latitude: number
  longitude: number
}

export interface Coordinates {
  latitude: number
  longitude: number
}

// ------------------------------------------------------------------- enums

export type MissionStatus =
  | 'CREATED'
  | 'DISPATCHED'
  | 'ACTIVE'
  | 'PAUSED'
  | 'COMPLETED'
  | 'CANCELLED'

export type RouteStatus =
  | 'ACTIVE'
  | 'DEGRADED'
  | 'FAILED'
  | 'ABORTED'
  | 'COMPLETED'
  | 'CANDIDATE'

export type VehicleStatus =
  | 'AVAILABLE'
  | 'EN_ROUTE'
  | 'AT_SCENE'
  | 'TRANSPORTING'
  | 'OFFLINE'

export type ResilienceRole = 'PRIMARY' | 'BACKUP' | 'CONTINGENCY'

export type PlanStatus =
  | 'DRAFT'
  | 'SUBMITTED'
  | 'APPROVED'
  | 'REJECTED'
  | 'SUPERSEDED'
  | 'READY_FOR_REVIEW'
  | 'EXECUTION_AUTHORIZED'
  | 'EXECUTING'
  | 'REPLAN_REQUIRED'

export type IncidentType =
  | 'ACCIDENT'
  | 'MEDICAL'
  | 'FIRE'
  | 'FLOOD'
  | 'EARTHQUAKE'
  | 'STRUCTURAL_COLLAPSE'
  | 'HAZMAT'
  | 'OTHER'

export type SimulationStatus =
  | 'PENDING'
  | 'RUNNING'
  | 'COMPLETED'
  | 'FAILED'
  | 'ABORTED'

export type SimulationMode = 'BASELINE' | 'CLEARPATH'

export type SimulationSafetyStatus =
  | 'APPROVED_SIMULATION_ONLY'
  | 'NO_ACTION_PROPOSED'
  | 'REJECTED_SIMULATION_ONLY'

/** Exact `SafetyStatus` enum from the OpenAPI document. */
export type SafetyStatus = SimulationSafetyStatus

export type EvidenceStatus =
  | 'COMPARABLE'
  | 'BASELINE_FAILED'
  | 'CLEARPATH_FAILED'
  | 'COMPARISON_UNAVAILABLE'
  /** Returned by the review package when no pair has ever been run. */
  | 'NO_EVIDENCE'
  | 'MEASURED_COMPARISON'
  | 'INCOMPLETE_PAIR'

export type ResilienceLevel =
  | 'HIGH_RESILIENCE'
  | 'MEDIUM_RESILIENCE'
  | 'LOW_RESILIENCE'
  | 'NO_RESILIENCE'

/** Exact `EventType` enum from the OpenAPI document. */
export type EventType =
  | 'MISSION_CREATED'
  | 'MISSION_STATUS_CHANGED'
  | 'VEHICLE_POSITION_UPDATE'
  | 'VEHICLE_STATUS_CHANGED'
  | 'INCIDENT_CREATED'
  | 'INCIDENT_UPDATED'
  | 'ROAD_CLOSURE'
  | 'ACCIDENT_DETECTED'
  | 'HAZARD_UPDATED'
  | 'CONGESTION_CHANGED'
  | 'ROUTE_ASSIGNED'
  | 'ROUTE_UPDATED'
  | 'ROUTE_FAILED'
  | 'ROUTE_DEVIATION'
  | 'BACKUP_ROUTE_DEGRADED'
  | 'REPLAN_TRIGGERED'
  | 'PREDICTION_UPDATED'
  | 'HOSPITAL_CAPACITY_CHANGED'
  | 'CLEARPATH_REQUESTED'
  | 'CLEARPATH_UPDATED'
  | 'PLAN_CREATED'
  | 'PLAN_APPROVED'
  | 'PLAN_REJECTED'
  | 'PLAN_READY_FOR_REVIEW'
  | 'PLAN_MODIFIED'
  | 'PLAN_SUPERSEDED'
  | 'EXECUTION_AUTHORIZED'
  | 'MISSION_EXECUTION_STARTED'
  | 'MISSION_EXECUTION_COMPLETED'
  | 'PLAN_REQUIRES_REAPPROVAL'
  | 'TELEMETRY_RECEIVED'
  | 'SIMULATION_STARTED'
  | 'SIMULATION_COMPLETED'
  | 'SIMULATION_FAILED'

/** `SimulationMetrics`, transcribed. Every field is nullable by design. */
export interface SimulationMetricsRead {
  emergency_vehicle_travel_time_seconds: number | null
  total_delay_seconds: number | null
  stopped_time_seconds: number | null
  number_of_stops: number | null
  average_speed_meters_per_second: number | null
  route_completed: boolean | null
  simulation_duration_seconds: number | null
  background_average_delay_seconds: number | null
  background_vehicle_throughput: number | null
}

// ------------------------------------------------------------------ mission

export interface MissionRead {
  id: string
  status: MissionStatus
  priority: number
  objective: string
  created_at: string
  updated_at: string
  started_at: string | null
  completed_at: string | null
}

export interface IncidentRead {
  id: string
  mission_id: string | null
  type: IncidentType
  severity: number
  description: string
  location: GeoPoint | null
  occurred_at: string
  active: boolean
  created_at: string
  updated_at: string
}

export interface VehicleRead {
  id: string
  mission_id: string | null
  vehicle_type: string
  status: VehicleStatus
  call_sign: string
  capability: Record<string, unknown> | null
  current_location: GeoPoint | null
  latitude: number | null
  longitude: number | null
  heading: number | null
  speed: number | null
  created_at: string
  updated_at: string
}

/**
 * The `/state` endpoint returns a deliberately leaner route projection: no
 * geometry, no resilience role, and no timestamps. Map rendering must therefore
 * use the full `/routes` read; `/state` is for identity, not for drawing.
 */
export interface RouteState {
  id: string
  vehicle_id: string
  status: RouteStatus
  name: string
  distance_meters: number | null
  estimated_duration_seconds: number | null
  risk_score: number | null
}

/** Event projection inside `/state`. Note `id`/`occurred_at`, not `event_id`/`timestamp`. */
export interface EventState {
  id: string
  event_type: string
  source: string
  occurred_at: string
  correlation_id: string
  payload: Record<string, unknown>
}

export interface MissionStateRead {
  mission_id: string
  status: MissionStatus
  incident: IncidentRead | null
  mission: MissionRead | null
  incidents: IncidentRead[]
  vehicles: VehicleRead[]
  active_routes: RouteState[]
  routes: RouteState[]
  active_plan: PlanRead | null
  latest_events: EventState[]
  updated_at: string
}

// -------------------------------------------------------------------- route

export interface RouteRead {
  id: string
  mission_id: string
  vehicle_id: string
  status: RouteStatus
  name: string
  geometry: Coordinates[]
  distance_meters: number | null
  estimated_duration_seconds: number | null
  risk_score: number | null
  resilience_role: ResilienceRole | null
  created_at: string
  updated_at: string
}

export interface ResilienceRead {
  mission_id: string
  vehicle_id: string | null
  planning_cycle_id: string | null
  primary_route_id: string | null
  backup_route_id: string | null
  contingency_route_id: string | null
  resilience_level: ResilienceLevel | string
  resilience_score: number | null
  route_diversity: number | null
  failure_exposure: number | null
  primary_available: boolean
  backup_available: boolean
  contingency_available: boolean
  explanation: string[]
}

// --------------------------------------------------------------------- plan

export interface PlanRead {
  id: string
  mission_id: string
  version: number
  status: PlanStatus
  objective: string
  feasible: boolean | null
  score: number | null
  confidence: number | null
  rationale: string | null
  plan_payload: Record<string, unknown>
  score_coverage: number | null
  selected_hospital_id: string | null
  selected_route_id: string | null
  selected_resource_ids: string[] | null
  infeasible_reasons: string[] | null
  created_at: string
  updated_at: string
}

/** One measured run of a baseline/CLEARPATH pair. */
export interface SimulationRunEvidenceRead {
  simulation_id: string
  mode: SimulationMode | null
  status: SimulationStatus
  simulator: string
  network_id: string | null
  metrics: SimulationMetricsRead | null
  sumo_version: string | null
  error_code: string | null
  error_message: string | null
  started_at: string | null
  completed_at: string | null
}

export interface SimulationComparisonEvidenceRead {
  travel_time_delta_seconds: number | null
  travel_time_improvement_percent: number | null
  stopped_time_delta_seconds: number | null
  stops_delta: number | null
  note?: string
}

/**
 * The full `SimulationEvidenceRead` returned by `POST /plans/{id}/simulate`.
 * Unlike the review projection it always carries both runs, even when both
 * failed, plus the simulation-only action counters.
 */
export interface SimulationPairEvidenceRead {
  simulation_pair_id: string
  mission_id: string
  plan_id: string
  route_id: string | null
  vehicle_id: string | null
  plan_version: number | null
  baseline: SimulationRunEvidenceRead
  clearpath: SimulationRunEvidenceRead
  comparison: SimulationComparisonEvidenceRead
  evidence_status: EvidenceStatus
  safety_status: SafetyStatus
  corridor: Record<string, unknown> | null
  actions_requested: number
  actions_approved: number
  actions_executed: number
  actions_rejected: number
  seed: number
  max_simulation_seconds: number
  network: Record<string, unknown> | null
  simulation_only: boolean
  scope_notice: string
  generated_at: string
}

export interface RouteAlternativeRead {
  route_id: string
  resilience_role: ResilienceRole | null
  route_rank: number | null
  score: number | null
  is_selected: boolean
  backup_viable: boolean | null
  estimated_duration_seconds: number | null
  distance_meters: number | null
  risk_score: number | null
  canonical_road_edge_count: number | null
}

/**
 * The `simulation_evidence` block of the review package.
 *
 * This is a flattened projection, not the full `SimulationEvidenceRead` returned
 * by `POST /simulate`. The review endpoint serialises absent values as the
 * literal string "unavailable" (safety_status is a real enum on the other
 * endpoint), so `safety_status` is deliberately a plain string here.
 */
export interface SimulationEvidenceRead {
  evidence_status: EvidenceStatus | string
  comparable: boolean
  baseline_simulation_id: string | null
  clearpath_simulation_id: string | null
  baseline_metrics: SimulationMetricsRead | null
  clearpath_metrics: SimulationMetricsRead | null
  travel_time_delta_seconds: number | null
  travel_time_improvement_percent: number | null
  stopped_time_delta_seconds: number | null
  stops_delta: number | null
  route_completed: { baseline: boolean | null; clearpath: boolean | null } | null
  seed: number | null
  safety_status: string
  execution_mode: string
  summary: string
  reason?: string
  route_id?: string
}

export interface PlanReviewRead {
  mission: {
    id: string
    objective: string | null
    status: MissionStatus | null
    incident_ids: string[]
  }
  plan: {
    id: string
    version: number
    status: PlanStatus
    objective: string
    score: number | null
    score_coverage: number | null
    feasible: boolean | null
    rationale: string | null
    created_at: string | null
    score_note: string
  }
  selected: {
    hospital_id: string | null
    route_id: string | null
    vehicle_id: string | null
    resource_ids: string[] | null
  }
  route: {
    status?: string
    route_id?: string | null
    route_rank?: number | null
    vehicle_id?: string | null
    eta_seconds?: number | null
    candidate_id?: string | null
    /**
     * The live backend serialises these as the literal string "unavailable"
     * rather than null, so they are widened here. Anything that is not a finite
     * number is an absent value and must be rendered as such, never as a zero.
     */
    failure_risk?: number | string | null
    distance_meters?: number | null
    resilience_role?: ResilienceRole | null
    diversity_from_primary?: number | string | null
    canonical_road_edge_ids?: string[] | null
    canonical_road_edge_count?: number | null
    route_status?: RouteStatus | null
    alternatives?: RouteAlternativeRead[]
  }
  simulation_evidence: SimulationEvidenceRead
  network: {
    road_network_key: string | null
    plan_checksum: string | null
    current_checksum: string | null
    matches_plan: boolean
    stale: boolean
  }
  approval: {
    approval_state: PlanStatus
    plan_id: string
    plan_version: number
    requires_human_decision: boolean
    authorized_for_execution: boolean
    authoritative_note: string
  }
  warnings: string[]
  known_limitations: string[]
}

export interface GateReasonRead {
  code: string
  detail: string
}

export interface GateDecisionRead {
  decision: 'AUTHORIZED' | 'NOT_AUTHORIZED'
  reasons: GateReasonRead[]
  plan_id: string | null
  plan_version: number | null
  approval_id: string | null
  reviewer_id: string | null
}

export interface ApprovalRecordRead {
  approval_id: string
  plan_id: string
  plan_version: number
  decision: string
  reviewer_id: string
  reviewer_role: string | null
  comment: string | null
  previous_plan_status: string | null
  new_plan_status: string
  decided_at: string
  created_at: string
  evidence: Record<string, unknown>
  context: Record<string, unknown>
}

export interface ApprovalRead {
  approval: ApprovalRecordRead
  plan_id: string
  plan_version: number
  plan_status: PlanStatus
  execution_authorized: boolean
  requires_human_approval: boolean
  idempotent_replay: boolean
}

export interface ModifyPlanRead {
  previous_plan_id: string
  previous_plan_version: number
  previous_plan_status: string
  new_plan_id: string
  new_plan_version: number
  new_plan_status: PlanStatus
  applied_modifications: Array<Record<string, unknown>>
  requires_human_approval: boolean
  previous_approval_invalidated: boolean
}

export interface ReplanRead {
  replan_requested: boolean
  reason: string | null
  new_plan_id: string | null
  new_plan_version: number | null
  previous_plan_id: string | null
  previous_plan_version: number | null
  previous_plan_status: string | null
  auto_approved: boolean
  requires_human_approval: boolean
  note: string
}

export interface ExecutionRead {
  plan_id: string
  plan_version: number
  mission_id: string
  execution_mode: string
  authorized_by_approval_id: string | null
  reviewer_id: string | null
  status: string
  started_at: string
  notice: string
}

export interface ObservationRead {
  plan_id: string
  plan_version: number
  observed_samples: number
  deviation_detected: boolean
  reasons: string[]
  detail: Record<string, unknown>
  replan_required: boolean
  recommended_replan_reason: string | null
}

// --------------------------------------------------------------- simulation

export interface SimulationRunRead {
  simulation_id: string
  mission_id: string
  mode: SimulationMode | null
  status: SimulationStatus
  seed: number | null
  network_id: string | null
  simulator: string
  metadata: Record<string, unknown> | null
  metrics: SimulationMetricsRead | null
  clearpath_actions: SignalActionResult[] | null
  correlation_id: string | null
  started_at: string | null
  completed_at: string | null
  created_at: string
  simulation_only: boolean
  error_code: string | null
  error_message: string | null
}

/**
 * A signal action *inside the SUMO digital twin*. These are simulated control
 * actions against a synthetic junction, never instructions to real hardware.
 */
export interface SignalActionResult {
  edge_id?: string
  junction_id?: string
  action?: string
  approved?: boolean
  applied?: boolean
  reason?: string | null
  [key: string]: unknown
}

// ------------------------------------------------------------------- events

export interface MissionEventRead {
  event_id: string
  mission_id: string
  event_type: EventType | string
  timestamp: string
  source: string
  correlation_id: string
  payload: Record<string, unknown>
  created_at: string
}

export interface EventHistoryRead {
  items: MissionEventRead[]
  limit: number
  has_more: boolean
}

// ---------------------------------------------------------------- envelopes

export interface RouteCollectionRead {
  items: RouteRead[]
}

export interface SimulationCollectionRead {
  items: SimulationRunRead[]
  limit: number
  has_more: boolean
}

export interface PredictionCollectionRead {
  items: Array<Record<string, unknown>>
  limit: number
  has_more: boolean
}
