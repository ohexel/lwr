/* Germany 24-hour forecast map: PMTiles outlines (key only) joined
   client-side with data/values.json via feature-state. No basemap. */
(function () {
  "use strict";

  const SOURCE = "de";
  const LEVELS = ["lan", "krs", "gem"];
  const LEVEL_LABEL = { lan: "Land", krs: "Kreis", gem: "Gemeinde" };
  // ColorBrewer YlOrRd (9 classes): sequential, single-hue progression in
  // lightness, readable with the common colour-vision deficiencies.
  const RAMP = ["#ffffcc", "#ffeda0", "#fed976", "#feb24c", "#fd8d3c",
                "#fc4e2a", "#e31a1c", "#bd0026", "#800026"];
  const NO_DATA = getComputedStyle(document.documentElement)
    .getPropertyValue("--nodata").trim() || "#c9ced6";
  const GERMANY_BOUNDS = [[5.6, 47.1], [15.3, 55.2]];

  const state = { level: "krs", weighting: "area", values: null, hovered: null, pinned: null };
  const APPARENT = "apparent_temperature_shade";
  const AIR = "temperature";
  const $ = (id) => document.getElementById(id);

  // ---------------------------------------------------------------- format
  function parseLocal(label) {
    // "YYYY-MM-DDTHH:MM" in Europe/Berlin; format without re-zoning.
    const [d, t] = label.split("T");
    const [y, m, day] = d.split("-").map(Number);
    const [hh, mm] = t.split(":").map(Number);
    return new Date(Date.UTC(y, m - 1, day, hh, mm));
  }
  const dayFmt = new Intl.DateTimeFormat("en-GB", { weekday: "short", day: "numeric", month: "short", timeZone: "UTC" });
  const timeFmt = new Intl.DateTimeFormat("en-GB", { hour: "2-digit", minute: "2-digit", timeZone: "UTC" });
  function when(hourIndex) {
    if (hourIndex === null || hourIndex === undefined) return "";
    const date = parseLocal(state.values.valid_times_berlin[hourIndex]);
    return `${dayFmt.format(date)}, ${timeFmt.format(date)}`;
  }
  const numberFmt = new Intl.NumberFormat("en-GB");
  const degrees = (v) => (v === null || v === undefined ? "–" : `${v.toFixed(1)} °C`);

  // ---------------------------------------------------------------- values
  // Keys of values.json: max_<measure>_c for a Gemeinde, and
  // max_<measure>_<area|population>_weighted_c for a Kreis or Land. The lead
  // hour (index into valid_times_berlin) is the same key ending in _lead_hour.
  function valueKey(level, measure, weighting = state.weighting) {
    return level === "gem" ? `max_${measure}_c` : `max_${measure}_${weighting}_weighted_c`;
  }
  const hourKey = (key) => key.replace(/_c$/, "_lead_hour");
  function valueAndTime(level, record, measure, weighting) {
    const key = valueKey(level, measure, weighting);
    return `${degrees(record[key])} · ${when(record[hourKey(key)])}`;
  }
  function valueOf(level, record) {
    return record ? record[valueKey(level, APPARENT)] : null;
  }

  // Colour domain: this run's Gemeinde range, widened to whole 5 °C steps.
  // It is shared by all levels but changes between runs (see legend).
  function domain() {
    const [low, high] = state.values.apparent_range_c;
    const lo = Math.floor(low / 5) * 5;
    return [lo, Math.max(Math.ceil(high / 5) * 5, lo + 5)];
  }

  function colourExpression() {
    const [low, high] = domain();
    const span = Math.max(high - low, 1);
    const stops = [];
    RAMP.forEach((colour, i) => stops.push(low + (span * i) / (RAMP.length - 1), colour));
    return ["case",
      ["==", ["typeof", ["feature-state", "v"]], "number"],
      ["interpolate", ["linear"], ["feature-state", "v"], ...stops],
      NO_DATA];
  }

  function applyValues(map, levels) {
    for (const level of levels) {
      const records = state.values[level];
      for (const key of Object.keys(records)) {
        map.setFeatureState(
          { source: SOURCE, sourceLayer: level, id: key },
          { v: valueOf(level, records[key]) }
        );
      }
    }
  }

  // ---------------------------------------------------------------- panel
  function parentNames(level, record) {
    const v = state.values;
    if (level === "gem") {
      const kreis = v.krs[record.kreis_key];
      const land = v.lan[record.land_key];
      return [record.gemeinde_type, kreis && kreis.name, land && land.name].filter(Boolean).join(" · ");
    }
    if (level === "krs") {
      const land = v.lan[record.land_key];
      return [record.kreis_type, land && land.name].filter(Boolean).join(" · ");
    }
    return "Land";
  }

  function residents(record) {
    if (record.population_total === null || record.population_total === undefined) return "no census record";
    // Defensive: not present in the current extract (see normalized_schema.sql).
    if (record.population_status === "census_zero_or_set_to_zero") return "0 (exactly zero or set to zero)";
    return numberFmt.format(record.population_total);
  }

  function rows(level, record) {
    if (level === "gem") {
      return [
        ["Max. apparent", valueAndTime(level, record, APPARENT)],
        ["Max. air temp.", valueAndTime(level, record, AIR)],
        ["Residents", residents(record)],
      ];
    }
    const chosen = state.weighting;
    const other = chosen === "population" ? "area" : "population";
    return [
      [`Max. apparent (${chosen})`, valueAndTime(level, record, APPARENT, chosen)],
      [`Max. air temp. (${chosen})`, valueAndTime(level, record, AIR, chosen)],
      [`Max. apparent (${other})`, valueAndTime(level, record, APPARENT, other)],
      ["Residents", residents(record)],
      ["Gemeinden", numberFmt.format(record.gemeinde_count)],
    ];
  }

  function renderDetails(level, key) {
    const panel = $("details");
    panel.replaceChildren();
    const record = state.values[level][key];
    if (!record) {
      const p = document.createElement("p");
      p.className = "hint";
      p.textContent = "Hover over or click a region.";
      panel.append(p);
      return;
    }
    const h2 = document.createElement("h2");
    h2.textContent = record.name;
    const context = document.createElement("p");
    context.className = "context";
    context.textContent = `${LEVEL_LABEL[level]} ${key} · ${parentNames(level, record)}`;
    const dl = document.createElement("dl");
    for (const [label, value] of rows(level, record)) {
      const dt = document.createElement("dt");
      dt.textContent = label;
      const dd = document.createElement("dd");
      const [main, ...rest] = value.split(" · ");
      const strong = document.createElement("b");
      strong.textContent = main;
      dd.append(strong);
      if (rest.length) dd.append(` · ${rest.join(" · ")}`);
      dl.append(dt, dd);
    }
    panel.append(h2, context, dl);
  }

  function renderTooltip(level, key, point) {
    const tip = $("tooltip");
    const record = state.values[level][key];
    if (!record) { tip.hidden = true; return; }
    tip.replaceChildren();
    const name = document.createElement("strong");
    name.textContent = record.name;
    const value = document.createElement("span");
    const v = valueOf(level, record);
    const hour = record[hourKey(valueKey(level, APPARENT))];
    value.textContent = `${degrees(v)} · ${when(hour)}`;
    tip.append(name, value);
    tip.style.left = `${point.x + 14}px`;
    tip.style.top = `${point.y + 14}px`;
    tip.hidden = false;
  }

  // ---------------------------------------------------------------- legend
  function renderLegend() {
    const [low, high] = domain();
    $("legend-bar").style.background = `linear-gradient(to right, ${RAMP.join(", ")})`;
    const ticks = $("legend-ticks");
    ticks.replaceChildren();
    const step = 5;
    const steps = Math.round((high - low) / step);
    for (let i = 0; i <= steps; i += 1) {
      const span = document.createElement("span");
      span.textContent = `${low + i * step}${i === steps ? " °C" : ""}`;
      span.style.left = `${(100 * i) / steps}%`;
      ticks.append(span);
    }
  }

  function renderHeader() {
    const v = state.values;
    const run = parseLocal(v.run_time_berlin);
    const last = parseLocal(v.valid_times_berlin[v.valid_times_berlin.length - 1]);
    const el = $("run-time");
    el.replaceChildren();
    el.append(`Forecast run ${dayFmt.format(run)}, ${timeFmt.format(run)} (Berlin time)`);
    const small = document.createElement("small");
    small.textContent = ` · ${v.run_time_utc.replace("T", " ").replace("Z", " UTC")} · valid until ${dayFmt.format(last)}, ${timeFmt.format(last)}`;
    el.append(small);
    const b = v.bund;
    const part = (weighting, label) => {
      const key = valueKey("bund", APPARENT, weighting);
      return `${degrees(b[key])} ${label} (${when(b[hourKey(key)])})`;
    };
    $("bund").textContent =
      `Germany as a whole: max. apparent ${part("area", "area-weighted")}, ` +
      `${part("population", "population-weighted")}; ` +
      `${numberFmt.format(b.population_total)} residents with a census record.`;
  }

  // ---------------------------------------------------------------- map
  function setHighlight(map, which, target) {
    const previous = state[which];
    if (previous) map.setFeatureState(previous, { [which]: false });
    state[which] = target;
    if (target) map.setFeatureState(target, { [which]: true });
  }

  function setLevel(map, level) {
    state.level = level;
    for (const l of LEVELS) {
      const visible = l === level ? "visible" : "none";
      map.setLayoutProperty(`${l}-fill`, "visibility", visible);
      map.setLayoutProperty(`${l}-highlight`, "visibility", visible);
    }
    map.setLayoutProperty("gem-line", "visibility", level === "gem" ? "visible" : "none");
    $("weighting-field").disabled = level === "gem";
    setHighlight(map, "hovered", null);
    setHighlight(map, "pinned", null);
    renderDetails(level, null);
  }

  function buildMap(tilesUrl) {
    const protocol = new pmtiles.Protocol();
    maplibregl.addProtocol("pmtiles", protocol.tile);

    const fill = (level) => ({
      id: `${level}-fill`, type: "fill", source: SOURCE, "source-layer": level,
      layout: { visibility: level === state.level ? "visible" : "none" },
      paint: { "fill-color": colourExpression(), "fill-opacity": 0.92 },
    });
    const highlight = (level) => ({
      id: `${level}-highlight`, type: "line", source: SOURCE, "source-layer": level,
      layout: { visibility: level === state.level ? "visible" : "none" },
      paint: {
        "line-color": "#111",
        "line-width": ["case", ["boolean", ["feature-state", "pinned"], false], 3,
                       ["boolean", ["feature-state", "hovered"], false], 2, 0],
      },
    });

    const map = new maplibregl.Map({
      container: "map",
      bounds: GERMANY_BOUNDS,
      maxBounds: [[0, 44], [21, 58]],
      minZoom: 4,
      maxZoom: 12,
      attributionControl: false,
      style: {
        version: 8,
        sources: {
          [SOURCE]: {
            type: "vector",
            url: `pmtiles://${tilesUrl}`,
            promoteId: "key",
            attribution:
              '<a href="https://www.bkg.bund.de">© GeoBasis-DE / BKG 2022 (Daten verändert)</a> · ' +
              "Zensus 2022 · DWD ICON-D2-RUC",
          },
        },
        layers: [
          { id: "background", type: "background", paint: { "background-color": "#eef1f4" } },
          fill("lan"), fill("krs"), fill("gem"),
          { id: "gem-line", type: "line", source: SOURCE, "source-layer": "gem",
            layout: { visibility: state.level === "gem" ? "visible" : "none" },
            minzoom: 7,
            paint: { "line-color": "#ffffff", "line-width": 0.3, "line-opacity": 0.7 } },
          { id: "krs-line", type: "line", source: SOURCE, "source-layer": "krs",
            paint: { "line-color": "#ffffff", "line-width": ["interpolate", ["linear"], ["zoom"], 4, 0.3, 9, 1.2] } },
          { id: "lan-line", type: "line", source: SOURCE, "source-layer": "lan",
            paint: { "line-color": "#30363d", "line-width": ["interpolate", ["linear"], ["zoom"], 4, 0.7, 9, 2] } },
          highlight("lan"), highlight("krs"), highlight("gem"),
        ],
      },
    });
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
    map.addControl(new maplibregl.AttributionControl({ compact: true }), "bottom-right");
    return map;
  }

  function featureAt(map, point) {
    const features = map.queryRenderedFeatures(point, { layers: [`${state.level}-fill`] });
    return features.length ? String(features[0].id) : null;
  }

  function target(key) {
    return key ? { source: SOURCE, sourceLayer: state.level, id: key } : null;
  }

  function wire(map) {
    map.on("mousemove", (event) => {
      const key = featureAt(map, event.point);
      map.getCanvas().style.cursor = key ? "pointer" : "";
      if (!key) {
        $("tooltip").hidden = true;
        setHighlight(map, "hovered", null);
        if (!state.pinned) renderDetails(state.level, null);
        return;
      }
      if (!state.hovered || state.hovered.id !== key) setHighlight(map, "hovered", target(key));
      renderTooltip(state.level, key, event.point);
      if (!state.pinned) renderDetails(state.level, key);
    });
    map.on("mouseout", () => {
      $("tooltip").hidden = true;
      setHighlight(map, "hovered", null);
    });
    map.on("click", (event) => {
      const key = featureAt(map, event.point);
      setHighlight(map, "pinned", target(key));
      renderDetails(state.level, key);
    });

    for (const input of document.querySelectorAll('input[name="level"]')) {
      input.addEventListener("change", () => setLevel(map, input.value));
    }
    for (const input of document.querySelectorAll('input[name="weighting"]')) {
      input.addEventListener("change", () => {
        state.weighting = input.value;
        applyValues(map, ["krs", "lan"]);
        if (state.pinned) renderDetails(state.level, state.pinned.id);
      });
    }
  }

  // ---------------------------------------------------------------- start
  async function start() {
    try {
      const response = await fetch("data/values.json", { cache: "no-cache" });
      if (!response.ok) throw new Error(`values.json: HTTP ${response.status}`);
      state.values = await response.json();
      if (state.values.schema !== 2) throw new Error("values.json: unexpected schema");
    } catch (error) {
      const el = $("run-time");
      el.className = "run error";
      el.textContent = `The forecast data could not be loaded (${error.message}).`;
      return;
    }
    renderHeader();
    renderLegend();
    const tilesUrl = new URL(`data/${state.values.tiles}`, window.location.href).href;
    const map = buildMap(tilesUrl);
    map.on("load", () => {
      applyValues(map, LEVELS);
      wire(map);
    });
    // Feature-state set before tiles arrive is kept; re-apply once the source
    // is fully loaded in case the first call raced the source.
    map.once("idle", () => applyValues(map, LEVELS));
  }

  document.addEventListener("DOMContentLoaded", start);
})();
