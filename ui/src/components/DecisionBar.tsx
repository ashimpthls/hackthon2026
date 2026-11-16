/**
 * Human decision — the last step, and the only one that matters.
 *
 * Every path through this component is explicit that nothing is applied. The
 * approval is recorded for audit so that a forecast a human acted on can be
 * traced back to the exact model version that produced it.
 */

import { useState } from 'react'
import type { DecisionRecord, PolicyKey, PolicyPlan } from '../lib/types'
import { POLICY_LABELS, formatRate } from '../lib/format'

/** What approving "tighten" would actually change, in numbers. */
function ProposedChange({ plan }: { plan: PolicyPlan | null }) {
  if (!plan) {
    return <p className="panel-note">Run the drill to see the concrete thresholds.</p>
  }
  if (!plan.available) {
    return <p className="panel-note">No threshold suggestion: {plan.reason}.</p>
  }
  const applied = plan.per_type.filter((row) => row.apply)
  if (applied.length === 0) {
    return (
      <p className="panel-note">
        No traffic type can be tightened without dropping last week's legitimate peaks.
      </p>
    )
  }
  return (
    <ul className="proposed-change">
      {applied.map((row) => (
        <li key={row.traffic_type} className="mono">
          {row.traffic_type}: policy {formatRate(row.current_policy_pps)} →{' '}
          <strong>{formatRate(row.suggested_policy_pps)}</strong> (
          {(row.change_pct * 100).toFixed(0)}%) · last {plan.history_days.toFixed(0)} days it
          would have dropped {row.backtest_legit_lost_pct.toFixed(3)}% of legitimate packets
        </li>
      ))}
    </ul>
  )
}

interface Props {
  selected: PolicyKey | null
  recommended: PolicyKey | null
  policyPlan: PolicyPlan | null
  modelVersion: string
  decisionEpoch: number | null
  onDecide: (verdict: 'approved' | 'rejected' | 'changed', note: string) => Promise<void>
  /** Run the attack again, now under the policy just approved. */
  onReplay: () => void
  canReplay: boolean
  lastDecision: DecisionRecord | null
  busy: boolean
  error: string | null
}

export function DecisionBar({
  selected,
  recommended,
  policyPlan,
  modelVersion,
  decisionEpoch,
  onDecide,
  onReplay,
  canReplay,
  lastDecision,
  busy,
  error,
}: Props) {
  const [note, setNote] = useState('')
  const ready = selected !== null && decisionEpoch !== null && !busy

  const submit = async (verdict: 'approved' | 'rejected' | 'changed') => {
    await onDecide(verdict, note)
    setNote('')
  }

  const verdictForSelection: 'approved' | 'changed' =
    selected !== null && recommended !== null && selected !== recommended
      ? 'changed'
      : 'approved'

  return (
    <section className="panel decision-panel">
      <header className="panel-head">
        <h2>Human decision</h2>
        <span className="panel-note">
          audited · approved threshold changes go to the simulated policy store
        </span>
      </header>

      <div className="decision-body">
        <p className="decision-selection">
          {selected ? (
            <>
              Selected: <strong>{POLICY_LABELS[selected]}</strong>
              {recommended && selected !== recommended && (
                <span className="override-tag">
                  differs from the advisor's pick ({POLICY_LABELS[recommended]})
                </span>
              )}
            </>
          ) : (
            <>Pick a response in the rehearsal room to record a decision.</>
          )}
        </p>
        {selected === 'tighten_vector' && <ProposedChange plan={policyPlan} />}

        <label className="decision-note">
          Note for the record
          <input
            type="text"
            value={note}
            maxLength={500}
            placeholder="Why this choice?"
            onChange={(event) => setNote(event.target.value)}
            disabled={busy}
          />
        </label>

        <div className="decision-actions">
          <button
            type="button"
            className="primary"
            onClick={() => submit(verdictForSelection)}
            disabled={!ready}
          >
            {verdictForSelection === 'changed' ? 'Record my override' : 'Approve'}
          </button>
          <button
            type="button"
            className="secondary"
            onClick={() => submit('rejected')}
            disabled={!ready}
          >
            Reject — take no action
          </button>
        </div>

        <p className="decision-provenance mono">
          model {modelVersion}
          {decisionEpoch !== null && ` · deciding at window ${decisionEpoch}`}
        </p>
      </div>

      {error && <p className="error">{error}</p>}

      {lastDecision && (
        <div className="decision-receipt">
          <p>
            {lastDecision.applied ? 'Applied' : 'Recorded'}: <strong>{lastDecision.verdict}</strong>{' '}
            — {lastDecision.policy_name}
            {lastDecision.policy_change?.change ? ` (${lastDecision.policy_change.change})` : ''}.{' '}
            {lastDecision.note_to_auditor}
          </p>
          {lastDecision.applied && (
            <button type="button" className="primary" onClick={onReplay} disabled={!canReplay}>
              Replay this attack under policy v{lastDecision.policy_version_after}
            </button>
          )}
        </div>
      )}
    </section>
  )
}
