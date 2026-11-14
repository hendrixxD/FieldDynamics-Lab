#!/usr/bin/env python3
"""
PRD A2: rainy-season onset, cessation and dry spells per Kaduna LGA and year.

Input : data/processed/chirps/chirps_lga_daily.csv (lga, date, precip_mm),
        CHIRPS 1981 on; any daily table with those columns works (--rain).
Check : data/raw/nimet/scp2025_kaduna_lga.csv (NiMet SCP 2025 per-LGA onset
        and cessation), compared automatically when present.
Pure standard library (csv, datetime), so it runs anywhere.

Definitions (PRD A2, standard West African agronomic rules)
  wet day    : rain >= 1 mm; dry day: < 1 mm
  onset      : first wet day on or after 1 April that starts a 3-day total of
               >= 25 mm, with no dry spell longer than 7 days in the following
               30 days (rules out "false starts" that wither seedlings).
               The 7-day false-start test is Sivakumar (1988). The textbook
               3-day total is 20 mm; on CHIRPS it puts onset ~19 days before
               NiMet's 2025 dates, because CHIRPS spreads rain over more,
               lighter days than a gauge. 25 mm was chosen by testing 20/25/30 mm
               x 7/10-day rules against NiMet SCP 2025 for all 23 LGAs: median
               gap -4 days (vs -19), 2025 mean absolute error 8 days, north-south
               rank agreement rho 0.85. Use --onset-mm 20 --false-start 10 for
               the textbook rule.
  cessation  : first day on or after 1 September when a 70 mm soil-water bucket,
               filled by rain from onset and drained by 5 mm/day evaporation,
               runs empty (Stern et al. water-balance definition)
  dry spell  : run of consecutive dry days between onset and cessation; we
               report the longest, its start date, and the count of 10+ day runs

Outputs, in data/processed/a2/:
  a2_lga_year.csv      one row per LGA-year
  a2_lga_summary.csv   per LGA: median onset/cessation/length, 20th and 80th
                       percentile onset (an "early" and "late" year), share of
                       years with a 10+ day dry spell, and a 1981-2025 trend in
                       onset (Sen's slope, days per decade, Mann-Kendall p)
  a2_vs_nimet2025.csv  CHIRPS-detected 2025 onset/cessation and the LGA's
                       1991-2020 median vs NiMet's 2025 prediction

Run (from the repo root)
  python -m data.onset_a2
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import logging
import math
import re
import statistics as st
from collections import defaultdict
from pathlib import Path

log = logging.getLogger("onset_a2")

REPO_ROOT = Path(__file__).resolve().parents[1]
WET_MM, ONSET_3DAY_MM, FALSE_START_DAYS, LOOKAHEAD = 1.0, 25.0, 7, 30
BUCKET_MM, EVAP_MM = 70.0, 5.0
ONSET_FROM, CESSATION_FROM = (4, 1), (9, 1)
NORMAL = (1991, 2020)


def norm(name):
    """Match LGA spellings across sources (Birnin-Gwari, Jema'A, Zango-Kataf...)."""
    k = re.sub(r"[^a-z]", "", name.lower())
    return {"zangokataf": "zangonkataf"}.get(k, k)


def load_rain(path):
    rain = defaultdict(dict)
    with open(path) as f:
        for r in csv.DictReader(f):
            if r["precip_mm"] not in ("", "nan"):
                rain[r["lga"]][dt.date.fromisoformat(r["date"][:10])] = float(r["precip_mm"])
    return rain


def runs_of_dry(series):
    """[(start_index, length)] of consecutive dry days."""
    out, start = [], None
    for i, mm in enumerate(series):
        if mm < WET_MM:
            start = i if start is None else start
        elif start is not None:
            out.append((start, i - start))
            start = None
    if start is not None:
        out.append((start, len(series) - start))
    return out


def season(r, year):
    """A2 metrics for one LGA-year; r maps date -> mm."""
    day = r.get
    d0 = dt.date(year, *ONSET_FROM)
    last_onset = dt.date(year, 8, 31)
    onset = None
    d = d0
    while d <= last_onset:
        window = [day(d + dt.timedelta(i)) for i in range(3)]
        if None in window:
            return None  # incomplete year
        if window[0] >= WET_MM and sum(window) >= ONSET_3DAY_MM:
            ahead = [day(d + dt.timedelta(i)) for i in range(1, LOOKAHEAD + 1)]
            if None in ahead:
                return None
            if not any(n >= FALSE_START_DAYS for _, n in runs_of_dry(ahead)):
                onset = d
                break
        d += dt.timedelta(1)
    if onset is None:
        return {"year": year, "onset": None}

    water, d, cessation = 0.0, onset, None
    year_end = dt.date(year, 12, 31)
    while d <= year_end:
        mm = day(d)
        if mm is None:
            break
        water = min(BUCKET_MM, water + mm - EVAP_MM)
        if d >= dt.date(year, *CESSATION_FROM) and water <= 0:
            cessation = d
            break
        water = max(water, 0.0)
        d += dt.timedelta(1)

    end = cessation or year_end
    days = [onset + dt.timedelta(i) for i in range((end - onset).days + 1)]
    series = [day(x) for x in days]
    if None in series:
        return None
    dry = runs_of_dry(series)
    longest = max(dry, key=lambda x: x[1], default=(None, 0))
    return {
        "year": year,
        "onset": onset,
        "cessation": cessation,
        "length_days": (cessation - onset).days if cessation else None,
        "season_rain_mm": round(sum(series), 1),
        "longest_dry_spell_days": longest[1],
        "longest_dry_spell_start": days[longest[0]] if longest[0] is not None else None,
        "dry_spells_10plus": sum(1 for _, n in dry if n >= FALSE_START_DAYS),
    }


def sen_mk(xs, ys):
    """Sen's slope (per unit x) and two-sided Mann-Kendall p (no tie correction)."""
    pts = [(x, y) for x, y in zip(xs, ys, strict=True) if y is not None]
    n = len(pts)
    if n < 8:
        return None, None
    slopes = [
        (pts[j][1] - pts[i][1]) / (pts[j][0] - pts[i][0])
        for i in range(n)
        for j in range(i + 1, n)
        if pts[j][0] != pts[i][0]
    ]
    s = sum(
        (pts[j][1] > pts[i][1]) - (pts[j][1] < pts[i][1]) for i in range(n) for j in range(i + 1, n)
    )
    var = n * (n - 1) * (2 * n + 5) / 18
    z = (s - 1) / math.sqrt(var) if s > 0 else (s + 1) / math.sqrt(var) if s < 0 else 0
    p = math.erfc(abs(z) / math.sqrt(2))
    return st.median(slopes), p


def pct(vals, q):
    v = sorted(vals)
    k = (len(v) - 1) * q
    f = math.floor(k)
    return v[f] + (v[min(f + 1, len(v) - 1)] - v[f]) * (k - f)


def doy(d):
    return d.timetuple().tm_yday if d else None


def fmt(day_of_year, year=2025):
    if day_of_year is None:
        return ""
    return (dt.date(year, 1, 1) + dt.timedelta(round(day_of_year) - 1)).strftime("%d %b")


def spearman(a, b):
    def ranks(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0] * len(v)
        for rank, i in enumerate(order):
            r[i] = rank
        return r

    ra, rb = ranks(a), ranks(b)
    n = len(a)
    return 1 - 6 * sum((x - y) ** 2 for x, y in zip(ra, rb, strict=True)) / (n * (n * n - 1))


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--rain", default=str(REPO_ROOT / "data/processed/chirps/chirps_lga_daily.csv"))
    ap.add_argument("--nimet", default=str(REPO_ROOT / "data/raw/nimet/scp2025_kaduna_lga.csv"))
    ap.add_argument("--out", default=str(REPO_ROOT / "data/processed/a2"))
    ap.add_argument(
        "--onset-mm",
        type=float,
        default=ONSET_3DAY_MM,
        help="3-day rain total that can start the season (default 25)",
    )
    ap.add_argument(
        "--false-start",
        type=int,
        default=FALSE_START_DAYS,
        help="a dry spell of this many days in the next 30 voids an onset (default 7)",
    )
    args = ap.parse_args()
    globals()["ONSET_3DAY_MM"], globals()["FALSE_START_DAYS"] = args.onset_mm, args.false_start
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    rain = load_rain(args.rain)
    rows = []
    for lga in sorted(rain):
        years = sorted({d.year for d in rain[lga]})
        for y in years:
            s = season(rain[lga], y)
            if s:
                rows.append({"lga": lga, **s})

    cols = [
        "lga",
        "year",
        "onset",
        "onset_doy",
        "cessation",
        "cessation_doy",
        "length_days",
        "season_rain_mm",
        "longest_dry_spell_days",
        "longest_dry_spell_start",
        "dry_spells_10plus",
    ]
    with open(out / "a2_lga_year.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow(
                {
                    **{c: r.get(c, "") for c in cols},
                    "onset_doy": doy(r.get("onset")),
                    "cessation_doy": doy(r.get("cessation")),
                }
            )

    by = defaultdict(list)
    for r in rows:
        if r.get("onset"):
            by[r["lga"]].append(r)
    summary = []
    for lga, rs in sorted(by.items()):
        on = [doy(r["onset"]) for r in rs]
        ce = [doy(r["cessation"]) for r in rs if r.get("cessation")]
        ln = [r["length_days"] for r in rs if r.get("length_days")]
        normal = [doy(r["onset"]) for r in rs if NORMAL[0] <= r["year"] <= NORMAL[1]]
        normal_c = [
            doy(r["cessation"])
            for r in rs
            if NORMAL[0] <= r["year"] <= NORMAL[1] and r.get("cessation")
        ]
        slope, p = sen_mk([r["year"] for r in rs], on)
        summary.append(
            {
                "lga": lga,
                "years": len(rs),
                "onset_median": fmt(st.median(on)),
                "onset_p20_early": fmt(pct(on, 0.2)),
                "onset_p80_late": fmt(pct(on, 0.8)),
                "onset_normal_1991_2020": fmt(st.median(normal)) if normal else "",
                "onset_normal_doy": round(st.median(normal), 1) if normal else "",
                "cessation_median": fmt(st.median(ce)) if ce else "",
                "cessation_normal_doy": round(st.median(normal_c), 1) if normal_c else "",
                "length_median_days": round(st.median(ln)) if ln else "",
                "longest_dry_spell_median_days": st.median(r["longest_dry_spell_days"] for r in rs),
                "share_years_dry_spell_10plus": round(
                    sum(r["dry_spells_10plus"] > 0 for r in rs) / len(rs), 2
                ),
                "onset_trend_days_per_decade": round(slope * 10, 1) if slope is not None else "",
                "onset_trend_p": round(p, 3) if p is not None else "",
            }
        )
    with open(out / "a2_lga_summary.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(summary[0]))
        w.writeheader()
        w.writerows(summary)

    log.info(
        f"{'LGA':14} {'onset med':>9} {'early':>7} {'late':>7} {'cess med':>9} {'len':>4} "
        f"{'dry10+':>6} {'trend d/dec':>11}"
    )
    for s in sorted(summary, key=lambda s: s["onset_normal_doy"] or 0):
        significant = s["onset_trend_p"] != "" and s["onset_trend_p"] < 0.05
        trend = str(s["onset_trend_days_per_decade"]) + (" *" if significant else "")
        log.info(
            f"{s['lga'][:14]:14} {s['onset_median']:>9} {s['onset_p20_early']:>7} "
            f"{s['onset_p80_late']:>7} {s['cessation_median']:>9} {s['length_median_days']:>4} "
            f"{s['share_years_dry_spell_10plus']:>6} "
            f"{trend:>11}"
        )

    nimet_path = Path(args.nimet)
    if not nimet_path.exists():
        log.info(f"\nNo NiMet table at {nimet_path}; comparison skipped")
        return
    nimet = {norm(r["lga"]): r for r in csv.DictReader(open(nimet_path))}
    yr25 = {norm(r["lga"]): r for r in rows if r["year"] == 2025}
    summ = {norm(s["lga"]): s for s in summary}
    comp = []
    for k, n in nimet.items():
        if k not in summ:
            log.info(f"  NiMet LGA {n['lga']} not matched in rain data")
            continue
        n_on = doy(dt.date.fromisoformat(n["onset"]))
        n_ce = doy(dt.date.fromisoformat(n["cessation"]))
        obs = yr25.get(k, {})
        comp.append(
            {
                "lga": summ[k]["lga"],
                "nimet_onset_2025": n["onset"],
                "chirps_onset_2025": obs.get("onset") or "",
                "onset_diff_days_chirps_minus_nimet": (doy(obs["onset"]) - n_on)
                if obs.get("onset")
                else "",
                "normal_onset_1991_2020": summ[k]["onset_normal_1991_2020"],
                "normal_minus_nimet_days": round(summ[k]["onset_normal_doy"] - n_on, 1),
                "nimet_cessation_2025": n["cessation"],
                "chirps_cessation_2025": obs.get("cessation") or "",
                "cessation_diff_days": (doy(obs["cessation"]) - n_ce)
                if obs.get("cessation")
                else "",
                "normal_cessation_minus_nimet_days": (
                    round(summ[k]["cessation_normal_doy"] - n_ce, 1)
                    if summ[k]["cessation_normal_doy"] != ""
                    else ""
                ),
                "_n_on": n_on,
                "_n_ce": n_ce,
                "_norm_on": summ[k]["onset_normal_doy"],
            }
        )
    fields = [c for c in comp[0] if not c.startswith("_")]
    with open(out / "a2_vs_nimet2025.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows([{c: r[c] for c in fields} for r in comp])

    d_obs = [
        r["onset_diff_days_chirps_minus_nimet"]
        for r in comp
        if r["onset_diff_days_chirps_minus_nimet"] != ""
    ]
    d_norm = [r["normal_minus_nimet_days"] for r in comp]
    d_ce = [r["cessation_diff_days"] for r in comp if r["cessation_diff_days"] != ""]
    rho = spearman([r["_n_on"] for r in comp], [r["_norm_on"] for r in comp])
    log.info(f"\nNiMet 2025 vs CHIRPS ({len(comp)} LGAs)")
    log.info(
        "  north-south ordering of onset, NiMet vs CHIRPS 1991-2020 normal: "
        f"Spearman rho = {rho:.2f}"
    )
    log.info(
        f"  CHIRPS normal onset minus NiMet 2025: median {st.median(d_norm):+.0f} days "
        f"(range {min(d_norm):+.0f} to {max(d_norm):+.0f})"
    )
    if d_obs:
        log.info(
            f"  CHIRPS-observed 2025 onset minus NiMet 2025: median {st.median(d_obs):+.0f} days, "
            f"mean absolute {st.mean(abs(x) for x in d_obs):.0f} days"
        )
    if d_ce:
        log.info(
            f"  CHIRPS-observed 2025 cessation minus NiMet: median {st.median(d_ce):+.0f} days, "
            f"mean absolute {st.mean(abs(x) for x in d_ce):.0f} days"
        )
    log.info(f"Wrote {out}/a2_lga_year.csv, a2_lga_summary.csv, a2_vs_nimet2025.csv")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    main()
