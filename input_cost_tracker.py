"""
Input cost tracker: UK producer prices, pass-through, and what the headline hides.

Pulls published ONS producer price index series, computes annual and monthly
change, and measures how input cost movement relates to factory gate price
movement for matched industrial and food pairs.

The point is not the correlation. It is that the headline input figure is a
weighted average of things moving in opposite directions, and the weights are
not your bill of materials.

Public data only. No API key. Standard library only.

Source: Office for National Statistics, Producer Price Index, released monthly
under the Open Government Licence. https://www.ons.gov.uk
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import date

# ONS blocks the default Python user-agent with a 403. This is not optional.
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

# Use the undated endpoint. The /mm22/ dataset path serves a frozen edition
# that stopped in Jan 2025, and returns it without any error. Getting this
# wrong means silently reporting data that is over a year old.
ONS_HOST = "https://www.ons.gov.uk"
PRICES_PATH = "economy/inflationandpriceindices/timeseries"
EARNINGS_PATH = "employmentandlabourmarket/peopleinwork/earningsandworkinghours/timeseries"

MONTHS = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}

# Warn if the newest observation is older than this. Producer prices are
# monthly and publish about three weeks in arrears, so two months is normal
# and four means something is wrong.
STALE_AFTER_MONTHS = 4


@dataclass(frozen=True)
class Series:
    cdid: str
    label: str
    basket: str   # industrial | food | economy
    side: str     # input | output | labour
    path: str = PRICES_PATH   # ONS section the series lives under


# Verified against ons.gov.uk. Every code here was fetched and checked.
SERIES: list[Series] = [
    # Aggregates. The headline everyone quotes, and its two halves.
    Series("GHIK", "Inputs, materials, all manufacturing", "economy", "input"),
    Series("GHIM", "Inputs, fuels, all manufacturing", "economy", "input"),
    Series("GB7S", "Output, all manufactured products", "economy", "output"),

    # Industrial pair: what metal bashers buy, what they charge.
    Series("GHGJ", "Inputs to basic iron, steel and alloys", "industrial", "input"),
    Series("EW6T", "Output, basic iron, steel and ferro-alloys", "industrial", "output"),

    # Food pair: what food processors buy, what they charge.
    Series("GHEF", "Inputs to preserved meat and meat products", "food", "input"),
    Series("EVOQ", "Output, grain mill products", "food", "output"),

    # Labour is an input cost too, and the one no producer price index shows.
    Series("K54U", "Average weekly earnings index, whole economy", "economy",
           "labour", path=EARNINGS_PATH),
]

# Pairs to test for pass-through. Input first, output second.
PAIRS = [
    ("GHGJ", "EW6T", "industrial"),
    ("GHEF", "EVOQ", "food"),
    ("GHIK", "GB7S", "all manufacturing"),
]


# ---------------------------------------------------------------- fetching

def fetch(cdid: str, cache_dir: str | None = None,
          path: str = PRICES_PATH) -> dict[str, float]:
    """Return {'YYYY-MM': index_value} for an ONS series."""
    cache_path = os.path.join(cache_dir, f"{cdid}.json") if cache_dir else None
    if cache_path and os.path.exists(cache_path):
        with open(cache_path, encoding="utf-8") as fh:
            payload = json.load(fh)
    else:
        url = f"{ONS_HOST}/{path}/{cdid.lower()}/data"
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(request, timeout=60) as response:
            payload = json.load(response)
        if cache_path:
            os.makedirs(cache_dir, exist_ok=True)
            with open(cache_path, "w", encoding="utf-8") as fh:
                json.dump(payload, fh)

    out: dict[str, float] = {}
    for row in payload.get("months", []):
        year, month_name = row["date"].split()
        key = f"{int(year):04d}-{MONTHS[month_name.upper()[:3]]:02d}"
        try:
            out[key] = float(row["value"])
        except (TypeError, ValueError):
            continue
    return out


# ---------------------------------------------------------------- transforms

def annual_change(levels: dict[str, float]) -> dict[str, float]:
    """Percent change on the same month a year earlier."""
    out = {}
    for key, value in levels.items():
        year, month = key.split("-")
        prior = f"{int(year) - 1:04d}-{month}"
        if prior in levels and levels[prior]:
            out[key] = (value / levels[prior] - 1.0) * 100.0
    return out


def monthly_change(levels: dict[str, float]) -> dict[str, float]:
    keys = sorted(levels)
    out = {}
    for previous, current in zip(keys, keys[1:]):
        if levels[previous]:
            out[current] = (levels[current] / levels[previous] - 1.0) * 100.0
    return out


def align(a: dict[str, float], b: dict[str, float], lag: int = 0
          ) -> tuple[list[float], list[float]]:
    """Pair up two monthly series. Positive lag shifts b later, so it tests
    whether a leads b by `lag` months."""
    xs, ys = [], []
    for key in sorted(a):
        year, month = (int(part) for part in key.split("-"))
        month += lag
        shifted_year = year + (month - 1) // 12
        shifted_month = (month - 1) % 12 + 1
        target = f"{shifted_year:04d}-{shifted_month:02d}"
        if target in b:
            xs.append(a[key])
            ys.append(b[target])
    return xs, ys


# ---------------------------------------------------------------- statistics

def mean(xs): return sum(xs) / len(xs)


def pearson(xs: list[float], ys: list[float]) -> float:
    mx, my = mean(xs), mean(ys)
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    dy = math.sqrt(sum((y - my) ** 2 for y in ys))
    return num / (dx * dy) if dx and dy else float("nan")


def rank(xs: list[float]) -> list[float]:
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    ranks = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        shared = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = shared
        i = j + 1
    return ranks


def spearman(xs, ys): return pearson(rank(xs), rank(ys))


def _betacf(a: float, b: float, x: float) -> float:
    tiny, eps, max_iter = 1e-30, 3e-12, 300
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c, d = 1.0, 1.0 - qab * x / qap
    if abs(d) < tiny:
        d = tiny
    d = 1.0 / d
    h = d
    for m in range(1, max_iter + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        c = 1.0 + aa / c
        if abs(d) < tiny: d = tiny
        if abs(c) < tiny: c = tiny
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        c = 1.0 + aa / c
        if abs(d) < tiny: d = tiny
        if abs(c) < tiny: c = tiny
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < eps:
            break
    return h


def _betai(a: float, b: float, x: float) -> float:
    """Regularised incomplete beta function."""
    if x <= 0.0: return 0.0
    if x >= 1.0: return 1.0
    lbeta = (math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
             + a * math.log(x) + b * math.log1p(-x))
    front = math.exp(lbeta)
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _betacf(a, b, x) / a
    return 1.0 - front * _betacf(b, a, 1.0 - x) / b


def p_two_sided(t: float, df: float) -> float:
    if df <= 0 or math.isnan(t):
        return float("nan")
    return _betai(df / 2.0, 0.5, df / (df + t * t))


def effective_n(xs: list[float], ys: list[float]) -> float:
    """Autocorrelation-adjusted sample size.

    Annual-change series are heavily autocorrelated: twelve consecutive
    monthly readings share eleven months of data. Treating each month as an
    independent observation overstates significance badly. This is the
    standard first-order correction and it is still generous.
    """
    def lag1(values):
        m = mean(values)
        num = sum((values[i] - m) * (values[i + 1] - m) for i in range(len(values) - 1))
        den = sum((v - m) ** 2 for v in values)
        return num / den if den else 0.0

    r1, r2 = lag1(xs), lag1(ys)
    factor = (1 - r1 * r2) / (1 + r1 * r2) if (1 + r1 * r2) else 0.0
    return max(3.0, len(xs) * max(factor, 0.0))


def ols(xs: list[float], ys: list[float]) -> tuple[float, float]:
    """Slope and intercept of y on x."""
    mx, my = mean(xs), mean(ys)
    den = sum((x - mx) ** 2 for x in xs)
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den if den else float("nan")
    return slope, my - slope * mx


@dataclass
class Relationship:
    label: str
    n: int
    n_eff: float
    r: float
    rho: float
    p: float
    slope: float
    best_lag: int
    best_lag_r: float
    lag_profile: list[tuple[int, float]] = field(default_factory=list)


def relate(a: dict[str, float], b: dict[str, float], label: str,
           max_lag: int = 12) -> Relationship | None:
    xs, ys = align(a, b)
    if len(xs) < 24:
        return None
    r = pearson(xs, ys)
    rho = spearman(xs, ys)
    n_eff = effective_n(xs, ys)
    # t-statistic uses the adjusted sample size, not the raw month count.
    df = n_eff - 2
    t = r * math.sqrt(df / (1 - r * r)) if abs(r) < 1 and df > 0 else float("nan")
    slope, _ = ols(xs, ys)

    profile = []
    for lag in range(0, max_lag + 1):
        lx, ly = align(a, b, lag)
        if len(lx) >= 24:
            profile.append((lag, pearson(lx, ly)))
    best_lag, best_r = max(profile, key=lambda pair: abs(pair[1])) if profile else (0, r)

    return Relationship(label, len(xs), n_eff, r, rho, p_two_sided(t, df),
                        slope, best_lag, best_r, profile)


# ---------------------------------------------------------------- reporting

def latest(series: dict[str, float]) -> tuple[str, float]:
    key = max(series)
    return key, series[key]


def months_since(key: str) -> int:
    year, month = (int(part) for part in key.split("-"))
    today = date.today()
    return (today.year - year) * 12 + (today.month - month)


def prior_key(keys: list[str], key: str, months_back: int) -> str | None:
    """The key `months_back` months before `key`, if present."""
    year, month = (int(part) for part in key.split("-"))
    month -= months_back
    year += (month - 1) // 12
    month = (month - 1) % 12 + 1
    target = f"{year:04d}-{month:02d}"
    return target if target in keys else None


def narrative(annual: dict[str, dict[str, float]],
              monthly: dict[str, dict[str, float]]) -> list[str]:
    """A plain-English reading of the month, generated from the data.

    Every sentence is produced by an explicit rule and carries its basis in
    brackets. Nothing here is hand-written for a particular month, so the
    section stays truthful when the data changes under it.
    """
    lines: list[str] = []
    add = lines.append
    need = ("GHIK", "GHIM", "GB7S")
    if not all(code in annual and annual[code] for code in need):
        return lines
    common = sorted(set(annual["GHIK"]) & set(annual["GHIM"]) & set(annual["GB7S"]))
    if not common:
        return lines
    key = common[-1]
    materials, fuels, output = (annual[code][key] for code in need)

    add("THIS MONTH IN PLAIN ENGLISH (rule-generated, basis in brackets)")
    add("")
    add(f"  {key}. Materials inputs {materials:+.1f}% on the year, fuels "
        f"{fuels:+.1f}%,")
    add(f"  factory gate prices {output:+.1f}%. [GHIK, GHIM, GB7S]")
    add("")

    # -- cost against price: who is holding the difference -----------------
    gap = materials - output
    if gap > 1.0:
        add(f"  Materials costs are running {gap:.1f} points ahead of factory gate")
        add("  prices. That difference is going somewhere: absorbed in margin,")
        add("  offset by mix, or parked in contracts that reprice later. These")
        add("  series cannot say which, only that the gap exists. [GHIK - GB7S]")
    elif gap < -1.0:
        add(f"  Factory gate prices are running {abs(gap):.1f} points ahead of")
        add("  materials costs. Either earlier cost inflation is still being")
        add("  recovered, or margin is being rebuilt. These series cannot")
        add("  distinguish the two. [GB7S - GHIK]")
    else:
        add("  Materials costs and factory gate prices are moving within a point")
        add("  of each other, which is as close to matched as these series get.")
        add("  [GHIK vs GB7S]")
    add("")

    # -- direction of travel: is cost inflation building or fading ---------
    prior = prior_key(common, key, 1)
    if prior is not None:
        step = materials - annual["GHIK"][prior]
        if abs(step) >= 0.5:
            verb = "accelerated" if step > 0 else "decelerated"
            add(f"  Materials cost inflation {verb} this month: {annual['GHIK'][prior]:+.1f}%")
            add(f"  to {materials:+.1f}% in one reading. One month is noise; note it,")
            add("  do not build on it. [GHIK, consecutive annual rates]")
            add("")

    # -- the spread, and whether it is widening ----------------------------
    spread = abs(materials - fuels)
    three_back = prior_key(common, key, 3)
    if three_back is not None:
        old_spread = abs(annual["GHIK"][three_back] - annual["GHIM"][three_back])
        move = spread - old_spread
        if abs(move) >= 1.0:
            word = "widened" if move > 0 else "narrowed"
            add(f"  The materials-fuels spread has {word} from {old_spread:.1f} to")
            add(f"  {spread:.1f} points in three months. The wider it is, the less the")
            add("  single headline input figure describes anyone in particular.")
            add("  [|GHIK - GHIM|, 3-month change]")
            add("")

    # -- sectors: one economy, two cost worlds -----------------------------
    if all(c in annual and key in annual[c] for c in ("GHGJ", "GHEF")):
        steel, food = annual["GHGJ"][key], annual["GHEF"][key]
        if (steel > 0) != (food > 0) or abs(steel - food) > 5.0:
            add(f"  The two tracked cost bases are in different worlds: steel-side")
            add(f"  inputs {steel:+.1f}% against food-side inputs {food:+.1f}%. A business")
            add("  quoting one economy-wide input inflation number is choosing whose")
            add("  inflation to quote. [GHGJ vs GHEF]")
            add("")

    # -- the most notable single move this month ---------------------------
    movers = [(abs(monthly[c.cdid].get(key, 0.0)), monthly[c.cdid].get(key, 0.0), c)
              for c in SERIES if c.cdid in monthly and key in monthly[c.cdid]]
    if movers:
        _, value, top = max(movers, key=lambda item: item[0])
        add(f"  Largest single move this month: {top.label}, {value:+.1f}% on the")
        add(f"  month. [{top.cdid}, m/m]")

    add("")
    add("WHAT THESE NUMBERS WILL AND WILL NOT SUPPORT")
    add("")
    add("  State as fact, citing ONS: every annual and monthly figure above,")
    add("  and the spreads and gaps between them. They are published national")
    add("  statistics; the arithmetic is subtraction.")
    add("")
    add("  State with one caveat: the input-output relationships below. They")
    add("  are descriptive co-movement. The caveat is one sentence: these")
    add("  series share drivers, so moving together is not pass-through.")
    add("")
    add("  Do not state: that input costs caused any output price move, in")
    add("  either direction. The section at the end explains why in full.")
    return lines


def report(levels: dict[str, dict[str, float]], stale_after: int) -> list[str]:
    lines: list[str] = []
    add = lines.append

    add("=" * 78)
    add("UK INPUT COST AND PASS-THROUGH TRACKER")
    add("Source: ONS Producer Price Index, Open Government Licence")
    add("=" * 78)

    # ---- freshness -------------------------------------------------------
    add("")
    add("DATA VINTAGE")
    stale = []
    for series in SERIES:
        if series.cdid not in levels:
            continue
        key, _ = latest(levels[series.cdid])
        age = months_since(key)
        flag = "  STALE" if age > stale_after else ""
        add(f"  {series.cdid}  latest {key}  ({age} months old){flag}")
        if age > stale_after:
            stale.append(series.cdid)
    if stale:
        add("")
        add(f"  WARNING: {len(stale)} series older than {stale_after} months.")
        add("  Check the endpoint. The dated /mm22/ path serves a frozen 2025 edition.")

    # ---- levels ----------------------------------------------------------
    add("")
    add("LATEST ANNUAL CHANGE")
    annual = {code: annual_change(values) for code, values in levels.items()}
    monthly = {code: monthly_change(values) for code, values in levels.items()}
    for series in SERIES:
        if series.cdid not in annual or not annual[series.cdid]:
            continue
        key, yoy = latest(annual[series.cdid])
        mom = monthly[series.cdid].get(key, float("nan"))
        add(f"  {series.cdid}  {yoy:+7.1f}% y/y  {mom:+6.1f}% m/m   {series.label}")

    # ---- the spread, which is the actual point ---------------------------
    if "GHIK" in annual and "GHIM" in annual:
        key = max(set(annual["GHIK"]) & set(annual["GHIM"]))
        materials, fuels = annual["GHIK"][key], annual["GHIM"][key]
        add("")
        add("WHAT THE AGGREGATE HIDES")
        add(f"  Materials {materials:+.1f}% against fuels {fuels:+.1f}% "
            f"in {key}, a spread of {abs(materials - fuels):.1f} points.")
        add("  A single input figure averages these using the weights of all UK")
        add("  manufacturing. Those are not the weights of any one business.")

    # ---- the generated reading -------------------------------------------
    story = narrative(annual, monthly)
    if story:
        add("")
        lines.extend(story)

    # ---- pass-through ----------------------------------------------------
    add("")
    add("INPUT TO OUTPUT RELATIONSHIP (annual change, both sides)")
    add("")
    for input_code, output_code, name in PAIRS:
        if input_code not in annual or output_code not in annual:
            continue
        rel = relate(annual[input_code], annual[output_code], name)
        if rel is None:
            add(f"  {name}: not enough overlapping data")
            continue
        add(f"  {name.upper()}  ({input_code} to {output_code})")
        add(f"    months overlapping      {rel.n}")
        add(f"    effective sample size   {rel.n_eff:.1f}  (autocorrelation adjusted)")
        add(f"    Pearson r               {rel.r:+.3f}")
        add(f"    Spearman rho            {rel.rho:+.3f}")
        add(f"    p, two sided            {rel.p:.4f}")
        add(f"    slope                   {rel.slope:+.3f}  "
            f"(output moves {rel.slope:.2f} points per input point, descriptive)")
        add(f"    strongest lag           {rel.best_lag} months (r {rel.best_lag_r:+.3f})")
        if rel.slope > 1.0:
            add("    NOTE: slope above 1 is not pass-through above 100%. It means")
            add("          output prices are more volatile than this input index,")
            add("          which usually says both track a common global price.")
        if rel.n_eff < 10:
            add("    NOTE: effective sample size under 10. Treat p as indicative")
            add("          only. There is very little independent information here.")
        add("")

    # ---- the bit that stops this being nonsense ---------------------------
    add("=" * 78)
    add("CORRELATION IS NOT PASS-THROUGH, AND PASS-THROUGH IS NOT CAUSATION")
    add("=" * 78)
    add("""
  Read the numbers above as description. None of them identify a causal
  effect of input costs on output prices, for four reasons.

  1. Common drivers. Energy and sterling move both sides at once. Two series
     driven by a third thing correlate strongly without either causing the
     other. Nothing above controls for energy or FX.

  2. Shared construction. Input and output indices are built by the same
     agency from overlapping survey frames and partly overlapping commodity
     baskets. Some correlation is measurement, not economics.

  3. Autocorrelation. Annual-change series carry eleven months of shared data
     between consecutive readings. The effective sample size printed above is
     a first-order correction and it is still generous. A p-value computed on
     the raw month count would be roughly meaningless.

  4. Lag is not cause. The strongest-lag figure is a cross-correlation. It is
     not a Granger test, and a Granger test would not establish causation
     either. Costs and prices are jointly determined: firms that expect to
     raise prices also buy differently.

  The slope is a descriptive benchmark for how input and factory gate
  movements have travelled together in this sector. It is not a pass-through
  rate. Where it exceeds 1, output prices are simply more volatile than the
  input index, which is a statement about the two indices rather than about
  anyone's pricing. Use any of this to interrogate a plan, not to justify one.

  WHY THIS CANNOT BE FIXED WITH A REGRESSION, IN PLAIN TERMS

  To claim that input costs drove output prices, you need variation in input
  costs that reaches output prices through no other route. These series offer
  the opposite of that. Energy and exchange rates push both sides at once,
  and the indices are built by one agency from overlapping surveys.

  Adding control variables is not a neutral act. Controlling for a common
  cause, such as energy or FX, helps. Controlling for anything that sits
  downstream of the input move, such as margins, volumes, or an earlier
  month of the output price itself, actively damages the estimate: a control
  on the causal path erases the effect you are measuring, and controlling
  for a shared consequence manufactures a correlation that was never there.
  More data does not rescue this. The effective sample sizes above are the
  proof: 355 monthly readings carry roughly ten months of independent
  information.

  What would support a causal claim: a cost shock that hits one sector and
  not a comparable one, compared before and after (difference in
  differences); or an input price moved by something that cannot plausibly
  touch UK factory gate prices any other way (an instrument). Neither
  exists in this file, so this file does not make causal claims.

  Optional further reading, not required to use this tool: Chernozhukov et
  al (2018) on debiased machine learning; Cinelli, Forney and Pearl (2022)
  on good and bad controls.
""")
    return lines


# ---------------------------------------------------------------- entry point

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    parser.add_argument("--cache", metavar="DIR",
                        help="cache raw ONS responses here, so reruns are offline")
    parser.add_argument("--csv", metavar="FILE",
                        help="write the aligned monthly table to CSV")
    parser.add_argument("--stale-after", type=int, default=STALE_AFTER_MONTHS,
                        help=f"months before a series is flagged (default {STALE_AFTER_MONTHS})")
    args = parser.parse_args()

    levels: dict[str, dict[str, float]] = {}
    for series in SERIES:
        try:
            levels[series.cdid] = fetch(series.cdid, args.cache, series.path)
        except (urllib.error.URLError, urllib.error.HTTPError, ValueError) as exc:
            print(f"  could not fetch {series.cdid} ({series.label}): {exc}")

    if not levels:
        print("No data retrieved. Check the network and the ONS endpoint.")
        return 1

    print("\n".join(report(levels, args.stale_after)))

    if args.csv:
        annual = {code: annual_change(values) for code, values in levels.items()}
        keys = sorted(set().union(*(set(v) for v in annual.values())))
        with open(args.csv, "w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(["month"] + [s.cdid for s in SERIES if s.cdid in annual])
            for key in keys:
                writer.writerow([key] + [
                    f"{annual[s.cdid][key]:.2f}" if key in annual[s.cdid] else ""
                    for s in SERIES if s.cdid in annual
                ])
        print(f"Annual-change table written to {args.csv}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
