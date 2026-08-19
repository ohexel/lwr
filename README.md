# Local heat stress in Berlin
Geospatial data pipeline that combines Berlin population and administrative data with weather data and forecasts. Produces neighborhood-level heat stress indicators for urban planners.

## Data
### Lebensweltlich orientierte Räume (LOR)
[Berlin Open Data platform](https://daten.berlin.de/datensaetze/lebensweltlich-orientierte-raume-lor-01-01-2021-wfs-34c86848)

Fine-grained local geometries used by Berlin's local government for planning purposes.

### Population by LOR
[Berlin Open Data platform](https://daten.berlin.de/datensaetze/einwohnerinnen-und-einwohner-in-berlin-in-lor-planungsraumen-am-31-12-2025)

Population data by LOR provided by Amt für Statistik Berlin-Brandenburg.

### ICON-D2-RUC
[Source link](https://dwd-geoportal.de/products/iconruc/)

Fine-grained weather forecast for Germany. This project uses the lead-time 0 estimate as the model-based estimate for current local conditions.

Spatial grid used for ICON D2: [Source link](http://icon-downloads.mpimet.mpg.de/dwd_grids.xml#grid47)
