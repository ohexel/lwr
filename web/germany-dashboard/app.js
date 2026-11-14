/* Karte der 24-Stunden-Vorhersage für Deutschland: PMTiles outlines (key only) joined
   client-side with data/values.json via feature-state. No basemap. */
(function () {
  "use strict";

  const SOURCE = "de";
  const LEVELS = ["lan", "krs", "gem"];
  const LEVEL_LABEL = { lan: "Land", krs: "Kreis", gem: "Gemeinde" };
  const WEIGHT_LABEL = { area: "flächengewichtet", population: "bevölkerungsgewichtet" };
  // "max" and "min" are the first part of the values.json keys.
  const EXTREME = {
    max: { value: "Höchstwert", legend: "Höchste gefühlte Temperatur im Schatten, nächste 24 h" },
    min: { value: "Tiefstwert", legend: "Niedrigste gefühlte Temperatur im Schatten, nächste 24 h" },
  };
  // Fixed cool-to-warm scale (ColorBrewer RdYlBu, reversed, with the neutral
  // pale yellow moved to 16 °C). The scale is the same on every day, so a
  // cold day (0-10 °C) is blue to pale blue, never orange or red. Blue-red
  // diverging colours stay distinguishable with the common colour-vision
  // deficiencies. Values outside the range take the end colours.
  const SCALE_MIN = -20;
  const SCALE_MAX = 30;
  const SCALE = [
    [-20, "#313695"], [-10, "#4575b4"], [0, "#74add1"], [8, "#abd9e9"],
    [16, "#ffffbf"], [22, "#fdae61"], [26, "#f46d43"], [30, "#d73027"],
  ];
  const NO_DATA = getComputedStyle(document.documentElement)
    .getPropertyValue("--nodata").trim() || "#c9ced6";
  const GERMANY_BOUNDS = [[5.6, 47.1], [15.3, 55.2]];

  const state = { level: "krs", weighting: "area", extreme: "max", values: null, hovered: null, pinned: null };
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
  const dayFmt = new Intl.DateTimeFormat("de-DE", { weekday: "short", day: "numeric", month: "short", timeZone: "UTC" });
  const timeFmt = new Intl.DateTimeFormat("de-DE", { hour: "2-digit", minute: "2-digit", timeZone: "UTC" });
  function when(hourIndex) {
    if (hourIndex === null || hourIndex === undefined) return "";
    const date = parseLocal(state.values.valid_times_berlin[hourIndex]);
    return `${dayFmt.format(date)}, ${timeFmt.format(date)}`;
  }
  const numberFmt = new Intl.NumberFormat("de-DE");
  const oneDecimal = new Intl.NumberFormat("de-DE", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
  const degrees = (v) => (v === null || v === undefined ? "–" : `${oneDecimal.format(v)} °C`);

  // ---------------------------------------------------------------- values
  // Keys of values.json: <max|min>_<measure>_c for a Gemeinde, and
  // <max|min>_<measure>_<area|population>_weighted_c for a Kreis or Land. The
  // lead hour (index into valid_times_berlin) is the same key ending in
  // _lead_hour. The max_ keys exist today; the min_ keys follow the same
  // naming once the backend provides them (see hasExtreme).
  function valueKey(level, measure, weighting = state.weighting, extreme = state.extreme) {
    return level === "gem" ? `${extreme}_${measure}_c` : `${extreme}_${measure}_${weighting}_weighted_c`;
  }
  function hasExtreme(extreme) {
    const records = state.values.gem;
    const first = records[Object.keys(records)[0]];
    return Boolean(first) && `${extreme}_${APPARENT}_c` in first;
  }
  const hourKey = (key) => key.replace(/_c$/, "_lead_hour");
  function valueAndTime(level, record, measure, weighting) {
    const key = valueKey(level, measure, weighting);
    return `${degrees(record[key])} · ${when(record[hourKey(key)])}`;
  }
  function valueOf(level, record) {
    return record ? record[valueKey(level, APPARENT)] : null;
  }

  function colourExpression() {
    const stops = [];
    for (const [temperature, colour] of SCALE) stops.push(temperature, colour);
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
    if (record.population_total === null || record.population_total === undefined) return "kein Zensus-Datensatz";
    // Defensive: not present in the current extract (see normalized_schema.sql).
    if (record.population_status === "census_zero_or_set_to_zero") return "0 (genau null oder auf null gesetzt)";
    return numberFmt.format(record.population_total);
  }

  function rows(level, record) {
    const e = EXTREME[state.extreme].value;
    if (level === "gem") {
      return [
        [`${e}, gefühlte Temperatur`, valueAndTime(level, record, APPARENT)],
        [`${e}, Lufttemperatur`, valueAndTime(level, record, AIR)],
        ["Einwohner", residents(record)],
      ];
    }
    const chosen = state.weighting;
    const other = chosen === "population" ? "area" : "population";
    return [
      [`${e}, gefühlt (${WEIGHT_LABEL[chosen]})`, valueAndTime(level, record, APPARENT, chosen)],
      [`${e}, Lufttemperatur (${WEIGHT_LABEL[chosen]})`, valueAndTime(level, record, AIR, chosen)],
      [`${e}, gefühlt (${WEIGHT_LABEL[other]})`, valueAndTime(level, record, APPARENT, other)],
      ["Einwohner", residents(record)],
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
      p.textContent = "Region mit der Maus überfahren oder anklicken.";
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
  const minus = (t) => (t < 0 ? `\u2212${-t}` : String(t));
  function renderLegend() {
    $("legend-title").textContent = EXTREME[state.extreme].legend;
    const position = (t) => (100 * (t - SCALE_MIN)) / (SCALE_MAX - SCALE_MIN);
    $("legend-bar").style.background =
      `linear-gradient(to right, ${SCALE.map(([t, colour]) => `${colour} ${position(t)}%`).join(", ")})`;
    const ticks = $("legend-ticks");
    ticks.replaceChildren();
    for (let t = SCALE_MIN; t <= SCALE_MAX; t += 10) {
      const span = document.createElement("span");
      span.textContent = `${minus(t)}${t === SCALE_MAX ? " °C" : ""}`;
      span.style.left = `${position(t)}%`;
      ticks.append(span);
    }
  }

  function renderHeader() {
    const v = state.values;
    const run = parseLocal(v.run_time_berlin);
    const last = parseLocal(v.valid_times_berlin[v.valid_times_berlin.length - 1]);
    const el = $("run-time");
    el.replaceChildren();
    el.append(`Vorhersagelauf ${dayFmt.format(run)}, ${timeFmt.format(run)} Uhr (Ortszeit Berlin)`);
    const small = document.createElement("small");
    small.textContent = ` · ${v.run_time_utc.replace("T", " ").replace("Z", " UTC")} · gültig bis ${dayFmt.format(last)}, ${timeFmt.format(last)} Uhr`;
    el.append(small);
    const b = v.bund;
    const part = (weighting) => {
      const key = valueKey("bund", APPARENT, weighting);
      return `${degrees(b[key])} ${WEIGHT_LABEL[weighting]} (${when(b[hourKey(key)])})`;
    };
    $("bund").textContent =
      `Deutschland insgesamt, ${EXTREME[state.extreme].value} gefühlt: ${part("area")}, ` +
      `${part("population")}; ` +
      `${numberFmt.format(b.population_total)} Einwohner mit Zensus-Datensatz.`;
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
    for (const input of document.querySelectorAll('input[name="extreme"]')) {
      input.addEventListener("change", () => {
        state.extreme = input.value;
        applyValues(map, LEVELS);
        renderLegend();
        renderHeader();
        $("tooltip").hidden = true;
        renderDetails(state.level, state.pinned ? state.pinned.id : null);
      });
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
      el.textContent = `Die Vorhersagedaten konnten nicht geladen werden (${error.message}).`;
      return;
    }
    // Tiefstwerte need min_ keys in values.json; until then the option is off.
    if (!hasExtreme("min")) {
      document.querySelector('input[name="extreme"][value="min"]').disabled = true;
      $("extreme-note").hidden = false;
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
