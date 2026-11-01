# Map Architecture

## What the map needs

| Layer | Service | Notes |
| --- | --- | --- |
| Basemap (streets) | OpenStreetMap or CARTO Positron tiles | Free, no key. Positron is light and lets your data layers stand out |
| Basemap (satellite view) | Esri World Imagery | Free for this use, needs attribution. Lets farmers recognise their own fields |
| Live NASA imagery | NASA GIBS (WMTS tiles) | Free, no key. Shows MODIS true colour, NDVI, and more directly from NASA. Strong signal to judges that NASA data is in the map itself |
| Boundaries (state, LGAs) | geoBoundaries GeoJSON | Downloaded once, served as a static file |
| Analysis layers (soil moisture, heat stress, erosion risk, water stress) | Your own precomputed data, served by FastAPI | See below |

## Analysis layers

PNG overlays to show, a GeoJSON grid to tap, switched by zoom level. The farmer sees one seamless map; under the hood, two formats each do the job they're good at.

### How it works

**State view (zoomed out): PNG overlays only.**
The whole of Kaduna shows as coloured images, one per layer (soil moisture, heat stress, erosion risk, water stress). Nothing is clickable except LGA boundaries. Tapping an LGA zooms into it.

**LGA view (zoomed in): PNG plus a clickable grid.**
Once an LGA is selected, the app loads a GeoJSON grid for that LGA only, drawn as transparent cells on top of the PNG. The farmer still sees the PNG colours; the grid just catches taps. Tapping a cell opens its values and links to the strategy cards for that spot.

### Why this split

- **PNGs are for seeing.** One image covers the whole state in a few hundred kilobytes, whatever the resolution. That keeps the first load fast on a cheap phone and a weak network.
- **GeoJSON is for tapping.** Every cell is an object Leaflet can click, but thousands of them for a whole state would freeze a phone. Loading one LGA at a time keeps it to a manageable number of cells.
- **The grid carries the numbers, the PNG carries the colours.** So you never have to style thousands of polygons in the browser, and the map always looks right even before the grid loads.

### Resolution

| View | Grid | Why |
| --- | --- | --- |
| State | 5 km | Enough to show patterns; keeps the PNG crisp and the files small |
| LGA | 1 km | Close to field scale for smallholders; roughly 1,000 to 3,000 cells per LGA, which phones handle fine |

Be honest in the map legend: SMAP soil moisture is natively about 9 km, so at 1 km it's resampled, not new detail. Label each layer with its native resolution. Judges will respect that.

### What FastAPI serves

| Endpoint | Returns | Used when |
| --- | --- | --- |
| `/static/layers/state/{layer}.png` | Coloured state image | Map first loads |
| `/static/layers/lga/{lga}/{layer}.png` | Coloured LGA image | LGA selected |
| `/layers/{lga}/grid.geojson` | Cells with all values as properties | LGA selected |
| `/point?lat=&lon=` | Values for one location | Fallback tap outside the grid, or a farmer pinning their field |
| `/legend/{layer}` | Colour scale and units | Legend panel |

All PNGs and GeoJSON are precomputed by the data pipeline and stored as static files. Only `/point` and the scoring run live.

### How the pipeline produces them

1. Harmonized rasters (from the data track) are clipped to the state and to each LGA.
2. A Python script applies one fixed colour scale per layer and writes the PNGs, plus the bounding box for each.
3. The same script turns each LGA's 1 km raster into grid cells and writes the GeoJSON, with every layer's value stored on each cell.
4. One command regenerates everything, so if the model changes on day 1, the map updates with it.

### One rule for the team

The colour scale lives in one config file, used both by the PNG script and by the legend endpoint. If those two drift apart, the map shows one thing and the legend says another, and that's the kind of bug a judge notices in a 30-second video.
