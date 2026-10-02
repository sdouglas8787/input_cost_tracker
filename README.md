# Input cost tracker

Pulls published ONS producer price series, computes annual and monthly change,
and measures how input cost movement has travelled with factory gate prices in
matched industrial and food sectors.

Public data only. No API key. Python standard library only, so it runs on a
clean machine with no install step.

```
python3 input_cost_tracker.py
python3 input_cost_tracker.py --cache .cache --csv annual_change.csv
```

## Why it exists

The headline producer input figure is a weighted average of things moving in
opposite directions, weighted for the whole of UK manufacturing. In July 2026
materials inputs were up 4.8% on the year while fuels inputs were up 9.1%, a
spread of over four points inside one number. Neither weight belongs to any
particular business.

The tool prints that spread first, because it is the finding that changes what
you do. It then generates a plain-English reading of the month from explicit
rules, each sentence carrying its basis in brackets, and states which of its
numbers can be quoted as fact, which need a caveat, and which claims the data
cannot support at all. The output is self-contained: no interpretation is left
to the reader, and the causal reasoning is written out in the report rather
than delegated to citations.

## What it does not do

It does not estimate a causal effect of costs on prices, and it says so at
length every time it runs. Input and output indices share energy and currency
drivers, share survey frames, and are autocorrelated to the point where
consecutive readings carry almost no independent information. The report prints
an effective sample size alongside the raw month count for exactly that reason.
On a 355 month overlap the effective sample size is about 10.

That correction is deliberately conservative. The honest reading is that these
series contain far less evidence than their length suggests.

## Validation

Run against the ONS release of 19 August 2026, the tool reproduces the
published headline from index levels alone:

| Measure | Tool | ONS bulletin |
|---|---|---|
| Output, all manufactured products, annual | +3.1% | +3.1% |
| Output, all manufactured products, monthly | +0.2% | +0.2% |

Materials inputs come out at +4.8% against the bulletin headline of +4.9%,
because the published headline combines materials and fuels on ONS weights
while this tool keeps them separate on purpose.

## The endpoint trap

ONS retired `api.ons.gov.uk` in November 2024. The live path is on
`www.ons.gov.uk`, and the dataset-qualified form matters:

```
/timeseries/{cdid}/data          current, runs to the latest release
/timeseries/{cdid}/mm22/data     frozen edition, stops in January 2025
```

The frozen path returns a valid 200 with plausible-looking data and no warning
of any kind. This tool uses the undated path and prints the age of every series
so a silent regression to stale data is visible rather than invisible.

ONS also returns 403 to the default Python user-agent. A browser user-agent is
set in the source.

## Series used

All codes verified against ons.gov.uk.

| CDID | Series | Basket |
|---|---|---|
| GHIK | Inputs, materials, all manufacturing | aggregate |
| GHIM | Inputs, fuels, all manufacturing | aggregate |
| GB7S | Output, all manufactured products | aggregate |
| GHGJ | Inputs to basic iron, steel and alloys | industrial |
| EW6T | Output, basic iron, steel and ferro-alloys | industrial |
| GHEF | Inputs to preserved meat and meat products | food |
| EVOQ | Output, grain mill products | food |

Adding a series means adding one row to `SERIES` in the source. Pairs to test
go in `PAIRS`.

## Licence and attribution

Contains public sector information licensed under the Open Government Licence
v3.0. Source: Office for National Statistics.
