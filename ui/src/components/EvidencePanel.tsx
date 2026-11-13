/**
 * Evidence panel — the agents' briefing, with the validator's verdict shown.
 *
 * The validator's result is rendered, not hidden. An operator who can see that
 * every claim was checked is an operator who can calibrate how much to trust
 * this panel; silently discarding failures would remove exactly that signal.
 */

import type { AgentOutput, Briefing } from '../lib/types'
import { featureLabel } from '../lib/format'

interface Props {
  briefing: Briefing | null
  loading: boolean
  error: string | null
  onRequest: () => void
  canRequest: boolean
}

const READING_LABELS: Record<string, string> = {
  attack: 'Attack beginning',
  flash_crowd: 'Legitimate surge',
  ambiguous: 'Ambiguous',
}

export function EvidencePanel({ briefing, loading, error, onRequest, canRequest }: Props) {
  return (
    <section className="panel evidence-panel">
      <header className="panel-head">
        <h2>Evidence &amp; explanation</h2>
        <span className="panel-note">
          {briefing ? `engine: ${briefing.engine}` : 'two bounded agents + code validator'}
        </span>
      </header>

      {!briefing && !loading && (
        <div className="evidence-empty">
          <p>
            The agents gather evidence and explain the forecast. They never produce risk
            numbers — the model owns those.
          </p>
          <button type="button" className="primary" onClick={onRequest} disabled={!canRequest}>
            Ask the agents
          </button>
        </div>
      )}

      {loading && <p className="empty">Agents are gathering evidence…</p>}
      {error && <p className="error">{error}</p>}

      {briefing && (
        <>
          {briefing.state === 'MONITORING_ONLY' && (
            <p className="degraded-note">
              <strong>Monitoring only.</strong> At least one explanation failed validation,
              so DFSI is withholding it. Treat the situation as unknown.
            </p>
          )}

          <AgentCard
            agent={briefing.investigator}
            title="Investigator"
            subtitle="Attack, or flash crowd?"
            badge={
              briefing.investigator.output.reading
                ? READING_LABELS[briefing.investigator.output.reading]
                : undefined
            }
          />
          <AgentCard
            agent={briefing.advisor}
            title="Advisor"
            subtitle="What are the options?"
          />

          <button type="button" className="secondary" onClick={onRequest} disabled={loading}>
            Re-run the agents
          </button>
        </>
      )}
    </section>
  )
}

function AgentCard({
  agent,
  title,
  subtitle,
  badge,
}: {
  agent: AgentOutput
  title: string
  subtitle: string
  badge?: string
}) {
  const { output, validated, validator } = agent
  return (
    <article className={`agent-card${validated ? '' : ' is-rejected'}`}>
      <header>
        <div>
          <h3>{title}</h3>
          <span className="agent-subtitle">{subtitle}</span>
        </div>
        {badge && <span className="agent-badge">{badge}</span>}
      </header>

      <p className="agent-headline">{output.headline}</p>
      <p className="agent-summary">{output.summary}</p>

      {output.reason && <p className="agent-reason">{output.reason}</p>}

      {output.evidence_cited && output.evidence_cited.length > 0 && (
        <ul className="evidence-list">
          {output.evidence_cited.map((item, index) => (
            <li key={`${item.feature}-${index}`}>
              <span className="evidence-label">{featureLabel(item.feature, item.label)}</span>
              <span className="evidence-value mono">{item.value}</span>
              {item.why && <span className="evidence-why">{item.why}</span>}
            </li>
          ))}
        </ul>
      )}

      {output.contrary_evidence && output.contrary_evidence.length > 0 && (
        <div className="contrary">
          <h4>Evidence against this reading</h4>
          <ul>
            {output.contrary_evidence.map((text, index) => (
              <li key={index}>{text}</li>
            ))}
          </ul>
        </div>
      )}

      <footer className="validator-strip">
        <span className={`validator-verdict${validated ? ' is-pass' : ' is-fail'}`}>
          {validated ? 'validator: passed' : 'validator: rejected'}
        </span>
        <span className="validator-checks">
          {Object.entries(validator.checks).map(([check, passed]) => (
            <span key={check} className={passed ? 'check-pass' : 'check-fail'}>
              {check.replace(/_/g, ' ')}
            </span>
          ))}
        </span>
        {agent.tool_calls.length > 0 && (
          <span className="tool-trace">
            read: {agent.tool_calls.map((call) => call.tool).join(', ')}
          </span>
        )}
      </footer>

      {validator.failures.length > 0 && (
        <ul className="validator-failures">
          {validator.failures.map((failure, index) => (
            <li key={index}>{failure}</li>
          ))}
        </ul>
      )}
    </article>
  )
}
