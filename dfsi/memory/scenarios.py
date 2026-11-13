"""Memory, part 1 — the ship's logbook.

Generates synthetic, ANL-shaped 10-second traffic windows across the five
scenario families DFSI is evaluated on:

    normal · flash_crowd · gradual_buildup · sustained_attack · recovery

Everything here is synthetic. No customer traffic, no raw IP addresses: source
information is reduced to a country-concentration index and a per-account
attack-history count, both computed chronologically so they can never see the
future.

Each *episode* is one contiguous hour for one protected range, holding rows for
every traffic type in that hour. Episodes are laid out across a full week so
that hour-of-week baselines are meaningful. The episode id doubles as the
scenario id used for train/test splitting — it is never given to the model.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterator, Sequence

import numpy as np

from ..config import SCENARIO_SET_VERSION, WINDOW_SECONDS

TRAFFIC_TYPES: tuple[str, ...] = ("udp", "tcp_syn", "http")
SCENARIO_TYPES: tuple[str, ...] = (
    "normal",
    "flash_crowd",
    "gradual_buildup",
    "sustained_attack",
    "recovery",
)
# Scenario families in which blocking above the start threshold actually occurs.
ATTACK_SCENARIOS: frozenset[str] = frozenset(
    {"gradual_buildup", "sustained_attack", "recovery"}
)

# Mean packet size per traffic type, in bytes. UDP reflection carries large
# packets; SYN floods are tiny; HTTP sits in between.
_MEAN_PACKET_BYTES = {"udp": 1100, "tcp_syn": 64, "http": 700}

# Policy headroom: the configured policy threshold is this multiple of the
# range's normal baseline rate, so ordinary peaks never reach it.
_POLICY_HEADROOM = 5.0

WINDOWS_PER_EPISODE = 360  # one hour of 10s windows
EPOCH_BASE = 1_767_225_600  # 2026-01-01T00:00:00Z, a Thursday


@dataclass(frozen=True)
class RangeProfile:
    """A protected IP range and its steady-state traffic characteristics."""

    range_id: int
    account_id: int
    range_type: str
    base_pps: dict[str, float]

    def policy_pps(self, traffic_type: str) -> float:
        return self.base_pps[traffic_type] * _POLICY_HEADROOM

    def policy_mbps(self, traffic_type: str) -> float:
        return _pps_to_mbps(self.policy_pps(traffic_type), traffic_type)


def _pps_to_mbps(pps: float | np.ndarray, traffic_type: str) -> float | np.ndarray:
    return pps * _MEAN_PACKET_BYTES[traffic_type] * 8.0 / 1_000_000.0


DEFAULT_RANGES: tuple[RangeProfile, ...] = (
    RangeProfile(
        range_id=90_110_001,
        account_id=421_001,
        range_type="INFRA_PROTECT_TOPOLOGY_TYPE_RANGE",
        base_pps={"udp": 14_000.0, "tcp_syn": 22_000.0, "http": 48_000.0},
    ),
    RangeProfile(
        range_id=90_110_002,
        account_id=421_001,
        range_type="INFRA_PROTECT_TOPOLOGY_TYPE_RANGE",
        base_pps={"udp": 9_000.0, "tcp_syn": 11_000.0, "http": 26_000.0},
    ),
    RangeProfile(
        range_id=90_110_003,
        account_id=558_777,
        range_type="INFRA_PROTECT_TOPOLOGY_TYPE_SUBNET",
        base_pps={"udp": 31_000.0, "tcp_syn": 18_000.0, "http": 64_000.0},
    ),
    RangeProfile(
        range_id=90_110_004,
        account_id=612_430,
        range_type="INFRA_PROTECT_TOPOLOGY_TYPE_RANGE",
        base_pps={"udp": 6_500.0, "tcp_syn": 7_200.0, "http": 15_000.0},
    ),
)

# How often each scenario family is drawn when building a week of history.
# Heavily skewed towards normal, which is what production looks like.
_SCENARIO_WEIGHTS = {
    "normal": 0.70,
    "flash_crowd": 0.09,
    "gradual_buildup": 0.07,
    "sustained_attack": 0.08,
    "recovery": 0.06,
}


@dataclass
class EpisodePlan:
    """Chronological placement of one scenario episode."""

    scenario_id: str
    scenario_type: str
    range_profile: RangeProfile
    start_epoch: int
    attack_vector: str
    seed: int
    # Past-only: how many attack episodes this account had before this episode.
    account_attack_history: int = 0
    n_windows: int = WINDOWS_PER_EPISODE

    @property
    def end_epoch(self) -> int:
        return self.start_epoch + self.n_windows * WINDOW_SECONDS


def _diurnal(epoch: np.ndarray) -> np.ndarray:
    """Daily + weekly demand shape, as a multiplier around 1.0."""
    seconds_of_day = epoch % 86_400
    day_of_week = ((epoch // 86_400) + 3) % 7  # EPOCH_BASE is a Thursday
    daily = 1.0 + 0.45 * np.sin(2 * np.pi * (seconds_of_day - 21_600) / 86_400)
    weekend = np.where(day_of_week >= 5, 0.72, 1.0)
    return daily * weekend


def _legit_multiplier(
    scenario_type: str, n: int, rng: np.random.Generator
) -> np.ndarray:
    """Multiplier applied to *legitimate* traffic on every traffic type."""
    noise = 1.0 + rng.normal(0.0, 0.035, size=n)
    if scenario_type != "flash_crowd":
        return noise

    # A product launch / news event: a smooth bump that is large but stays
    # under the policy threshold. This is the false-alarm trap for DFSI.
    peak = rng.uniform(3.2, 4.4)
    centre = rng.integers(140, 240)
    width = rng.integers(45, 90)
    idx = np.arange(n)
    bump = (peak - 1.0) * np.exp(-0.5 * ((idx - centre) / width) ** 2)
    return (1.0 + bump) * noise


def _attack_pps(
    scenario_type: str, base_pps: float, n: int, rng: np.random.Generator
) -> np.ndarray:
    """Attack packets-per-second added to the attack vector, per window."""
    idx = np.arange(n, dtype=float)
    attack = np.zeros(n)

    if scenario_type in ("normal", "flash_crowd"):
        return attack

    peak = base_pps * rng.uniform(14.0, 28.0)

    if scenario_type == "gradual_buildup":
        # Slow probing ramp: the case a fixed threshold notices far too late.
        onset = int(rng.integers(70, 110))
        ramp_end = onset + int(rng.integers(110, 170))
        ramp = np.clip((idx - onset) / max(1, ramp_end - onset), 0.0, 1.0)
        attack = peak * ramp**1.6
    elif scenario_type == "sustained_attack":
        onset = int(rng.integers(80, 130))
        rise = int(rng.integers(4, 12))
        ramp = np.clip((idx - onset) / rise, 0.0, 1.0)
        attack = peak * ramp
    elif scenario_type == "recovery":
        # Episode opens mid-attack and decays to nothing.
        decay_end = int(rng.integers(150, 230))
        decay = np.clip(1.0 - idx / decay_end, 0.0, 1.0)
        attack = peak * decay**1.3

    # Attacks are not smooth: pulse the carrier.
    pulse = 1.0 + 0.12 * np.sin(2 * np.pi * idx / rng.uniform(5, 14))
    jitter = 1.0 + rng.normal(0.0, 0.05, size=n)
    return np.clip(attack * pulse * jitter, 0.0, None)


def _country_hhi(
    scenario_type: str, attack_share: np.ndarray, rng: np.random.Generator
) -> np.ndarray:
    """Herfindahl concentration of the source-country mix, in [0, 1].

    Derived from country counts only — no addresses are generated or stored.
    Normal traffic is diverse; flash crowds skew regional; floods are either
    highly concentrated in a few reflector networks or extremely spoofed.
    """
    n = attack_share.shape[0]
    quiet = 0.14 + rng.normal(0.0, 0.012, size=n)
    if scenario_type == "flash_crowd":
        target = 0.38
    elif scenario_type == "normal":
        target = 0.14
    else:
        target = 0.68
    blended = quiet + (target - 0.14) * np.clip(attack_share * 2.2, 0.0, 1.0)
    return np.clip(blended, 0.02, 0.98)


def generate_episode(plan: EpisodePlan) -> list[dict]:
    """Materialise one episode as a list of ANL-shaped window rows."""
    rng = np.random.default_rng(plan.seed)
    n = plan.n_windows
    epoch = plan.start_epoch + np.arange(n) * WINDOW_SECONDS
    demand = _diurnal(epoch)
    legit_mult = _legit_multiplier(plan.scenario_type, n, rng)

    rows: list[dict] = []
    for traffic_type in TRAFFIC_TYPES:
        base = plan.range_profile.base_pps[traffic_type]
        legit_pps = base * demand * legit_mult
        attack = (
            _attack_pps(plan.scenario_type, base, n, rng)
            if traffic_type == plan.attack_vector
            else np.zeros(n)
        )
        total_pps = np.clip(legit_pps + attack, 1.0, None)

        policy_pps = plan.range_profile.policy_pps(traffic_type)
        policy_mbps = plan.range_profile.policy_mbps(traffic_type)
        # Behemoth ("the wall") drops whatever exceeds the configured policy.
        blocked_pps = np.clip(total_pps - policy_pps, 0.0, None)

        total_mbps = _pps_to_mbps(total_pps, traffic_type)
        blocked_mbps = _pps_to_mbps(blocked_pps, traffic_type)
        attack_share = attack / total_pps
        hhi = _country_hhi(plan.scenario_type, attack_share, rng)

        for i in range(n):
            rows.append(
                {
                    "scenario_id": plan.scenario_id,
                    "scenario_type": plan.scenario_type,
                    "scenario_set_version": SCENARIO_SET_VERSION,
                    "window_start_epoch": int(epoch[i]),
                    "range_id": plan.range_profile.range_id,
                    "account_id": plan.range_profile.account_id,
                    "range_type": plan.range_profile.range_type,
                    "traffic_type": traffic_type,
                    "total_pps": float(total_pps[i]),
                    "total_mbps": float(total_mbps[i]),
                    "blocked_pps": float(blocked_pps[i]),
                    "blocked_mbps": float(blocked_mbps[i]),
                    "policy_pps": float(policy_pps),
                    "policy_mbps": float(policy_mbps),
                    "country_hhi": float(hhi[i]),
                    "account_attack_history": int(plan.account_attack_history),
                    "is_attack_vector": traffic_type == plan.attack_vector,
                }
            )
    return rows


def plan_week(
    ranges: Sequence[RangeProfile] = DEFAULT_RANGES,
    days: int = 7,
    seed: int = 20260101,
    weights: dict[str, float] | None = None,
) -> list[EpisodePlan]:
    """Lay out `days` of hourly episodes per range, in chronological order.

    `account_attack_history` is filled in as the plan is walked forward, so it
    only ever counts episodes that already happened.
    """
    rng = np.random.default_rng(seed)
    weights = weights or _SCENARIO_WEIGHTS
    types = list(weights)
    probs = np.array([weights[t] for t in types], dtype=float)
    probs = probs / probs.sum()

    plans: list[EpisodePlan] = []
    history: dict[int, int] = {r.account_id: 0 for r in ranges}

    for day in range(days):
        for hour in range(24):
            start = EPOCH_BASE + (day * 24 + hour) * 3_600
            for profile in ranges:
                scenario_type = str(rng.choice(types, p=probs))
                vector = str(rng.choice(TRAFFIC_TYPES))
                scenario_id = f"{profile.range_id}-d{day:02d}h{hour:02d}"
                plans.append(
                    EpisodePlan(
                        scenario_id=scenario_id,
                        scenario_type=scenario_type,
                        range_profile=profile,
                        start_epoch=start,
                        attack_vector=vector,
                        seed=int(rng.integers(0, 2**31 - 1)),
                        account_attack_history=history[profile.account_id],
                    )
                )
                if scenario_type in ATTACK_SCENARIOS:
                    history[profile.account_id] += 1
    return plans


def iter_rows(plans: Sequence[EpisodePlan]) -> Iterator[dict]:
    for plan in plans:
        yield from generate_episode(plan)
