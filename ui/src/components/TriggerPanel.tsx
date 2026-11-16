/** Trigger controls — pick a scenario and start the replay. */

import { useEffect, useState } from 'react'
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

interface Account {
  account_id: number
  ranges: { range_id: number; has_onset?: boolean }[]
}

interface RawData {
  accounts: Account[]
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
  const [scenarios, setScenarios] = useState<string[]>([])
  const [accounts, setAccounts] = useState<Account[]>([])
  const [selectedScenario, setSelectedScenario] = useState<string>('')
  const [selectedAccountId, setSelectedAccountId] = useState<number | ''>('')
  const [selectedRangeId, setSelectedRangeId] = useState<number | ''>('')

  useEffect(() => {
    const uniqueScenarios = [...new Set(catalogue.map((entry) => entry.scenario_type))].sort()
    setScenarios(uniqueScenarios)
    if (uniqueScenarios.length > 0 && !selectedScenario) {
      setSelectedScenario(uniqueScenarios[0])
    }
  }, [catalogue])

  useEffect(() => {
    const loadAccounts = async () => {
      try {
        const response = await fetch('/raw.json')
        const data: RawData = await response.json()
        setAccounts(data.accounts)
        if (data.accounts.length > 0) {
          setSelectedAccountId(data.accounts[0].account_id)
        }
      } catch (error) {
        console.error('Failed to load accounts:', error)
      }
    }
    loadAccounts()
  }, [])

  useEffect(() => {
    if (selectedAccountId && scenarios.length > 0 && selectedScenario) {
      const account = accounts.find((acc) => acc.account_id === selectedAccountId)
      if (account && account.ranges.length > 0 && !selectedRangeId) {
        setSelectedRangeId(account.ranges[0].range_id)
      }
    }
  }, [selectedAccountId, selectedScenario, accounts, selectedRangeId, scenarios])

  useEffect(() => {
    if (selectedScenario && selectedAccountId && selectedRangeId) {
      const matching = catalogue.find(
        (entry) =>
          entry.scenario_type === selectedScenario &&
          entry.range_id === selectedRangeId,
      )
      if (matching) {
        onSelect(matching.replay_id)
      }
    }
  }, [selectedScenario, selectedAccountId, selectedRangeId, catalogue, onSelect])

  const currentAccount = accounts.find((acc) => acc.account_id === selectedAccountId)
  const ranges = currentAccount?.ranges ?? []
  const selectedReplay = catalogue.find((entry) => entry.replay_id === selected)

  // Prefer raw.json has_onset as source of truth, fallback to backend if needed
  const getOnsetStatus = (): boolean | null => {
    if (selectedRangeId) {
      const rangeData = currentAccount?.ranges.find((r) => r.range_id === selectedRangeId)
      if (rangeData?.has_onset !== undefined) {
        return rangeData.has_onset
      }
    }
    // Fallback to backend catalogue if raw.json doesn't have it
    if (selectedReplay?.has_onset !== undefined) {
      return selectedReplay.has_onset
    }
    return null
  }

  const onsetStatus = getOnsetStatus()

  return (
    <section className="panel trigger">
      <header className="panel-head">
        <h2>Trigger a replay</h2>
        <span className="panel-note">Synthetic traffic · nothing is applied</span>
      </header>

      <div className="trigger-selectors">
        <label>
          Scenario
          <select
            value={selectedScenario}
            onChange={(event) => setSelectedScenario(event.target.value)}
            disabled={running}
          >
            {scenarios.map((scenario) => (
              <option key={scenario} value={scenario}>
                {SCENARIO_LABELS[scenario] ?? scenario}
              </option>
            ))}
          </select>
        </label>

        <label>
          Account ID
          <select
            value={selectedAccountId}
            onChange={(event) => {
              const value = Number(event.target.value)
              setSelectedAccountId(value)
              setSelectedRangeId('')
            }}
            disabled={running}
          >
            {accounts.map((account) => (
              <option key={account.account_id} value={account.account_id}>
                {account.account_id}
              </option>
            ))}
          </select>
        </label>

        <label>
          Range ID
          <select
            value={selectedRangeId}
            onChange={(event) => setSelectedRangeId(Number(event.target.value))}
            disabled={running || ranges.length === 0}
          >
            {ranges.length === 0 ? (
              <option disabled value="">
                No ranges available
              </option>
            ) : (
              ranges.map((range) => (
                <option key={range.range_id} value={range.range_id}>
                  {range.range_id}
                </option>
              ))
            )}
          </select>
        </label>
      </div>

      {onsetStatus !== null && (
        <div className="replay-info">
          <span className={`scenario-tag${onsetStatus ? ' has-onset' : ''}`}>
            {onsetStatus ? 'onset in this replay' : 'no onset'}
          </span>
        </div>
      )}

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
