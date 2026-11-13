"""Command line interface: run, validate, serve."""

import argparse
import json
import sys

from . import engine, model, report, server


def _load(path, max_iterations=model.MAX_ITERATIONS):
    with open(path, "r", encoding="utf-8") as f:
        return model.parse(json.load(f), max_iterations)


def main(argv=None, out=None):
    out = out or sys.stdout
    ap = argparse.ArgumentParser(prog="fair-sim", description="FAIR-style cyber risk quantification with Monte Carlo")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="simulate and print a report")
    r.add_argument("input")
    r.add_argument("--format", choices=["md", "html", "json"], default="md")
    r.add_argument("--iterations", type=int)
    r.add_argument("--seed", type=int)
    r.add_argument("--max-prob-over-appetite", type=float, help="exit 2 if the chance of exceeding appetite is above this (0..1)")
    v = sub.add_parser("validate", help="check an input file")
    v.add_argument("input")
    s = sub.add_parser("serve", help="local web UI on 127.0.0.1")
    s.add_argument("input", nargs="?")
    s.add_argument("--port", type=int, default=8600)
    a = ap.parse_args(argv)
    try:
        if a.cmd == "validate":
            m = _load(a.input)
            out.write("ok: %d scenarios, %d controls\n" % (len(m.scenarios), len(m.controls)))
            return 0
        if a.cmd == "run":
            m = _load(a.input)
            if a.iterations:
                if not model.MIN_ITERATIONS <= a.iterations <= model.MAX_ITERATIONS:
                    raise model.ValidationError("iterations must be %d to %d" % (model.MIN_ITERATIONS, model.MAX_ITERATIONS))
                m.iterations = a.iterations
            if a.seed is not None:
                m.seed = a.seed
            res = engine.analyse(m)
            if a.format == "json":
                out.write(json.dumps(res, indent=2) + "\n")
            elif a.format == "html":
                out.write(report.html(res))
            else:
                out.write(report.markdown(res))
            lim = a.max_prob_over_appetite
            if lim is not None and res["appetite"] is not None and res["portfolio"]["prob_over_appetite"] > lim:
                sys.stderr.write("chance of exceeding appetite %.1f%% is above %.1f%%\n" % (res["portfolio"]["prob_over_appetite"] * 100, lim * 100))
                return 2
            return 0
        if a.cmd == "serve":
            srv = server.create(a.port, a.input)
            out.write("fair-sim listening on http://127.0.0.1:%d\n" % srv.server_address[1])
            out.flush()
            try:
                srv.serve_forever()
            except KeyboardInterrupt:
                pass
            return 0
    except (OSError, ValueError) as e:
        sys.stderr.write("error: %s\n" % e)
        return 1
    return 1


if __name__ == "__main__":
    sys.exit(main())
