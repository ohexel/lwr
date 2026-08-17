# Project scope
## Geographical scope
**Summary**: Highly granular but local > Coarse but global (national)

I decided to focus on Berlin instead of all of Germany for several reasons:

1. High geospatial granularity: Berlin provides sub-Gemeinde geospatial objects for analysis. This enables more granular analyses than using the Germany-wide Gemeinde or Zensusgitter data. It also makes for a more interesting engineering challenge.
2. Range and diversity of indicators is more important than geographic coverage.
3. High-quality local data: Berlin's open data infrastructure is excellent. 

## Domain scope
My initial ideas were motivated by two observations:

1. there is a lot of open data at the sub-national level in Germany but it is usually created, provided, and maintained by local, disconnected initiatives
2. there is a lot of open weather and climate data

Regarding the second point, it being summer and the headlines being full of news about wildfires and heat deaths also played a role.

The next question became: how do I make this useful beyond my personal curiosity? I decided to:

- focus on a small number (but >1) of weather/climate indicators; keeping a tight focus makes the project more legible and respects the time constraint; but focusing only on one or two indicators makes it too much of a toy example and undermines the domain and technical learning goals
- focusing on a small area but with a range of material and sociodemographic dimensions more realistically serves the likely complexity that end users face
