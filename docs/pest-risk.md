# Pest and disease risk: evidence for Kaduna and design

Status: design, 14 Nov 2026. Nothing here is built yet. It consolidates a literature and news review of pests and diseases in Kaduna with the three pest features proposed for the app, and checks each against the data the repo already holds.

The rule for every line below: the app may say a risk is higher or lower because of soil, weather or a farmer's own field history. It may never say a pest is present. No dataset in this repo observes pests.

## 1. What hits Kaduna farms

| Pest or disease | Crops | Kaduna evidence | What drives it | Can our data see the driver? |
|---|---|---|---|---|
| Striga hermonthica (witchweed) | maize, sorghum, millet | Kaduna farmer study: 69% of fields infested, about 47% maize yield loss. Northern Guinea savanna field surveys: about 74% of cereal fields infested. | Low soil N and organic matter, sandy soil, continuous cereals. Sorghum is the main host for seed production. Drought makes the damage worse. | Partly. Soil from iSDA (sand, total N, organic C); drought from A2 dry spells; cereal history only from the farmer. |
| Striga gesnerioides | cowpea | Worst in the northern Guinea savanna, where most of Kaduna sits. | Host: cowpea. | Only from the farmer (crop history). |
| Fall armyworm | maize, sorghum | Arrived in Nigeria in 2016. NAERLS rated Kaduna's 2022 wet-season infestation heavy. Damage is worst 2 to 6 weeks after planting. | Present every year since 2016. Dry-season maize is hit hardest. Within the rainy season the weather link is weak in the literature. | No. There's no reliable weather signal for it in the rainy season. |
| Maize leaf blights, rust, Curvularia leaf spot | maize | Recorded in farmers' fields in Samaru, Zaria. Southern corn leaf blight, northern corn leaf blight and Curvularia are endemic to the Guinea savanna. | Warm temperatures, high humidity and long leaf wetness. | Partly. POWER has daily RH2M and T2M, but rainy-season humidity is 77 to 87% in every LGA, so only an anomaly can separate years. |
| Ginger blight (fungal) | ginger | 2023 epidemic in 7 southern LGAs (Kachia, Zangon Kataf, Kaura, Kagarko, Jema'a, Jaba, Sanga): 80 to 95% losses, about N12 billion. It returned in 2026. | Wet conditions, low ground, poor drainage, continuous cropping. Air- and soil-borne. Crop rotation is the advice. | Weather yes. Ginger is outside the crop list (`config/crops.yaml`), so this stays on the Learn page. |
| Cowpea flower thrips, Maruca pod borer, pod-sucking bugs | cowpea | The main cowpea pests in the Nigerian savannas. Losses are worst after flowering. | Crop stage. One spray at flowering raised yield 75% in trials. | Crop stage yes, from the A9 calendar. |
| Aflatoxin (Aspergillus flavus) | groundnut | Pre-harvest in the drier northern Guinea savanna. | Drought or erratic rain near maturity. | Yes. A2's late-season dry spells and cessation date. |
| Tuta absoluta (tomato leaf miner) | tomato | 2016: about 80% of Kaduna's tomato crop was lost (Makarfi, Soba and other areas). | Dry-season irrigated tomato. | Out of scope: tomato isn't a rotation crop here. |

## 2. Feature 1: Striga risk per field (changes the rotation score)

**Soil base (per LGA, from iSDA 0 to 20 cm, cropland means).** Three soil properties count against a field: more sand, less total N and less organic C. Each scores 0 to 2 by Kaduna tercile, and the soil base is the rounded mean of the three.

Honest limit: Kaduna's LGAs differ little on these. Sand runs 49 to 64% (coefficient of variation 0.06), total N 0.48 to 0.80 g/kg (0.17) and organic C 4.9 to 8.0 g/kg (0.15). Every LGA sits in the sandy, low-N range where Striga thrives, so the soil base ranks LGAs against each other. It doesn't separate safe soils from risky ones.

**Field history (per farm, from the farmer).** Count the cereal seasons in a row on this field: maize, sorghum or millet. A sorghum season counts 1.5, because sorghum is the main host for Striga seed production. This is the part that really separates one field from another.

**Risk level (proposed).**
- High: 2 or more cereal seasons in a row, or 1 or more with a soil base of 2.
- Medium: 1 cereal season.
- Low: a legume or fallow last season.

These cut-offs are a design choice, not from the literature. They go in `config/` marked as such.

**Effect on the engine.** At High, the score adds weight to a legume next season.
- Soybean is preferred: in northern Guinea savanna trials, one legume year cut Striga 35% and two cut it 76%, and soybean beat cowpea at both.
- If cowpea is the legume, the card names Striga gesnerioides and suggests a resistant variety.
- At High, a cereal-only plan is flagged.

**Validation: none possible with current data.** There are no LGA Striga survey points in the repo. The card says "risk from your soil and your field's history", never "infested". If IITA or ABU Zaria field survey points can be obtained, the soil base can be checked against them.

## 3. Feature 2: weather-driven risk lines (on the card, not in the score)

Each line uses data the repo already holds, and each is relative to the LGA's own normal (A3's approach). The thresholds below are proposals; none is in the literature as a number.

1. **Dry spell during a cereal season → Striga damage worse.** The source is A2's 10+ day dry spells inside the crop's season (A9 calendar). The evidence is direct: drought on Striga-infested maize increases damage. The line shows only when the field's Striga risk is Medium or High.
2. **Humid spell → maize leaf disease.** The source is POWER daily RH2M and T2M during the maize season. Count days whose humidity is above that LGA-month's 90th percentile, with mean temperature 20 to 32 °C. A run of 5 or more such days triggers the line. Because rainy-season humidity is high everywhere, a fixed humidity threshold would fire every year in every LGA. Only the anomaly is informative.
3. **Late-season dry spell for groundnut → aflatoxin.** The source is A2: a dry spell or early cessation in the last 3 to 4 weeks before groundnut harvest (A9 calendar).
4. **Ginger blight (Learn page, southern LGAs).** Wet-season conditions plus the 2023 and 2026 outbreaks. The advice is rotation and drainage.

Standing scouting lines, which need no weather:
- **Maize:** fall armyworm, 2 to 6 weeks after planting.
- **Cowpea:** thrips and pod borer at flowering.

These are the highest-value pest messages for Kaduna. They come from crop stage alone, which the app already computes.

## 4. Feature 3: crop stress flag and the Learn page

A6 doesn't predict stress: no model beats each LGA's normal (`docs/model-results.md`). The flag must therefore show the **observed** record: the share of past years each LGA had stressed greenness, by season type (`a6_lga_season_type_stress.csv`, observed columns). It must not show a forecast.

Learn text (proposed):

> Satellites can see when crops are less green than usual. They cannot see why. Dry weather, poor soil, Striga, fall armyworm and disease all look the same from space. If your crop looks weaker than your neighbours', check for pests with your extension officer.

## 5. Can a better A6 target fix A6? Tested, 14 Nov 2026

Each candidate was scored with leave-one-year-out Ridge on A6's weather features (all logged in `docs/experiments.csv`). Skill above 0 means better than predicting each LGA's normal.

| Target | Skill | Verdict |
|---|---|---|
| Aug to Sep peak NDVI (current) | −0.040 | no skill |
| Jun to Oct mean NDVI | +0.113 | artefact: drops to +0.011 without the SMAP-missing flag |
| Jun to Jul mean NDVI | +0.210 | artefact: drops to −0.025 detrended; early-season NDVI falls with year (r = −0.78) |
| VIIRS peak NDVI (13 years) | −0.152 | no skill |
| MODIS ET/PET season anomaly | −0.075 | no skill |
| HLS peak minus dry (11 years) | −0.323 | no skill |
| Peak NDVI with A9 maize stress days added | −0.040 | A9 stress occurs in 3.8% of LGA-years, too rare to help |

The apparent skill came from the `smap_missing` indicator. It flags the years before 2015, so it works as an era marker, tracking a falling trend in early-season greenness rather than stress. No available target gives real skill. What would change this is ground truth that isn't in the repo: LGA or field yields, or crop-cut surveys. Until then, A6 stays a documented baseline.

Two follow-ups:
- The falling early-season greenness trend was checked on 14 Nov 2026: not sensor drift, most likely land conversion (`docs/model-results.md`). It stays off the cards.
- `smap_missing` was dropped from A6 on 14 Nov 2026 after an ablation test (`docs/model-results.md`).

## Sources

- Kaduna Striga study (farmer perception, 42 Kaduna communities): https://www.researchgate.net/publication/231908741
- Striga population dynamics, northern Guinea savanna: https://www.sciencedirect.com/science/article/abs/pii/026121949400004R
- Striga infestation by savanna zone, northeast Nigeria: https://cgspace.cgiar.org/items/e30eaa9e-231b-4c48-89c0-7e6fe8a3b688
- Cereal-legume rotation and Striga, northern Guinea savanna: https://core.ac.uk/works/82334507
- Striga and soil fertility: https://stud.epsilon.slu.se/4488/1/larsson_m_120704.pdf ; https://www.biorxiv.org/content/10.1101/2025.10.03.680328v1.full
- Drought and Striga combined (Menkir et al. 2020): https://www.frontiersin.org/journals/plant-science/articles/10.3389/fpls.2020.00166/epub
- NAERLS 2022 wet-season performance survey: https://naerls.gov.ng/wp-content/uploads/2025/04/Agricultural-Performance-Survey-of-2022-Wet-Season-in-Nigeria.pdf
- Fall armyworm in Nigeria: https://www.researchgate.net/figure/Survey-fields-and-severity-of-damage-due-to-fall-armyworms_fig1_372546232 ; Kano survey: https://saheljls.fudutsinma.edu.ng/index.php/saheljls/article/download/649/527/1546
- Maize leaf blight, Samaru: https://fjs.fudutsinma.edu.ng/index.php/fjs/article/download/4795/3132/13870 ; foliar diseases by zone: https://link.springer.com/article/10.1007/s42976-023-00395-1
- Ginger blight: https://dailytrust.com/?p=1947929 ; https://allafrica.com/stories/202406130023.html ; https://thisdaylive.com/index.php/2024/03/26/ginger-disease-outbreak-fg-floats-n1-6bn-recovery-package-for-farmers ; https://agrifocusafrica.com/2023/12/29/ravaging-diseases-plunge-farmers-into-n12bn-loss-face-loan-default/
- Cowpea pests: https://cgspace.cgiar.org/items/d4102218-6607-41a7-8419-3a09635d5435
- Aflatoxin in Nigerian groundnut: https://agris.fao.org/search/en/records/6471d3e969d6cbfdd4a30f30
- Tuta absoluta, Kaduna 2016: https://wire.farmradio.fm/farmer-stories/nigeria-tomato-farmers-struggle-to-combat-pest-infestation/ ; https://nifst.org/?p=7765
