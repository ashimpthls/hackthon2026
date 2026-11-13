/** Trigger controls — pick a scenario and start the replay. */

import type { CatalogueEntry } from '../lib/types'
import { SCENARIO_LABELS } from '../lib/format'

interface Props {
  catalogue: CatalogueEntry[]
  selected: string | null
  onSelect: (replayId: string) => void
  speed: number
  onSpeedChange: (speed: number) => void
  running: boolean
  onStart: () => void
  onStop: () => void
  disabled: boolean
}

const SPEEDS = [
  { label: '1× (10s / window)', value: 1 },
  { label: '4× (2.5s)', value: 0.25 },
  { label: '20× (0.5s)', value: 0.05 },
  { label: 'As fast as possible', value: 0 },
]

export function TriggerPanel({
  catalogue,
  selected,
  onSelect,
  speed,
  onSpeedChange,
  running,
  onStart,
  onStop,
  disabled,
}: Props) {
  const grouped = catalogue.reduce<Record<string, CatalogueEntry[]>>((acc, entry) => {
    acc[entry.scenario_type] = [...(acc[entry.scenario_type] ?? []), entry]
    return acc
  }, {})

  return (
    <section className="panel trigger">
      <header className="panel-head">
        <h2>Trigger a replay</h2>
        <span className="panel-note">Synthetic traffic · nothing is applied</span>
      </header>

      <div className="scenario-grid">
        {Object.entries(grouped).map(([type, entries]) => (
          <div key={type} className="scenario-group">
            <h3>{SCENARIO_LABELS[type] ?? type}</h3>
            {entries.map((entry) => (
              <button
                key={entry.replay_id}
                type="button"
                className={`scenario-button${selected === entry.replay_id ? ' is-selected' : ''}`}
                onClick={() => onSelect(entry.replay_id)}
                disabled={running}
              >
                <span className="scenario-range">range {entry.range_id}</span>
                <span className={`scenario-tag${entry.has_onset ? ' has-onset' : ''}`}>
                  {entry.has_onset ? 'onset in this replay' : 'no onset'}
                </span>
              </button>
            ))}
          </div>
        ))}
      </div>

      <div className="trigger-controls">
        <label>
          Replay speed
          <select
            value={speed}
            onChange={(event) => onSpeedChange(Number(event.target.value))}
            disabled={running}
          >
            {SPEEDS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </label>

        {running ? (
          <button type="button" className="primary stop" onClick={onStop}>
            Stop replay
          </button>
        ) : (
          <button
            type="button"
            className="primary"
            onClick={onStart}
            disabled={disabled || !selected}
          >
            Start replay
          </button>
        )}
      </div>
    </section>
  )
}
