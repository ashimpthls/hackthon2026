/**
 * Rehearsal room — the four fixed responses, side by side.
 *
 * The operator's decision is made here, so the costly columns (collateral,
 * blast radius) carry the same visual weight as the effective ones. A table
 * that only showed suppression would be an argument for full scrubbing every
 * time.
 */

import type { DrillResult, PolicyKey, PolicyPlan, SimulationCandidate } from '../lib/types'
import { formatRate, formatSeconds } from '../lib/format'

/** The concrete per-type thresholds behind "Tighten the vector threshold". */
function PolicyPlanTable({ plan }: { plan: PolicyPlan | null }) {
  if (!plan) return null
  if (!plan.available) {
    return (
      <div className="policy-plan">
        <h3>Suggested policy change</h3>
        <p className="empty">
          No suggestion: {plan.reason}. The tighten option above uses its stated assumption.
        </p>
      </div>
    )
  }
  return (
    <div className="policy-plan">
      <h3>Suggested policy change</h3>
      <p className="panel-note">
        Learned from the past {plan.history_days.toFixed(1)} days of this range's traffic
        ({plan.excluded_incident_hours} incident hours excluded). The highlighted rows are
        what "Tighten the vector threshold" changes.
      </p>
      <table className="tradeoff">
        <thead>
          <tr>
            <th scope="col">Traffic type</th>
            <th scope="col">Now vs normal</th>
            <th scope="col">Current policy</th>
            <th scope="col">Clean peak (7d, p99.9)</th>
            <th scope="col">Suggested policy</th>
            <th scope="col">Change</th>
            <th scope="col">Last week it would have dropped</th>
          </tr>
        </thead>
        <tbody>
          {plan.per_type.map((row) => (
            <tr key={row.traffic_type} className={row.apply ? 'is-selected' : undefined}>
              <th scope="row">
                {row.traffic_type}
                {row.suspect && <span className="recommended-tag">suspect vector</span>}
                {row.note && (
                  <ul className="policy-notes">
                    <li>{row.note}</li>
                  </ul>
                )}
              </th>
              <td className="mono">{row.ratio_to_normal_now.toFixed(1)}×</td>
              <td className="mono">{formatRate(row.current_policy_pps)}</td>
              <td className="mono">{formatRate(row.legit_envelope_pps)}</td>
              <td className="mono">
                {row.changes ? formatRate(row.suggested_policy_pps) : 'keep current'}
              </td>
              <td className="mono">
                {row.changes ? `${(row.change_pct * 100).toFixed(0)}%` : '—'}
              </td>
              <td className="mono">
                {row.changes
                  ? `${row.backtest_legit_lost_pct.toFixed(3)}% of non-incident traffic · ${row.backtest_windows_blocked} windows`
                  : '—'}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

interface Props {
  drill: DrillResult | null
  loading: boolean
  error: string | null
  onRun: () => void
  canRun: boolean
  recommended: PolicyKey | null
  selected: PolicyKey | null
  onSelect: (policy: PolicyKey) => void
}

export function TradeOffTable({
  drill,
  loading,
  error,
  onRun,
  canRun,
  recommended,
  selected,
  onSelect,
}: Props) {
  return (
    <section className="panel drill-panel">
      <header className="panel-head">
        <h2>Rehearsal room</h2>
        <span className="panel-note">simulation only · nothing is applied</span>
      </header>

      {!drill && !loading && (
        <div className="evidence-empty">
          <p>
            Try all four candidate responses against this replay and compare what each
            buys and what it costs.
          </p>
          <button type="button" className="primary" onClick={onRun} disabled={!canRun}>
            Run the drill
          </button>
        </div>
      )}

      {loading && <p className="empty">Running the drill…</p>}
      {error && <p className="error">{error}</p>}

      {drill && (
        <>
          <table className="tradeoff">
            <thead>
              <tr>
                <th scope="col">Response</th>
                <th scope="col">Attack removed</th>
                <th scope="col">Legitimate traffic lost</th>
                <th scope="col">Time to contain</th>
                <th scope="col">Blast radius</th>
                <th scope="col">Undo</th>
              </tr>
            </thead>
            <tbody>
              {drill.candidates.map((candidate) => (
                <Row
                  key={candidate.policy_key}
                  candidate={candidate}
                  isRecommended={recommended === candidate.policy_key}
                  isSelected={selected === candidate.policy_key}
                  onSelect={onSelect}
                />
              ))}
            </tbody>
          </table>

          <PolicyPlanTable plan={drill.policy_plan} />

          <p className="disclaimer">{drill.disclaimer}</p>
          <button type="button" className="secondary" onClick={onRun} disabled={loading}>
            Re-run the drill
          </button>
        </>
      )}
    </section>
  )
}

function Row({
  candidate,
  isRecommended,
  isSelected,
  onSelect,
}: {
  candidate: SimulationCandidate
  isRecommended: boolean
  isSelected: boolean
  onSelect: (policy: PolicyKey) => void
}) {
  const collateralClass =
    candidate.legitimate_packets_lost_pct >= 10
      ? 'cost-high'
      : candidate.legitimate_packets_lost_pct >= 4
        ? 'cost-medium'
        : 'cost-low'

  return (
    <tr
      className={`${isSelected ? 'is-selected' : ''}${isRecommended ? ' is-recommended' : ''}`}
      onClick={() => onSelect(candidate.policy_key)}
    >
      <th scope="row">
        <span className="policy-name">{candidate.policy_name}</span>
        {isRecommended && <span className="recommended-tag">advisor's pick</span>}
        {candidate.notes.length > 0 && (
          <ul className="policy-notes">
            {candidate.notes.map((note, index) => (
              <li key={index}>{note}</li>
            ))}
          </ul>
        )}
      </th>
      <td className="mono">{candidate.attack_packets_blocked_pct.toFixed(1)}%</td>
      <td className={`mono ${collateralClass}`}>
        {candidate.legitimate_packets_lost_pct.toFixed(2)}%
      </td>
      <td className="mono">
        {candidate.time_to_contain_seconds === null
          ? 'not contained'
          : formatSeconds(candidate.time_to_contain_seconds)}
      </td>
      <td className="blast">{candidate.blast_radius}</td>
      <td className="mono">{formatSeconds(candidate.reversible_seconds)}</td>
    </tr>
  )
}
