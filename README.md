# DFSI — DDoS Future-State Intelligence

> See the storm before it lands.
>
> **ML makes the numbers. AI explains them. Humans decide.**

A predictive lookout layer that sits *beside* `netsec-ddos-shared-state-2`. Shared
State ("the bell") today waits for blocking to persist for the full start
duration before it declares an attack. DFSI forecasts a new lifecycle state —
**`ABOUT_TO_START`** — so an operator gets a head start on the same event.

DFSI is **read-only and simulation-only.** It never changes a customer's
configuration, never blocks traffic and never executes a policy.

---

## The four components

| | What it is | Where |
|---|---|---|
| **Memory** | PySpark over months of historical traffic → features ("clues"). *Like a ship's logbook.* | `dfsi/memory/` |
| **Forecaster** | Gradient-boosted trees + MLflow; a risk score for the next 120s, every run and model version tracked. *Like a lookout with a spyglass.* | `dfsi/forecaster/` |
| **Rehearsal room** | Policy simulator: tries four candidate responses on a replay and shows the trade-offs. Nothing touches production. *Like a practice drill.* | `dfsi/rehearsal/` |
| **Explainers** | Two bounded AI agents plus a code-based validator that checks every claim. *Like the first mate briefing the captain.* | `dfsi/agents/` |

---

## Prerequisites

| | Version | Why |
|---|---|---|
| Python | **3.12** | What the project is pinned and measured against |
| JDK | **11, 17 or 21** | PySpark 3.5 breaks on JDK 23+ — `javax.security.auth.Subject.getSubject` was removed and Hadoop still calls it |
| Node | 20+ | Vite 5 / React 18 dashboard |

Only the Memory stage needs the JDK. Training, the API and the UI do not.

```bash
brew install openjdk@17          # if you have none
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
(cd ui && npm install)
```

`scripts/env.sh` resolves a usable JDK automatically and fails loudly if it
cannot find one, so you do not need to export `JAVA_HOME` yourself.

---

## Running it — command line

One command, from a bare checkout to a dashboard. It creates the venv, installs
both dependency sets, builds Memory, trains the Forecaster, starts the API,
waits for it to answer, then opens the UI. Every stage is skipped when its
output already exists, so a second run serves in seconds, and one Ctrl-C stops
both servers.

Generated output is not committed: `.venv/`, `data/`, `models/`, `mlruns/`,
`logs/`, `ui/node_modules/` and `ui/dist/` are in `.gitignore`. On a fresh
checkout the first `scripts/run.sh` builds them (the Memory layer takes about a
minute).

```bash
scripts/run.sh
```

```bash
scripts/run.sh --rebuild        # force Memory + training again
scripts/run.sh --test           # run the 474 tests before serving
scripts/run.sh --no-ui          # API only
scripts/run.sh --build-only     # build, don't serve
scripts/run.sh --days 28        # weeks of history (default 28: warm-up, fit, calibration, test)
```

`DFSI_PORT` and `DFSI_UI_PORT` move the two servers off 8000/5173; the UI's
`/api` proxy follows automatically.

### The stages on their own

Four stages, in order. The first two are build steps; the last two are the demo.

```bash
scripts/build_memory.sh          # Memory: PySpark → features + replays (~1 min)
scripts/train.sh                 # Forecaster: train, calibrate, register in MLflow
scripts/serve_api.sh             # API on http://127.0.0.1:8000  (docs at /docs)
cd ui && npm run dev             # Dashboard on http://127.0.0.1:5173
```

`scripts/demo.sh` is the older version of the same path; it assumes the venv and
dependencies are already in place, where `scripts/run.sh` builds them.

Useful variations:

```bash
scripts/build_memory.sh --days 14                # smaller: then train with `scripts/train.sh --split scenario`
scripts/build_memory.sh --env prod               # 300s start duration instead of 30s
scripts/train.sh --false-alarm-budget 0.05       # trade false alarms for lead time
.venv/bin/python -m pytest -q                    # 474 tests
.venv/bin/python -m mlflow ui --backend-store-uri mlruns   # browse tracked runs
```

### Approving a policy change, then replaying the attack

`raw.json` is the policy database: per account, range and traffic type, the
configured line (`max_threshold_pps` / `max_threshold_mbps`). The API loads it
into an in-memory, versioned policy store, and every replay is re-derived under
the store's current lines. That covers what the wall blocks, the policy-based
features the forecaster reads, and when Shared State's bell rings.

1. Replay an attack, run the drill, and pick **Tighten the vector threshold**.
   The suggestion comes from the past week of that range's traffic.
2. **Approve** it. The suggested lines are written to the store (a new version,
   with history), and the audit record says `applied: true`.
3. **Replay the same attack.** It now runs under the new lines. The *Policy
   store* panel compares the two runs: when DFSI warned, when the bell rang, how
   much was dropped at the wall, and the peak still reaching the range.

Only threshold changes are applied, and only to this simulated store. Nothing
reaches Behemoth, Shared State or a customer's configuration. Restarting the
API, or **Reset to raw.json** (`POST /api/policies/reset`), restores the seed.
`GET /api/policies` shows the store and its history. `DFSI_POLICY_DB` points
the store at a different file.

Each range can also set its own `risk_threshold`: the risk score at or above
which a window counts towards a DFSI warning on that range. A range without one
uses the model's calibrated threshold. It moves the dashed line on the timeline,
the risk bands, the warning, and so the head start. It is read when the API
starts, so edit `raw.json` and restart to change it. `GET /api/risk-thresholds`
shows what is in effect.

### The agents

Set `ANTHROPIC_API_KEY` before starting the API to run the two explainers on
Claude (`claude-opus-5`). Without it they fall back to an offline rule engine
and `/api/health` reports `agent_engine: "offline-rules"` — a supported mode,
and what the test suite exercises.

```bash
export ANTHROPIC_API_KEY=sk-ant-...
scripts/serve_api.sh
```

Either way the **validator runs in code, not in a prompt.** It checks tenant
scope, output format, evidence freshness, citation of every number, and the
allowed action list. Agents are forbidden from producing risk figures — ML owns
those. On any validation failure DFSI falls back to **`MONITORING_ONLY`**, never
to "low risk".

---

## Running it — IntelliJ IDEA

The project ships `.idea/` with six run configurations. Open
`~/netsec-ddos-shared-state-2/dfsi` as the project root.

**One-time setup:** point the interpreter at the venv.
*Settings → Project: dfsi → Python Interpreter → Add Interpreter → Existing
environment →* `<project>/.venv/bin/python`, and name it **`Python 3.12 (dfsi)`**
(the run configurations reference that name). IntelliJ usually detects `.venv`
on first open and offers it.

Then pick from the run dropdown:

| Configuration | What it does |
|---|---|
| **1 · Memory (PySpark)** | Builds the feature table. Pins `JAVA_HOME` to JDK 17 for you. |
| **2 · Forecaster (train)** | Trains, calibrates the threshold, logs to MLflow. |
| **3 · API (uvicorn 8000)** | Serves the trigger, with `--reload`. |
| **4 · UI (vite 5173)** | `npm run dev` in `ui/`. |
| **Demo (API + UI)** | Compound — starts 3 and 4 together. |
| **Tests (all)** | pytest over `tests/`. |

Run **1**, then **2**, then **Demo**. Configurations 1–3 all export
`DFSI_HOME=$PROJECT_DIR$`, so nothing is written outside this directory.

If the IDE flags `from dfsi...` as unresolved, mark the project root as a
source root (right-click → *Mark Directory as → Sources Root*); `.idea/dfsi.iml`
already declares it, but the setting only takes effect once the interpreter is
configured.

---

## The demo flow

1. **Replay synthetic traffic** — pick a scenario and press *Start replay*. One
   SSE event per 10-second window.
2. **The risk score climbs before the threshold alarm** — the timeline draws
   the gap between DFSI's first warning and the moment the bell would ring.
3. **Ask the agents** — *attack or flash crowd?* The investigator gathers
   evidence; the validator strip shows every check it passed.
4. **Compare four responses** in the rehearsal room — `hold`,
   `tighten_vector`, `rate_limit_sources`, `full_scrub` — with suppression,
   collateral, time-to-contain and blast radius for each.
5. **A human approves** one. The decision is appended to an audit log and
   **never applied.**

---

## Measured results

Synthetic scenario set, stage thresholds. Four weeks of traffic: the first is
warm-up for the baselines, the model is fitted on the next, the decision
threshold calibrated on the one after, and everything below is measured on the
final week, which the model never saw (672 scenarios, 95 forecastable attacks).
Decision threshold 0.55, evidence gate 3.0:

| | DFSI | Threshold alarm (today) |
|---|---|---|
| Median head start | **60s** | 0s |
| p10 head start | 10s | 0s |
| Attacks caught early | **98.9%** (94/95) | 0% |
| Late warnings | 0 | 95 |
| False-alarm rate | 2.7% | 0% |

One week and 95 attacks is a small sample: read these as estimates, not
guarantees. The head start varies a lot by scenario type, and is mostly *early
detection* rather than foresight for the fast ones: `gradual_buildup` is warned
about a median 260s ahead, but `sustained_attack` only 30s and `pulse_wave` 10s,
which is about the 30s start duration on this stage configuration.

### The evidence gate

The forecaster leans on context (recent attack history on the account) as well
as traffic, so an ordinary flash crowd on an account attacked last week could
alert with the traffic still inside its normal range. An alert therefore also
needs **traffic evidence**: the strongest per-vector deviation must exceed 3 (the
pipeline's own bar for an abnormal window). The risk score itself is untouched,
and a withheld window still shows it, flagged `gated` with the reason. Missing
data never suppresses an alert.

On the same model and threshold, with the gate off versus on:

| | no gate | gate |
|---|---|---|
| False-alarm rate | 5.9% | **2.7%** |
| Flash-crowd false alarms | 25% | **5.6%** |
| Attacks caught | 94/95 | 94/95 |
| Median / p10 head start | 60s / 10s | 60s / 10s |

The cost is small and worth knowing: `gradual_buildup`'s median head start moves
from 270s to 260s, because a slow ramp is not alerted until its deviation passes
the bar. The effect was measured on synthetic data only. A second-stage
classifier trained to tell attacks from flash crowds was tried first and removed
one point of flash-crowd false alarms, because the flash crowds that alert look
attack-like on traffic; see `dfsi/forecaster/gate.py`.

**How lead time is counted** — two rules, because getting either wrong moves the
headline by tens of seconds:

- *The clock starts at the bell, not at the threshold crossing.* Shared State
  crosses the policy-relative threshold, then waits out the start duration
  before declaring. Scoring a detector on the crossing credits it with that
  wait as though it were foresight — a free 20s here, 290s in production. The
  incumbent's warning **is** the declaration, so its lead is zero by
  construction. That is the comparison, not a bug.
- *A warning is credited when it can first be acted on.* It takes two windows in
  a row above the threshold, and the head start is counted from the second, not
  back to the first, because nobody can be told about a window before the next
  one has been seen.
- *Lead time is only defined where there was a runway.* `recovery` episodes open
  already over threshold, so there is no pre-attack history to forecast from.
  Those 20 scenarios are reported separately as `in_progress_detected` (100%
  detected) rather than averaged into the head-start figure.

See `dfsi/forecaster/metrics.py` and `tests/test_metrics.py` — both rules are
asserted, not assumed.

---

## Anti-leakage discipline

A forecaster that can see the future scores beautifully and is worthless.

- The split is by **whole scenario** (`GroupShuffleSplit` on `scenario_id`), so
  neighbouring 10-second windows can never straddle it.
- `scenario_id` and `scenario_type` are **never model inputs**.
- Every feature is built from a window frame ending at the current row. The
  label is the only forward-looking column, and it is dropped before fitting.
- Baselines come from quiet windows only, broadcast-joined, so a test scenario
  never contributes to the baseline it is measured against.
- `assert_no_leakage` fails the run loudly if a forbidden column reaches the
  feature list. `tests/test_leakage.py` holds 19 tests on this.

---

## Safety model

- **Read-only.** No write path to any customer configuration exists in this
  codebase.
- **Simulation only.** Every rehearsal result is tagged `simulation_only` and
  carries its stated assumptions.
- **Bounded agents.** Each gets a few read-only tools and a role-scoped tool
  allow-list; reaching outside it fails validation.
- **Fails safe.** Any failure in the prediction or explanation path returns
  `MONITORING_ONLY` — explicitly "unknown", never "low risk" or "all clear".
- **No raw IPs.** Source information is reduced to country-mix concentration
  and a per-account attack count. Nothing in `data/` contains an address.
- **Versioned and auditable.** Every forecast records its feature version and
  model version; every simulated decision is appended to `data/decisions/`.

All traffic in this project is **synthetic** (`dfsi/memory/scenarios.py`).

---

## Known limitations

Served live at `/api/model-card` and shown in the dashboard footer. In short:
synthetic data only; `recovery` scenarios cannot be forecast by construction; a
flash crowd that crosses the policy threshold is a true onset by DFSI's label
definition even with no attacker involved; the rehearsal room's suppression and
collateral figures are stated assumptions rather than measurements of
Behemoth's real behaviour; and an attack evenly spread across many countries
loses the `source_profile` signal.

Related: **PPNG-6760** — Behemoth can block a whole /24 and catch Teams/Zoom
traffic by mistake. That is why `full_scrub` always carries a blast-radius note
in the rehearsal room.

---

## Layout

```
dfsi/
  config.py            thresholds, paths, versions, state vocabulary
  memory/              scenarios.py · features.py · pipeline.py   (PySpark)
  forecaster/          train.py · metrics.py · serve.py           (sklearn + MLflow)
  rehearsal/           simulator.py — the four candidate policies
  agents/              orchestrator.py · tools.py · validator.py
  api/                 app.py · replay.py · schemas.py            (FastAPI + SSE)
ui/                    React + Vite + TypeScript dashboard
scripts/               run.sh (build + run, one command) · env.sh · build_memory.sh
                       train.sh · serve_api.sh · demo.sh
tests/                 474 tests: simulator · validator · metrics · leakage · scenarios
data/ models/ mlruns/  generated — safe to delete and rebuild
```
