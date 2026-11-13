"""Monte Carlo engine with common random numbers.

Each scenario and iteration draws its random numbers once (threat event frequency, vulnerability,
a uniform for the event count, and the loss of each potential event). Control what-if runs reuse the
same draws and only change the parameters, so the difference between two runs is the effect of the
controls and not simulation noise.
"""

import math
import random
from statistics import NormalDist

MAX_EVENTS = 50
LEC_POINTS = 60


def pert(rng, mn, mode, mx, lam=4.0):
    """Sample a modified PERT distribution."""
    if mx <= mn:
        return mn
    a = 1.0 + lam * (mode - mn) / (mx - mn)
    b = 1.0 + lam * (mx - mode) / (mx - mn)
    return mn + rng.betavariate(a, b) * (mx - mn)


def poisson_inv(lam, u):
    """Inverse-CDF Poisson sample: monotone in lam for a fixed u (needed for common random numbers)."""
    if lam <= 0:
        return 0
    if lam > 40:
        return min(MAX_EVENTS, max(0, int(round(NormalDist(lam, math.sqrt(lam)).inv_cdf(min(max(u, 1e-12), 1 - 1e-12))))))
    p = math.exp(-lam)
    c = p
    k = 0
    while u > c and k < MAX_EVENTS:
        k += 1
        p *= lam / k
        c += p
    return k


class Draws:
    """Pre-drawn random numbers for one scenario over all iterations."""

    def __init__(self, sc, iterations, seed, index):
        self.tef = []
        self.vuln = []
        self.u = []
        self.events = []  # per iteration: list of event losses (base magnitude)
        for i in range(iterations):
            rng = random.Random((seed * 1000003 + index) * 1000003 + i)
            tef = pert(rng, *sc.tef)
            vuln = pert(rng, *sc.vulnerability)
            u = rng.random()
            self.tef.append(tef)
            self.vuln.append(vuln)
            self.u.append(u)
            n = poisson_inv(tef * vuln, u)  # baseline count; controls only ever reduce it
            ev = []
            for _ in range(n):
                loss = pert(rng, *sc.primary_loss)
                if sc.secondary_p > 0 and rng.random() < sc.secondary_p:
                    loss += pert(rng, *sc.secondary_loss)
                ev.append(loss)
            self.events.append(ev)


def multipliers(model, applied_ids):
    """Per-scenario (frequency, vulnerability, magnitude) multipliers for a set of control ids."""
    mult = {s.id: [1.0, 1.0, 1.0] for s in model.scenarios}
    chosen = set(applied_ids)
    for c in model.controls:
        if c.id not in chosen:
            continue
        targets = c.applies_to if c.applies_to is not None else [s.id for s in model.scenarios]
        for sid in targets:
            m = mult[sid]
            m[0] *= 1.0 - c.frequency_reduction
            m[1] *= 1.0 - c.vulnerability_reduction
            m[2] *= 1.0 - c.magnitude_reduction
    return mult


class Simulator:
    def __init__(self, model):
        self.model = model
        self.draws = [Draws(s, model.iterations, model.seed, k) for k, s in enumerate(model.scenarios)]

    def run(self, applied_ids=()):
        """Annual loss per iteration for each scenario, with the given controls applied."""
        mult = multipliers(self.model, applied_ids)
        out = {}
        for s, d in zip(self.model.scenarios, self.draws):
            mf, mv, mm = mult[s.id]
            losses = []
            for i in range(self.model.iterations):
                lam = d.tef[i] * mf * min(1.0, d.vuln[i] * mv)
                n = poisson_inv(lam, d.u[i])
                ev = d.events[i]
                n = min(n, len(ev))
                losses.append(sum(ev[:n]) * mm if n else 0.0)
            out[s.id] = losses
        return out


def portfolio(per_scenario):
    cols = list(per_scenario.values())
    return [sum(vals) for vals in zip(*cols)]


def percentile(sorted_vals, q):
    if not sorted_vals:
        return 0.0
    k = (len(sorted_vals) - 1) * q
    lo = int(math.floor(k))
    hi = int(math.ceil(k))
    if lo == hi:
        return sorted_vals[lo]
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (k - lo)


def summarize(losses, appetite=None):
    s = sorted(losses)
    n = len(s)
    out = {
        "mean": sum(s) / n,
        "median": percentile(s, 0.5),
        "p90": percentile(s, 0.90),
        "p95": percentile(s, 0.95),
        "p99": percentile(s, 0.99),
        "max": s[-1],
        "prob_any_loss": sum(1 for v in s if v > 0) / n,
    }
    if appetite is not None:
        out["prob_over_appetite"] = sum(1 for v in s if v > appetite) / n
    return out


def exceedance_curve(losses, points=LEC_POINTS):
    """Loss exceedance curve: [(loss, probability that annual loss exceeds it)]."""
    s = sorted(losses)
    n = len(s)
    hi = percentile(s, 0.995)
    if hi <= 0:
        return [(0.0, 0.0)]
    out = []
    import bisect
    for k in range(points + 1):
        x = hi * k / points
        out.append((x, (n - bisect.bisect_right(s, x)) / n))
    return out


def analyse(model, with_controls=True):
    sim = Simulator(model)
    base = sim.run()
    port = portfolio(base)
    result = {
        "organisation": model.organisation,
        "currency": model.currency,
        "iterations": model.iterations,
        "seed": model.seed,
        "appetite": model.appetite,
        "budget": model.budget,
        "portfolio": summarize(port, model.appetite),
        "lec": exceedance_curve(port),
        "scenarios": [],
        "controls": [],
        "bundle": None,
    }
    for s in model.scenarios:
        row = summarize(base[s.id], model.appetite)
        row["id"] = s.id
        row["name"] = s.name
        result["scenarios"].append(row)
    if with_controls and model.controls:
        base_mean = result["portfolio"]["mean"]
        for c in model.controls:
            loss = portfolio(sim.run([c.id]))
            mean = sum(loss) / len(loss)
            reduction = base_mean - mean
            result["controls"].append({
                "id": c.id, "name": c.name, "annual_cost": c.annual_cost,
                "ale_after": mean, "ale_reduction": reduction,
                "rosi": (reduction - c.annual_cost) / c.annual_cost if c.annual_cost > 0 else None,
                "net_benefit": reduction - c.annual_cost,
            })
        result["controls"].sort(key=lambda r: r["net_benefit"], reverse=True)
        result["bundle"] = recommend(model, sim, base_mean)
    return result


def recommend(model, sim, base_mean):
    """Greedy bundle: repeatedly add the control with the best marginal net benefit while the budget allows."""
    budget = model.budget if model.budget is not None else float("inf")
    chosen = []
    spent = 0.0
    cur_mean = base_mean
    remaining = list(model.controls)
    steps = []
    while remaining:
        best = None
        for c in remaining:
            if spent + c.annual_cost > budget:
                continue
            loss = portfolio(sim.run(chosen + [c.id]))
            mean = sum(loss) / len(loss)
            gain = cur_mean - mean - c.annual_cost
            if gain > 0 and (best is None or gain > best[0]):
                best = (gain, c, mean, loss)
        if best is None:
            break
        gain, c, mean, loss = best
        chosen.append(c.id)
        remaining.remove(c)
        spent += c.annual_cost
        steps.append({"id": c.id, "name": c.name, "marginal_net_benefit": gain, "ale_after": mean})
        cur_mean = mean
    final = portfolio(sim.run(chosen))
    return {
        "controls": steps,
        "spent": spent,
        "ale_before": base_mean,
        "ale_after": sum(final) / len(final),
        "portfolio_after": summarize(final, model.appetite),
        "lec_after": exceedance_curve(final),
    }
