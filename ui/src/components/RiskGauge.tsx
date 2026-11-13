/** Risk gauge — the model's number, and only the model's number. */

import type { Forecast } from '../lib/types'
import { BAND_COLOURS, BAND_LABELS, formatRiskScore, formatSeconds } from '../lib/format'

interface Props {
  forecast: Forecast | null
  leadSeconds: number | null
  bellRung: boolean
}

const RADIUS = 68
const CIRCUMFERENCE = 2 * Math.PI * RADIUS

export function RiskGauge({ forecast, leadSeconds, bellRung }: Props) {
  const score = forecast?.risk_score ?? null
  const band = forecast?.risk_band ?? 'unknown'
  const colour = BAND_COLOURS[band]
  const filled = score === null ? 0 : Math.max(0, Math.min(1, score))

  return (
    <section className="panel gauge-panel">
      <header className="panel-head">
        <h2>Risk of onset</h2>
        <span className="panel-note">
          next {forecast?.horizon_seconds ?? 120}s · from the model
        </span>
      </header>

      <div className="gauge-body">
        <svg viewBox="0 0 160 160" className="gauge" role="img" aria-label="risk gauge">
          <circle cx="80" cy="80" r={RADIUS} className="gauge-track" />
          <circle
            cx="80"
            cy="80"
            r={RADIUS}
            className="gauge-fill"
            stroke={colour}
            strokeDasharray={CIRCUMFERENCE}
            strokeDashoffset={CIRCUMFERENCE * (1 - filled)}
            transform="rotate(-90 80 80)"
          />
          <text x="80" y="76" className="gauge-score" fill={colour}>
            {formatRiskScore(score)}
          </text>
          <text x="80" y="98" className="gauge-band">
            {BAND_LABELS[band]}
          </text>
        </svg>

        <dl className="gauge-facts">
          <div>
            <dt>DFSI state</dt>
            <dd className={forecast?.degraded ? 'is-degraded' : undefined}>
              {forecast?.degraded
                ? 'Monitoring only'
                : forecast?.predicted_state === 'ABOUT_TO_START'
                  ? 'About to start'
                  : 'Quiet'}
            </dd>
          </div>
          <div>
            <dt>Shared State</dt>
            <dd>{bellRung ? 'Attack declared' : 'No attack declared'}</dd>
          </div>
          <div>
            <dt>Head start</dt>
            <dd className={leadSeconds ? 'is-lead' : undefined}>
              {formatSeconds(leadSeconds)}
            </dd>
          </div>
          <div>
            <dt>Model</dt>
            <dd className="mono">{forecast?.model_version ?? '—'}</dd>
          </div>
        </dl>
      </div>

      {forecast?.degraded && (
        <p className="degraded-note">
          <strong>Monitoring only.</strong> {forecast.degraded_reason} Treat this as
          unknown, not as low risk.
        </p>
      )}
    </section>
  )
}
