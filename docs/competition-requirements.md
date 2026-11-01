# Competition Requirements

NASA Space Apps Challenge 2026: FieldShift challenge.

## What the judges will check for

- Compare at least three rotation strategies, including the farmer's existing one
- For each strategy: why it was suggested, supporting evidence, benefits, information gaps, and risks needing local review
- Farmer inputs for priorities and constraints (water, equipment, market)
- Surface bare-field periods, vulnerable seasons, and pest-cycle breaks
- Show how the model handles uncertainty and where human judgment matters
- Plain-language explanations of terms like NDVI, evapotranspiration, soil moisture anomalies, etc.
- Explain satellite vs ground data and why combining them helps
- A clear "this supports, not replaces, your judgment" framing

## Core requirement (non-negotiable)

- A decision-support tool (app, model, interactive map, or similar)
- Uses NASA Earth observations
- Combines them with local soil information, crop characteristics, and farmer priorities
- Helps farmers explore crop rotation strategies that improve soil health and adapt to changing conditions
- Positioned as supportive: does not replace farmer judgment or local expertise

## Objectives (most required)

### Rotation comparison

- Compare at least three rotation strategies
- One of them is the farmer's existing rotation

For each strategy, show:

- Why it was suggested
- What evidence supports it
- What benefits it may offer
- Where information gaps exist
- Risks that need local review by farmers or agricultural experts

### Tool highlights

- Times when fields are typically bare
- Seasons when current crops are more vulnerable
- Rotation opportunities that could:
  - Interrupt pest cycles
  - Reduce water demand
  - Improve infiltration
  - Add supplemental income

### Patterns to analyze (from NASA data)

- Temperature
- Heat stress
- Rainfall
- Soil moisture
- Evaporation
- Frost risk
- Extreme precipitation
- Vegetation stress
- Long-term water trends

### Farmer priorities the tool can weigh

- Water use
- Forage value (the nutritional worth, digestibility, and economic value of plant material, such as grasses, legumes, or silages, used to feed livestock)
- Nitrogen-fixing potential
- Erosion reduction
- Organic matter improvement
- Market suitability

### Named NASA datasets (use as many as are genuinely useful)

- SMAP
- ECOSTRESS
- GPM
- Landsat
- MODIS
- NASA POWER
- GRACE / GRACE-FO
- SRTM or other NASA elevation products
- GEDI (mentioned in considerations)

## Tier 2: Potential considerations

### Understanding conditions and data

- Relate temperature, soil moisture, rainfall, evaporation, vegetation stress to crop vulnerability and soil health
- Plain-language explanations of: evapotranspiration, soil moisture anomalies, NDVI, frost risk, water balance indicators
- Explain how satellite data differs from ground measurements, and why combining them helps

### SI, modeling, data fusion

- Compare different SI or statistical approaches
- Use SI to highlight seasonal transitions, bare-field periods, crop stress periods
- Explain how the model handles uncertainty
- Show where local expertise is needed to validate results
- Harmonize datasets to a common analysis grid (resample, aggregate, downsample)

### Rotation strategy exploration

- Compare scenarios on: nutrient cycling, erosion risk, organic matter, pest-cycle disruption, water use, forage potential, market considerations
- Map which rotations best match farmer priorities (conserve water, extra income, soil structure)
- Brief explanations of how nitrogen-fixing, cover, deep-rooting, and drought-tolerant crops build soil health

### Geospatial mapping and visualization

- Interactive maps of long-term trends (soil moisture, heat waves, water storage decline)
- Field-level vulnerability zones: erosion risk, water stress hotspots, frost-prone areas
- Optional add-ons: timelines, dataset overlays, scenario sliders for rainfall or temperature

### Farmer-centered design

- Easy to interpret for farmers with limited technical experience
- Farmers can input their own priorities and constraints (water availability, equipment, market preferences)
- Reminders that the tool supports, not replaces, local expertise
- Flag where human judgment is especially important

## Tier 3: Submission requirements (from the Space Apps submission guide)

- Demo: video up to 30 seconds OR slide deck up to 7 slides (going over disqualifies us from global judging)
- All links public and working
- Clearly state which NASA and partner datasets were used
- Link to code in a public repository
- Complete every required field on the project page
- Tick the terms and submission requirements checkbox

## State of interest: Kaduna

Three reasons.

First, the rotation question is very real there. It's in the northern Guinea savanna, part of Nigeria's maize belt, where farmers rotate or intercrop maize with soybean, cowpea, and sorghum. That's exactly the legume-cereal rotation story the brief describes.

Second, the climate pressure is visible: rain onset is shifting, dry spells are getting worse, and soils are degrading.

Third, and this is the big one, it has evidence. The Institute for Agricultural Research at Samaru, in Zaria, has run crop rotation trials for decades. That gives you published results to check your engine against, which goes a long way toward closing your validity gap.

- **It's a major grain belt.** Kaduna is among Nigeria's top producers of maize, soybean, and sorghum, so the rotation choices matter at scale.
- **There's a validation base.** IAR Samaru and Ahmadu Bello University in Zaria have run rotation and soil fertility trials for decades.
- **Climate pressure.** Rain onset is shifting, dry spells are getting worse, and the soils are degrading.
- **It's a transition zone.** Kaduna sits between the wetter south and the drier north. A tool proven there carries over to both, which helps your Africa-ready pitch.
- **There's an extension network.** The state agricultural development programme gives you a real path to farmers.
- **Agritech is already active.** Firms working across the North, like ThriveAgri, show farmers there can be reached digitally.
- **There's a market angle.** Soybean feeds Kaduna's poultry and processing industries, which fits the "supplemental income" objective.
