/**
 * DFSI dashboard.
 *
 * Drives the demo flow from the pitch: replay synthetic traffic → watch the
 * risk score climb before the threshold alarm → ask the agents whether it is
 * an attack or a flash crowd → compare the four responses → record a human
 * decision.
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { api, streamReplay } from './lib/api'
import type {
  Briefing,
  CatalogueEntry,
  DecisionRecord,
  DrillResult,
  Health,
  ModelCard,
  PolicyKey,
  ReplayEvent,
  WindowEvent,
} from './lib/types'
import { DecisionBar } from './components/DecisionBar'
import { EvidencePanel } from './components/EvidencePanel'
import { RiskGauge } from './components/RiskGauge'
import { StatusBar } from './components/StatusBar'
import { Timeline } from './components/Timeline'
import { TradeOffTable } from './components/TradeOffTable'
import { TriggerPanel } from './components/TriggerPanel'

export default function App() {
  const [health, setHealth] = useState<Health | null>(null)
  const [modelCard, setModelCard] = useState<ModelCard | null>(null)
  const [catalogue, setCatalogue] = useState<CatalogueEntry[]>([])
  const [bootError, setBootError] = useState<string | null>(null)

  const [selectedReplay, setSelectedReplay] = useState<string | null>(null)
  const [speed, setSpeed] = useState(0.25)
  const [running, setRunning] = useState(false)
  const [windows, setWindows] = useState<WindowEvent[]>([])
  const [streamError, setStreamError] = useState<string | null>(null)
  const abortRef = useRef<(() => void) | null>(null)

  // The window the operator is reasoning about. Follows the stream while it
  // runs, then stays put so the drill and the briefing agree on one instant.
  const [focusEpoch, setFocusEpoch] = useState<number | null>(null)

  const [briefing, setBriefing] = useState<Briefing | null>(null)
  const [briefingLoading, setBriefingLoading] = useState(false)
  const [briefingError, setBriefingError] = useState<string | null>(null)

  const [drill, setDrill] = useState<DrillResult | null>(null)
  const [drillLoading, setDrillLoading] = useState(false)
  const [drillError, setDrillError] = useState<string | null>(null)

  const [selectedPolicy, setSelectedPolicy] = useState<PolicyKey | null>(null)
  const [lastDecision, setLastDecision] = useState<DecisionRecord | null>(null)
  const [decisionBusy, setDecisionBusy] = useState(false)
  const [decisionError, setDecisionError] = useState<string | null>(null)

  // ---------------------------------------------------------------- boot
  useEffect(() => {
    let cancelled = false
    void (async () => {
      try {
        const [healthResult, replayResult] = await Promise.all([
          api.health(),
          api.replays(),
        ])
        if (cancelled) return
        setHealth(healthResult)
        setCatalogue(replayResult.replays)
        setSelectedReplay(
          replayResult.replays.find((entry) => entry.scenario_type === 'gradual_buildup')
            ?.replay_id ?? replayResult.replays[0]?.replay_id ?? null,
        )
      } catch (error) {
        if (!cancelled) {
          setBootError(error instanceof Error ? error.message : String(error))
        }
      }
      try {
        const card = await api.modelCard()
        if (!cancelled) setModelCard(card)
      } catch {
        // The model card is informational; the dashboard works without it.
      }
    })()
    return () => {
      cancelled = true
    }
  }, [])

  // Abort any live stream when the component goes away.
  useEffect(() => () => abortRef.current?.(), [])

  // ---------------------------------------------------------------- replay
  const handleEvent = useCallback((event: ReplayEvent) => {
    switch (event.type) {
      case 'start':
        setWindows([])
        setStreamError(null)
        break
      case 'window':
        setWindows((previous) => [...previous, event])
        setFocusEpoch(event.window_start_epoch)
        break
      case 'summary':
        break
      case 'error':
        setStreamError(event.message)
        break
    }
  }, [])

  const start = useCallback(() => {
    if (!selectedReplay) return
    abortRef.current?.()
    setWindows([])
    setBriefing(null)
    setDrill(null)
    setSelectedPolicy(null)
    setLastDecision(null)
    setStreamError(null)
    setRunning(true)

    abortRef.current = streamReplay(
      { replayId: selectedReplay, speedSeconds: speed },
      handleEvent,
      (error) => {
        setRunning(false)
        abortRef.current = null
        if (error) setStreamError(error.message)
      },
    )
  }, [handleEvent, selectedReplay, speed])

  const stop = useCallback(() => {
    abortRef.current?.()
    abortRef.current = null
    setRunning(false)
  }, [])

  // ------------------------------------------------------- agents & drill
  const requestBriefing = useCallback(async () => {
    if (!selectedReplay) return
    setBriefingLoading(true)
    setBriefingError(null)
    try {
      const result = await api.explain(selectedReplay, focusEpoch ?? undefined)
      setBriefing(result)
      const pick = result.advisor.output.recommended_policy_key
      if (pick) setSelectedPolicy(pick)
    } catch (error) {
      setBriefingError(error instanceof Error ? error.message : String(error))
    } finally {
      setBriefingLoading(false)
    }
  }, [focusEpoch, selectedReplay])

  const runDrill = useCallback(async () => {
    if (!selectedReplay) return
    setDrillLoading(true)
    setDrillError(null)
    try {
      setDrill(await api.drill(selectedReplay, focusEpoch ?? undefined))
    } catch (error) {
      setDrillError(error instanceof Error ? error.message : String(error))
    } finally {
      setDrillLoading(false)
    }
  }, [focusEpoch, selectedReplay])

  const decide = useCallback(
    async (verdict: 'approved' | 'rejected' | 'changed', note: string) => {
      if (!selectedReplay || !selectedPolicy) return
      const epoch = drill?.decision_epoch ?? focusEpoch
      if (epoch === null || epoch === undefined) return
      setDecisionBusy(true)
      setDecisionError(null)
      try {
        setLastDecision(
          await api.decide({
            replayId: selectedReplay,
            decisionEpoch: epoch,
            policyKey: selectedPolicy,
            verdict,
            note,
            modelVersion: health?.model_version ?? 'unknown',
          }),
        )
      } catch (error) {
        setDecisionError(error instanceof Error ? error.message : String(error))
      } finally {
        setDecisionBusy(false)
      }
    },
    [drill?.decision_epoch, focusEpoch, health?.model_version, selectedPolicy, selectedReplay],
  )

  // ---------------------------------------------------------------- render
  const latest = windows.length > 0 ? windows[windows.length - 1] : null
  const focused =
    windows.find((window) => window.window_start_epoch === focusEpoch) ?? latest
  const recommended = briefing?.advisor.output.recommended_policy_key ?? null

  if (bootError) {
    return (
      <main className="app">
        <StatusBar health={health} modelCard={modelCard} streamError={null} />
        <section className="panel">
          <h2>DFSI is not ready</h2>
          <p className="error">{bootError}</p>
          <p>
            Build the Memory layer and train the forecaster, then restart the API:
          </p>
          <pre>
            {`python -m dfsi.memory.pipeline --days 28
python -m dfsi.forecaster.train
uvicorn dfsi.api.app:app --port 8000`}
          </pre>
        </section>
      </main>
    )
  }

  return (
    <main className="app">
      <StatusBar health={health} modelCard={modelCard} streamError={streamError} />

      <div className="layout">
        <div className="column-left">
          <TriggerPanel
            catalogue={catalogue}
            selected={selectedReplay}
            onSelect={setSelectedReplay}
            speed={speed}
            onSpeedChange={setSpeed}
            running={running}
            onStart={start}
            onStop={stop}
            disabled={!health?.forecaster_ready}
          />
          <RiskGauge
            forecast={focused?.forecast ?? null}
            leadSeconds={latest?.lead_seconds ?? null}
            bellRung={Boolean(latest?.shared_state.declared)}
          />
        </div>

        <div className="column-right">
          <Timeline
            windows={windows}
            threshold={modelCard?.decision_threshold ?? 0.25}
            selectedEpoch={focusEpoch}
            onScrub={(epoch) => {
              if (!running) setFocusEpoch(epoch)
            }}
          />
          <EvidencePanel
            briefing={briefing}
            loading={briefingLoading}
            error={briefingError}
            onRequest={requestBriefing}
            canRequest={Boolean(selectedReplay) && !briefingLoading}
          />
          <TradeOffTable
            drill={drill}
            loading={drillLoading}
            error={drillError}
            onRun={runDrill}
            canRun={Boolean(selectedReplay) && !drillLoading}
            recommended={recommended}
            selected={selectedPolicy}
            onSelect={setSelectedPolicy}
          />
          <DecisionBar
            selected={selectedPolicy}
            recommended={recommended}
            policyPlan={drill?.policy_plan ?? null}
            modelVersion={health?.model_version ?? 'unknown'}
            decisionEpoch={drill?.decision_epoch ?? focusEpoch}
            onDecide={decide}
            lastDecision={lastDecision}
            busy={decisionBusy}
            error={decisionError}
          />
        </div>
      </div>

      <footer className="app-footer">
        <p>
          DFSI is read-only and simulation-only. It never changes a customer's
          configuration, never blocks traffic and never applies a policy. Every number
          on this page comes from the model or the simulator; the agents only explain.
        </p>
        {modelCard && (
          <details>
            <summary>Known limitations of this model</summary>
            <ul>
              {modelCard.known_limitations.map((limitation, index) => (
                <li key={index}>{limitation}</li>
              ))}
            </ul>
          </details>
        )}
      </footer>
    </main>
  )
}
