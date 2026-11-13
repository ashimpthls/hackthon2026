/**
 * Human decision — the last step, and the only one that matters.
 *
 * Every path through this component is explicit that nothing is applied. The
 * approval is recorded for audit so that a forecast a human acted on can be
 * traced back to the exact model version that produced it.
 */

import { useState } from 'react'
import type { DecisionRecord, PolicyKey } from '../lib/types'
import { POLICY_LABELS } from '../lib/format'

interface Props {
  selected: PolicyKey | null
  recommended: PolicyKey | null
  modelVersion: string
  decisionEpoch: number | null
  onDecide: (verdict: 'approved' | 'rejected' | 'changed', note: string) => Promise<void>
  lastDecision: DecisionRecord | null
  busy: boolean
  error: string | null
}

export function DecisionBar({
  selected,
  recommended,
  modelVersion,
  decisionEpoch,
  onDecide,
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
        <span className="panel-note">recorded for audit · never applied</span>
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
        <p className="decision-receipt">
          Recorded: <strong>{lastDecision.verdict}</strong> — {lastDecision.policy_name}.{' '}
          {lastDecision.note_to_auditor}
        </p>
      )}
    </section>
  )
}
