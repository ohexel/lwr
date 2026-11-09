"use strict";

const SVG_NS = "http://www.w3.org/2000/svg";
const MAP_WIDTH = 760;
const MAP_HEIGHT = 620;
const state = {
  areas: [],
  byId: new Map(),
  shapes: new Map(),
  detailCache: new Map(),
  activeId: null,
  pinned: false,
  hoverTimer: null,
  requestNumber: 0,
  runTime: null,
  metric: "maximum_apparent_temperature_difference_c",
};

const elements = {};

document.addEventListener("DOMContentLoaded", initialize);

async function initialize() {
  for (const id of [
    "run-status", "area-count", "year-count", "map-metric", "area-search",
    "search-results", "map-wrap", "berlin-map", "map-loading",
    "legend-gradient", "legend-low", "legend-high", "detail-placeholder",
    "detail-content", "detail-plr-id", "detail-name", "pin-toggle",
    "metric-maximum", "metric-maximum-at", "metric-difference",
    "metric-difference-at", "metric-sum", "metric-population",
    "metric-population-total", "temperature-chart-section", "temperature-chart-title",
    "chart-date", "chart-wrap", "temperature-chart", "chart-tooltip", "chart-loading",
    "temperature-history-legend", "temperature-comparison-legend", "temperature-history-note",
    "apparent-chart-section", "apparent-chart-date", "apparent-chart-wrap",
    "apparent-temperature-chart", "apparent-chart-tooltip", "apparent-chart-loading",
    "apparent-history-legend", "apparent-comparison-legend", "apparent-history-note",
  ]) {
    elements[id] = document.getElementById(id);
  }

  elements["map-metric"].addEventListener("change", event => {
    state.metric = event.target.value;
    updateMapColors();
    if (state.activeId && state.detailCache.has(state.activeId)) {
      renderChart(state.detailCache.get(state.activeId));
    } else {
      updateChartLayout();
    }
  });
  elements["area-search"].addEventListener("input", updateSearchResults);
  elements["area-search"].addEventListener("focus", updateSearchResults);
  elements["pin-toggle"].addEventListener("click", togglePin);
  document.addEventListener("keydown", event => {
    if (event.key === "Escape") releasePin();
  });
  document.addEventListener("click", event => {
    if (!event.target.closest(".search-box")) hideSearchResults();
  });

  try {
    const response = await fetch("data/map.json", { cache: "no-store" });
    if (!response.ok) throw new Error(`Map data returned HTTP ${response.status}`);
    const payload = await response.json();
    validateMapPayload(payload);
    state.areas = payload.areas;
    state.byId = new Map(payload.areas.map(area => [area.id, area]));
    state.runTime = payload.run_time_berlin;
    elements["area-count"].textContent = formatInteger(payload.areas.length);
    elements["year-count"].textContent = payload.historical_year_count || "—";
    elements["run-status"].textContent = `Model run ${formatLocalDateTime(payload.run_time_berlin)} Berlin time`;
    document.querySelector(".status-dot").classList.add("ready");
    drawMap();
    updateMapColors();
    elements["map-wrap"].setAttribute("aria-busy", "false");
    elements["map-loading"].hidden = true;
  } catch (error) {
    elements["map-loading"].textContent = `Dashboard data unavailable. ${error.message}`;
    elements["run-status"].textContent = "Export the dashboard data and reload";
    console.error(error);
  }
}

function validateMapPayload(payload) {
  if (payload.format_version !== 2 || !Array.isArray(payload.areas) || payload.areas.length === 0) {
    throw new Error("The map export does not satisfy dashboard format version 2.");
  }
  for (const area of payload.areas) {
    if (!area.id || !area.name || !area.geometry) {
      throw new Error("A planning area is missing its ID, name, or geometry.");
    }
  }
}

function drawMap() {
  const svg = elements["berlin-map"];
  svg.replaceChildren();
  const bounds = geometryBounds(state.areas.map(area => area.geometry));
  const projection = createProjection(bounds, MAP_WIDTH, MAP_HEIGHT, 20);
  const ordered = [...state.areas].sort((a, b) => a.id.localeCompare(b.id));

  for (const [index, area] of ordered.entries()) {
    const path = svgElement("path", {
      d: geometryPath(area.geometry, projection),
      class: "plr-shape",
      role: "button",
      tabindex: index === 0 ? "0" : "-1",
      "data-plr-id": area.id,
      "aria-label": `${area.name}, PLR ${area.id}`,
    });
    path.addEventListener("pointerenter", () => scheduleArea(area.id));
    path.addEventListener("focus", () => selectArea(area.id));
    path.addEventListener("click", () => selectArea(area.id, { pin: true }));
    path.addEventListener("keydown", event => handleMapKey(event, index, ordered));
    svg.append(path);
    state.shapes.set(area.id, path);
  }
}

function handleMapKey(event, index, ordered) {
  let nextIndex = null;
  if (["ArrowRight", "ArrowDown"].includes(event.key)) nextIndex = (index + 1) % ordered.length;
  if (["ArrowLeft", "ArrowUp"].includes(event.key)) nextIndex = (index - 1 + ordered.length) % ordered.length;
  if (nextIndex !== null) {
    event.preventDefault();
    state.shapes.get(ordered[index].id).tabIndex = -1;
    const nextShape = state.shapes.get(ordered[nextIndex].id);
    nextShape.tabIndex = 0;
    nextShape.focus();
  }
  if (["Enter", " "].includes(event.key)) {
    event.preventDefault();
    selectArea(ordered[index].id, { pin: true });
  }
}

function scheduleArea(plrId) {
  if (state.pinned) return;
  window.clearTimeout(state.hoverTimer);
  state.hoverTimer = window.setTimeout(() => {
    if (!state.pinned) selectArea(plrId);
  }, 70);
}

async function selectArea(plrId, options = {}) {
  if (!state.byId.has(plrId)) return;
  if (options.pin) {
    state.pinned = true;
    window.clearTimeout(state.hoverTimer);
  }
  state.activeId = plrId;
  updateSelectionStyles();
  renderSummary(state.byId.get(plrId));
  hideSearchResults();

  const requestNumber = ++state.requestNumber;
  elements["chart-loading"].hidden = false;
  elements["apparent-chart-loading"].hidden = false;
  elements["chart-wrap"].setAttribute("aria-busy", "true");
  elements["apparent-chart-wrap"].setAttribute("aria-busy", "true");
  try {
    const detail = await loadAreaDetail(plrId);
    if (requestNumber !== state.requestNumber || state.activeId !== plrId) return;
    renderChart(detail);
  } catch (error) {
    if (requestNumber !== state.requestNumber) return;
    renderChartError(error);
  } finally {
    if (requestNumber === state.requestNumber) {
      elements["chart-loading"].hidden = true;
      elements["apparent-chart-loading"].hidden = true;
      elements["chart-wrap"].setAttribute("aria-busy", "false");
      elements["apparent-chart-wrap"].setAttribute("aria-busy", "false");
    }
  }
}

async function loadAreaDetail(plrId) {
  if (state.detailCache.has(plrId)) return state.detailCache.get(plrId);
  const version = encodeURIComponent(state.runTime || "current");
  const response = await fetch(`data/areas/${plrId}.json?v=${version}`);
  if (!response.ok) throw new Error(`Neighborhood detail returned HTTP ${response.status}`);
  const detail = await response.json();
  if (
    detail.plr_id !== plrId ||
    detail.forecast_temperatures_c?.length !== 25 ||
    detail.forecast_apparent_temperatures_c?.length !== 25 ||
    detail.historical_median_temperatures_c?.length !== 25 ||
    detail.historical_median_apparent_temperatures_c?.length !== 25 ||
    detail.valid_times_berlin?.length !== 25
  ) {
    throw new Error("Neighborhood plotting data is incomplete.");
  }
  state.detailCache.set(plrId, detail);
  return detail;
}

function renderSummary(area) {
  elements["detail-placeholder"].hidden = true;
  elements["detail-content"].hidden = false;
  elements["detail-plr-id"].textContent = `Planning area ${area.id}`;
  elements["detail-name"].textContent = area.name;
  elements["pin-toggle"].setAttribute("aria-pressed", String(state.pinned));
  elements["pin-toggle"].textContent = state.pinned ? "Pinned" : "Pin";
  elements["metric-maximum"].textContent = formatTemperature(area.maximum_temperature_c);
  elements["metric-maximum-at"].textContent = formatLocalDateTime(area.maximum_temperature_at_berlin);
  elements["metric-difference"].textContent = formatSignedTemperature(area.maximum_difference_c);
  elements["metric-difference-at"].textContent = formatLocalDateTime(area.maximum_difference_at_berlin);
  elements["metric-sum"].textContent = formatSignedTemperature(area.summed_difference_c);
  if (area.population_status === "available" && area.population_65plus !== null) {
    elements["metric-population"].textContent = formatInteger(area.population_65plus);
    elements["metric-population-total"].textContent = `${formatInteger(area.population_total)} residents total`;
  } else {
    elements["metric-population"].textContent = "Unavailable";
    elements["metric-population-total"].textContent = area.population_status.replaceAll("_", " ");
  }
}

function togglePin() {
  state.pinned = !state.pinned;
  elements["pin-toggle"].setAttribute("aria-pressed", String(state.pinned));
  elements["pin-toggle"].textContent = state.pinned ? "Pinned" : "Pin";
}

function releasePin() {
  state.pinned = false;
  if (elements["pin-toggle"]) {
    elements["pin-toggle"].setAttribute("aria-pressed", "false");
    elements["pin-toggle"].textContent = "Pin";
  }
}

function updateSelectionStyles() {
  for (const [plrId, shape] of state.shapes) {
    shape.classList.toggle("selected", plrId === state.activeId);
  }
}

function updateSearchResults() {
  const query = normalizeText(elements["area-search"].value.trim());
  if (!query) {
    hideSearchResults();
    return;
  }
  const matches = state.areas
    .filter(area => normalizeText(`${area.name} ${area.id}`).includes(query))
    .slice(0, 8);
  elements["search-results"].replaceChildren();
  for (const area of matches) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "search-result";
    button.setAttribute("role", "option");
    const name = document.createElement("span");
    name.textContent = area.name;
    const id = document.createElement("small");
    id.textContent = area.id;
    button.append(name, id);
    button.addEventListener("click", () => selectArea(area.id, { pin: true }));
    elements["search-results"].append(button);
  }
  elements["search-results"].hidden = matches.length === 0;
}

function hideSearchResults() {
  elements["search-results"].hidden = true;
}

function normalizeText(value) {
  return value.toLocaleLowerCase("de-DE").normalize("NFD").replace(/[\u0300-\u036f]/g, "");
}

function updateMapColors() {
  if (!state.areas.length) return;
  const values = state.areas
    .map(area => metricValue(area, state.metric))
    .filter(Number.isFinite)
    .sort((a, b) => a - b);
  const low = quantile(values, .05);
  const high = quantile(values, .95);
  const anomalyLimit = Math.max(Math.abs(low), Math.abs(high), 1);

  for (const area of state.areas) {
    const shape = state.shapes.get(area.id);
    const value = metricValue(area, state.metric);
    shape.style.fill = metricColor(state.metric, value, low, high, anomalyLimit);
    shape.setAttribute("aria-label", `${area.name}, ${metricLabel(state.metric, value)}`);
  }

  if (state.metric === "maximum_apparent_temperature_difference_c") {
    elements["legend-gradient"].style.background = "linear-gradient(90deg, #477e9b, #efe6cb, #d6533a)";
    elements["legend-low"].textContent = `${formatNumber(-anomalyLimit, 1)}°C`;
    elements["legend-high"].textContent = `+${formatNumber(anomalyLimit, 1)}°C`;
  } else if (state.metric === "maximum_temperature_c") {
    elements["legend-gradient"].style.background = "linear-gradient(90deg, #d9e7e7, #e2b658, #d6533a)";
    elements["legend-low"].textContent = `${formatNumber(low, 1)}°C`;
    elements["legend-high"].textContent = `${formatNumber(high, 1)}°C`;
  } else {
    elements["legend-gradient"].style.background = "linear-gradient(90deg, #e5eeea, #73a4a0, #164652)";
    elements["legend-low"].textContent = formatInteger(low);
    elements["legend-high"].textContent = formatInteger(high);
  }
}

function metricValue(area, metric) {
  const value = area[metric];
  return value === null || value === "" ? Number.NaN : Number(value);
}

function metricColor(metric, value, low, high, anomalyLimit) {
  if (!Number.isFinite(value)) return "#deddd7";
  if (metric === "maximum_apparent_temperature_difference_c") {
    const ratio = clamp((value + anomalyLimit) / (2 * anomalyLimit), 0, 1);
    return threeStopColor(ratio, "#477e9b", "#efe6cb", "#d6533a");
  }
  if (metric === "population_65plus") {
    const logLow = Math.log1p(Math.max(0, low));
    const logHigh = Math.log1p(Math.max(1, high));
    const ratio = clamp((Math.log1p(Math.max(0, value)) - logLow) / (logHigh - logLow || 1), 0, 1);
    return interpolateColor("#e5eeea", "#164652", ratio);
  }
  const ratio = clamp((value - low) / (high - low || 1), 0, 1);
  return threeStopColor(ratio, "#d9e7e7", "#e2b658", "#d6533a");
}

function metricLabel(metric, value) {
  if (!Number.isFinite(value)) return "value unavailable";
  if (metric === "population_65plus") return `${formatInteger(value)} residents aged 65 and older`;
  if (metric === "maximum_apparent_temperature_difference_c") {
    return `highest apparent-minus-forecast difference ${formatSignedTemperature(value)}`;
  }
  return `highest forecast ${formatTemperature(value)}`;
}

function chartSpecifications() {
  return [
    {
      svg: "temperature-chart",
      wrap: "chart-wrap",
      tooltip: "chart-tooltip",
      date: "chart-date",
      legend: "temperature-history-legend",
      note: "temperature-history-note",
      forecastKey: "forecast_temperatures_c",
      medianKey: "historical_median_temperatures_c",
      historyKey: "temperatures_c",
      forecastLabel: "Forecast temperature",
      comparisonKey: "forecast_apparent_temperatures_c",
      comparisonLabel: "Forecast apparent temperature",
      comparisonLegend: "temperature-comparison-legend",
    },
    {
      svg: "apparent-temperature-chart",
      wrap: "apparent-chart-wrap",
      tooltip: "apparent-chart-tooltip",
      date: "apparent-chart-date",
      legend: "apparent-history-legend",
      note: "apparent-history-note",
      forecastKey: "forecast_apparent_temperatures_c",
      medianKey: "historical_median_apparent_temperatures_c",
      historyKey: "apparent_temperatures_c",
      forecastLabel: "Forecast apparent temperature",
      comparisonKey: "forecast_temperatures_c",
      comparisonLabel: "Forecast temperature",
      comparisonLegend: "apparent-comparison-legend",
    },
  ];
}

function updateChartLayout() {
  const compareApparent = state.metric === "maximum_apparent_temperature_difference_c";
  elements["apparent-chart-section"].hidden = !compareApparent;
  elements["temperature-chart-title"].textContent = compareApparent
    ? "Forecast temperature"
    : "The next 25 hours";
  for (const specification of chartSpecifications()) {
    elements[specification.comparisonLegend].hidden = !compareApparent;
  }
  return compareApparent;
}

function renderChart(detail) {
  const compareApparent = updateChartLayout();
  const historical = Array.isArray(detail.historical_years) ? detail.historical_years : [];
  const specifications = chartSpecifications();
  renderSeriesChart(detail, historical, specifications[0], compareApparent);
  if (compareApparent) {
    renderSeriesChart(detail, historical, specifications[1], compareApparent);
  }
}

function renderSeriesChart(detail, historical, specification, showComparison) {
  const svg = elements[specification.svg];
  svg.replaceChildren();
  elements[specification.tooltip].hidden = true;
  const allValues = [
    ...detail[specification.forecastKey],
    ...detail[specification.medianKey],
    ...historical.flatMap(line => line[specification.historyKey]),
    ...(showComparison ? detail[specification.comparisonKey] : []),
  ].filter(Number.isFinite);
  if (!allValues.length) {
    return renderChartErrorForSpecification(
      specification,
      new Error("No chart temperatures are available."),
    );
  }

  const width = 700;
  const height = 410;
  const margin = { top: 24, right: 18, bottom: 50, left: 52 };
  const innerWidth = width - margin.left - margin.right;
  const innerHeight = height - margin.top - margin.bottom;
  let minimum = Math.floor(Math.min(...allValues) - 1);
  let maximum = Math.ceil(Math.max(...allValues) + 1);
  if (maximum - minimum < 6) {
    minimum -= 3;
    maximum += 3;
  }
  const x = index => margin.left + index * innerWidth / 24;
  const y = value => margin.top + (maximum - value) * innerHeight / (maximum - minimum);

  for (let tick = 0; tick <= 4; tick += 1) {
    const value = minimum + tick * (maximum - minimum) / 4;
    const yPosition = y(value);
    svg.append(svgElement("line", { x1: margin.left, x2: width - margin.right, y1: yPosition, y2: yPosition, class: "grid-line" }));
    const label = svgElement("text", { x: margin.left - 9, y: yPosition + 4, class: "axis-label", "text-anchor": "end" });
    label.textContent = `${formatNumber(value, 0)}°`;
    svg.append(label);
  }

  for (const index of [0, 4, 8, 12, 16, 20, 24]) {
    const label = svgElement("text", { x: x(index), y: height - 20, class: "axis-label", "text-anchor": "middle" });
    label.textContent = timePart(detail.valid_times_berlin[index]);
    svg.append(label);
  }

  const dates = detail.valid_times_berlin.map(datePart);
  for (let index = 1; index < dates.length; index += 1) {
    if (dates[index] !== dates[index - 1]) {
      svg.append(svgElement("line", { x1: x(index), x2: x(index), y1: margin.top, y2: height - margin.bottom, class: "grid-line" }));
    }
  }

  for (const line of historical) {
    svg.append(svgElement("path", { d: seriesPath(line[specification.historyKey], x, y), class: "history-line", "data-year": line.year }));
  }
  svg.append(svgElement("path", { d: seriesPath(detail[specification.medianKey], x, y), class: "median-line" }));
  if (showComparison) {
    svg.append(svgElement("path", {
      d: seriesPath(detail[specification.comparisonKey], x, y),
      class: "comparison-line",
    }));
  }
  svg.append(svgElement("path", { d: seriesPath(detail[specification.forecastKey], x, y), class: "forecast-line" }));
  detail[specification.forecastKey].forEach((value, index) => {
    svg.append(svgElement("circle", { cx: x(index), cy: y(value), r: 3.2, class: "forecast-point" }));
  });

  const crosshair = svgElement("line", { y1: margin.top, y2: height - margin.bottom, class: "chart-crosshair" });
  crosshair.hidden = true;
  svg.append(crosshair);
  const hitArea = svgElement("rect", { x: margin.left, y: margin.top, width: innerWidth, height: innerHeight, class: "chart-hit-area" });
  hitArea.addEventListener("pointermove", event => {
    const rect = svg.getBoundingClientRect();
    const svgX = (event.clientX - rect.left) * width / rect.width;
    const index = clamp(Math.round((svgX - margin.left) * 24 / innerWidth), 0, 24);
    crosshair.hidden = false;
    crosshair.setAttribute("x1", x(index));
    crosshair.setAttribute("x2", x(index));
    showChartTooltip(event, detail, historical, index, specification, showComparison);
  });
  hitArea.addEventListener("pointerleave", () => {
    crosshair.hidden = true;
    elements[specification.tooltip].hidden = true;
  });
  svg.append(hitArea);

  elements[specification.date].textContent = dateRange(detail.valid_times_berlin);
  elements[specification.legend].hidden = historical.length === 0;
  elements[specification.note].hidden = historical.length !== 0;
}

function showChartTooltip(event, detail, historical, index, specification, showComparison) {
  const historicalValues = historical
    .map(line => Number(line[specification.historyKey][index]))
    .filter(Number.isFinite);
  const range = historicalValues.length
    ? `${formatNumber(Math.min(...historicalValues), 1)}–${formatNumber(Math.max(...historicalValues), 1)}°C historical range`
    : "Individual historical years unavailable";
  const tooltip = elements[specification.tooltip];
  tooltip.replaceChildren();
  const heading = document.createElement("strong");
  heading.textContent = formatLocalDateTime(detail.valid_times_berlin[index]);
  const forecast = document.createElement("div");
  forecast.className = "tooltip-forecast";
  forecast.textContent = `${specification.forecastLabel} ${formatTemperature(detail[specification.forecastKey][index])}`;
  const comparison = document.createElement("div");
  comparison.className = "tooltip-comparison";
  comparison.textContent = `${specification.comparisonLabel} ${formatTemperature(detail[specification.comparisonKey][index])}`;
  const median = document.createElement("div");
  median.className = "tooltip-median";
  median.textContent = `Median ${formatTemperature(detail[specification.medianKey][index])}`;
  const historyRange = document.createElement("div");
  historyRange.textContent = range;
  tooltip.append(heading, forecast);
  if (showComparison) tooltip.append(comparison);
  tooltip.append(median, historyRange);
  tooltip.hidden = false;
  const wrap = elements[specification.wrap].getBoundingClientRect();
  tooltip.style.left = `${clamp(event.clientX - wrap.left + 13, 8, wrap.width - 174)}px`;
  tooltip.style.top = `${clamp(event.clientY - wrap.top - 45, 5, wrap.height - 100)}px`;
}

function renderChartError(error) {
  updateChartLayout();
  for (const specification of chartSpecifications()) {
    renderChartErrorForSpecification(specification, error);
  }
  console.error(error);
}

function renderChartErrorForSpecification(specification, error) {
  elements[specification.svg].replaceChildren();
  elements[specification.note].hidden = false;
  elements[specification.note].textContent = `Plot unavailable: ${error.message}`;
}

function geometryBounds(geometries) {
  const bounds = { minX: Infinity, minY: Infinity, maxX: -Infinity, maxY: -Infinity };
  for (const geometry of geometries) {
    visitCoordinates(geometry.coordinates, coordinate => {
      bounds.minX = Math.min(bounds.minX, coordinate[0]);
      bounds.maxX = Math.max(bounds.maxX, coordinate[0]);
      bounds.minY = Math.min(bounds.minY, coordinate[1]);
      bounds.maxY = Math.max(bounds.maxY, coordinate[1]);
    });
  }
  return bounds;
}

function visitCoordinates(coordinates, visitor) {
  if (typeof coordinates?.[0] === "number") return visitor(coordinates);
  for (const child of coordinates || []) visitCoordinates(child, visitor);
}

function createProjection(bounds, width, height, padding) {
  const mapWidth = bounds.maxX - bounds.minX;
  const mapHeight = bounds.maxY - bounds.minY;
  const scale = Math.min((width - 2 * padding) / mapWidth, (height - 2 * padding) / mapHeight);
  const offsetX = (width - mapWidth * scale) / 2;
  const offsetY = (height - mapHeight * scale) / 2;
  return coordinate => [
    offsetX + (coordinate[0] - bounds.minX) * scale,
    height - offsetY - (coordinate[1] - bounds.minY) * scale,
  ];
}

function geometryPath(geometry, projection) {
  const polygons = geometry.type === "Polygon" ? [geometry.coordinates] : geometry.coordinates;
  return polygons.map(polygon => polygon.map(ring => {
    const points = ring.map(projection);
    return points.map((point, index) => `${index ? "L" : "M"}${point[0].toFixed(1)},${point[1].toFixed(1)}`).join("") + "Z";
  }).join("")).join("");
}

function seriesPath(values, x, y) {
  return values.map((value, index) => `${index ? "L" : "M"}${x(index).toFixed(1)},${y(value).toFixed(1)}`).join("");
}

function svgElement(name, attributes) {
  const element = document.createElementNS(SVG_NS, name);
  for (const [key, value] of Object.entries(attributes)) element.setAttribute(key, String(value));
  return element;
}

function quantile(sorted, probability) {
  if (!sorted.length) return 0;
  const index = (sorted.length - 1) * probability;
  const lower = Math.floor(index);
  const fraction = index - lower;
  return sorted[lower + 1] === undefined ? sorted[lower] : sorted[lower] + fraction * (sorted[lower + 1] - sorted[lower]);
}

function threeStopColor(ratio, start, middle, end) {
  return ratio <= .5 ? interpolateColor(start, middle, ratio * 2) : interpolateColor(middle, end, (ratio - .5) * 2);
}

function interpolateColor(start, end, ratio) {
  const from = hexToRgb(start);
  const to = hexToRgb(end);
  const channel = name => Math.round(from[name] + (to[name] - from[name]) * ratio);
  return `rgb(${channel("r")}, ${channel("g")}, ${channel("b")})`;
}

function hexToRgb(hex) {
  return { r: parseInt(hex.slice(1, 3), 16), g: parseInt(hex.slice(3, 5), 16), b: parseInt(hex.slice(5, 7), 16) };
}

function clamp(value, minimum, maximum) { return Math.min(maximum, Math.max(minimum, value)); }
function formatNumber(value, digits = 1) { return new Intl.NumberFormat("en", { minimumFractionDigits: digits, maximumFractionDigits: digits }).format(value); }
function formatInteger(value) { return new Intl.NumberFormat("en", { maximumFractionDigits: 0 }).format(value); }
function formatTemperature(value) { return `${formatNumber(value, 1)}°C`; }
function formatSignedTemperature(value) { return `${value > 0 ? "+" : ""}${formatNumber(value, 1)}°C`; }
function datePart(value) { return String(value).slice(0, 10); }
function timePart(value) { return String(value).slice(11, 16); }

function formatLocalDateTime(value) {
  const [date, time = ""] = String(value).split("T");
  const [year, month, day] = date.split("-");
  const monthName = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"][Number(month) - 1];
  return `${day} ${monthName} ${year}, ${time.slice(0, 5)}`;
}

function dateRange(times) {
  const start = formatLocalDateTime(times[0]);
  const end = formatLocalDateTime(times[times.length - 1]);
  return `${start} – ${end}`;
}
