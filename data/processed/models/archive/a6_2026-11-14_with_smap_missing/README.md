# A6 as it stood before dropping `smap_missing` (14 Nov 2026)

Copy of `data/processed/models/a6/` and the executed notebook, kept before Ridge stopped
receiving the SMAP-missing indicator. Why it was dropped: the indicator is 1 for every year
before 2015, so it acts as an era marker. On early-season greenness targets it carried all of
the apparent skill (Jun-Jul: +0.210 with it, +0.203 with it alone, +0.010 without it). On the
shipped Aug-Sep target, dropping it moved Ridge skill from -0.040 to -0.034. Logged in
docs/experiments.csv.
