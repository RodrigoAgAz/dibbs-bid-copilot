"""Continuous distribution of the winning unit price W built from predicted quantiles.

Quantiles are interpolated monotonically (PCHIP) in (z, log price) space, where z = Phi^-1(p).
That makes the distribution exactly lognormal when the quantiles are lognormal, gives smooth
monotone CDFs in between, and extends the tails linearly in z (lognormal-shaped tails).

    P(win | bid b) ~= P(W > b) = 1 - F_W(b) = dist.sf(b)
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
from scipy.interpolate import PchipInterpolator
from scipy.stats import norm

QUANTILE_LEVELS: tuple[float, ...] = (0.05, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95)
_TAIL_P = (0.0005, 0.9995)


class PriceDistribution:
    def __init__(self, levels: Sequence[float], prices: Sequence[float]) -> None:
        lv = np.asarray(levels, dtype=float)
        pr = np.asarray(prices, dtype=float)
        if lv.shape != pr.shape or lv.size < 2:
            raise ValueError("need >= 2 matching levels and prices")
        order = np.argsort(lv)
        lv = lv[order]
        # Enforce monotone quantiles (sorting fixes crossing quantile models).
        pr = np.sort(np.clip(pr[order], 1e-6, None))
        x = np.log(pr)
        # Strictly increasing x so the inverse interpolator is well defined.
        for i in range(1, x.size):
            if x[i] <= x[i - 1]:
                x[i] = x[i - 1] + 1e-6
        z = norm.ppf(lv)
        # Linear tail extension in z-space using the outer segments' slopes.
        z_lo, z_hi = norm.ppf(_TAIL_P[0]), norm.ppf(_TAIL_P[1])
        slope_lo = (x[1] - x[0]) / (z[1] - z[0])
        slope_hi = (x[-1] - x[-2]) / (z[-1] - z[-2])
        x_full = np.concatenate([[x[0] + slope_lo * (z_lo - z[0])], x, [x[-1] + slope_hi * (z_hi - z[-1])]])
        z_full = np.concatenate([[z_lo], z, [z_hi]])
        self.levels = lv
        self.prices = np.exp(x)
        self._z = z_full
        self._x = x_full
        self._x_of_z = PchipInterpolator(z_full, x_full, extrapolate=True)
        self._z_of_x = PchipInterpolator(x_full, z_full, extrapolate=True)

    # ------------------------------------------------------------------ API
    def cdf(self, b):
        b = np.asarray(b, dtype=float)
        x = np.log(np.clip(b, 1e-9, None))
        z = self._z_of_x(np.clip(x, self._x[0], self._x[-1]))
        # Beyond the tail anchors continue linearly in z.
        lo, hi = x < self._x[0], x > self._x[-1]
        if np.any(lo) or np.any(hi):
            s_lo = (self._z[1] - self._z[0]) / (self._x[1] - self._x[0])
            s_hi = (self._z[-1] - self._z[-2]) / (self._x[-1] - self._x[-2])
            z = np.where(lo, self._z[0] + s_lo * (x - self._x[0]), z)
            z = np.where(hi, self._z[-1] + s_hi * (x - self._x[-1]), z)
        out = norm.cdf(z)
        return float(out) if out.ndim == 0 else out

    def sf(self, b):
        """P(W > b): the approximate probability of winning with bid b."""
        c = self.cdf(b)
        return 1.0 - c

    def ppf(self, p):
        p = np.clip(np.asarray(p, dtype=float), 1e-6, 1 - 1e-6)
        out = np.exp(self._x_of_z(norm.ppf(p)))
        return float(out) if out.ndim == 0 else out

    @property
    def median(self) -> float:
        return float(self.ppf(0.5))

    def widen(self, factor: float) -> PriceDistribution:
        """Scale log-deviations from the median (used for cold-start / low-data items)."""
        m = np.log(self.median)
        return PriceDistribution(self.levels, np.exp(m + factor * (np.log(self.prices) - m)))

    def to_dict(self) -> dict:
        return {"levels": [float(v) for v in self.levels], "prices": [round(float(v), 6) for v in self.prices]}

    @classmethod
    def from_dict(cls, d: dict) -> PriceDistribution:
        return cls(d["levels"], d["prices"])

    @classmethod
    def lognormal(cls, mu: float, sigma: float, levels: Sequence[float] = QUANTILE_LEVELS) -> PriceDistribution:
        lv = np.asarray(levels, dtype=float)
        return cls(lv, np.exp(mu + sigma * norm.ppf(lv)))
