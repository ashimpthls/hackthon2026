/** Display helpers. Kept separate so components stay about layout. */

import type { PolicyKey, RiskBand } from './types'

export const BAND_COLOURS: Record<RiskBand, string> = {
  calm: '#3f9c6c',
  watch: '#c9a227',
  warn: '#d9743a',
  critical: '#c93a3a',
  unknown: '#6b7280',
}

export const BAND_LABELS: Record<RiskBand, string> = {
  calm: 'Calm',
  watch: 'Watch',
  warn: 'Warning',
  critical: 'Critical',
  unknown: 'Unknown',
}

export const SCENARIO_LABELS: Record<string, string> = {
  normal: 'Normal',
  flash_crowd: 'Flash crowd',
  gradual_buildup: 'Gradual build-up',
  sustained_attack: 'Sustained attack',
  recovery: 'Recovery',
}

export const POLICY_LABELS: Record<PolicyKey, string> = {
  hold: 'Hold — keep watching',
  tighten_vector: 'Tighten the vector threshold',
  rate_limit_sources: 'Rate-limit concentrated sources',
  full_scrub: 'Divert to full scrubbing',
}

export function formatRate(pps: number): string {
  if (pps >= 1_000_000) return `${(pps / 1_000_000).toFixed(2)}M pps`
  if (pps >= 1_000) return `${(pps / 1_000).toFixed(1)}k pps`
  return `${Math.round(pps)} pps`
}

export function formatMbps(mbps: number): string {
  if (mbps >= 1_000) return `${(mbps / 1_000).toFixed(2)} Gbps`
  return `${mbps.toFixed(1)} Mbps`
}

export function formatPercent(fraction: number, digits = 1): string {
  return `${(fraction * 100).toFixed(digits)}%`
}

export function formatClock(epoch: number): string {
  // The replay timeline is relative; an absolute UTC time is what an operator
  // would correlate against other tooling.
  return new Date(epoch * 1000).toISOString().slice(11, 19)
}

export function formatSeconds(seconds: number | null): string {
  if (seconds === null || Number.isNaN(seconds)) return '—'
  if (seconds < 60) return `${Math.round(seconds)}s`
  const minutes = Math.floor(seconds / 60)
  const rest = Math.round(seconds % 60)
  return rest ? `${minutes}m ${rest}s` : `${minutes}m`
}

export function formatRiskScore(score: number | null): string {
  return score === null ? '—' : score.toFixed(3)
}

/** Readable label for a feature, falling back to the raw name. */
export function featureLabel(feature: string, label?: string): string {
  if (label && label !== feature) return label
  return feature.replace(/_/g, ' ')
}
