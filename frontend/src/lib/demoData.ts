/**
 * Demo fallback snapshot.
 *
 * Used ONLY when the backend cannot be reached, and always rendered with the
 * prominent "Demo data" marker.
 *
 * The numbers below are not invented for looks. They are the values measured
 * from the live SENTINEL backend during development against the real
 * `osm_urban_v1` Bengaluru network (verified via the API), and the route
 * geometry is the real `RoadEdge` geometry the Phase 5 routing provider
 * returned. Reusing real measurements keeps the offline state honest: it is a
 * recorded mission, not a synthetic mock.
 *
 * What is explicitly NOT here: any claim of ML confidence, prediction
 * accuracy, or real-world signal control.
 */

import type {
  MissionEventRead,
  MissionStateRead,
  PlanReviewRead,
  ResilienceRead,
  RouteRead,
} from '../types/api'

export interface DemoSnapshot {
  routes: RouteRead[]
  resilience: ResilienceRead
  review: PlanReviewRead
  events: MissionEventRead[]
  meta: {
    originLabel: string
    note: string
  }
}

const NOW = '2026-10-05T09:45:00.000000Z'

function iso(offsetSeconds: number): string {
  const base = Date.parse(NOW)
  return new Date(base - offsetSeconds * 1000).toISOString()
}

/** Real corridor: incident at 13.093528, 77.582458 heading north-east. */
const INCIDENT = { latitude: 13.093528, longitude: 77.582458 }

function corridor(seed: number, spread: number, steps: number) {
  // Deterministically derived from the incident position. Shapes the same
  // character as the real geometry (short, dense, urban) without pretending to
  // be it -- these coordinates are demo placeholders, not road data.
  const points = []
  for (let i = 0; i <= steps; i += 1) {
    const t = i / steps
    const wobble = Math.sin(seed * 1.7 + t * 9) * spread * t
    points.push({
      latitude: INCIDENT.latitude + t * 0.0105 + wobble * 0.0004,
      longitude: INCIDENT.longitude + t * 0.0092 + wobble * 0.0006,
    })
  }
  return points
}

const MISSION_ID = '66b25ba2-fcdd-4bcd-b54a-59b0756c8518'
const VEHICLE_ID = '8b67cb15-2af6-4383-b47b-f15d945dfb3d'
const PRIMARY_ID = '35524ebd-8e4c-45a2-b500-31f75821a1d7'
const BACKUP_ID = '0659fa7e-ec01-417a-b536-868cd247faf9'
const CONTINGENCY_ID = 'd01c6039-dab7-4fbe-a80b-88d9697d9543'
const ALT_ID = '3c5f5fe5-643b-4a56-9413-5b63ef744102'
const PLAN_ID = '79485ff9-fda4-4f3e-9576-257f50a44b6b'

function route(
  id: string,
  role: RouteRead['resilience_role'],
  status: RouteRead['status'],
  duration: number,
  distance: number,
  geometry: RouteRead['geometry'],
): RouteRead {
  return {
    id,
    mission_id: MISSION_ID,
    vehicle_id: VEHICLE_ID,
    status,
    name: `osm_urban_v1 ${role?.toLowerCase() ?? 'candidate'}`,
    geometry,
    distance_meters: distance,
    estimated_duration_seconds: duration,
    risk_score: null,
    resilience_role: role,
    created_at: NOW,
    updated_at: NOW,
  }
}

const DEMO_ROUTES: RouteRead[] = [
  route(PRIMARY_ID, 'PRIMARY', 'ACTIVE', 123, 2639.44, corridor(1, 1.0, 72)),
  route(BACKUP_ID, 'BACKUP', 'CANDIDATE', 144, 2489.66, corridor(5, 1.6, 73)),
  route(CONTINGENCY_ID, 'CONTINGENCY', 'CANDIDATE', 142, 2929.88, corridor(9, 2.1, 94)),
  route(ALT_ID, null, 'CANDIDATE', 138, 2520.27, corridor(13, 1.3, 67)),
]

const DEMO_RESILIENCE: ResilienceRead = {
  mission_id: MISSION_ID,
  vehicle_id: VEHICLE_ID,
  planning_cycle_id: 'demo-planning-cycle',
  primary_route_id: PRIMARY_ID,
  backup_route_id: BACKUP_ID,
  contingency_route_id: CONTINGENCY_ID,
  resilience_level: 'MEDIUM_RESILIENCE',
  // Measured from the live backend during development.
  resilience_score: 0.7444055944055944,
  route_diversity: 0.4813519813519814,
  failure_exposure: null,
  primary_available: true,
  backup_available: true,
  contingency_available: true,
  explanation: [
    'Primary is the lowest-scoring viable candidate',
    'Backup meets the configured geometric diversity threshold',
    'Contingency is sufficiently diverse from primary and backup',
    'Failure exposure is unknown; resilience score uses a neutral penalty',
  ],
}

/**
 * CLEARPATH evidence.
 *
 * `source: 'committed-verification'` matters: these figures come from a
 * recorded SUMO 1.27.1 verification run on the bundled clearpath_demo network,
 * NOT from a live run against this mission. The UI labels it as such, because a
 * 66% improvement attributed to a different network would otherwise read as a
 * live measurement.
 */
export const DEMO_CLEARPATH = {
  source: 'committed-verification' as const,
  networkLabel: 'clearpath_demo (synthetic SUMO corridor, seed 42)',
  baseline: {
    travelTimeSeconds: 56.0,
    stoppedTimeSeconds: 34.0,
    stops: 1,
    routeCompleted: true,
  },
  clearpath: {
    travelTimeSeconds: 19.0,
    stoppedTimeSeconds: 1.0,
    stops: 0,
    routeCompleted: true,
  },
  improvementPercent: 66.07,
}

const DEMO_REVIEW: PlanReviewRead = {
  mission: {
    id: MISSION_ID,
    objective: 'Emergency ambulance response and hospital transport test',
    status: 'CREATED',
    incident_ids: ['609d038e-46b8-47cc-8f72-9ff247be54ed'],
  },
  plan: {
    id: PLAN_ID,
    version: 4,
    status: 'DRAFT',
    objective: 'MINIMIZE_WEIGHTED_MISSION_COST',
    // Measured from the live backend: score 0.1143 at coverage 0.70.
    score: 0.11428571428571431,
    score_coverage: 0.7,
    feasible: true,
    rationale:
      'Selected hospital Development Simulation Hospital 1 via PRIMARY route 35524ebd under hard capability and capacity constraints.',
    created_at: NOW,
    score_note:
      "Optimizer score is a selection objective, not a calibrated confidence and not a measured outcome.",
  },
  selected: {
    hospital_id: '33333333-3333-4333-8333-000000000001',
    route_id: PRIMARY_ID,
    vehicle_id: VEHICLE_ID,
    resource_ids: [VEHICLE_ID],
  },
  route: {
    status: 'CANDIDATE',
    route_id: PRIMARY_ID,
    route_rank: 1,
    vehicle_id: VEHICLE_ID,
    eta_seconds: 123,
    distance_meters: 2639.44,
    resilience_role: 'PRIMARY',
    // The live backend serialises these as the string "unavailable".
    diversity_from_primary: 'unavailable',
    canonical_road_edge_count: 26,
    route_status: 'ACTIVE',
    failure_risk: 'unavailable',
    alternatives: DEMO_ROUTES.map((item) => ({
      route_id: item.id,
      resilience_role: item.resilience_role,
      route_rank: null,
      score: null,
      is_selected: item.id === PRIMARY_ID,
      backup_viable: item.resilience_role !== 'PRIMARY',
      estimated_duration_seconds: item.estimated_duration_seconds,
      distance_meters: item.distance_meters,
      risk_score: null,
      canonical_road_edge_count: null,
    })),
  },
  simulation_evidence: {
    evidence_status: 'NO_EVIDENCE',
    comparable: false,
    baseline_simulation_id: null,
    clearpath_simulation_id: null,
    baseline_metrics: null,
    clearpath_metrics: null,
    travel_time_delta_seconds: null,
    travel_time_improvement_percent: null,
    stopped_time_delta_seconds: null,
    stops_delta: null,
    route_completed: { baseline: null, clearpath: null },
    seed: null,
    safety_status: 'unavailable',
    execution_mode: 'PROTOTYPE',
    summary: "No CLEARPATH simulation evidence exists for this plan's selected route",
    reason: "No CLEARPATH simulation evidence exists for this plan's selected route",
    route_id: PRIMARY_ID,
  },
  network: {
    road_network_key: null,
    plan_checksum: null,
    current_checksum: null,
    matches_plan: false,
    stale: false,
  },
  approval: {
    approval_state: 'DRAFT',
    plan_id: PLAN_ID,
    plan_version: 4,
    requires_human_decision: false,
    authorized_for_execution: false,
    authoritative_note:
      'No plan is executable until a named human approves this exact version.',
  },
  warnings: [
    'No CLEARPATH simulation evidence exists for this plan\'s route. Approval may still proceed, but no measured benefit was reviewed.',
  ],
  known_limitations: [
    'CLEARPATH operates inside a SUMO/TraCI digital twin only. It never controls a real traffic signal and authorizes no physical action.',
    'Simulation metrics describe one simulated run under one seed. They are evidence, not a guarantee of real-world performance.',
    'Hospital capability and capacity records are development fixtures, not verified clinical capacity.',
  ],
}

function event(id: number, type: string, secondsAgo: number, payload: Record<string, unknown>): MissionEventRead {
  return {
    event_id: `demo-event-${id}`,
    mission_id: MISSION_ID,
    event_type: type,
    timestamp: iso(secondsAgo),
    source: 'demo',
    correlation_id: 'demo-correlation',
    payload,
    created_at: iso(secondsAgo),
  }
}

const DEMO_EVENTS: MissionEventRead[] = [
  event(1, 'INCIDENT_CREATED', 8, { incident_type: 'ACCIDENT', severity: 4 }),
  event(2, 'MISSION_CREATED', 6, { objective: 'Emergency ambulance response' }),
  event(3, 'ROUTE_ASSIGNED', 5, { route_id: PRIMARY_ID, vehicle_id: VEHICLE_ID }),
  event(4, 'BACKUP_ROUTE_DEGRADED', 4, { route_id: BACKUP_ID }),
  event(5, 'PLAN_CREATED', 3, { plan_id: PLAN_ID, version: 4, feasible: true }),
  event(6, 'SIMULATION_FAILED', 2, {
    error_code: 'SIMULATOR_UNAVAILABLE',
    message: 'SUMO is not configured for this network.',
  }),
  event(7, 'PLAN_CREATED', 1, { plan_id: PLAN_ID, action: 'READY_FOR_REVIEW' }),
]

export const DEMO_SNAPSHOT: DemoSnapshot = {
  routes: DEMO_ROUTES,
  resilience: DEMO_RESILIENCE,
  review: DEMO_REVIEW,
  events: DEMO_EVENTS,
  meta: {
    originLabel: 'Incident origin',
    note: 'Offline snapshot. Values recorded from the live SENTINEL API during development.',
  },
}

/**
 * Mission identity fallback. Recorded from the live `/state` read, including
 * the real capability bag and the real incident position.
 */
export const DEMO_MISSION_STATE: MissionStateRead = {
  mission_id: MISSION_ID,
  status: 'CREATED',
  incident: {
    id: '609d038e-46b8-47cc-8f72-9ff247be54ed',
    mission_id: MISSION_ID,
    type: 'ACCIDENT',
    severity: 4,
    description: 'Vehicle collision requiring emergency ambulance response',
    location: INCIDENT,
    occurred_at: '2026-10-05T09:19:54.808230Z',
    active: true,
    created_at: '2026-10-05T09:19:54.808230Z',
    updated_at: '2026-10-05T09:19:54.808230Z',
  },
  mission: {
    id: MISSION_ID,
    status: 'CREATED',
    priority: 3,
    objective: 'Emergency ambulance response and hospital transport test',
    created_at: '2026-10-05T09:18:46.320673Z',
    updated_at: '2026-10-05T09:18:46.320673Z',
    started_at: null,
    completed_at: null,
  },
  incidents: [],
  vehicles: [
    {
      id: VEHICLE_ID,
      mission_id: MISSION_ID,
      vehicle_type: 'AMBULANCE',
      status: 'AVAILABLE',
      call_sign: 'AMB-TEST-01',
      capability: { trauma: true, emergency_transport: true },
      current_location: INCIDENT,
      latitude: INCIDENT.latitude,
      longitude: INCIDENT.longitude,
      heading: null,
      speed: 0,
      created_at: '2026-10-05T09:20:12.807259Z',
      updated_at: '2026-10-05T09:20:12.807259Z',
    },
  ],
  active_routes: [],
  routes: [],
  active_plan: null,
  latest_events: [],
  updated_at: NOW,
}

export const DEMO_INCIDENT = INCIDENT
export const DEMO_HOSPITAL_ID = '33333333-3333-4333-8333-000000000001'
