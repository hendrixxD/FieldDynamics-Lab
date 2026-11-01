# FieldDynamics-Lab

Our entry for the **2026 NASA Space Apps Challenge**, **Field Shift: Adapting Farms with NASA Data** (FieldShift).

We are building a decision-support tool that helps farmers explore crop rotation strategies, using NASA Earth observations together with local soil information, crop characteristics, and farmer priorities. Our state of interest is **Kaduna, Nigeria**.

The tool supports farmers' judgment and local expertise. It does not replace them.

- [First principle validative measure](docs/first-principle-validative-measure.md): the official challenge statement, which we use to check we aren't drifting from the core expectation
- [Competition requirements](docs/competition-requirements.md): the team's working breakdown of the brief
- [Deliverables](docs/deliverables.md): the checklist that guides what we build
- [Tech stack](docs/tech-stack.md): the tools for each part of the build
- [Model evaluation](docs/model-evaluation.md): how we test each model, with metrics and tools
- [Map architecture](docs/map-architecture.md): map layers, the PNG and GeoJSON split, and API endpoints

## Challenge summary

### Core requirement (non-negotiable)

- A decision-support tool, such as an app, model, or interactive map
- Uses NASA Earth observations
- Combines them with local soil information, crop characteristics, and farmer priorities
- Helps farmers explore rotations that improve soil health and adapt to changing conditions
- Supports farmer judgment and local expertise rather than replacing them

### What the judges will check for

- At least three rotation strategies compared, including the farmer's existing one
- For each strategy: why it was suggested, supporting evidence, benefits, information gaps, and risks needing local review
- Farmer inputs for priorities and constraints (water, equipment, market)
- Bare-field periods, vulnerable seasons, and pest-cycle breaks
- How the model handles uncertainty, and where human judgment matters
- Plain-language explanations of terms such as NDVI, evapotranspiration, and soil moisture anomalies
- Satellite versus ground data, and why combining them helps
- A clear "this supports, not replaces, your judgment" framing

### Objectives (most required)

**Rotation comparison.** Compare at least three strategies, one of them the farmer's existing rotation. Show the why, evidence, benefits, information gaps, and local-review risks for each.

**Tool highlights.** Show when fields are typically bare, which seasons leave current crops most vulnerable, and rotation opportunities that could interrupt pest cycles, reduce water demand, improve infiltration, or add supplemental income.

**Patterns to analyze from NASA data.** Temperature, heat stress, rainfall, soil moisture, evaporation, frost risk, extreme precipitation, vegetation stress, and long-term water trends.

**Farmer priorities to weigh.** Water use, forage value, nitrogen-fixing potential, erosion reduction, organic matter improvement, and market suitability.

**Named NASA datasets.** Use as many as are genuinely useful:

| Dataset |
| --- |
| SMAP |
| ECOSTRESS |
| GPM |
| Landsat |
| MODIS |
| NASA POWER |
| GRACE / GRACE-FO |
| SRTM or other NASA elevation products |
| GEDI (mentioned in considerations) |

### Tier 2: Potential considerations

- **Conditions and data:** relate weather, soil moisture, and vegetation stress to crop vulnerability and soil health. Explain evapotranspiration, soil moisture anomalies, NDVI, frost risk, and water balance indicators in plain language, along with how satellite data differs from ground measurements.
- **SI, modeling, and data fusion:** compare SI or statistical approaches, use SI to highlight seasonal transitions, bare-field periods, and crop stress periods, explain how the model handles uncertainty, show where local expertise must validate results, and harmonize datasets to a common analysis grid.
- **Rotation exploration:** compare scenarios on nutrient cycling, erosion risk, organic matter, pest-cycle disruption, water use, forage potential, and market considerations. Map rotations to farmer priorities, and briefly explain how nitrogen-fixing, cover, deep-rooting, and drought-tolerant crops build soil health.
- **Mapping and visualization:** interactive maps of long-term trends, field-level vulnerability zones for erosion, water stress, and frost, plus optional timelines, dataset overlays, and rainfall or temperature scenario sliders.
- **Farmer-centered design:** easy to interpret with limited technical experience, with inputs for priorities and constraints, reminders that the tool supports local expertise, and flags where human judgment matters most.

### Tier 3: Submission requirements

- Demo: a video of up to 30 seconds **or** a slide deck of up to 7 slides. Going over disqualifies us from global judging.
- All links public and working
- Clear statement of which NASA and partner datasets were used
- Link to code in a public repository
- Every required field on the project page completed
- Terms and submission requirements checkbox ticked

## Why Kaduna

- **The rotation question is real.** Kaduna is in the northern Guinea savanna, part of Nigeria's maize belt, where farmers rotate or intercrop maize with soybean, cowpea, and sorghum.
- **It's a major grain belt.** The state is among Nigeria's top producers of maize, soybean, and sorghum, so rotation choices matter at scale.
- **There's a validation base.** The Institute for Agricultural Research (IAR) at Samaru and Ahmadu Bello University in Zaria have run rotation and soil fertility trials for decades, giving us published results to check our engine against.
- **Climate pressure is visible.** Rain onset is shifting, dry spells are getting worse, and soils are degrading.
- **It's a transition zone.** Kaduna sits between the wetter south and the drier north, so a tool proven there carries over to both and supports an Africa-ready pitch.
- **There's an extension network.** The state agricultural development programme gives us a real path to farmers.
- **Agritech is already active.** Firms working across the North, like ThriveAgri, show farmers there can be reached digitally.
- **There's a market angle.** Soybean feeds Kaduna's poultry and processing industries, which fits the supplemental income objective.
