/** Header strip: what DFSI is, and whether it is actually working. */

import type { Health, ModelCard } from '../lib/types'
import { formatPercent, formatSeconds } from '../lib/format'

interface Props {
  health: Health | null
  modelCard: ModelCard | null
  streamError: string | null
}

export function StatusBar({ health, modelCard, streamError }: Props) {
  const dfsi = modelCard?.headline_metrics
  const baseline = modelCard?.baseline_metrics

  // The incumbent's lead is zero by construction — its warning *is* the bell.
  // Quoting its recall against ours would read as a broken comparison, so the
  // honest headline is the gap between the two warning times.
  const leadGain =
    dfsi && baseline
      ? (dfsi.median_lead_seconds ?? 0) - (baseline.median_lead_seconds ?? 0)
      : null

  return (
    <header className="status-bar">
      <div className="brand">
        <h1>DFSI</h1>
        <p>
          DDoS Future-State Intelligence — <em>see the storm before it lands</em>
        </p>
        <p className="motto">ML makes the numbers. AI explains them. Humans decide.</p>
      </div>

      <dl className="status-metrics">
        {dfsi && (
          <>
            <div title="Measured against the moment Shared State would declare, over attacks that had a runway to forecast from.">
              <dt>median head start</dt>
              <dd>{formatSeconds(dfsi.median_lead_seconds ?? null)}</dd>
            </div>
            <div
              title={`Warned early on ${dfsi.n_forecastable_scenarios ?? 0} forecastable attacks. ${
                dfsi.n_in_progress_scenarios ?? 0
              } more were already under way at the first window — detected, but not forecast.`}
            >
              <dt>attacks caught early</dt>
              <dd>{formatPercent(dfsi.attacks_caught ?? 0, 0)}</dd>
            </div>
            <div>
              <dt>false alarms</dt>
              <dd>{formatPercent(dfsi.false_alarm_rate ?? 0, 1)}</dd>
            </div>
            <div title="The threshold alarm's warning is the declaration itself, so its lead time is zero by construction. This is the gap DFSI buys.">
              <dt>vs threshold alarm</dt>
              <dd>
                {leadGain !== null && leadGain > 0
                  ? `+${formatSeconds(leadGain)} earlier`
                  : 'no gain'}
              </dd>
            </div>
          </>
        )}
      </dl>

      <div className="status-chips">
        <span className={`chip${health?.status === 'ok' ? ' is-ok' : ' is-degraded'}`}>
          {health ? `api ${health.status}` : 'api unreachable'}
        </span>
        <span className={`chip${health?.forecaster_ready ? ' is-ok' : ' is-degraded'}`}>
          {health?.forecaster_ready ? 'forecaster ready' : 'no model'}
        </span>
        <span className="chip">agents: {health?.agent_engine ?? '—'}</span>
        <span className="chip mono">{health?.model_version ?? '—'}</span>
        <span className="chip is-readonly">read-only · simulation only</span>
      </div>

      {health?.forecaster_error && <p className="error">{health.forecaster_error}</p>}
      {streamError && <p className="error">{streamError}</p>}
    </header>
  )
}
