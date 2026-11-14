# Rotation engine

Data decides; published Kaduna and savanna studies check the decision.

- Code: `model/rotation_engine.py` (rules), `model/engine_data.py` (loads the precomputed outputs once), `model/engine_validation.py` (the study check).
- Settings: `config/models.yaml` (`engine`).
- Crop facts: `config/crops.yaml`.
- Published studies: `config/rotations.yaml`.
- Validation notebook: `notebooks/a13_engine_validation.ipynb`.
- Tests: `tests/test_rotation_engine.py`.

```python
from model.engine_data import load_engine_data
from model.rotation_engine import Request, score_request

data = load_engine_data()  # once, at app start
result = score_request(
    Request(
        lga="Zaria", past_seasons=[["maize"], ["maize"]], priorities=["add_nitrogen", "fewer_pests"]
    ),
    data,
)
```

`result` is what `/api/score` returns. It holds three cards (Current, Option A, Option B), plus:
- the Striga risk;
- the weights used;
- the close-call flag;
- every dropped candidate, with its reason;
- a low-cropland flag.

One request takes about 0.1 s.

## How the data decides

1. **Candidates come from data, not papers.** The engine takes every two-season sequence of two different crops with a water balance: maize, sorghum, millet, soybean, cowpea, groundnut, sesame, cassava and tomato, 72 sequences in all.
2. **Filters from the LGA's own record.**
   - A9 under the chosen season type: a crop short of water in more than half the seasons (rain only) is dropped.
   - A crop that fits the rainy season in half the years or fewer is dropped. Below 8 in 10 years, it stays with a risk line.
   - Cassava is allowed into the dry season; its dry-season shortfall is a risk line.
   - If fewer than two candidates survive, the water rule is relaxed and the card says so.
3. **Seven rows, 0 to 1.** A row is scored only when every crop in the rotation has data for it. Otherwise it reads "not enough information".

| Row | How | Data |
|---|---|---|
| Nutrients | N-fixing seasons: half credit, plus the rest scaled by net N left after grain | crop facts |
| Organic matter | 0.7 x ground-cover days + 0.3 x legume seasons | FAO-56 stages, crop facts |
| Erosion and infiltration | 0.5 x ground-cover days + 0.5 x rooting depth; reason names the LGA's erosion band | FAO-56, A10 |
| Pests | Crop family change between seasons, minus a repeated Striga host, plus a Striga trap crop | crop facts |
| Water use | Share of seasons short: half from the chosen season type, half from all four types weighted by how often each comes in this LGA | A9, A5 |
| Forage | Share of residue animals eat | crop facts |
| Market | Price per mudu in this LGA, relative to the dearest crop there; "home use" halves cash crops | KDBS retail prices, all 23 LGAs |

4. **Weights start from the LGA's data, then the farmer's priorities multiply them.**
   - Each row's base weight is 0.5, 1 or 1.5, for the best, middle and worst third of Kaduna on that row's data:
     - nutrients: iSDA total N;
     - organic matter: iSDA organic C;
     - erosion: A10 index;
     - water: how often 10+ day dry spells come (A2);
     - pests: the Striga soil base.
   - A chosen priority multiplies its row by 3.
   - High Striga risk on the farmer's own field (cereal history plus soil) adds 2 to Pests.
5. **Ranking.** The weighted mean of the scored rows, times the share of weight those rows carry. A rotation is never ranked up for data we don't have, so filling a gap can only help a rotation.
   - Option B must use a different crop set from Option A.
   - Ties go to a sequence a study backs.
6. **Confidence.** The LGA's A12 level drops one step for a relaxed rule or unknown soil. Data alone does not lower confidence; a matching study is shown on the card as support.
7. **Risk lines (not scored).**
   - Striga, plus dry spells (A2), which make it worse.
   - Heat at flowering: A2's usual rain start plus the crop's early stages gives the flowering month, and A3 gives how often that month has a 35 °C day.
   - Cowpea's own Striga.
   - Humid spells in maize and sorghum seasons (leaf blight) and dry spells before the groundnut harvest (aflatoxin), from A14 (`notebooks/a14_risk_lines.ipynb`): shown when they came in 3 or more of 10 seasons of the chosen type.
   - Scouting lines: fall armyworm, cowpea pests, ginger blight.
   - A note when the advisor's soil choice differs from the map.

Weights and cut-offs marked "design" in `config/models.yaml` are our choices.

## How the studies check it (notebook a13)

The engine ranked every candidate (now drawn from 14 crops, filtered by A15 suitability and the A9 water rules) for every LGA, all four season types and two typical farms (continuous maize, continuous sorghum). Studies played no part in this ranking. All study statuses were checked against their papers on 14 Nov 2026 (`config/rotations.yaml` names each paper).

| Studied sequence | Study | Mean percentile in the data's ranking |
|---|---|---|
| Sorghum then cowpea | RT05, Shuaibu et al. 2015 (Bauchi); Bado et al. 2006 | 1.00 |
| Maize then cowpea | RT03 | 0.98 |
| Sorghum then groundnut | RT06a, Bado et al. 2006; Bagayoko et al. 2000 | 0.96 |
| Millet then groundnut | RT06b, Bationo and Ntare 2000 | 0.95 |
| Maize then groundnut | RT04, Kermah et al. 2018; N Ghana 11-year trial | 0.95 |
| Maize then soybean | RT02 | 0.90 |
| Cassava then maize | RT09, N Ghana 11-year trial | 0.65 |
| Lablab then maize | RT08, Franke et al. 2008 | 0.62 |

Together the eight studied sequences average the 88th percentile, against 50% for eight random sequences (permutation test, 10,000 draws, p = 0.0001). The six cereal and grain-legume sequences sit at the 90th to 100th percentile.

Cassava then maize and lablab then maize sit lower because the data on those crops is thinner: no KDBS price by the mudu for cassava, no market price for lablab, and no share-eaten figure for either. Their rows are skipped and the coverage multiplier pulls the total down. This is the engine being honest about missing data, not the data disagreeing with the trials.

RT07 (maize with cowpea, then soybean) is confirmed (Kureh, Kamara and Tarfa, northern Guinea savanna of Nigeria) but is not tested here: the engine does not generate intercrops yet.

**Limit.** The check is not fully independent. The engine's pest and nutrient rules come from the same agronomy the studies tested. The data's own part (which crops fit each LGA's climate, water, season length, soil and Striga risk) was not told the answer.

## What A15 answered from data

ECOCROP (FAO, read from the Recocrop R package) gives every crop its temperature, rain, pH and texture ranges. Notebook a15 scores them against POWER and CHIRPS monthly climate (1981 to 2025) and iSDA soil for all 23 LGAs:

- pH and texture ranges, drought tolerance (rain below optimum) and heat tolerance (heat above optimum): all 14 crops.
- Rain fit for rice, ginger and yam, which have no FAO-56 water balance: the engine falls back to A15's rain-shortfall share.
- The data reproduces ginger's southern range (mean climate score 0.93 South, 0.51 North) with no zone list given.

## Honest limits

- Options look alike across LGAs (a cereal then cowpea wins most scenarios). LGA data changes the weights and filters, but the crop facts that separate rotations are statewide.
- Nutrients rarely reads High: grain legumes leave little net N once the grain is sold.
- The soil input (I2) changes confidence and a risk line only, and equipment (I4) only the labour line.
- pH varies only 5.6 to 6.1 across Kaduna at the LGA scale, so pH rarely separates crops.

## Data gaps to fill (new data incoming)

**Crop facts (`config/crops.yaml`, listed under each crop's `gaps`):**
- Share of forage eaten: soybean (holds back maize then soybean), sesame, rice, ginger, tomato, yam, cassava, lablab, stylosanthes. Lablab and stylo now have a sourced forage quality (high protein) but no share-eaten figure on the same scale as the others.
- Comparable Kaduna prices: groundnut (KDBS gives no unit), sesame, ginger, lablab and stylo (not in KDBS), tomato, yam and cassava (sold by rubber or tuber, not by the mudu).
- Market role (food or cash): maize, sorghum, millet, sesame, rice, tomato, yam, cassava, lablab, stylo.
- Labour days per hectare: sorghum, millet, sesame, rice, ginger, tomato, yam, cassava, lablab, stylo.
- Pest groups: sesame, yam, cassava, lablab, stylo.
- Residue amounts: IPCC 2006 Table 11.2 ratios are in `config/residue.yaml` for maize, sorghum, millet, rice, soybean and groundnut. They need LGA yields, which no source in the repo gives.
- Groundnut net N range (only the mean is sourced).

**Water balance:**
- Rice, ginger and yam use A15's rain fit, not a daily FAO-56 balance.
- Cassava and tomato use FAO-56 rows not specific to West Africa.

**Studies:**
- A southern Kaduna trial for yam or ginger. A farm survey (Franke et al., WUR) reports ginger and yam fields are rotated in Kachia but does not name the next crop.
- The zone crop lists are unverified.

**Field checks of the risk lines:**
- GBIF holds only 144 Striga hermonthica records for all of Nigeria (7 in Kaduna) and 89 fall armyworm records with no state, checked 14 Nov 2026. That is too few to test LGA cut-offs.
- No open Kaduna aflatoxin or crop-disease survey found. The humid-spell and late-dry-spell thresholds stay design choices until our own survey (or an extension-office record) gives field points.

## Serving it (`POST /api/score`)

The deployed app does not read `data/` (`.vercelignore` drops it, and `data/raw/` is not in git). `python -m model.build` ends by writing the engine's tables to `api/engine_inputs/` (nine CSVs, about 150 KB), and the app reads only those. `python -m model.engine_data` rewrites them alone. Two tests guard the copy: the bundle must score all 23 LGAs exactly as the pipeline outputs do, and the committed bundle must match the current outputs.

- Body: the engine's `Request` (LGA, soil, water, equipment, last one or two seasons, up to three priorities, where the farmer sells, season type).
- Response: `ScoreResponse` in `api/schemas.py`: Current, Option A and Option B cards, the Striga risk, the weights, whether the top two are a close call, and why each other rotation was dropped.
- An unknown LGA or priority returns 422.
- The bundle loads once per process (about 0.1 s); a request then takes about 40 ms.
- The app now imports numpy, pandas and pyyaml. Installed with fastapi, jinja2, pydantic and uvicorn they take about 150 MB, under Vercel's 250 MB function limit. No pyarrow: the bundle is CSV.
