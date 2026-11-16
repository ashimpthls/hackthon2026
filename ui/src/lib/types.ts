/** Shapes returned by the DFSI API. Mirrors `dfsi/api/schemas.py`. */

export type RiskBand = 'calm' | 'watch' | 'warn' | 'critical' | 'unknown'

export interface EvidenceItem {
  feature: string
  label: string
  value: number
  importance: number
  traffic_type?: string | null
  why?: string
}

export interface Forecast {
  window_start_epoch: number
  range_id: number
  account_id: number
  risk_score: number | null
  risk_band: RiskBand
  predicted_state: string
  horizon_seconds: number
  model_version: string
  feature_version: string
  evidence: EvidenceItem[]
  degraded: boolean
  degraded_reason: string | null
}

export interface TrafficSummary {
  total_pps: number
  total_mbps: number
  blocked_pps: number
  policy_pps: number
  blocked_share: number
  policy_utilisation: number
  dominant_traffic_type: string
  baseline_deviation_pps: number
  country_hhi: number
  per_traffic_type: {
    traffic_type: string
    total_pps: number
    blocked_pps: number
    baseline_deviation_pps: number
  }[]
}

export interface WindowEvent {
  type: 'window'
  replay_id: string
  scenario_type: string
  cursor: number
  total: number
  window_start_epoch: number
  traffic: TrafficSummary
  forecast: Forecast
  shared_state: {
    over_threshold: boolean
    declared: boolean
    bell_epoch: number | null
  }
  dfsi_state: string
  first_warning_epoch: number | null
  onset_epoch: number | null
  lead_seconds: number | null
}

export interface StartEvent {
  type: 'start'
  replay_id: string
  scenario_type: string
  range_id: number
  total_windows: number
  horizon_seconds: number
  model_version: string
  feature_version: string
  forecaster_ready: boolean
}

export interface SummaryEvent {
  type: 'summary'
  replay_id: string
  scenario_type: string
  first_warning_epoch: number | null
  onset_epoch: number | null
  bell_epoch: number | null
  lead_seconds: number | null
  windows_streamed: number
}

export interface ErrorEvent {
  type: 'error'
  message: string
}

export type ReplayEvent = StartEvent | WindowEvent | SummaryEvent | ErrorEvent

export interface CatalogueEntry {
  replay_id: string
  scenario_type: string
  range_id: number
  has_onset: boolean
  window_count: number
}

export interface Health {
  status: 'ok' | 'degraded'
  forecaster_ready: boolean
  forecaster_error: string | null
  model_version: string
  feature_version: string
  horizon_seconds: number
  agent_engine: string
  replays_available: number
  environment: string
}

export type PolicyKey = 'hold' | 'tighten_vector' | 'rate_limit_sources' | 'full_scrub'

export interface SimulationCandidate {
  policy_key: PolicyKey
  policy_name: string
  attack_packets_blocked_pct: number
  legitimate_packets_lost_pct: number
  peak_blocked_share: number
  seconds_over_threshold: number
  time_to_contain_seconds: number | null
  operational_cost: string
  blast_radius: string
  reversible_seconds: number
  engagement_delay_seconds: number
  notes: string[]
}

export interface PolicyTypeSuggestion {
  traffic_type: string
  current_policy_pps: number
  current_policy_mbps: number
  legit_median_pps: number
  legit_envelope_pps: number
  legit_max_pps: number
  suggested_policy_pps: number
  suggested_policy_mbps: number
  change_pct: number
  changes: boolean
  ratio_to_normal_now: number
  suspect: boolean
  apply: boolean
  backtest_windows_blocked: number
  backtest_hours_affected: number
  backtest_legit_lost_pct: number
  note: string | null
}

/** Per-type thresholds learned from at least a week of this range's traffic. */
export interface PolicyPlan {
  available: boolean
  range_id: number
  reason: string | null
  history_start_epoch: number | null
  history_end_epoch: number | null
  history_days: number
  history_required_days: number
  coverage: number
  excluded_incident_hours: number
  per_type: PolicyTypeSuggestion[]
  change: string
}

export interface DrillResult {
  simulation_only: true
  replay_id: string
  scenario_type: string
  decision_epoch: number
  environment: string
  candidates: SimulationCandidate[]
  policy_plan: PolicyPlan | null
  disclaimer: string
}

export interface ValidatorVerdict {
  checks: Record<string, boolean>
  failures: string[]
  warnings: string[]
}

export interface AgentOutput {
  agent: string
  engine: string
  output: {
    reading?: 'attack' | 'flash_crowd' | 'ambiguous'
    headline?: string
    summary?: string
    state?: string
    reason?: string
    evidence_cited?: EvidenceItem[]
    contrary_evidence?: string[]
    options?: {
      policy_key: PolicyKey
      name: string
      what_it_buys: string
      what_it_costs: string
      blast_radius: string
    }[]
    recommended_policy_key?: PolicyKey | null
    recommendation_rationale?: string
  }
  validated: boolean
  validator: ValidatorVerdict
  tool_calls: { tool: string; arguments: Record<string, unknown> }[]
  model_version: string | null
}

export interface Briefing {
  engine: string
  replay_id: string
  scenario_type: string
  decision_epoch: number
  range_id: number
  account_id: number
  model_version: string
  feature_version: string
  state: string
  investigator: AgentOutput
  advisor: AgentOutput
  requires_human_decision: true
}

export interface DecisionRecord {
  recorded_at_epoch: number
  replay_id: string
  decision_epoch: number
  policy_key: string
  policy_name: string
  verdict: 'approved' | 'rejected' | 'changed'
  operator: string
  note: string
  model_version: string
  feature_version: string
  policy_change: {
    basis: string
    change: string | null
    reason: string | null
    per_type: {
      traffic_type: string
      current_policy_pps: number
      suggested_policy_pps: number
      backtest_legit_lost_pct: number
    }[]
  } | null
  applied: false
  note_to_auditor: string
}

export interface ModelCard {
  model_version: string
  feature_version: string
  horizon_seconds: number
  decision_threshold: number
  features: string[]
  headline_metrics: Record<string, number>
  baseline_metrics: Record<string, number>
  per_scenario_type: Record<string, Record<string, number>>
  feature_importance: Record<string, number>
  known_limitations: string[]
}
