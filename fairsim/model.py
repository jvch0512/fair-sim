"""Input model and strict validation. Everything the engine and server accept goes through here."""

import math
import re

MAX_SCENARIOS = 30
MAX_CONTROLS = 30
MIN_ITERATIONS = 1000
MAX_ITERATIONS = 200000
ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")


class ValidationError(ValueError):
    pass


def _num(v, field, lo=None, hi=None):
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise ValidationError("%s must be a number" % field)
    v = float(v)
    if not math.isfinite(v):
        raise ValidationError("%s must be finite" % field)
    if lo is not None and v < lo:
        raise ValidationError("%s must be >= %g" % (field, lo))
    if hi is not None and v > hi:
        raise ValidationError("%s must be <= %g" % (field, hi))
    return v


def _text(v, field, maxlen):
    if not isinstance(v, str):
        raise ValidationError("%s must be a string" % field)
    v = v.strip()
    if not v or len(v) > maxlen:
        raise ValidationError("%s must be 1 to %d characters" % (field, maxlen))
    return v


def _obj(v, field):
    if not isinstance(v, dict):
        raise ValidationError("%s must be an object" % field)
    return v


def parse_range(v, field, lo=0.0, hi=None):
    """A three-point estimate {min, mode, max}; a bare number means a fixed value."""
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        x = _num(v, field, lo, hi)
        return (x, x, x)
    if not isinstance(v, dict):
        raise ValidationError("%s must be a number or a {min, mode, max} object" % field)
    o = v
    mn = _num(o.get("min"), field + ".min", lo, hi)
    mode = _num(o.get("mode"), field + ".mode", lo, hi)
    mx = _num(o.get("max"), field + ".max", lo, hi)
    if not (mn <= mode <= mx):
        raise ValidationError("%s needs min <= mode <= max" % field)
    return (mn, mode, mx)


class Scenario:
    def __init__(self, id, name, tef, vulnerability, primary_loss, secondary_p, secondary_loss):
        self.id = id
        self.name = name
        self.tef = tef
        self.vulnerability = vulnerability
        self.primary_loss = primary_loss
        self.secondary_p = secondary_p
        self.secondary_loss = secondary_loss


class Control:
    def __init__(self, id, name, annual_cost, applies_to, frequency_reduction, vulnerability_reduction, magnitude_reduction):
        self.id = id
        self.name = name
        self.annual_cost = annual_cost
        self.applies_to = applies_to  # None means all scenarios
        self.frequency_reduction = frequency_reduction
        self.vulnerability_reduction = vulnerability_reduction
        self.magnitude_reduction = magnitude_reduction


class Model:
    def __init__(self):
        self.organisation = "Unnamed organisation"
        self.currency = "EUR"
        self.iterations = 10000
        self.seed = 42
        self.appetite = None
        self.budget = None
        self.scenarios = []
        self.controls = []


def parse(data, max_iterations=MAX_ITERATIONS):
    d = _obj(data, "input")
    m = Model()
    if "organisation" in d:
        m.organisation = _text(d["organisation"], "organisation", 100)
    if "currency" in d:
        m.currency = _text(d["currency"], "currency", 5)
    if "iterations" in d:
        it = _num(d["iterations"], "iterations", MIN_ITERATIONS, max_iterations)
        m.iterations = int(it)
    if "seed" in d:
        m.seed = int(_num(d["seed"], "seed", 0, 2 ** 31))
    if d.get("appetite") is not None:
        m.appetite = _num(d["appetite"], "appetite", 0)
    if d.get("budget") is not None:
        m.budget = _num(d["budget"], "budget", 0)

    sc = d.get("scenarios")
    if not isinstance(sc, list) or not sc:
        raise ValidationError("scenarios must be a non-empty list")
    if len(sc) > MAX_SCENARIOS:
        raise ValidationError("at most %d scenarios" % MAX_SCENARIOS)
    seen = set()
    for i, s in enumerate(sc):
        f = "scenarios[%d]" % i
        o = _obj(s, f)
        sid = _text(o.get("id"), f + ".id", 40)
        if not ID_RE.match(sid):
            raise ValidationError(f + ".id may only contain letters, digits, '-' and '_'")
        if sid in seen:
            raise ValidationError("duplicate scenario id: " + sid)
        seen.add(sid)
        sec = o.get("secondary") or {}
        _obj(sec, f + ".secondary")
        sp = _num(sec.get("probability", 0), f + ".secondary.probability", 0, 1)
        sl = parse_range(sec.get("loss", 0), f + ".secondary.loss")
        m.scenarios.append(Scenario(
            sid, _text(o.get("name", sid), f + ".name", 100),
            parse_range(o.get("tef"), f + ".tef"),
            parse_range(o.get("vulnerability"), f + ".vulnerability", 0.0, 1.0),
            parse_range(o.get("primary_loss"), f + ".primary_loss"),
            sp, sl))

    ct = d.get("controls", [])
    if not isinstance(ct, list):
        raise ValidationError("controls must be a list")
    if len(ct) > MAX_CONTROLS:
        raise ValidationError("at most %d controls" % MAX_CONTROLS)
    cseen = set()
    for i, c in enumerate(ct):
        f = "controls[%d]" % i
        o = _obj(c, f)
        cid = _text(o.get("id"), f + ".id", 40)
        if not ID_RE.match(cid):
            raise ValidationError(f + ".id may only contain letters, digits, '-' and '_'")
        if cid in cseen:
            raise ValidationError("duplicate control id: " + cid)
        cseen.add(cid)
        applies = o.get("applies_to")
        if applies is not None:
            if not isinstance(applies, list) or not all(isinstance(a, str) for a in applies):
                raise ValidationError(f + ".applies_to must be a list of scenario ids")
            for a in applies:
                if a not in seen:
                    raise ValidationError("%s.applies_to references unknown scenario %s" % (f, a))
        fr = _num(o.get("frequency_reduction", 0), f + ".frequency_reduction", 0, 1)
        vr = _num(o.get("vulnerability_reduction", 0), f + ".vulnerability_reduction", 0, 1)
        mr = _num(o.get("magnitude_reduction", 0), f + ".magnitude_reduction", 0, 1)
        m.controls.append(Control(cid, _text(o.get("name", cid), f + ".name", 100),
                                  _num(o.get("annual_cost", 0), f + ".annual_cost", 0), applies, fr, vr, mr))
    return m
