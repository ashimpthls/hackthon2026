/**
 * Timeline — the core of the demo.
 *
 * Two series on one time axis: the model's risk score, and the traffic the
 * existing threshold alarm reacts to. The gap between the warning marker and
 * the bell marker *is* the head start DFSI claims, so it is drawn explicitly
 * rather than left for the viewer to infer.
 */

import { useMemo } from 'react'
import type { WindowEvent } from '../lib/types'
import { BAND_COLOURS, formatClock, formatRate, formatSeconds } from '../lib/format'

interface Props {
  windows: WindowEvent[]
  /** The selected range's decision threshold: its raw.json `risk_threshold`, or the model's. */
  threshold: number
  onScrub?: (epoch: number) => void
  selectedEpoch: number | null
}

const WIDTH = 920
const HEIGHT = 260
const PAD = { top: 18, right: 56, bottom: 28, left: 52 }

export function Timeline({ windows, threshold, onScrub, selectedEpoch }: Props) {
  const geometry = useMemo(() => {
    if (windows.length === 0) return null

    const plotWidth = WIDTH - PAD.left - PAD.right
    const plotHeight = HEIGHT - PAD.top - PAD.bottom
    const epochs = windows.map((w) => w.window_start_epoch)
    const minEpoch = Math.min(...epochs)
    const maxEpoch = Math.max(...epochs)
    const span = Math.max(1, maxEpoch - minEpoch)
    const maxPps = Math.max(...windows.map((w) => w.traffic.total_pps), 1)

    const x = (epoch: number) => PAD.left + ((epoch - minEpoch) / span) * plotWidth
    const yRisk = (score: number) => PAD.top + (1 - score) * plotHeight
    const yPps = (pps: number) => PAD.top + (1 - pps / maxPps) * plotHeight

    const riskPath = windows
      .map((w, index) => {
        // A degraded window has no score; break the line rather than draw a
        // zero, which would read as "safe".
        const score = w.forecast.risk_score
        if (score === null) return ''
        const command = index === 0 ? 'M' : 'L'
        return `${command}${x(w.window_start_epoch).toFixed(1)},${yRisk(score).toFixed(1)}`
      })
      .filter(Boolean)
      .join(' ')

    const trafficPath = windows
      .map((w, index) => {
        const command = index === 0 ? 'M' : 'L'
        return `${command}${x(w.window_start_epoch).toFixed(1)},${yPps(w.traffic.total_pps).toFixed(1)}`
      })
      .join(' ')

    const latest = windows[windows.length - 1]
    return {
      x,
      yRisk,
      yPps,
      riskPath,
      trafficPath,
      maxPps,
      minEpoch,
      maxEpoch,
      plotWidth,
      plotHeight,
      warningEpoch: latest.first_warning_epoch,
      bellEpoch: latest.shared_state.bell_epoch,
      onsetEpoch: latest.onset_epoch,
    }
  }, [windows])

  if (!geometry) {
    return (
      <section className="panel timeline-panel">
        <header className="panel-head">
          <h2>Timeline</h2>
        </header>
        <p className="empty">Start a replay to see the forecast against live traffic.</p>
      </section>
    )
  }

  const { warningEpoch, bellEpoch } = geometry
  const lead = warningEpoch !== null && bellEpoch !== null ? bellEpoch - warningEpoch : null

  return (
    <section className="panel timeline-panel">
      <header className="panel-head">
        <h2>Timeline</h2>
        <div className="legend">
          <span className="legend-item risk">risk score</span>
          <span className="legend-item traffic">packet rate</span>
          <span className="legend-item warn-line">DFSI warning</span>
          <span className="legend-item bell-line">threshold alarm</span>
        </div>
      </header>

      <svg viewBox={`0 0 ${WIDTH} ${HEIGHT}`} className="timeline">
        {/* Decision threshold this range is read against. */}
        <line
          x1={PAD.left}
          x2={WIDTH - PAD.right}
          y1={geometry.yRisk(threshold)}
          y2={geometry.yRisk(threshold)}
          className="threshold-line"
        />
        <text x={WIDTH - PAD.right + 4} y={geometry.yRisk(threshold) + 4} className="axis-label">
          {threshold.toFixed(2)}
        </text>

        {/* The head start, drawn as the band between warning and bell. */}
        {warningEpoch !== null && bellEpoch !== null && (
          <rect
            x={geometry.x(warningEpoch)}
            y={PAD.top}
            width={Math.max(1, geometry.x(bellEpoch) - geometry.x(warningEpoch))}
            height={geometry.plotHeight}
            className="lead-band"
          />
        )}

        <path d={geometry.trafficPath} className="traffic-path" />
        <path d={geometry.riskPath} className="risk-path" />

        {warningEpoch !== null && (
          <g>
            <line
              x1={geometry.x(warningEpoch)}
              x2={geometry.x(warningEpoch)}
              y1={PAD.top}
              y2={HEIGHT - PAD.bottom}
              className="warn-marker"
            />
            <text x={geometry.x(warningEpoch) + 4} y={PAD.top + 12} className="marker-label warn">
              DFSI warns
            </text>
          </g>
        )}

        {bellEpoch !== null && (
          <g>
            <line
              x1={geometry.x(bellEpoch)}
              x2={geometry.x(bellEpoch)}
              y1={PAD.top}
              y2={HEIGHT - PAD.bottom}
              className="bell-marker"
            />
            <text x={geometry.x(bellEpoch) + 4} y={PAD.top + 28} className="marker-label bell">
              bell rings
            </text>
          </g>
        )}

        {/* Per-window dots, coloured by band and clickable for inspection. */}
        {windows.map((w) => {
          const score = w.forecast.risk_score
          if (score === null) return null
          const isSelected = selectedEpoch === w.window_start_epoch
          return (
            <circle
              key={w.window_start_epoch}
              cx={geometry.x(w.window_start_epoch)}
              cy={geometry.yRisk(score)}
              r={isSelected ? 5 : 2.5}
              fill={BAND_COLOURS[w.forecast.risk_band]}
              className="window-dot"
              onClick={() => onScrub?.(w.window_start_epoch)}
            >
              <title>
                {formatClock(w.window_start_epoch)} · risk {score.toFixed(3)} ·{' '}
                {formatRate(w.traffic.total_pps)}
              </title>
            </circle>
          )
        })}

        <line
          x1={PAD.left}
          x2={WIDTH - PAD.right}
          y1={HEIGHT - PAD.bottom}
          y2={HEIGHT - PAD.bottom}
          className="axis"
        />
        <text x={PAD.left} y={HEIGHT - 8} className="axis-label">
          {formatClock(geometry.minEpoch)}
        </text>
        <text x={WIDTH - PAD.right} y={HEIGHT - 8} className="axis-label" textAnchor="end">
          {formatClock(geometry.maxEpoch)}
        </text>
      </svg>

      <p className="timeline-summary">
        {lead !== null ? (
          <>
            DFSI warned <strong>{formatSeconds(lead)}</strong> before the threshold alarm
            declared the attack.
          </>
        ) : warningEpoch !== null ? (
          <>DFSI has warned; the threshold alarm has not fired yet.</>
        ) : (
          <>No warning raised yet.</>
        )}
      </p>
    </section>
  )
}
