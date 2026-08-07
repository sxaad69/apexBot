#!/usr/bin/env python3
"""
PARSE FORENSICS REPORT - Detailed Overview from Daily Settle JSON
==================================================================
Reads a forensics_report_YYYY-MM-DD.json produced by apex_forensics.py --settle
and renders it as a human-readable, detailed overview report (stdout + optional file).

Usage:
  python3 audit/parse_forensics_report.py
  python3 audit/parse_forensics_report.py --date 2026-08-07
  python3 audit/parse_forensics_report.py --report data/reports/forensics_report_2026-08-07.json
  python3 audit/parse_forensics_report.py --output /tmp/overview.txt
"""

import argparse
import json
import os
from datetime import datetime, timezone
from collections import Counter, defaultdict

REPORTS_DIR = "data/reports"


def _resolve_report_path(args):
    if args.report:
        return args.report
    if args.date:
        path = os.path.join(REPORTS_DIR, f"forensics_report_{args.date}.json")
        if os.path.exists(path):
            return path
        raise SystemExit(f"✗ Report not found: {path}")
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    path = os.path.join(REPORTS_DIR, f"forensics_report_{today}.json")
    if os.path.exists(path):
        return path
    files = sorted(
        f for f in os.listdir(REPORTS_DIR)
        if f.startswith("forensics_report_") and f.endswith(".json")
    ) if os.path.isdir(REPORTS_DIR) else []
    if files:
        return os.path.join(REPORTS_DIR, files[-1])
    raise SystemExit(f"✗ No forensics report found in {REPORTS_DIR}")


def _load(path):
    with open(path) as fh:
        return json.load(fh)


def _fmt(x, suffix="", places=2):
    if x is None:
        return "-"
    return f"{x:,.{places}f}{suffix}"


def _pct(x):
    return f"{x:+.1f}%"


def _line(char, width=80):
    return char * width


def _status_section(payload):
    out = []
    summary = payload.get("summary", {})
    out.append(f"{_line('=')}")
    out.append("📋 MODE 1 — EXIT FORENSICS")
    out.append(f"{_line('=')}")
    out.append(f"  Trades analyzed : {summary.get('trades_analyzed', '-')}")
    out.append(f"  Failed to audit : {summary.get('failed', 0)}")
    out.append(f"  Avg exit P&L    : {_fmt(summary.get('avg_exit_pnl'))}%")
    out.append(f"  Win rate        : {_fmt(summary.get('win_rate'), '%', 1)}")
    out.append(f"  TP reached      : {summary.get('tp_reached', 0)}")
    out.append(f"  SL reached      : {summary.get('sl_reached', 0)}")
    out.append(f"  Good captures   : {summary.get('good_captures', 0)}")
    out.append(f"  Better exits    : {summary.get('better_exits', 0)}")

    results = payload.get("results", [])
    if not results:
        out.append("  (no trades in window)")
        return "\n".join(out)

    out.append("")
    out.append(f"  Trade Detail (n={len(results)}):")
    out.append(f"  {'SYMBOL':<20} {'SIDE':<4} {'REASON':<22} {'ENTRY':>10} {'EXIT':>10} "
               f"{'P&L%':>8} {'PEAK%':>8} {'CAPTURE':>8} {'RIDE_Q':>8} {'EXIT_Q':>8} "
               f"{'EARLY':>6}")
    out.append("  " + "-" * 120)

    reason_counter = Counter()
    winners = 0
    for t in results:
        side = str(t.get("side", "?")).upper()
        reason = str(t.get("reason", "?"))
        reason_counter[reason] += 1
        pnl = t.get("exit_pnl")
        if pnl is not None and pnl > 0:
            winners += 1
        out.append(f"  {t.get('sym', '?'):<20} {side:<4} {reason:<22} "
                   f"{_fmt(t.get('entry')):>10} {_fmt(t.get('exit')):>10} "
                   f"{_fmt(pnl, '%', 2):>8} {_fmt(t.get('peak_profit_pct'), '%', 2):>8} "
                   f"{_fmt(t.get('peak_capture'), '%', 1):>8} {_fmt(t.get('ride_quality'), '', 1):>8} "
                   f"{_fmt(t.get('exit_quality'), '', 1):>8} {str(t.get('early_exit')):>6}")
        for key in ("tp_status", "sl_status", "better_exit"):
            val = t.get(key)
            if val:
                out.append(f"      · {key.replace('_', ' ').title()}: {val}")

    out.append("")
    out.append("  Exit Reason Breakdown:")
    for reason, count in reason_counter.most_common():
        out.append(f"    {reason:<24} {count:>3}")
    return "\n".join(out)


def _traders_section(payload):
    out = []
    out.append(f"{_line('=')}")
    out.append("📈 MODE 2 — TOP GAINERS RETROSPECTIVE")
    out.append(f"{_line('=')}")
    out.append(f"  Scan pool  : {payload.get('scan_pool', '-')}")
    out.append(f"  Analyzed   : {payload.get('analyzed', '-')}")
    coverage = payload.get("coverage", {})
    if coverage:
        out.append(f"  Coverage   : {coverage.get('traded', 0)} traded / "
                   f"{coverage.get('watched', 0)} watched / {coverage.get('missed', 0)} missed")

    gainers = payload.get("top_gainers", [])
    if gainers:
        out.append("")
        out.append("  Top Gainers:")
        for i, g in enumerate(gainers[:10], 1):
            out.append(f"    {i:>2}. {g.get('symbol', '?'):<16} {_pct(g.get('pct_change')):>9}  "
                       f"{g.get('status', '')}")
    losers = payload.get("top_losers", [])
    if losers:
        out.append("")
        out.append("  Top Losers:")
        for i, l in enumerate(losers, 1):
            out.append(f"    {i:>2}. {l.get('symbol', '?'):<16} {_pct(l.get('pct_change')):>9}  "
                       f"{l.get('status', '')}")
    return "\n".join(out)


def _alpha_section(payload):
    out = []
    out.append(f"{_line('=')}")
    out.append("🎯 MODE 3 — MISSED ALPHA ANALYSIS")
    out.append(f"{_line('=')}")
    out.append(f"  Signals analyzed : {payload.get('analyzed', '-')}")
    out.append(f"  Winners          : {payload.get('winners', '-')}")
    out.append(f"  Missed alpha     : {_fmt(payload.get('missed_total_pct'), '%', 2)}")

    events = payload.get("top_events", [])
    if events:
        out.append("")
        out.append("  Top Missed Events:")
        out.append(f"  {'SYMBOL':<20} {'TIME':<19} {'STRATEGY':<20} {'SIDE':<4} "
                   f"{'LAYER':<24} {'CONF':>6} {'POT P&L':>9} {'MAX DD':>8}")
        out.append("  " + "-" * 120)
        for e in events[:15]:
            out.append(f"  {e.get('symbol', '?'):<20} {str(e.get('timestamp', '?')):<19} "
                       f"{str(e.get('strategy', '?')):<20} {str(e.get('side', '?')).upper():<4} "
                       f"{str(e.get('layer', '?')):<24} {e.get('confidence', 0):>5.0%} "
                       f"{_fmt(e.get('potential_pnl')):>8} {_fmt(e.get('max_drawdown'), '%', 2):>8}")

    friction = payload.get("friction", {})
    out.append("")
    out.append("  Rejection Friction (by layer):")
    for category in ("STRATEGY", "RISK", "OTHER"):
        layers = friction.get(category, {})
        if not layers:
            out.append(f"    {category:<10} — none")
            continue
        out.append(f"    {category}:")
        for layer, stats in layers.items():
            out.append(f"      {layer:<28} {stats.get('count', 0):>4} rej | "
                       f"{stats.get('winners', 0):>4} would-win | "
                       f"missed alpha {_fmt(stats.get('missed_alpha'), '%', 2)}")
    return "\n".join(out)


def render(payload, output_path=None):
    lines = []
    lines.append(_line("="))
    lines.append("APEX HUNTER — FORENSICS OVERVIEW REPORT")
    lines.append(_line("="))
    lines.append(f"  Report date   : {payload.get('report_date', '-')}")
    lines.append(f"  Generated at  : {payload.get('generated_at', '-')}")
    period = payload.get("period", {})
    lines.append(f"  Analysis window: {period.get('from', '-')} → {period.get('to', '-')}")

    summary = payload.get("summary", {})
    lines.append("")
    lines.append(_line("-"))
    lines.append("EXECUTIVE SUMMARY")
    lines.append(_line("-"))
    lines.append(f"  Trades audited            : {summary.get('trades_audited', '-')}")
    lines.append(f"  Avg exit P&L              : {_fmt(summary.get('avg_exit_pnl'), '%', 2)}")
    lines.append(f"  Missed continuation total : {_fmt(summary.get('missed_continuation_total'), '%', 2)}")
    ma = summary.get("missed_alpha", {})
    if ma:
        lines.append(f"  Missed alpha               : {_fmt(ma.get('missed_total_pct'), '%', 2)} "
                     f"across {ma.get('analyzed', 0)} signals "
                     f"({ma.get('winners', 0)} winners)")
    coverage = summary.get("coverage", {})
    if coverage:
        lines.append(f"  Market coverage            : {coverage.get('traded', 0)} traded / "
                     f"{coverage.get('watched', 0)} watched / {coverage.get('missed', 0)} missed")
    if summary.get("top_gainers"):
        lines.append("  Top gainers                : " + ", ".join(
            f"{g.get('symbol')} ({_pct(g.get('pct_change'))})" for g in summary["top_gainers"][:5]))
    if summary.get("top_losers"):
        lines.append("  Top losers                 : " + ", ".join(
            f"{g.get('symbol')} ({_pct(g.get('pct_change'))})" for g in summary["top_losers"][:5]))

    modes = payload.get("modes", {})
    if "exit_forensics" in modes:
        lines.append("")
        lines.append(_status_section(modes["exit_forensics"]))
    if "top_gainers" in modes:
        lines.append("")
        lines.append(_traders_section(modes["top_gainers"]))
    if "missed_alpha" in modes:
        lines.append("")
        lines.append(_alpha_section(modes["missed_alpha"]))

    text = "\n".join(lines) + "\n"
    if output_path:
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        with open(output_path, "w") as fh:
            fh.write(text)
        print(f"✅ Overview report written to {output_path}")
    else:
        print(text)
    return text


def main():
    parser = argparse.ArgumentParser(description="Parse a forensics_report JSON into a detailed overview")
    parser.add_argument("--report", help="Explicit path to forensics_report_*.json")
    parser.add_argument("--date", help="Report date YYYY-MM-DD (looks in data/reports/)")
    parser.add_argument("--output", "-o", help="Write overview to this file (default: stdout)")
    args = parser.parse_args()

    path = _resolve_report_path(args)
    payload = _load(path)
    print(f"📄 Reading report: {path}")
    render(payload, args.output)


if __name__ == "__main__":
    main()
