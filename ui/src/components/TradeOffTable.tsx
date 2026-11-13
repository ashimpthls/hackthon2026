/**
 * Rehearsal room — the four fixed responses, side by side.
 *
 * The operator's decision is made here, so the costly columns (collateral,
 * blast radius) carry the same visual weight as the effective ones. A table
 * that only showed suppression would be an argument for full scrubbing every
 * time.
 */

import type { DrillResult, PolicyKey, SimulationCandidate } from '../lib/types'
import { formatSeconds } from '../lib/format'

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
