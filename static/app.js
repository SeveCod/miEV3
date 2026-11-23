"use strict";
// Panel e informes de MiEV3 (se sirve en /app.js, con la misma contraseña que el panel)
const $ = id => document.getElementById(id);
const j = u => fetch(u).then(r => { if (!r.ok) throw new Error(`${u} → ${r.status}`); return r.json(); });
const fmt = (n, d=0) => n == null || isNaN(n) ? "–" : Number(n).toLocaleString("es-ES",{maximumFractionDigits:d,minimumFractionDigits:d});
const dt = s => s ? new Date(s).toLocaleString("es-ES",{day:"2-digit",month:"short",hour:"2-digit",minute:"2-digit"}) : "–";
const pad = n => String(n).padStart(2,"0");
const hhmm = m => `${pad(Math.floor(Math.min(m,1439)/60))}:${pad(Math.min(m,1439)%60)}`;   // minutos del día → "HH:MM"
const ymdDate = d => new Date(+d.slice(0,4), +d.slice(4,6)-1, +d.slice(6,8));             // "YYYYMMDD" → Date local
const dshort = d => d.toLocaleDateString("es-ES",{day:"numeric", month:"short"});
const dday = d => d ? dshort(ymdDate(d)) : "–";
const dlong = d => d.toLocaleDateString("es-ES",{weekday:"short", day:"numeric", month:"short"});
const tripMin = t => { const h = String(t.start_time).padStart(6,"0"); return +h.slice(0,2)*60 + +h.slice(2,4); };   // "HHMMSS" → minutos del día
const css = getComputedStyle(document.documentElement);
const C = v => css.getPropertyValue(v).trim();
const tile = (icon, value, label, cls="", href="") => {
  const tag = href ? `a href="${href}" target="_blank" rel="noopener"` : "div";
  return `<${tag} class="tile"><div class="ico" style="--m:url(/img/${icon})"></div><div><b class="${cls}" title="${value}">${value}</b><span title="${label}">${label}</span></div></${href ? "a" : "div"}>`;
};

function renderNow(k, v2c){
  $("fill").style.width = (k.soc||0) + "%";
  $("battery").classList.toggle("charging", !!k.charging);
  $("soc").textContent = fmt(k.soc) + "%";
  $("battery").setAttribute("aria-label", `Batería al ${fmt(k.soc)} %, ${fmt(k.range_km)} km de autonomía${k.charging ? ", cargando" : k.plugged ? ", enchufado" : ""}`);
  const h = k.charge_remaining_min;
  $("range").innerHTML = fmt(k.range_km) + " km<br>" +
    (k.charging ? `⚡ ${fmt(k.charge_power_kw,1)} kW` + (h ? ` · al ${fmt(k.charge_limit_ac)} % a las ${new Date(new Date(k.car_updated_at || k.ts).getTime() + h*60000).toLocaleTimeString("es-ES",{hour:"2-digit",minute:"2-digit"})}` : "") : (k.plugged ? "enchufado" : ""));
  $("updated").textContent = "Dato del coche: " + dt(k.car_updated_at || k.ts);
  const lock = k.locked == null ? "–" : k.locked ? "Cerrado" : "Abierto";
  const open = [k.doors_open ? "puertas" : null, k.windows_open ? "ventanillas" : null].filter(Boolean);
  $("now").innerHTML = [
    tile("icon-car.png", lock, open.length ? "abierto: " + open.join(" y ") : "todo cerrado", k.locked === 0 || open.length ? "bad" : ""),
    tile("icon-plug.png", k.plugged ? "Enchufado" : "Sin enchufar", `AC ${fmt(k.charge_limit_ac)} % · DC ${fmt(k.charge_limit_dc)} %`),
    tile("icon-battery.png", `Salud ${fmt(k.soh)} %`, `${fmt(k.batt_temp_min)}–${fmt(k.batt_temp_max)} °C · 12 V ${fmt(k.battery_12v)} %`),
    tile("icon-car.png", fmt(k.odometer) + " km", "odómetro"),
    // Última posición que reporta el coche; se abre en Google Maps (no se geocodifica fuera)
    k.lat != null && k.lon != null ? tile("icon-car.png", "Ver en mapa ↗", `${(+k.lat).toFixed(4)}, ${(+k.lon).toFixed(4)}`, "",
      `https://www.google.com/maps/search/?api=1&query=${k.lat},${k.lon}`) : "",
    // Reservada para el Trydan: solo aparece cuando llegan lecturas del cargador
    v2c ? tile("icon-plug.png", fmt((v2c.charge_power_w||0)/1000,1) + " kW", "Trydan ahora") : "",
  ].join("");
}

function gaugeSVG(val, ref){
  // Escala alrededor del consumo homologado del coche (con WLTP 15: de 8 a 23)
  const min = Math.floor(ref * .55), max = Math.ceil(ref * 1.5), cx = 110, cy = 105, r = 86;
  const pt = x => { const a = Math.PI * (1 - (Math.min(Math.max(x,min),max) - min) / (max - min)); return [cx + r*Math.cos(a), cy - r*Math.sin(a)]; };
  const arc = (a, b, col, w) => { const [x1,y1] = pt(a), [x2,y2] = pt(b); return `<path d="M${x1} ${y1} A${r} ${r} 0 0 1 ${x2} ${y2}" fill="none" stroke="${col}" stroke-width="${w}" stroke-linecap="round"/>`; };
  const [mx, my] = pt(ref), [nx, ny] = val == null ? [cx, cy - r] : pt(val);
  const col = val == null ? C("--line") : val <= ref ? C("--charge") : C("--euro");
  return `<svg viewBox="0 0 220 120" role="img" aria-label="${val == null ? "sin datos" : fmt(val,1) + " kWh/100 km"}">
    ${arc(min, max, C("--line"), 12)}${val == null ? "" : arc(min, val, col, 12)}
    <line x1="${mx}" y1="${my-10}" x2="${mx}" y2="${my+10}" stroke="${C("--ink")}" stroke-width="2"/>
    <text x="${mx}" y="${my-14}" font-size="10" text-anchor="middle" fill="${C("--soft")}">WLTP</text>
    <circle cx="${nx}" cy="${ny}" r="5" fill="${C("--ink")}"/>
    <text x="22" y="118" font-size="10" fill="${C("--soft")}">${min}</text><text x="198" y="118" font-size="10" text-anchor="end" fill="${C("--soft")}">${max}</text></svg>`;
}

function renderEff(e){
  const g = (title, x) => `<div class="gauge">${gaugeSVG(x && x.kwh_100, e.wltp_kwh_100)}
    <div class="v">${fmt(x && x.kwh_100,1)}</div><div class="u">kWh/100 km</div>
    <div class="l">≈ ${fmt(x && x.l_100,2)} L/100 km</div>
    <div class="t">${title}${x && x.km ? " · " + fmt(x.km) + " km" : ""}</div></div>`;
  const d = e.last_day && e.last_day.day;
  $("gauges").innerHTML = g(d ? `${d.slice(6,8)}/${d.slice(4,6)}` : "Último día", e.last_day) + g("30 días", e.d30) + g("Desde nuevo", e.lifetime);
  const cal = e.calibrated
    ? `Calibrado con el % de batería: coche × ${fmt(e.soc_factor,2)} (${fmt(e.soc_basis_km)} km sin cargar); por día, aproximado.`
    : `Según el contador del coche, que suele quedarse corto: falta base para calibrarlo con el % de batería.`;
  $("effnote").textContent = `${cal} Frente al WLTP de ${fmt(e.wltp_kwh_100,1)} kWh/100 km. L/100 km equivalente en energía (1 L de ${e.fuel.short} ≈ ${fmt(e.fuel.kwh_per_l,1)} kWh).`;
  const b = e.breakdown_30d, parts = [["Motor", b.engine_wh, C("--charge")], ["Clima", b.climate_wh, C("--euro")], ["Electrónica", b.electronics_wh, "#6C8CA0"], ["Cuidado batería", b.battery_care_wh, "#9A7FB0"]];
  const tot = parts.reduce((a,p) => a + (p[1]||0), 0);
  $("split").innerHTML = tot ? parts.map(p => `<div style="width:${(p[1]||0)/tot*100}%;background:${p[2]}" title="${p[0]}"></div>`).join("") : "";
  $("legend").innerHTML = tot ? parts.map(p => `<span><i style="background:${p[2]}"></i>${p[0]} ${fmt((p[1]||0)/tot*100)} % · ${fmt((p[1]||0)/1000,1)} kWh</span>`).join("") : "";
}

function speedColor(v){
  const stops = [[0,[127,200,190]],[40,[47,143,131]],[80,[201,138,27]],[120,[192,86,59]]];
  for (let i = 1; i < stops.length; i++) if (v <= stops[i][0]) {
    const [a, ca] = stops[i-1], [b, cb] = stops[i], f = (v - a) / (b - a);
    return `rgb(${ca.map((c,k) => Math.round(c + (cb[k]-c)*f)).join(",")})`;
  }
  return "rgb(192,86,59)";
}

// Motivo de fin de una carga (sessions.motivo_fin). Solo se marca lo anómalo: llegar al límite
// ('limit') es lo normal y no lleva icono. Textos fijos
const END_REASONS = {
  unplugged: ["🔌", "Desenchufado antes del límite"],
  stopped: ["⚠️", "Se paró enchufado antes del límite: ¿corte de luz, el cargador o el horario de carga?"],
};
const endReason = s => {
  const r = END_REASONS[s.end_reason];
  return r ? ` <span class="why" title="${r[1]}" aria-label="${r[1]}">${r[0]}</span>` : "";
};
function renderDays(trips, sessions, live, dayCost){
  const ymd = d => `${d.getFullYear()}${pad(d.getMonth()+1)}${pad(d.getDate())}`;
  const mins = d => d.getHours()*60 + d.getMinutes();
  const by = {}, slot = d => (by[d] = by[d] || {trips:[], charges:[]});
  trips.filter(t => t.distance_km > 0).forEach(t => slot(t.day).trips.push(t));
  // Cargas terminadas y la que está en curso; las que cruzan medianoche se reparten entre días
  const charges = sessions.map(s => ({...s, live:false}));
  if (live) charges.push({start_ts: live.ts, end_ts: new Date().toISOString(), soc_start: live.soc, soc_end: live.soc_now, live:true});
  const oldest = Date.now() - 30*864e5;
  charges.forEach(s => {
    const a = new Date(s.start_ts), b = new Date(s.end_ts);
    if (b < oldest) return;
    for (let d = new Date(a.getFullYear(), a.getMonth(), a.getDate()); d <= b; d.setDate(d.getDate()+1)) {
      const from = d.toDateString() === a.toDateString() ? mins(a) : 0;
      const to = d.toDateString() === b.toDateString() ? mins(b) : 1440;
      slot(ymd(d)).charges.push({...s, from, to, first: d.toDateString() === a.toDateString()});
    }
  });
  const days = Object.keys(by).sort().reverse();
  if (!days.length){ $("days").innerHTML = `<p class="empty">Los viajes del día se guardan a las 23:45.</p>`; return; }
  $("days").innerHTML = days.map(d => {
    const {trips:ts, charges:cs} = by[d];
    const km = ts.reduce((a,t) => a + t.distance_km, 0), min = ts.reduce((a,t) => a + (t.drive_min||0), 0);
    const vmax = Math.max(0, ...ts.map(t => t.max_speed||0));
    const date = dlong(ymdDate(d));
    const items = [
      ...ts.map(t => ({at: tripMin(t), html:
        `<tr><td>${hhmm(tripMin(t))}</td><td class="n">${fmt(t.distance_km,1)}</td><td class="n">${fmt(t.drive_min)}</td><td class="n">${fmt(t.idle_min)}</td><td class="n">${fmt(t.avg_speed)}</td><td class="n">${fmt(t.max_speed)}</td></tr>`,
        bar: `<span style="left:${tripMin(t)/14.4}%;width:${Math.max((t.drive_min||1)/14.4,.4)}%;background:${speedColor(t.avg_speed||0)}" title="${hhmm(tripMin(t))} · ${fmt(t.distance_km,1)} km · ${fmt(t.avg_speed)} km/h"></span>`})),
      ...cs.map(c => ({at: c.from, html:
        `<tr class="chgrow"><td>${hhmm(c.from)}</td><td colspan="5">⚡ ${c.live ? "Cargando" : "Carga"}${c.first ? "" : " (desde el día anterior)"} · ${fmt(c.soc_start)} → ${fmt(c.soc_end)} %${c.live ? " ahora" : ` · hasta las ${hhmm(c.to)}${c.to < 1440 ? endReason(c) : ""}`}${c.kwh_est != null && !c.live ? ` · ${fmt(c.kwh_meter ?? c.kwh_est,1)} kWh · ${fmt(c.cost_eur,2)} €` : ""}</td></tr>`,
        bar: `<span class="chg" style="left:${c.from/14.4}%;width:${Math.max((c.to-c.from)/14.4,.4)}%" title="Carga ${hhmm(c.from)}–${hhmm(c.to)}"></span>`})),
    ].sort((a,b) => a.at - b.at);
    const chips = [ts.length ? `<span><b>${ts.length}</b> viajes</span><span><b>${Math.floor(min/60)} h ${min%60} min</b> al volante</span><span>máx <b>${fmt(vmax)}</b> km/h</span>` : "",
                   cs.length ? `<span class="euro"><b>${cs.length}</b> ${cs.length > 1 ? "cargas" : "carga"}</span>` : ""].join("");
    const dc = dayCost[d];
    const costChip = dc && dc.cost_est_eur != null && km
      ? `<span class="euro"><b>${fmt(dc.cost_est_eur,2)} €</b> · ${fmt(dc.cost_est_eur/km*100,1)} cént/km</span>` : "";
    return `<article class="day">
      <div class="dayhead"><span class="d">${date}</span><span class="km">${fmt(km,1)}<small>km</small></span></div>
      <div class="chips">${chips}${costChip}</div>
      <div class="tl" role="img" aria-label="Viajes y cargas a lo largo del día">${items.map(i => i.bar).join("")}</div>
      <div class="hours"><span>0 h</span><span>6 h</span><span>12 h</span><span>18 h</span><span>24 h</span></div>
      <details><summary>Detalles</summary><div class="scroll"><table><thead><tr><th>Hora</th><th class="n">km</th><th class="n">min</th><th class="n">parado</th><th class="n">km/h media</th><th class="n">máx</th></tr></thead><tbody>${items.map(i => i.html).join("")}</tbody></table></div></details>
    </article>`;
  }).join("");
}

const stat = (value, unit, label) => `<div class="stat"><b>${value}<small>${unit}</small></b><span title="${label}">${label}</span></div>`;

function renderSummary(e, k, ins, months){
  const d = e.d30 || {}, life = e.lifetime || {}, cur = months.length ? months[months.length-1] : null;
  const limit = k && k.charge_limit_ac ? k.charge_limit_ac : 100;
  const p = ins.projection, ds = e.fuel;
  $("sumnote").textContent = `Costes estimados con tu consumo y ${fmt(e.price_eur_kwh,3)} €/kWh (incluye pérdidas de carga). `
    + `Coche de ${ds.short} de referencia: ${fmt(ds.l_100,1)} L/100 km a ${fmt(ds.price_eur_l,2)} €/L = ${fmt(ds.eur_100,2)} €/100 km.`;
  $("summary").innerHTML = [
    stat(fmt(d.eur_100,2), "€/100 km", "coste de circular · 30 días"),
    stat(fmt(d.saving_eur,0), "€", `ahorro vs. ${ds.short} · 30 días`),
    stat(fmt(life.saving_eur,0), "€", `ahorro vs. ${ds.short} · desde nuevo`),
    stat(fmt(cur && cur.cost_est_eur,2), "€", `gasto en ${cur ? new Date(cur.period + "-01T12:00").toLocaleDateString("es-ES",{month:"long"}) : "este mes"} · ${fmt(cur && cur.km)} km`),
    stat(fmt(d.range_full_km), "km", `al 100 % · ${fmt(d.range_full_km*limit/100)} km al ${limit} %`),
    stat(p ? fmt(p.km_year) : "–", "km/año", p ? `${fmt(p.km_per_day)} km/día desde el ${dday(p.since)}` : "proyección"),
  ].join("");
}

function renderRecords(ins){
  const r = ins.records;
  if (!r){ $("records").innerHTML = `<p class="empty">Sin viajes todavía.</p>`; return; }
  $("records").innerHTML = [
    stat(fmt(r.longest_trip.distance_km,1), "km", `viaje más largo · ${dday(r.longest_trip.day)}`),
    stat(fmt(r.top_day.km,1), "km", `día con más km · ${dday(r.top_day.day)}`),
    stat(fmt(r.max_speed.max_speed), "km/h", `velocidad máxima · ${dday(r.max_speed.day)}`),
    stat(r.best_day ? fmt(r.best_day.kwh_100,1) : "–", "kWh/100", r.best_day ? `más eficiente · ${dday(r.best_day.day)}` : "día más eficiente"),
  ].join("");
}

function renderHabits(h){
  const max = Math.max(1, ...h.flat()), days = ["lun","mar","mié","jue","vie","sáb","dom"];
  let html = `<span class="lbl"></span>` + Array.from({length:24}, (_,i) => `<span class="lbl" style="text-align:center">${i % 6 ? "" : i}</span>`).join("");
  h.forEach((row, i) => {
    html += `<span class="lbl">${days[i]}</span>` + row.map((km, hr) =>
      `<div title="${days[i]} ${hr} h · ${fmt(km,1)} km" style="${km ? `background:${C("--charge")};opacity:${.25 + .75*km/max}` : ""}"></div>`).join("");
  });
  $("heat").innerHTML = html;
  const top = h.flatMap((row, d) => row.map((km, hr) => ({km, d, hr}))).sort((a, b) => b.km - a.km)[0];
  $("heat").setAttribute("aria-label", top && top.km ? `Mapa de km por día y hora. Donde más conduces: ${days[top.d]} a las ${top.hr} h (${fmt(top.km)} km en total).` : "Mapa de km por día y hora: sin viajes todavía.");
}

// Barras de km y línea de kWh/100 km por periodo (histórico del panel e informes)
function kmChart(canvas, labels, rows, bar, {animation = true, maxTicks = 12} = {}){
  if (typeof Chart === "undefined") return null;   // CDN caído: sin gráfica, las tablas siguen
  return new Chart(canvas, {
    data:{labels, datasets:[
      {type:"bar", label:"km", data:rows.map(r => r.km), backgroundColor:bar, yAxisID:"y", order:2},
      {type:"line", label:"kWh/100 km", data:rows.map(r => r.kwh_100), borderColor:C("--euro"), backgroundColor:C("--euro"), pointRadius:3, tension:.25, yAxisID:"y1", spanGaps:true, order:1}]},
    options:{animation, maintainAspectRatio:false, plugins:{legend:{labels:{color:C("--soft")}}},
      scales:{y:{position:"left", title:{display:true, text:"km"}}, y1:{position:"right", min:0, grid:{drawOnChartArea:false}, title:{display:true, text:"kWh/100 km"}}, x:{ticks:{maxTicksLimit:maxTicks}}}}
  });
}
let histChart;
const hist = {};   // /api/history por agrupación, se pide una sola vez
async function renderHist(group){
  document.querySelectorAll("main .tabs button").forEach(b => b.setAttribute("aria-pressed", b.dataset.g === group));
  let h = await (hist[group] ??= j("/api/history?group=" + group));
  if (group === "day") h = h.slice(-31);
  $("hist").innerHTML = h.length ? h.slice().reverse().map(r => `<tr><td>${r.period}</td><td class="n">${fmt(r.km,1)}</td><td class="n">${fmt(r.trips)}</td><td class="n">${fmt(r.drive_min/60,1)}</td><td class="n">${fmt(r.kwh_100,1)}</td><td class="n">${fmt(r.l_100,2)}</td><td class="n">${fmt(r.cost_est_eur,2)}</td><td class="n">${fmt(r.charged_kwh,1)}</td><td class="n">${fmt(r.cost_eur,2)}</td></tr>`).join("")
    : `<tr><td colspan="9" class="empty">Sin datos todavía.</td></tr>`;
  if (histChart) histChart.destroy();
  histChart = kmChart($("histChart"), h.map(r => r.period), h, C("--charge"));
}

// Ajustes: formulario generado desde /api/settings (definición, límites y valor actual)
let setDefs = [];
const setVal = key => (setDefs.find(f => f.key === key) || {}).value;
function applyName(name){ $("carname").textContent = name; document.title = `${name} · MiEV3`; }
// Color del coche → variables del tema (ver :root). Sin opción conocida, se queda el neutro.
function applyTheme(key){
  const f = setDefs.find(f => f.key === "CAR_COLOR"), c = f && f.options[key];
  if (!c) return;
  const st = document.documentElement.style;
  st.setProperty("--paint", c.paint); st.setProperty("--acc-l", c.accent); st.setProperty("--acc-d", c.accent_dark);
}
const isPalette = f => f.kind === "choice" && Object.values(f.options).every(o => o.paint);
function choiceInput(f){
  if (isPalette(f)) {   // opciones con color de pintura → muestras; vista previa al pulsar
    const box = document.createElement("div"); box.className = "colors"; box.setAttribute("role", "radiogroup"); box.setAttribute("aria-label", f.label);
    Object.entries(f.options).forEach(([k, c]) => {
      const lb = document.createElement("label"), r = document.createElement("input"), i = document.createElement("i"), t = document.createElement("span");
      r.type = "radio"; r.name = f.key; r.value = k; r.checked = k === f.value; r.onchange = () => applyTheme(k);
      i.style.background = c.paint; t.textContent = c.label;
      lb.append(r, i, t); box.append(lb);
    });
    return box;
  }
  const sel = document.createElement("select"); sel.id = "set_" + f.key; sel.name = f.key;
  Object.entries(f.options).forEach(([k, o]) => sel.add(new Option(o.label, k, false, k === f.value)));
  return sel;
}
function renderSettings(defs){
  setDefs = defs;
  const groups = [...new Set(defs.map(f => f.group))];
  const box = $("setfields"); box.replaceChildren();
  groups.forEach(g => {
    const fs = document.createElement("fieldset"), lg = document.createElement("legend");
    lg.textContent = g; fs.append(lg);
    defs.filter(f => f.group === g).forEach(f => {
      const row = document.createElement("div"); row.className = "field" + (isPalette(f) ? " palette" : "");
      const lb = document.createElement(isPalette(f) ? "span" : "label"); lb.textContent = f.label;
      if (isPalette(f)) lb.className = "lbl-text"; else lb.htmlFor = "set_" + f.key;
      const wrap = document.createElement("div"); wrap.className = "in";
      if (f.kind === "choice") wrap.append(choiceInput(f));
      else {
        const inp = document.createElement("input"); inp.id = "set_" + f.key; inp.name = f.key;
        inp.value = f.value ?? "";
        if (f.kind === "text") { inp.type = "text"; inp.maxLength = f.hi; }
        else { inp.type = "text"; inp.inputMode = "decimal"; inp.autocomplete = "off"; }
        wrap.append(inp);
      }
      if (f.unit) { const u = document.createElement("span"); u.className = "unit"; u.textContent = f.unit; wrap.append(u); }
      row.append(lb, wrap);
      if (f.help) { const h = document.createElement("small"); h.textContent = f.help; row.append(h); }
      fs.append(row);
    });
    box.append(fs);
  });
}
$("openset").onclick = () => { renderSettings(setDefs); $("setmsg").textContent = ""; $("settings").showModal(); };
$("setcancel").onclick = () => $("settings").close();
$("settings").onclose = () => applyTheme(setVal("CAR_COLOR"));   // deshace la vista previa si no se guarda
$("setform").onsubmit = async ev => {
  ev.preventDefault();
  // Solo se envía lo que ha cambiado
  const changed = {};
  const els = $("setform").elements;
  setDefs.forEach(f => { const v = els[f.key].value; if (String(f.value ?? "") !== v) changed[f.key] = v; });
  if (!Object.keys(changed).length) { $("settings").close(); return; }
  try {
    const r = await fetch("/api/settings", {method:"PUT", headers:{"Content-Type":"application/json"}, body:JSON.stringify(changed)});
    const body = await r.json().catch(() => ({}));
    if (!r.ok) { $("setmsg").textContent = typeof body.detail === "string" ? body.detail : `No se ha guardado (error ${r.status}).`; return; }
  } catch {
    $("setmsg").textContent = "No se ha podido conectar con el servidor. Inténtalo de nuevo.";
    return;
  }
  location.reload();
};

// Informes: semana, mes o histórico. «Descargar PDF» abre la impresión del navegador
// (Guardar como PDF); el título de la página es el nombre que propone para el fichero.
function isoWeekMonday(key){   // "2026-W45" → Date del lunes de esa semana ISO
  const [y, w] = key.split("-W").map(Number), jan4 = new Date(y, 0, 4);
  return new Date(y, 0, 4 - (jan4.getDay() + 6) % 7 + (w - 1) * 7);
}
function periodLabel(p, key){
  if (p === "week") { const a = isoWeekMonday(key), b = new Date(a); b.setDate(b.getDate() + 6); return `${dshort(a)} – ${dshort(b)} ${b.getFullYear()}`; }
  return new Date(key + "-01T12:00").toLocaleDateString("es-ES",{month:"long", year:"numeric"});
}
let repPeriod = "month", repChart, mainTitle;
async function openReport(){
  mainTitle = document.title;
  document.body.classList.add("reporting"); $("report").hidden = false; window.scrollTo(0, 0);
  $("repback").focus();
  await repTab(repPeriod);
}
function closeReport(){ document.body.classList.remove("reporting"); $("report").hidden = true; document.title = mainTitle; $("openrep").focus(); }
async function repTab(p){
  repPeriod = p;
  document.querySelectorAll("#report .tabs button").forEach(b => b.setAttribute("aria-pressed", b.dataset.p === p));
  const sel = $("repkey"); sel.hidden = p === "all"; sel.replaceChildren();
  if (p !== "all") {
    const h = await (hist[p] ??= j("/api/history?group=" + p));
    h.slice().reverse().forEach(r => sel.add(new Option(periodLabel(p, r.period), r.period)));   // el más reciente primero
  }
  await renderReport();
}
async function renderReport(){
  const p = repPeriod, key = $("repkey").value;
  if (p !== "all" && !key) {
    $("reptitle").textContent = "Aún no hay viajes para hacer informes";
    ["repstats","replines","reptrips","replineshead"].forEach(id => $(id).replaceChildren());
    $("repnote").textContent = ""; $("reptripsbox").hidden = true; if (repChart) repChart.destroy();
    $("repprint").disabled = true;
    return;
  }
  const r = await j(`/api/report?period=${p}&key=${encodeURIComponent(key)}`), t = r.totals, f = r.fuel;
  $("repprint").disabled = !r.rows.length;
  $("repcar").textContent = setVal("CAR_NAME");
  $("reptitle").textContent = p === "all" ? `Informe histórico · desde el ${dday(r.first)} ${r.first.slice(0,4)}`
    : `Informe ${p === "week" ? "semanal" : "mensual"} · ${periodLabel(p, key)}`;
  $("repgen").textContent = "Generado el " + new Date(r.generated).toLocaleString("es-ES",{day:"numeric", month:"short", year:"numeric", hour:"2-digit", minute:"2-digit"});
  document.title = `MiEV3 informe ${p === "all" ? "historico" : key}`;
  $("repstats").innerHTML = [
    stat(fmt(t.km,1), "km", "recorridos"),
    stat(fmt(t.trips), "viajes", `${fmt(t.drive_min/60,1)} h al volante`),
    stat(fmt(t.kwh_100,1), "kWh/100", "consumo medio"),
    stat(fmt(t.kwh,1), "kWh", "energía consumida"),
    stat(fmt(t.cost_est_eur,2), "€", "coste estimado en luz"),
    stat(fmt(t.fuel_eur,2), "€", `lo mismo en ${f.short}`),
    stat(fmt(t.saving_eur,2), "€", `ahorro vs. ${f.short}`),
    stat(fmt(t.charges), t.charges === 1 ? "carga" : "cargas", `${fmt(t.charged_kwh,1)} kWh · ${fmt(t.cost_eur,2)} €`),
  ].join("");
  const byMonth = p === "all", lab = byMonth ? x => periodLabel("month", x.period)
    : x => dlong(new Date(x.period + "T12:00"));
  $("replinestitle").textContent = byMonth ? "Por mes" : "Por día";
  $("replineshead").innerHTML = `<tr><th>${byMonth ? "Mes" : "Día"}</th><th class="n">km</th><th class="n">viajes</th><th class="n">h</th><th class="n">kWh/100</th><th class="n">€ luz</th><th class="n">ahorro €</th><th class="n">kWh cargados</th></tr>`;
  $("replines").innerHTML = r.rows.length ? r.rows.map(x => `<tr><td>${lab(x)}</td><td class="n">${fmt(x.km,1)}</td><td class="n">${fmt(x.trips)}</td><td class="n">${fmt(x.drive_min/60,1)}</td><td class="n">${fmt(x.kwh_100,1)}</td><td class="n">${fmt(x.cost_est_eur,2)}</td><td class="n">${fmt(x.saving_eur,2)}</td><td class="n">${fmt(x.charged_kwh,1)}</td></tr>`).join("")
    : `<tr><td colspan="8" class="empty">Sin datos en este periodo.</td></tr>`;
  $("reptripsbox").hidden = byMonth;
  $("reptrips").innerHTML = r.trips.map(x => `<tr><td>${dday(x.day)}</td><td>${hhmm(tripMin(x))}</td><td class="n">${fmt(x.distance_km,1)}</td><td class="n">${fmt(x.drive_min)}</td><td class="n">${fmt(x.idle_min)}</td><td class="n">${fmt(x.avg_speed)}</td><td class="n">${fmt(x.max_speed)}</td></tr>`).join("")
    || `<tr><td colspan="7" class="empty">Sin viajes.</td></tr>`;
  const odo = r.odometer.start != null ? ` Odómetro: ${fmt(r.odometer.start)} → ${fmt(r.odometer.end)} km.` : "";
  $("repnote").textContent = `Consumo ${r.calibrated ? "calibrado con el % de batería" : "provisional: contador del coche sin calibrar todavía"}. `
    + `Coste en luz estimado a ${fmt(setVal("PRICE_EUR_KWH"),3)} €/kWh, con pérdidas de carga. `
    + `Comparación: ${f.label}, ${fmt(f.l_100,1)} L/100 km a ${fmt(f.price_eur_l,2)} €/L, sobre los km con consumo conocido. `
    + `Las cargas cuentan el día en que terminan.${odo}`;
  if (repChart) repChart.destroy();
  // Sin animación, para que la gráfica esté completa al imprimir; tono oscuro, legible en papel
  repChart = kmChart($("repChart"), r.rows.map(lab), r.rows, C("--acc-l"), {animation:false, maxTicks:16});
}
$("openrep").onclick = openReport;
$("repback").onclick = closeReport;
document.querySelectorAll("#report .tabs button").forEach(b => b.onclick = () => repTab(b.dataset.p));
$("repkey").onchange = renderReport;
$("repprint").onclick = () => window.print();
// Al imprimir, el navegador no avisa a Chart.js del ancho del papel: se redibuja al ancho útil de un
// A4 (210 mm − 2 × 12 mm de @page ≈ 700 px CSS) y vuelve al de pantalla después. Para otros
// papeles, el CSS de impresión la encoge si no cabe.
const PRINT_CHART_PX = 700;
addEventListener("beforeprint", () => repChart && repChart.resize(PRINT_CHART_PX, 220));
addEventListener("afterprint", () => repChart && repChart.resize());

// Cada sección se pinta por su cuenta: si un dato no llega o una sección falla, el resto se ve
// igual y la cabecera avisa (antes, un solo error dejaba el panel en blanco).
async function load(){
  const urls = ["/api/settings", "/api/status", "/api/snapshots?days=7", "/api/sessions", "/api/trips", "/api/efficiency", "/api/insights", "/api/history?group=month", "/api/history?group=day"];
  const got = await Promise.allSettled(urls.map(j));
  const [defs, st, snaps, ses, trips, eff, ins, months, days] = got.map(g => g.status === "fulfilled" ? g.value : null);
  let failed = got.filter(g => g.status === "rejected").length;
  const part = (...needs) => fn => {
    if (needs.some(x => x == null)) return;
    try { fn(); } catch (e) { failed++; console.warn("Sección no pintada:", e); }
  };
  if (months) hist.month = months;
  if (days) hist.day = days;
  part(defs)(() => { renderSettings(defs); applyTheme(setVal("CAR_COLOR")); applyName(setVal("CAR_NAME")); });
  part(st)(() => {
    if (st.photo) { $("hero").innerHTML = `<img src="/img/car.webp" alt="Foto del coche">`; $("hero").hidden = false; }
    if (st.kia) renderNow(st.kia, st.v2c);
    else {   // primera vez: bienvenida en lugar de un panel vacío
      $("updated").textContent = "Esperando la primera lectura del coche";
      const w = document.createElement("div"); w.className = "welcome";
      const b = document.createElement("b"); b.textContent = "Bienvenido a MiEV3";
      const t = document.createElement("span");
      t.textContent = "Todavía no hay datos de tu coche. La primera lectura llega unos segundos después de arrancar el colector "
        + "(pm2 start ecosystem.config.js). Si no aparece, la guía docs/conectar-api.md explica cómo comprobar la conexión. "
        + "Mientras, pulsa ⚙ Ajustes para poner el nombre, el color y los precios.";
      w.append(b, t); $("now").replaceChildren(w);
    }
  });
  part(eff)(() => renderEff(eff));
  part(eff, st, ins, months)(() => { eff.price_eur_kwh = st.price; renderSummary(eff, st.kia, ins, months); });
  part(ins)(() => { renderRecords(ins); renderHabits(ins.habits); });
  part(months)(() => {
    renderHist("month");
    document.querySelectorAll("main .tabs button").forEach(b => b.onclick = () => renderHist(b.dataset.g));
  });
  part(ses)(() => {
    $("sessions").innerHTML = ses.length ? ses.slice(0,15).map(s =>
      `<tr><td>${dt(s.end_ts)}</td><td>${fmt(s.soc_start)} → ${fmt(s.soc_end)}%${endReason(s)}</td><td class="n">${fmt(s.kwh_meter ?? s.kwh_est,1)}</td><td class="n">${fmt(s.cost_eur,2)}</td></tr>`).join("")
      : `<tr><td colspan="4" class="empty">Las cargas aparecerán aquí al terminar la primera.</td></tr>`;
  });
  part(trips, ses, st, days)(() => {
    const live = st.charging_since && st.kia ? {...st.charging_since, soc_now: st.kia.soc} : null;
    renderDays(trips, ses, live, Object.fromEntries(days.map(r => [r.period.replaceAll("-", ""), r])));
  });
  part(snaps)(() => {
    const v = snaps.map(s => s.soc).filter(x => x != null);
    if (v.length) $("socChart").setAttribute("aria-label", `Batería de los últimos 7 días: entre ${fmt(Math.min(...v))} % y ${fmt(Math.max(...v))} %, ahora ${fmt(v[v.length-1])} %.`);
  });
  part(snaps)(() => new Chart($("socChart"), {
    type:"line",
    data:{labels:snaps.map(s => dt(s.ts)), datasets:[{data:snaps.map(s => s.soc), borderColor:C("--charge"), borderWidth:2, pointRadius:snaps.length < 20 ? 3 : 0, tension:.25, fill:false}]},
    options:{maintainAspectRatio:false, plugins:{legend:{display:false}}, scales:{y:{min:0,max:100,ticks:{callback:v => v + "%"}}, x:{ticks:{maxTicksLimit:7}}}}
  }));
  if (failed) $("updated").textContent = `${st && st.kia ? $("updated").textContent + " · " : ""}⚠️ Parte de los datos no se ha podido cargar; recarga en un rato.`;
}
load();
