import copy
import http.client
import io
import json
import os
import random
import threading
import unittest

from fairsim import cli, engine, model, report, server

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAMPLE = os.path.join(ROOT, "samples", "costa-pay.json")


def sample():
    with open(SAMPLE, "r", encoding="utf-8") as f:
        return json.load(f)


def simple(tef=1.0, vuln=1.0, loss=100.0, iterations=20000, **extra):
    d = {"iterations": iterations, "seed": 7,
         "scenarios": [{"id": "s", "name": "S", "tef": tef, "vulnerability": vuln, "primary_loss": loss}]}
    d.update(extra)
    return d


class PertAndPoisson(unittest.TestCase):
    def test_pert_stays_in_bounds_and_has_the_right_mean(self):
        rng = random.Random(1)
        xs = [engine.pert(rng, 10, 20, 70) for _ in range(30000)]
        self.assertGreaterEqual(min(xs), 10)
        self.assertLessEqual(max(xs), 70)
        self.assertAlmostEqual(sum(xs) / len(xs), (10 + 4 * 20 + 70) / 6, delta=0.4)

    def test_degenerate_pert_is_constant(self):
        self.assertEqual(engine.pert(random.Random(1), 5, 5, 5), 5)

    def test_poisson_inverse_is_monotone_in_lambda_for_fixed_u(self):
        for u in (0.05, 0.5, 0.95, 0.999):
            prev = 0
            for lam in [0.1 * k for k in range(1, 600)]:
                n = engine.poisson_inv(lam, u)
                self.assertGreaterEqual(n, prev, "lam=%g u=%g" % (lam, u))
                prev = n

    def test_poisson_mean_matches_lambda(self):
        for lam in (0.3, 2.5, 8.0):
            us = [(k + 0.5) / 20000 for k in range(20000)]
            mean = sum(engine.poisson_inv(lam, u) for u in us) / len(us)
            self.assertAlmostEqual(mean, lam, delta=0.02 * max(1, lam))

    def test_zero_rate_never_produces_events(self):
        self.assertEqual(engine.poisson_inv(0, 0.99), 0)


class Validation(unittest.TestCase):
    def bad(self, mutate, fragment):
        d = sample()
        mutate(d)
        with self.assertRaises(model.ValidationError) as cm:
            model.parse(d)
        self.assertIn(fragment, str(cm.exception))

    def test_sample_is_valid(self):
        m = model.parse(sample())
        self.assertEqual(len(m.scenarios), 5)
        self.assertEqual(len(m.controls), 7)

    def test_rejections(self):
        self.bad(lambda d: d.update(scenarios=[]), "non-empty")
        self.bad(lambda d: d["scenarios"][0]["tef"].update(min=5, mode=1), "min <= mode <= max")
        self.bad(lambda d: d["scenarios"][0]["vulnerability"].update(max=1.5), "<= 1")
        self.bad(lambda d: d["scenarios"][0]["primary_loss"].update(min=-1), ">= 0")
        self.bad(lambda d: d["scenarios"][0].update(id="a b"), "letters")
        self.bad(lambda d: d["scenarios"][1].update(id="ransomware"), "duplicate")
        self.bad(lambda d: d["controls"][0].update(applies_to=["nope"]), "unknown scenario")
        self.bad(lambda d: d["controls"][0].update(vulnerability_reduction=1.2), "<= 1")
        self.bad(lambda d: d.update(iterations=10), ">=")
        self.bad(lambda d: d.update(iterations=10 ** 7), "<=")
        self.bad(lambda d: d["scenarios"][0].update(tef=float("nan")), "finite")
        self.bad(lambda d: d["scenarios"][0].update(tef="many"), "number")
        self.bad(lambda d: d["scenarios"][0].update(tef=True), "number")
        self.bad(lambda d: d.update(organisation="x" * 101), "characters")
        self.bad(lambda d: d.update(scenarios=d["scenarios"] * 7), "at most")

    def test_fixed_values_are_accepted(self):
        m = model.parse(simple())
        self.assertEqual(m.scenarios[0].tef, (1.0, 1.0, 1.0))

    def test_api_iteration_cap(self):
        d = sample()
        d["iterations"] = 100000
        with self.assertRaises(model.ValidationError):
            model.parse(d, max_iterations=50000)


class Engine(unittest.TestCase):
    def test_matches_the_analytic_expectation(self):
        # One Poisson(1) event stream with a fixed loss of 100: expected annual loss is 100.
        r = engine.analyse(model.parse(simple(tef=1.0, vuln=1.0, loss=100.0)))
        self.assertAlmostEqual(r["portfolio"]["mean"], 100.0, delta=4.0)
        # P(no event) = e^-1
        self.assertAlmostEqual(1 - r["portfolio"]["prob_any_loss"], 0.3679, delta=0.012)

    def test_vulnerability_scales_expected_loss(self):
        full = engine.analyse(model.parse(simple(tef=2.0, vuln=1.0)))["portfolio"]["mean"]
        half = engine.analyse(model.parse(simple(tef=2.0, vuln=0.5)))["portfolio"]["mean"]
        self.assertAlmostEqual(half / full, 0.5, delta=0.05)

    def test_secondary_loss_adds_to_primary(self):
        d = simple(tef=1.0, vuln=1.0, loss=100.0)
        d["scenarios"][0]["secondary"] = {"probability": 1.0, "loss": 50.0}
        r = engine.analyse(model.parse(d))
        self.assertAlmostEqual(r["portfolio"]["mean"], 150.0, delta=6.0)

    def test_same_seed_same_result_different_seed_differs(self):
        a = engine.analyse(model.parse(sample()), with_controls=False)
        b = engine.analyse(model.parse(sample()), with_controls=False)
        self.assertEqual(a["portfolio"], b["portfolio"])
        d = sample()
        d["seed"] = 43
        c = engine.analyse(model.parse(d), with_controls=False)
        self.assertNotEqual(a["portfolio"]["mean"], c["portfolio"]["mean"])

    def test_controls_never_increase_loss_in_any_iteration(self):
        m = model.parse(sample())
        sim = engine.Simulator(m)
        base = sim.run()
        for c in m.controls:
            after = sim.run([c.id])
            for sid in base:
                for x, y in zip(base[sid], after[sid]):
                    self.assertLessEqual(y, x + 1e-9, "%s on %s" % (c.id, sid))

    def test_zero_effect_control_changes_nothing(self):
        d = simple()
        d["controls"] = [{"id": "noop", "name": "No-op", "annual_cost": 10}]
        r = engine.analyse(model.parse(d))
        self.assertAlmostEqual(r["controls"][0]["ale_reduction"], 0.0, places=9)

    def test_control_only_affects_listed_scenarios(self):
        d = sample()
        m = model.parse(d)
        sim = engine.Simulator(m)
        base = sim.run()
        after = sim.run(["payment_controls"])  # applies to bec only
        for sid in base:
            if sid != "bec":
                self.assertEqual(base[sid], after[sid], sid)
        self.assertLess(sum(after["bec"]), sum(base["bec"]))

    def test_full_magnitude_reduction_removes_the_loss(self):
        d = simple()
        d["controls"] = [{"id": "wall", "name": "Wall", "annual_cost": 1, "magnitude_reduction": 1.0}]
        r = engine.analyse(model.parse(d))
        self.assertAlmostEqual(r["controls"][0]["ale_after"], 0.0, places=9)

    def test_exceedance_curve_is_non_increasing_and_starts_at_loss_probability(self):
        r = engine.analyse(model.parse(sample()), with_controls=False)
        ps = [p for _, p in r["lec"]]
        self.assertTrue(all(a >= b for a, b in zip(ps, ps[1:])))
        self.assertAlmostEqual(ps[0], r["portfolio"]["prob_any_loss"], places=9)

    def test_percentiles_are_ordered(self):
        p = engine.analyse(model.parse(sample()), with_controls=False)["portfolio"]
        self.assertLessEqual(p["median"], p["p90"])
        self.assertLessEqual(p["p90"], p["p95"])
        self.assertLessEqual(p["p95"], p["p99"])
        self.assertLessEqual(p["p99"], p["max"])

    def test_percentile_interpolates(self):
        self.assertEqual(engine.percentile([1, 2, 3, 4], 0.5), 2.5)
        self.assertEqual(engine.percentile([], 0.5), 0.0)

    def test_bundle_respects_budget_and_only_picks_profitable_controls(self):
        d = sample()
        d["budget"] = 50000
        r = engine.analyse(model.parse(d))
        b = r["bundle"]
        self.assertLessEqual(b["spent"], 50000)
        for step in b["controls"]:
            self.assertGreater(step["marginal_net_benefit"], 0)
        self.assertLessEqual(b["ale_after"], b["ale_before"])

    def test_bundle_is_empty_when_nothing_pays_for_itself(self):
        d = simple()
        d["controls"] = [{"id": "gold", "name": "Gold plating", "annual_cost": 10 ** 9, "vulnerability_reduction": 0.5}]
        self.assertEqual(engine.analyse(model.parse(d))["bundle"]["controls"], [])

    def test_appetite_probability(self):
        d = simple(tef=1.0, vuln=1.0, loss=100.0)
        d["appetite"] = 50
        r = engine.analyse(model.parse(d), with_controls=False)
        self.assertAlmostEqual(r["portfolio"]["prob_over_appetite"], r["portfolio"]["prob_any_loss"], places=9)


class Reports(unittest.TestCase):
    def test_markdown_has_the_sections(self):
        md = report.markdown(engine.analyse(model.parse(sample())))
        for part in ("Portfolio annual loss", "By scenario", "Controls ranked", "Recommended bundle", "exceeds the risk appetite"):
            self.assertIn(part, md)

    def test_html_escapes_input_and_has_no_script(self):
        d = sample()
        d["organisation"] = "<script>alert(1)</script>"
        d["scenarios"][0]["name"] = "<img src=x onerror=1>"
        html = report.html(engine.analyse(model.parse(d)))
        self.assertNotIn("<script", html)
        self.assertNotIn("<img", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertIn("<svg", html)

    def test_money_formatting(self):
        self.assertEqual(report.money("EUR", 1500000), "€1.50M")
        self.assertEqual(report.money("USD", 42000), "$42k")
        self.assertEqual(report.money("CHF", 7), "CHF 7")


class Cli(unittest.TestCase):
    def run_cli(self, *args):
        buf = io.StringIO()
        return cli.main(list(args), out=buf), buf.getvalue()

    def test_validate_and_run(self):
        rc, out = self.run_cli("validate", SAMPLE)
        self.assertEqual(rc, 0)
        self.assertIn("5 scenarios", out)
        rc, out = self.run_cli("run", SAMPLE, "--format", "json", "--iterations", "2000")
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out)["iterations"], 2000)

    def test_appetite_gate_sets_exit_code(self):
        self.assertEqual(self.run_cli("run", SAMPLE, "--iterations", "2000", "--max-prob-over-appetite", "0.01")[0], 2)
        self.assertEqual(self.run_cli("run", SAMPLE, "--iterations", "2000", "--max-prob-over-appetite", "0.99")[0], 0)

    def test_errors_return_one(self):
        self.assertEqual(self.run_cli("run", "/nonexistent.json")[0], 1)
        self.assertEqual(self.run_cli("run", SAMPLE, "--iterations", "5")[0], 1)


class Server(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = server.create(0)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def req(self, method, path, body=None, headers=None, host=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=60)
        h = {"Host": host or "127.0.0.1:%d" % self.port}
        h.update(headers or {})
        c.request(method, path, body=body, headers=h)
        r = c.getresponse()
        data = r.read()
        hdrs = dict((k.lower(), v) for k, v in r.getheaders())
        c.close()
        return r.status, hdrs, data

    GOOD = {"Content-Type": "application/json", "X-FairSim-Request": "1"}

    def test_static_files_have_strict_csp(self):
        status, h, body = self.req("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn("script-src 'self'", h["content-security-policy"])
        self.assertNotIn("unsafe-inline", h["content-security-policy"])
        self.assertEqual(self.req("GET", "/app.js")[0], 200)
        self.assertEqual(self.req("GET", "/api/sample")[0], 200)
        self.assertEqual(self.req("GET", "/favicon.ico")[0], 204)
        self.assertEqual(self.req("GET", "/nope")[0], 404)

    def test_foreign_host_header_is_refused(self):
        self.assertEqual(self.req("GET", "/api/sample", host="attacker.example")[0], 403)
        self.assertEqual(self.req("POST", "/api/simulate", "{}", self.GOOD, host="attacker.example")[0], 403)

    def test_post_requires_custom_header_json_and_same_origin(self):
        body = json.dumps(simple(iterations=1000))
        self.assertEqual(self.req("POST", "/api/simulate", body, {"Content-Type": "application/json"})[0], 403)
        self.assertEqual(self.req("POST", "/api/simulate", body, {"Content-Type": "text/plain", "X-FairSim-Request": "1"})[0], 403)
        bad = dict(self.GOOD, Origin="http://evil.example")
        self.assertEqual(self.req("POST", "/api/simulate", body, bad)[0], 403)
        ok = dict(self.GOOD, Origin="http://127.0.0.1:%d" % self.port)
        self.assertEqual(self.req("POST", "/api/simulate", body, ok)[0], 200)

    def test_simulate_validation_errors_and_limits(self):
        s, _, b = self.req("POST", "/api/simulate", json.dumps({"scenarios": []}), self.GOOD)
        self.assertEqual(s, 400)
        self.assertIn("non-empty", json.loads(b)["error"])
        self.assertEqual(self.req("POST", "/api/simulate", "not json", self.GOOD)[0], 400)
        d = sample()
        d["iterations"] = 100000
        self.assertEqual(self.req("POST", "/api/simulate", json.dumps(d), self.GOOD)[0], 400)
        big = json.dumps({"organisation": "x" * (server.MAX_BODY + 10)})
        self.assertEqual(self.req("POST", "/api/simulate", big, self.GOOD)[0], 400)
        self.assertEqual(self.req("POST", "/api/other", "{}", self.GOOD)[0], 404)

    def test_report_endpoints_after_a_run(self):
        d = sample()
        d["iterations"] = 2000
        d["organisation"] = "<b>Org</b>"
        s, _, b = self.req("POST", "/api/simulate", json.dumps(d), self.GOOD)
        self.assertEqual(s, 200)
        self.assertEqual(json.loads(b)["iterations"], 2000)
        s, h, html = self.req("GET", "/report.html")
        self.assertEqual(s, 200)
        self.assertIn("default-src 'none'", h["content-security-policy"])
        self.assertNotIn(b"<b>Org</b>", html)
        self.assertEqual(self.req("GET", "/report.md")[0], 200)


class UiSource(unittest.TestCase):
    def test_no_html_injection_sinks_or_remote_urls(self):
        web = os.path.join(ROOT, "fairsim", "web")
        with open(os.path.join(web, "app.js"), encoding="utf-8") as f:
            js = f.read()
        with open(os.path.join(web, "index.html"), encoding="utf-8") as f:
            html = f.read()
        for bad in ("innerHTML", "outerHTML", "insertAdjacentHTML", "eval(", "document.write"):
            self.assertNotIn(bad, js)
        for bad in (" onclick=", "<script>", "style="):
            self.assertNotIn(bad, html)
        self.assertNotIn("http://", js.replace("http://www.w3.org/2000/svg", ""))
        self.assertNotIn("https://", js + html)


if __name__ == "__main__":
    unittest.main()
