# First Principle Validative Measure

This is the official challenge statement. We use it to check that our work is not drifting from the core of what is expected.

We treat its "you may" and "consider" items as expected deliverables, not optional extras. Our [deliverables list](deliverables.md) was built from this document and deliberately covers more, because something not being mentioned here does not mean the judges won't ask about it. That list guides what we build.

- **Original PDF:** [first-principle-validative-measure.pdf](first-principle-validative-measure.pdf)
- **Source:** https://www.spaceappschallenge.org/2026/challenges/adapting-farms-with-nasa-data/?tab=details
- **Captured:** 30 October 2026, Details tab only. The Resources and Teams tabs are not included.

The text below is a verbatim transcription.

---

# Field Shift: Adapting Farms with NASA Data

**Event:** 2026 NASA Space Apps Challenge

**Difficulty:** Advanced, Intermediate

**Subjects:** Arts & Multimedia, Earth Science, Software

Farmers around the world navigate shifting temperatures, changing rainfall patterns, water shortages, extreme weather, and declining soil health. These pressures make it difficult to choose crop rotations that protect fields, conserve water, and support long-term resilience. Your challenge is to create a decision-support tool that uses NASA Earth observations along with local soil information, crop characteristics, and farmer priorities to help farmers explore rotation strategies that could strengthen soil health and adapt their farms to changing conditions.

## Background

Farmers face increasingly complex decisions as temperatures shift, rainfall becomes less predictable, and soil health declines across many regions. Water shortages, erosion, nutrient depletion, and extreme weather events add further uncertainty. Selecting new crops to add to a rotation can be difficult, because a crop that improves soil health in one place may consume too much water, introduce disease, conflict with local markets, or fall outside expected growing seasons somewhere else.

This challenge is inspired in part by multigenerational farming experience — many families have lived through seasons where better tools for tracking changing conditions could have helped identify healthier, more resilient rotation choices. Today, NASA's long-term environmental datasets offer new ways to understand local growing conditions, and SI techniques open opportunities to translate that data into practical, farmer-friendly insights.

## Objectives

You may consider combining NASA Earth observations with local agricultural information to create an app, model, interactive map, or other decision-support tool. The goal is to help farmers explore crop-rotation options that may improve soil health and strengthen resilience to changing weather and water conditions.

You may explore NASA datasets such as SMAP, ECOSTRESS, GPM, Landsat, MODIS, NASA POWER, GRACE and GRACE-FO, and SRTM or other NASA elevation products. Using these datasets, you may analyze patterns in temperature, heat stress, rainfall, soil moisture, evaporation, frost risk, extreme precipitation, vegetation stress, and long-term water trends. You may also consider local soil information, crop characteristics, and farmer priorities—such as water use, forage value, nitrogen-fixing potential, erosion reduction, organic matter improvement, or market suitability.

Your tool may highlight times when fields are typically bare, seasons when current crops appear more vulnerable, or opportunities for rotations that could interrupt pest cycles, reduce water demand, improve infiltration, or add supplemental income. You may compare at least three rotation strategies, including the existing rotation. For each strategy, you may explain why it was suggested, what evidence supports it, what benefits it may offer, where information gaps exist, and any risks that would need local review by farmers or agricultural experts.

The intended outcome is a supportive tool — one that helps identify patterns and possibilities, but does not replace farmer judgment or local agricultural expertise.

## Potential Considerations

*You may (but are not required to) consider the following:*

### Understanding Conditions & Data

- Explore how changes in temperature, soil moisture, rainfall, evaporation, and vegetation stress relate to crop vulnerability and soil health.
- Consider explaining scientific terms such as evapotranspiration, soil moisture anomalies, vegetation indices (e.g., NDVI), frost risk, and water-balance indicators.
- Clarify how satellite observations differ from ground-based measurements, and how combining both may improve accuracy.

### SI, Modeling, and Data Fusion

- You may compare different SI or statistical approaches for identifying patterns in historical climate and soil conditions.
- Consider how SI could highlight seasonal transitions, bare-field periods, or times when current crops show increased stress.
- Explain how the model interprets uncertainty and where local expertise would be needed to validate results.
- You may consider harmonizing datasets with different spatial and temporal resolutions by downsampling, aggregating, or resampling them to a common analysis grid. Harmonizing resolution can help the SI combine SMAP soil moisture, ECOSTRESS land-surface temperature, GPM precipitation, Landsat/MODIS vegetation indices, GEDI biomass, and soil datasets into coherent features that support clear, explainable rotation-strategy comparisons.

### Rotation Strategy Exploration

- Compare rotation scenarios by examining nutrient cycling, erosion risk, organic matter contributions, pest-cycle disruption, water use, forage potential, or market considerations.
- You may consider mapping which rotation ideas best align with farmer priorities—such as conserving water, generating supplemental income, or improving soil structure.
- Include brief explanations of how nitrogen-fixing crops, cover crops, deep-rooting crops, or drought-tolerant crops contribute to long-term soil health.

### Geospatial Mapping & Visualization

- Consider whether interactive maps could help users explore trends over time such as long-term soil moisture, heat waves, or water-storage declines.
- Explore ways to visualize field-level vulnerabilities (e.g., zones of erosion risk, water stress hotspots, frost-prone areas).
- You may include optional add-ons such as timelines, overlays comparing datasets, or scenario sliders that adjust rainfall or temperature patterns.

### Farmer-Centered Design

- Consider how to make the tool easily interpretable for farmers who may have limited technical or geospatial experience.
- Think about ways to allow farmers to input their own priorities or constraints, such as water availability, equipment limitations, or market preferences.
- Share reminders that the tool is intended to support—not replace—local expertise, and highlight where human judgment is particularly important.

*For data and resources related to this challenge, refer to the Resources tab at the top of the page. More resources may be added before the hackathon begins.*
