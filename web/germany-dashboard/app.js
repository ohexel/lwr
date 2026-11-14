/* Deutschland-Atlas: PMTiles outlines (key only) joined client-side with
   data/values.json (temperature) and data/employment.json (employment) via
   feature-state. No basemap. One theme is shown at a time. */
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
  const css = (name, fallback) =>
    getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
  const NO_DATA = css("--nodata", "#c9ced6");
  const SUPPRESSED = css("--suppressed", "#8f969f");
  const GERMANY_BOUNDS = [[5.6, 47.1], [15.3, 55.2]];

  const THEMES = {
    temperature: {
      hash: "#temperatur",
      title: "Deutschland: Höchst- und Tiefsttemperaturen über die nächsten 24 Stunden",
      eyebrow: "DWD ICON-D2-RUC · Zensus 2022 · VG250",
      mapLabel: "Karte von Deutschland, eingefärbt nach der gefühlten Temperatur im Schatten",
      // Fixed cool-to-warm scale (ColorBrewer RdYlBu, reversed, with the
      // neutral pale yellow moved to 16 °C). The same on every day, so a cold
      // day (0-10 °C) is blue to pale blue, never orange or red. Values outside
      // the range take the end colours.
      scale: [
        [-20, "#313695"], [-10, "#4575b4"], [0, "#74add1"], [8, "#abd9e9"],
        [16, "#ffffbf"], [22, "#fdae61"], [26, "#f46d43"], [30, "#d73027"],
      ],
      ticks: [[-20, "−20"], [-10, "−10"], [0, "0"], [10, "10"], [20, "20"], [30, "30 °C"]],
    },
    employment: {
      hash: "#beschaeftigung",
      title: "Deutschland: Beschäftigte am Arbeitsort je Beschäftigten am Wohnort",
      eyebrow: "Statistik der Bundesagentur für Arbeit · VG250",
      mapLabel:
        "Karte von Deutschland, eingefärbt nach Beschäftigten am Arbeitsort je Beschäftigten am Wohnort",
      // ColorBrewer PuOr (diverging, colour-vision-safe) on log2 of the ratio:
      // orange below 1 (more employed residents than jobs), purple above 1
      // (more jobs than employed residents). 1/4 and 4 are the ends; values
      // beyond take the end colours.
      scale: [
        [-2, "#b35806"], [-1, "#f1a340"], [-0.4, "#fee0b6"], [0, "#f7f7f7"],
        [0.4, "#d8daeb"], [1, "#998ec3"], [2, "#542788"],
      ],
      ticks: [[-2, "¼"], [-1, "½"], [0, "1"], [1, "2"], [2, "4"]],
    },
  };

  const state = {
    theme: "temperature",
    level: "krs",
    weighting: "area",
    extreme: "max",
    values: null,
    employment: null,
    hovered: null,
    pinned: null,
  };
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
  const dateFmt = new Intl.DateTimeFormat("de-DE", { day: "numeric", month: "long", year: "numeric", timeZone: "UTC" });
  function when(hourIndex) {
    if (hourIndex === null || hourIndex === undefined) return "";
    const date = parseLocal(state.values.valid_times_berlin[hourIndex]);
    return `${dayFmt.format(date)}, ${timeFmt.format(date)}`;
  }
  const numberFmt = new Intl.NumberFormat("de-DE");
  const oneDecimal = new Intl.NumberFormat("de-DE", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
  const twoDecimals = new Intl.NumberFormat("de-DE", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const degrees = (v) => (v === null || v === undefined ? "–" : `${oneDecimal.format(v)} °C`);
  const referenceDate = () => dateFmt.format(new Date(`${state.employment.reference_date}T00:00:00Z`));

  // ---------------------------------------------------------------- temperature values
  // Keys of values.json: <max|min>_<measure>_c for a Gemeinde, and
  // <max|min>_<measure>_<area|population>_weighted_c for a Kreis or Land. The
  // lead hour (index into valid_times_berlin) is the same key ending in
  // _lead_hour. A values.json without min_ keys disables Tiefstwerte.
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

  // ---------------------------------------------------------------- employment values
  // employment.json: per region jobs_ratio (own calculation) with
  // jobs_ratio_status, and the published BA values; a value is null when the
  // BA suppressed it or does not report the region ("status" says which).
  const EMPLOYMENT_MISSING = {
    suppressed: "von der BA geheimgehalten",
    not_reported: "von der BA nicht ausgewiesen",
    not_computable: "nicht berechenbar (keine Beschäftigten am Wohnort)",
  };
  const NOT_REPORTED_REASON = {
    no_source_record_unincorporated:
      "Gemeindefreies Gebiet: Die BA weist hier keine Beschäftigten aus.",
    no_source_record:
      "Diese Gemeinde wurde nach 2022 aufgelöst oder mit einer anderen zusammengelegt. " +
      "Die BA weist sie zum Stichtag nicht mehr aus; die Karte zeigt die Gemeindegrenzen von 2022.",
  };
  const ratioText = (record) =>
    record.jobs_ratio_status === "value"
      ? twoDecimals.format(record.jobs_ratio)
      : EMPLOYMENT_MISSING[record.jobs_ratio_status] || "–";
  function countText(record, measure) {
    if (record[measure] !== null && record[measure] !== undefined) return numberFmt.format(record[measure]);
    const status = record.status && record.status[measure];
    return EMPLOYMENT_MISSING[status] || "–";
  }
  function employmentRecord(level, key) {
    return state.employment ? state.employment[level][key] : null;
  }

  // ---------------------------------------------------------------- per-theme value of a region
  function featureState(level, key) {
    if (state.theme === "employment") {
      const record = employmentRecord(level, key);
      if (!record) return { v: null, s: "not_reported" };
      if (record.jobs_ratio_status !== "value") return { v: null, s: record.jobs_ratio_status };
      // log2 of the ratio; a ratio of 0 (no jobs) takes the lowest colour.
      return { v: Math.log2(Math.max(record.jobs_ratio, 1 / 16)), s: "value" };
    }
    const record = state.values[level][key];
    return { v: record ? record[valueKey(level, APPARENT)] : null, s: null };
  }

  function colourExpression() {
    const stops = [];
    for (const [stop, colour] of THEMES[state.theme].scale) stops.push(stop, colour);
    return ["case",
      ["==", ["typeof", ["feature-state", "v"]], "number"],
      ["interpolate", ["linear"], ["feature-state", "v"], ...stops],
      ["==", ["feature-state", "s"], "suppressed"], SUPPRESSED,
      NO_DATA];
  }

  function applyValues(map, levels) {
    for (const level of levels) {
      for (const key of Object.keys(state.values[level])) {
        map.setFeatureState({ source: SOURCE, sourceLayer: level, id: key }, featureState(level, key));
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

  function temperatureRows(level, record) {
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

  function employmentRows(level, key) {
    const record = employmentRecord(level, key);
    if (!record) return [["Beschäftigung", "keine Daten"]];
    const rows = [
      ["Beschäftigte am Arbeitsort je Beschäftigten am Wohnort (eigene Berechnung)", ratioText(record)],
      ["Sozialversicherungspflichtig Beschäftigte am Arbeitsort", countText(record, "employees_workplace")],
      ["Sozialversicherungspflichtig Beschäftigte am Wohnort", countText(record, "employees_residence")],
      ["Einpendler", countText(record, "commuters_in")],
      ["Auspendler", countText(record, "commuters_out")],
      ["Betriebe", countText(record, "establishments")],
    ];
    if (record.not_reported_reason) {
      rows.push(["Hinweis", NOT_REPORTED_REASON[record.not_reported_reason] || record.not_reported_reason]);
    }
    return rows;
  }

  function renderDetails(level, key) {
    const panel = $("details");
    panel.replaceChildren();
    const record = key ? state.values[level][key] : null;
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
    const rows = state.theme === "employment" ? employmentRows(level, key) : temperatureRows(level, record);
    for (const [label, value] of rows) {
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
    if (state.theme === "employment") {
      const employment = employmentRecord(level, key);
      value.textContent = employment ? `Verhältnis ${ratioText(employment)}` : "keine Daten";
    } else {
      const v = record[valueKey(level, APPARENT)];
      const hour = record[hourKey(valueKey(level, APPARENT))];
      value.textContent = `${degrees(v)} · ${when(hour)}`;
    }
    tip.append(name, value);
    tip.style.left = `${point.x + 14}px`;
    tip.style.top = `${point.y + 14}px`;
    tip.hidden = false;
  }

  // ---------------------------------------------------------------- legend and header
  function renderLegend() {
    const theme = THEMES[state.theme];
    const first = theme.scale[0][0];
    const last = theme.scale[theme.scale.length - 1][0];
    const position = (t) => (100 * (t - first)) / (last - first);
    $("legend-bar").style.background =
      `linear-gradient(to right, ${theme.scale.map(([t, colour]) => `${colour} ${position(t)}%`).join(", ")})`;
    const ticks = $("legend-ticks");
    ticks.replaceChildren();
    for (const [t, label] of theme.ticks) {
      const span = document.createElement("span");
      span.textContent = label;
      span.style.left = `${position(t)}%`;
      ticks.append(span);
    }
    const employment = state.theme === "employment";
    $("legend-title").textContent = employment
      ? "Beschäftigte am Arbeitsort je Beschäftigten am Wohnort"
      : EXTREME[state.extreme].legend;
    $("legend-ends").hidden = !employment;
    $("legend-suppressed").hidden = !employment;
    $("legend-missing-label").textContent = employment ? "von der BA nicht ausgewiesen" : "Kein Wert";
    $("legend-note").textContent = employment
      ? "Feste, logarithmische Skala: 1 heißt gleich viele Arbeitsplätze wie beschäftigte Einwohner; " +
        "Werte unter ¼ und über 4 haben die Endfarben."
      : "Feste Skala von −20 °C bis +30 °C; dieselbe Farbe bedeutet an jedem Tag dieselbe Temperatur.";
  }

  function renderHeader() {
    const theme = THEMES[state.theme];
    document.title = theme.title;
    $("title").textContent = theme.title;
    $("eyebrow").textContent = theme.eyebrow;
    $("map").setAttribute("aria-label", theme.mapLabel);
    const el = $("run-time");
    el.replaceChildren();
    const small = document.createElement("small");
    if (state.theme === "employment") {
      const e = state.employment;
      el.append(`Stichtag ${referenceDate()} · ${e.source.publisher}`);
      small.textContent = " · Gemeindegrenzen: Gebietsstand 31.12.2022";
      el.append(small);
      const b = e.bund;
      $("bund").textContent =
        `Deutschland insgesamt: ${numberFmt.format(b.employees_workplace)} Beschäftigte am Arbeitsort, ` +
        `${numberFmt.format(b.employees_residence)} am Wohnort (Verhältnis ${twoDecimals.format(b.jobs_ratio)}); ` +
        `${numberFmt.format(b.establishments)} Betriebe.`;
      return;
    }
    const v = state.values;
    const run = parseLocal(v.run_time_berlin);
    const last = parseLocal(v.valid_times_berlin[v.valid_times_berlin.length - 1]);
    el.append(`Vorhersagelauf ${dayFmt.format(run)}, ${timeFmt.format(run)} Uhr (Ortszeit Berlin)`);
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

  function renderThemeControls() {
    const temperature = state.theme === "temperature";
    $("extreme-field").hidden = !temperature;
    $("weighting-field").hidden = !temperature;
    for (const node of document.querySelectorAll("[data-theme]")) {
      node.hidden = node.dataset.theme !== state.theme;
    }
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

  function setTheme(map, theme) {
    state.theme = theme;
    for (const level of LEVELS) map.setPaintProperty(`${level}-fill`, "fill-color", colourExpression());
    applyValues(map, LEVELS);
    renderHeader();
    renderLegend();
    renderThemeControls();
    $("tooltip").hidden = true;
    renderDetails(state.level, state.pinned ? state.pinned.id : null);
    try {
      history.replaceState(null, "", THEMES[theme].hash);
    } catch (error) {
      // file:// previews may refuse; the theme still switches.
    }
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
      // Room for the attribution bar at the bottom edge.
      fitBoundsOptions: { padding: { top: 12, right: 12, bottom: 40, left: 12 } },
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
              "Zensus 2022 · DWD ICON-D2-RUC · " +
              '<a href="https://statistik.arbeitsagentur.de/">Statistik der Bundesagentur für Arbeit</a>',
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

    for (const input of document.querySelectorAll('input[name="theme"]')) {
      input.addEventListener("change", () => setTheme(map, input.value));
    }
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
  async function loadJson(path) {
    const response = await fetch(path, { cache: "no-cache" });
    if (!response.ok) throw new Error(`${path}: HTTP ${response.status}`);
    return response.json();
  }

  async function start() {
    try {
      state.values = await loadJson("data/values.json");
      if (state.values.schema !== 2) throw new Error("values.json: unexpected schema");
    } catch (error) {
      const el = $("run-time");
      el.className = "run error";
      el.textContent = `Die Kartendaten konnten nicht geladen werden (${error.message}).`;
      return;
    }
    // The employment theme is optional: without employment.json the page
    // offers the temperature theme only.
    try {
      const employment = await loadJson("data/employment.json");
      if (employment.schema !== 1) throw new Error("employment.json: unexpected schema");
      state.employment = employment;
    } catch (error) {
      document.querySelector('input[name="theme"][value="employment"]').disabled = true;
      $("theme-note").hidden = false;
    }
    // Tiefstwerte need min_ keys in values.json.
    if (!hasExtreme("min")) {
      document.querySelector('input[name="extreme"][value="min"]').disabled = true;
      $("extreme-note").hidden = false;
    }
    if (window.location.hash === THEMES.employment.hash && state.employment) {
      state.theme = "employment";
      document.querySelector('input[name="theme"][value="employment"]').checked = true;
    }
    renderHeader();
    renderLegend();
    renderThemeControls();
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
