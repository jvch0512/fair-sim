"""Markdown and standalone HTML (no scripts) reports with inline SVG charts."""

from xml.sax.saxutils import escape

SYMBOLS = {"EUR": "€", "USD": "$", "GBP": "£"}


def money(cur, v):
    sym = SYMBOLS.get(cur, cur + " ")
    a = abs(v)
    if a >= 1e6:
        s = "%.2fM" % (a / 1e6)
    elif a >= 1e3:
        s = "%.0fk" % (a / 1e3)
    else:
        s = "%.0f" % a
    return ("-" if v < 0 else "") + sym + s


def pct(p):
    return "%.1f%%" % (p * 100)


def markdown(r):
    c = r["currency"]
    p = r["portfolio"]
    out = ["# Cyber risk quantification report", "",
           "**Organisation:** %s  " % r["organisation"],
           "**Simulation:** %d iterations, seed %d  " % (r["iterations"], r["seed"]), "",
           "## Portfolio annual loss", "",
           "| Mean | Median | P90 | P95 | P99 | Chance of any loss |", "|---|---|---|---|---|---|",
           "| %s | %s | %s | %s | %s | %s |" % (money(c, p["mean"]), money(c, p["median"]), money(c, p["p90"]), money(c, p["p95"]), money(c, p["p99"]), pct(p["prob_any_loss"]))]
    if r["appetite"] is not None:
        out += ["", "Chance that annual loss exceeds the risk appetite of %s: **%s**." % (money(c, r["appetite"]), pct(p["prob_over_appetite"]))]
    out += ["", "## By scenario", "", "| Scenario | Mean | P95 | P99 | Chance of loss |", "|---|---|---|---|---|"]
    for s in sorted(r["scenarios"], key=lambda x: -x["mean"]):
        out.append("| %s | %s | %s | %s | %s |" % (s["name"].replace("|", "/"), money(c, s["mean"]), money(c, s["p95"]), money(c, s["p99"]), pct(s["prob_any_loss"])))
    if r["controls"]:
        out += ["", "## Controls ranked by net benefit", "", "| Control | Annual cost | Expected loss avoided | Net benefit | ROSI |", "|---|---|---|---|---|"]
        for k in r["controls"]:
            out.append("| %s | %s | %s | %s | %s |" % (k["name"].replace("|", "/"), money(c, k["annual_cost"]), money(c, k["ale_reduction"]), money(c, k["net_benefit"]),
                                                       "n/a" if k["rosi"] is None else "%.0f%%" % (k["rosi"] * 100)))
    b = r["bundle"]
    if b:
        out += ["", "## Recommended bundle", ""]
        if not b["controls"]:
            out.append("No control pays for itself inside the budget with these estimates.")
        else:
            out.append("Spending %s per year moves expected annual loss from %s to %s." % (money(c, b["spent"]), money(c, b["ale_before"]), money(c, b["ale_after"])))
            out.append("")
            for i, s in enumerate(b["controls"], 1):
                out.append("%d. %s (adds %s net benefit)" % (i, s["name"], money(c, s["marginal_net_benefit"])))
    out += ["", "---", "Estimates are inputs, not measurements: results are only as good as the ranges you provide. Use for prioritisation and discussion."]
    return "\n".join(out) + "\n"


def lec_svg(r, w=720, h=320):
    c = r["currency"]
    pad_l, pad_r, pad_t, pad_b = 56, 16, 14, 36
    curves = [("Today", r["lec"], "#d2503f")]
    if r["bundle"] and r["bundle"]["controls"]:
        curves.append(("With recommended controls", r["bundle"]["lec_after"], "#2e9e6a"))
    xmax = max(pts[-1][0] for _, pts, _ in curves) or 1.0
    if r["appetite"] and r["appetite"] < xmax * 1.5:
        xmax = max(xmax, r["appetite"] * 1.1)
    pmax = max(max(p for _, p in pts) for _, pts, _ in curves) or 1.0
    pmax = min(1.0, pmax * 1.1)
    iw, ih = w - pad_l - pad_r, h - pad_t - pad_b

    def X(x): return pad_l + iw * x / xmax
    def Y(p): return pad_t + ih * (1 - p / pmax)

    s = ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %d %d" role="img" aria-label="Loss exceedance curve">' % (w, h)]
    for i in range(5):
        p = pmax * i / 4
        s.append('<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" stroke="#e1e5ec"/>' % (pad_l, Y(p), w - pad_r, Y(p)))
        s.append('<text x="%d" y="%.1f" font-size="11" fill="#5b6677" text-anchor="end">%s</text>' % (pad_l - 6, Y(p) + 4, pct(p)))
    for i in range(5):
        x = xmax * i / 4
        s.append('<text x="%.1f" y="%d" font-size="11" fill="#5b6677" text-anchor="%s">%s</text>' % (X(x), h - 14, "end" if i == 4 else "middle", escape(money(c, x))))
    for name, pts, col in curves:
        d = " ".join("%.1f,%.1f" % (X(x), Y(p)) for x, p in pts)
        s.append('<polyline fill="none" stroke="%s" stroke-width="2.5" points="%s"/>' % (col, d))
    if r["appetite"] is not None and r["appetite"] <= xmax:
        ax = X(r["appetite"])
        s.append('<line x1="%.1f" y1="%d" x2="%.1f" y2="%d" stroke="#1c2330" stroke-dasharray="5 4"/>' % (ax, pad_t, ax, h - pad_b))
        s.append('<text x="%.1f" y="%d" font-size="11" fill="#1c2330">appetite</text>' % (ax + 4, pad_t + 12))
    lx = w - pad_r - 210
    for name, _, col in curves:
        s.append('<rect x="%d" y="%d" width="12" height="4" fill="%s"/><text x="%d" y="%d" font-size="11" fill="#1c2330">%s</text>' % (lx, pad_t + 22 + (0 if name == "Today" else 16) - 4, col, lx + 18, pad_t + 22 + (0 if name == "Today" else 16), escape(name)))
    s.append("</svg>")
    return "".join(s)


def html(r):
    c = r["currency"]
    p = r["portfolio"]
    css = ("body{font:15px/1.5 system-ui,sans-serif;margin:0;background:#f6f7f9;color:#1c2330}main{max-width:960px;margin:0 auto;padding:32px 24px}"
           "h1{margin:0 0 4px}h2{margin-top:32px}.meta{color:#5b6677}.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:12px;margin:16px 0}"
           ".k{background:#fff;border:1px solid #dde1e8;border-radius:10px;padding:12px 16px}.k b{display:block;font-size:22px}.k span{color:#5b6677;font-size:12px;text-transform:uppercase}"
           ".card{background:#fff;border:1px solid #dde1e8;border-radius:10px;padding:16px}table{border-collapse:collapse;width:100%;background:#fff}"
           "th,td{text-align:left;padding:8px 10px;border-bottom:1px solid #e6e9ef;font-size:14px}th{background:#eef1f6}td.n{text-align:right;white-space:nowrap}"
           ".foot{color:#5b6677;font-size:13px;margin-top:32px}")
    o = ['<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Cyber risk quantification report</title><style>%s</style></head><body><main>' % css,
         "<h1>Cyber risk quantification report</h1><div class=\"meta\">%s &middot; %d iterations, seed %d</div>" % (escape(r["organisation"]), r["iterations"], r["seed"]),
         '<div class="kpis">']
    for label, key in (("Expected annual loss", "mean"), ("P95", "p95"), ("P99", "p99")):
        o.append('<div class="k"><span>%s</span><b>%s</b></div>' % (label, escape(money(c, p[key]))))
    if r["appetite"] is not None:
        o.append('<div class="k"><span>Chance above appetite</span><b>%s</b></div>' % pct(p["prob_over_appetite"]))
    o.append("</div><h2>Loss exceedance curve</h2><div class=\"card\">%s</div>" % lec_svg(r))
    o.append("<h2>By scenario</h2><table><thead><tr><th>Scenario</th><th>Mean</th><th>P95</th><th>P99</th><th>Chance of loss</th></tr></thead><tbody>")
    for s in sorted(r["scenarios"], key=lambda x: -x["mean"]):
        o.append("<tr><td>%s</td><td class=\"n\">%s</td><td class=\"n\">%s</td><td class=\"n\">%s</td><td class=\"n\">%s</td></tr>" % (
            escape(s["name"]), escape(money(c, s["mean"])), escape(money(c, s["p95"])), escape(money(c, s["p99"])), pct(s["prob_any_loss"])))
    o.append("</tbody></table>")
    if r["controls"]:
        o.append("<h2>Controls ranked by net benefit</h2><table><thead><tr><th>Control</th><th>Annual cost</th><th>Loss avoided</th><th>Net benefit</th><th>ROSI</th></tr></thead><tbody>")
        for k in r["controls"]:
            o.append("<tr><td>%s</td><td class=\"n\">%s</td><td class=\"n\">%s</td><td class=\"n\">%s</td><td class=\"n\">%s</td></tr>" % (
                escape(k["name"]), escape(money(c, k["annual_cost"])), escape(money(c, k["ale_reduction"])), escape(money(c, k["net_benefit"])),
                "n/a" if k["rosi"] is None else "%.0f%%" % (k["rosi"] * 100)))
        o.append("</tbody></table>")
    b = r["bundle"]
    if b:
        o.append("<h2>Recommended bundle</h2><div class=\"card\">")
        if not b["controls"]:
            o.append("<p>No control pays for itself inside the budget with these estimates.</p>")
        else:
            o.append("<p>Spending <b>%s</b> per year moves expected annual loss from <b>%s</b> to <b>%s</b>.</p><ol>" % (escape(money(c, b["spent"])), escape(money(c, b["ale_before"])), escape(money(c, b["ale_after"]))))
            for s in b["controls"]:
                o.append("<li>%s <span class=\"meta\">(adds %s net benefit)</span></li>" % (escape(s["name"]), escape(money(c, s["marginal_net_benefit"]))))
            o.append("</ol>")
        o.append("</div>")
    o.append('<p class="foot">Estimates are inputs, not measurements: results are only as good as the ranges you provide. Use for prioritisation and discussion.</p></main></body></html>')
    return "".join(o)
