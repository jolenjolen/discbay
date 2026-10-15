import * as labels from "./labels.js";

// DiscBay Studio front end. Plain modules, no build step.

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

async function api(path, body) {
  const opts = body === undefined ? {} : {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-DiscBay-Studio": "1" },
    body: JSON.stringify(body),
  };
  const res = await fetch(path, opts);
  return res;
}
async function apiJson(path, body) {
  const res = await api(path, body);
  return res.json();
}

const state = {
  target: null,            // { kind, value, name, iconUrl, artUrl }
  coverSource: null,       // "image" | "icon" | null
  coverAuto: false,        // true when the cover came from the chosen app, not from the user
  colorTouched: false,
  nameTouched: false,
  labelTouched: false,
  media: null,
  space: null,
  burning: false,
  loaded: {},
};

// ------------------------------------------------------------------ colour
function rgbToHsl(r, g, b) {
  r /= 255; g /= 255; b /= 255;
  const max = Math.max(r, g, b), min = Math.min(r, g, b);
  let h = 0, s = 0; const l = (max + min) / 2;
  if (max !== min) {
    const d = max - min;
    s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
    h = max === r ? (g - b) / d + (g < b ? 6 : 0) : max === g ? (b - r) / d + 2 : (r - g) / d + 4;
    h /= 6;
  }
  return [h, s, l];
}
function hslToHex(h, s, l) {
  const f = (n) => {
    const k = (n + h * 12) % 12, a = s * Math.min(l, 1 - l);
    const c = l - a * Math.max(-1, Math.min(k - 3, 9 - k, 1));
    return Math.round(c * 255).toString(16).padStart(2, "0");
  };
  return `#${f(0)}${f(8)}${f(4)}`;
}

/** Dominant vivid colour of an image, adjusted so white text reads on it. */
function extractColor(source) {
  const c = document.createElement("canvas");
  c.width = c.height = 48;
  const ctx = c.getContext("2d", { willReadFrequently: true });
  ctx.drawImage(source, 0, 0, 48, 48);
  const px = ctx.getImageData(0, 0, 48, 48).data;
  const bins = Array.from({ length: 24 }, () => ({ w: 0, r: 0, g: 0, b: 0 }));
  let vivid = 0;
  for (let i = 0; i < px.length; i += 4) {
    const [r, g, b, a] = [px[i], px[i + 1], px[i + 2], px[i + 3] / 255];
    if (a < 0.5) continue;
    const [h, s, l] = rgbToHsl(r, g, b);
    const w = s * (1 - Math.abs(l - 0.5) * 1.6) * a;   // favour saturated mid-tones
    if (w <= 0.05) continue;
    const bin = bins[Math.floor(h * 24) % 24];
    bin.w += w; bin.r += r * w; bin.g += g * w; bin.b += b * w;
    vivid += w;
  }
  if (vivid < 20) return null;                          // greyscale image: keep the current colour
  const best = bins.reduce((a, b) => (b.w > a.w ? b : a));
  const [h, s, l] = rgbToHsl(best.r / best.w, best.g / best.w, best.b / best.w);
  return hslToHex(h, Math.max(0.42, Math.min(s, 0.85)), Math.max(0.36, Math.min(l, 0.52)));
}

function setAccent(hex, { fromUser = false } = {}) {
  document.documentElement.style.setProperty("--accent", hex);
  $("#color").value = hex;
  if (fromUser) state.colorTouched = true;
  $("#colorNote").textContent = state.colorTouched ? "Chosen by you" : state.coverSource ? "Picked from the cover" : "";
  if (state.coverSource === "icon") drawIconCover();
}

// ------------------------------------------------------------------ cropper
const crop = {
  canvas: $("#cropCanvas"),
  img: null, base: 1, zoom: 1, x: 0, y: 0,
  get ctx() { return this.canvas.getContext("2d"); },
  get size() { return this.canvas.width; },
};

function clampCrop() {
  const s = crop.base * crop.zoom, w = crop.img.width * s, h = crop.img.height * s;
  crop.x = Math.min(0, Math.max(crop.size - w, crop.x));
  crop.y = Math.min(0, Math.max(crop.size - h, crop.y));
}

function drawCrop() {
  const ctx = crop.ctx;
  ctx.clearRect(0, 0, crop.size, crop.size);
  if (!crop.img) return;
  const s = crop.base * crop.zoom;
  ctx.drawImage(crop.img, crop.x, crop.y, crop.img.width * s, crop.img.height * s);
}

/**
 * Smart crop: slide a square window along the image's long side and keep the
 * position with the most detail (edge energy), with a gentle pull to the centre.
 */
function smartCrop() {
  const img = crop.img;
  const scale = 160 / Math.max(img.width, img.height);
  const w = Math.max(1, Math.round(img.width * scale)), h = Math.max(1, Math.round(img.height * scale));
  const c = document.createElement("canvas");
  c.width = w; c.height = h;
  const ctx = c.getContext("2d", { willReadFrequently: true });
  ctx.drawImage(img, 0, 0, w, h);
  const px = ctx.getImageData(0, 0, w, h).data;
  const luma = new Float32Array(w * h);
  for (let i = 0; i < w * h; i++) luma[i] = px[i * 4] * 0.299 + px[i * 4 + 1] * 0.587 + px[i * 4 + 2] * 0.114;

  const horizontal = w >= h;                 // slide along x when the image is landscape
  const side = Math.min(w, h), span = (horizontal ? w : h) - side;
  const lineEnergy = new Float32Array(horizontal ? w : h);
  for (let y = 1; y < h - 1; y++) {
    for (let x = 1; x < w - 1; x++) {
      const i = y * w + x;
      const e = Math.abs(luma[i + 1] - luma[i - 1]) + Math.abs(luma[i + w] - luma[i - w]);
      lineEnergy[horizontal ? x : y] += e;
    }
  }
  let best = Math.round(span / 2), bestScore = -Infinity, sum = 0;
  for (let i = 0; i < side; i++) sum += lineEnergy[i];
  for (let start = 0; start <= span; start++) {
    if (start > 0) sum += lineEnergy[start + side - 1] - lineEnergy[start - 1];
    const centreBias = span ? 1 - 0.25 * Math.abs(start - span / 2) / (span / 2) : 1;
    const score = sum * centreBias;
    if (score > bestScore) { bestScore = score; best = start; }
  }
  crop.zoom = 1;
  $("#zoom").value = 1;
  crop.base = crop.size / Math.min(img.width, img.height);
  const offset = (best / scale) * crop.base;
  crop.x = horizontal ? -offset : 0;
  crop.y = horizontal ? 0 : -offset;
  clampCrop();
}

function exportCover(size = 512) {
  const out = renderCover(size);
  const png = out.toDataURL("image/png");
  return png.length > 1_600_000 ? out.toDataURL("image/jpeg", 0.9) : png;
}

let previewQueued = false;
function updatePreview() {
  if (previewQueued) return;
  previewQueued = true;
  requestAnimationFrame(() => {
    previewQueued = false;
    const url = state.coverSource ? crop.canvas.toDataURL("image/jpeg", 0.8) : null;
    document.documentElement.style.setProperty("--cover", url ? `url("${url}")` : "none");
  });
}

function loadImage(src) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = reject;
    img.src = src;
  });
}

async function useImage(src, { auto = false } = {}) {
  try {
    crop.img = await loadImage(src);
  } catch {
    showError("That file couldn't be opened as an image.");
    return;
  }
  state.coverSource = "image";
  state.coverAuto = auto;
  smartCrop();
  drawCrop();
  $("#dropzone").classList.add("has-image");
  $("#zoom").disabled = false;
  $("#autoCrop").disabled = false;
  $("#clearCover").hidden = false;
  if (!state.colorTouched) {
    const color = extractColor(crop.img);
    if (color) setAccent(color);
  }
  $("#colorNote").textContent = state.colorTouched ? "Chosen by you" : "Picked from the cover";
  updatePreview();
}

let iconImage = null;
function drawIconCover() {
  if (!iconImage) return;
  paintIconCover(crop.ctx, crop.size);
  updatePreview();
}

/** An app icon on a soft gradient of the disc colour, at any size. */
function paintIconCover(ctx, n) {
  const accent = getComputedStyle(document.documentElement).getPropertyValue("--accent").trim() || "#2f6feb";
  const [h, s, l] = rgbToHsl(...hexToRgb(accent));
  const g = ctx.createRadialGradient(n * 0.35, n * 0.3, n * 0.05, n * 0.5, n * 0.5, n * 0.75);
  g.addColorStop(0, hslToHex(h, s, Math.min(0.75, l + 0.2)));
  g.addColorStop(1, hslToHex(h, s, Math.max(0.15, l - 0.15)));
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, n, n);
  const size = n * 0.42;
  ctx.save();
  ctx.shadowColor = "rgba(0,0,0,.35)";
  ctx.shadowBlur = n * 0.04;
  ctx.shadowOffsetY = n * 0.015;
  // Sit the icon in the outer ring, above the hole, so the centre stays clear like a real disc.
  ctx.drawImage(iconImage, (n - size) / 2, n * 0.12, size, size);
  ctx.restore();
}

/**
 * The cover at any resolution, drawn again from the original image (not the
 * 480 px editor), so printed labels stay sharp.
 */
function renderCover(size) {
  const out = document.createElement("canvas");
  out.width = out.height = size;
  const ctx = out.getContext("2d");
  if (state.coverSource === "image" && crop.img) {
    const k = size / crop.size, s = crop.base * crop.zoom * k;
    ctx.imageSmoothingQuality = "high";
    ctx.drawImage(crop.img, crop.x * k, crop.y * k, crop.img.width * s, crop.img.height * s);
  } else if (state.coverSource === "icon" && iconImage) {
    paintIconCover(ctx, size);
  } else {
    const accent = $("#color").value;
    const [h, sat, l] = rgbToHsl(...hexToRgb(accent));
    const g = ctx.createRadialGradient(size * 0.35, size * 0.3, size * 0.05, size * 0.5, size * 0.5, size * 0.75);
    g.addColorStop(0, hslToHex(h, sat, Math.min(0.75, l + 0.2)));
    g.addColorStop(1, hslToHex(h, sat, Math.max(0.15, l - 0.15)));
    ctx.fillStyle = g;
    ctx.fillRect(0, 0, size, size);
  }
  return out;
}
function hexToRgb(hex) {
  const v = hex.replace("#", "");
  return [0, 2, 4].map((i) => parseInt(v.slice(i, i + 2), 16));
}

async function useIconCover() {
  if (!state.target?.iconUrl) return;
  try {
    iconImage = await loadImage(state.target.iconUrl);
  } catch {
    return;
  }
  crop.img = null;
  state.coverSource = "icon";
  state.coverAuto = true;
  $("#dropzone").classList.add("has-image");
  $("#zoom").disabled = true;
  $("#autoCrop").disabled = true;
  $("#clearCover").hidden = false;
  if (!state.colorTouched) {
    const color = extractColor(iconImage);
    if (color) {
      document.documentElement.style.setProperty("--accent", color);
      $("#color").value = color;
    }
  }
  $("#colorNote").textContent = state.colorTouched ? "Chosen by you" : "Picked from the icon";
  drawIconCover();
}

function clearCover() {
  crop.img = null;
  iconImage = null;
  state.coverSource = null;
  crop.ctx.clearRect(0, 0, crop.size, crop.size);
  $("#dropzone").classList.remove("has-image");
  $("#zoom").disabled = true;
  $("#autoCrop").disabled = true;
  $("#clearCover").hidden = true;
  $("#colorNote").textContent = state.colorTouched ? "Chosen by you" : "";
  updatePreview();
}

function setupCropper() {
  const zone = $("#dropzone"), input = $("#fileInput");
  const pick = () => input.click();
  zone.addEventListener("click", () => { if (state.coverSource !== "image") pick(); });
  zone.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); pick(); } });
  input.addEventListener("change", () => { if (input.files[0]) useImage(URL.createObjectURL(input.files[0])); input.value = ""; });

  zone.addEventListener("dragover", (e) => { e.preventDefault(); zone.classList.add("dragging"); });
  zone.addEventListener("dragleave", () => zone.classList.remove("dragging"));
  zone.addEventListener("drop", (e) => {
    e.preventDefault();
    zone.classList.remove("dragging");
    const file = [...e.dataTransfer.files].find((f) => f.type.startsWith("image/"));
    if (file) useImage(URL.createObjectURL(file));
  });
    // The whole page accepts a dropped image, so a near miss still works.
    window.addEventListener("dragover", (e) => e.preventDefault());
    window.addEventListener("drop", (e) => {
      e.preventDefault();
      const file = [...(e.dataTransfer?.files || [])].find((f) => f.type.startsWith("image/"));
      if (file) useImage(URL.createObjectURL(file));
    });

      // Drag to reposition.
      let drag = null;
      zone.addEventListener("pointerdown", (e) => {
        if (state.coverSource !== "image") return;
        zone.setPointerCapture(e.pointerId);
        const ratio = crop.size / zone.clientWidth;
        drag = { x: e.clientX, y: e.clientY, ox: crop.x, oy: crop.y, ratio, moved: false };
      });
      zone.addEventListener("pointermove", (e) => {
        if (!drag) return;
        const dx = (e.clientX - drag.x) * drag.ratio, dy = (e.clientY - drag.y) * drag.ratio;
        if (Math.abs(dx) + Math.abs(dy) > 2) drag.moved = true;
        crop.x = drag.ox + dx; crop.y = drag.oy + dy;
        clampCrop(); drawCrop(); updatePreview();
      });
      const end = () => { drag = null; };
      zone.addEventListener("pointerup", end);
      zone.addEventListener("pointercancel", end);
      zone.addEventListener("wheel", (e) => {
        if (state.coverSource !== "image") return;
        e.preventDefault();
        setZoom(crop.zoom * (e.deltaY < 0 ? 1.06 : 1 / 1.06));
      }, { passive: false });

      $("#zoom").addEventListener("input", (e) => setZoom(parseFloat(e.target.value)));
      $("#autoCrop").addEventListener("click", () => { smartCrop(); drawCrop(); updatePreview(); });
      $("#useIcon").addEventListener("click", useIconCover);
      $("#useSteamArt").addEventListener("click", () => state.target?.artUrl && useImage(state.target.artUrl, { auto: true }));
      $("#clearCover").addEventListener("click", clearCover);
      $("#color").addEventListener("input", (e) => setAccent(e.target.value, { fromUser: true }));
}

function setZoom(z) {
  if (!crop.img) return;
  z = Math.max(1, Math.min(3, z));
  const c = crop.size / 2, s0 = crop.base * crop.zoom, s1 = crop.base * z;
  // Zoom around the centre of the view.
  crop.x = c - ((c - crop.x) / s0) * s1;
  crop.y = c - ((c - crop.y) / s0) * s1;
  crop.zoom = z;
  $("#zoom").value = z;
  clampCrop(); drawCrop(); updatePreview();
}

// ------------------------------------------------------------------ choosing a target
function labelFromName(name) {
  const label = name.toUpperCase().replace(/[^A-Z0-9]+/g, "_").replace(/^_+|_+$/g, "");
  return (label || "DISC").slice(0, 32);
}

function selectTarget(target, element) {
  state.target = target;
  $$('[aria-selected="true"]', $("#form")).forEach((el) => { if (!el.classList.contains("seg")) el.setAttribute("aria-selected", "false"); });
  element?.setAttribute("aria-selected", "true");
  if (!state.nameTouched) {
    $("#name").value = target.name;
    if (!state.labelTouched) $("#label").value = labelFromName(target.name);
  }
  $("#useIcon").disabled = !target.iconUrl;
  $("#useSteamArt").hidden = !target.artUrl;
  // Give every disc a cover straight away; a cover the user chose is kept.
  if (!state.coverSource || state.coverAuto) {
    if (target.artUrl) useImage(target.artUrl, { auto: true });
    else if (target.iconUrl) useIconCover();
    else clearCover();
  }
  refresh();
}

function emptyState(container, text) {
  container.innerHTML = "";
  const p = document.createElement("p");
  p.className = "empty";
  p.textContent = text;
  container.append(p);
}

function makeTile({ label, imgUrl, glyph, onPick, title }) {
  const b = document.createElement("button");
  b.type = "button";
  b.className = "tile";
  b.setAttribute("role", "option");
  b.setAttribute("aria-selected", "false");
  if (title) b.title = title;
  if (imgUrl) {
    const img = document.createElement("img");
    img.src = imgUrl; img.alt = ""; img.loading = "lazy";
    img.onerror = () => img.replaceWith(Object.assign(document.createElement("span"), { className: "glyph", textContent: glyph }));
    b.append(img);
  } else {
    b.append(Object.assign(document.createElement("span"), { className: "glyph", textContent: glyph }));
  }
  b.append(Object.assign(document.createElement("span"), { textContent: label }));
  b.addEventListener("click", () => onPick(b));
  return b;
}

async function loadApps() {
  const box = $("#appTiles");
  emptyState(box, "Loading your apps");
  const apps = await apiJson("/api/apps");
  state.loaded.apps = apps;
  renderApps();
}
function renderApps() {
  const box = $("#appTiles"), q = $("#appSearch").value.trim().toLowerCase(), gamesOnly = $("#gamesOnly").checked;
  const apps = (state.loaded.apps || []).filter((a) =>
  (!gamesOnly || a.game) && (!q || a.name.toLowerCase().includes(q) || a.id.toLowerCase().includes(q)));
  if (!apps.length) return emptyState(box, q || gamesOnly ? "No apps match." : "No apps found.");
  box.innerHTML = "";
  for (const a of apps) {
    const iconUrl = a.has_icon ? `/api/icon/${encodeURIComponent(a.id)}` : null;
    const tile = makeTile({
      label: a.name, imgUrl: iconUrl, glyph: a.name[0]?.toUpperCase() || "?", title: a.id,
                          onPick: (el) => selectTarget({ kind: "app", value: a.id, name: a.name, iconUrl }, el),
    });
    if (state.target?.kind === "app" && state.target.value === a.id) tile.setAttribute("aria-selected", "true");
    box.append(tile);
  }
}

async function loadSteam() {
  const box = $("#steamTiles");
  emptyState(box, "Looking in your Steam library");
  const games = await apiJson("/api/steam");
  if (!games.length) return emptyState(box, "No installed Steam games found. You can enter a game's app ID under Other.");
  box.innerHTML = "";
  for (const g of games) {
    const artUrl = g.has_art ? `/api/steam/${g.id}/art` : null;
    box.append(makeTile({
      label: g.name, imgUrl: artUrl, glyph: g.name[0]?.toUpperCase() || "?", title: `Steam app ${g.id}`,
                        onPick: (el) => selectTarget({ kind: "steam", value: g.id, name: g.name, artUrl }, el),
    }));
  }
}

async function loadAppImages() {
  const box = $("#appimageRows");
  emptyState(box, "Looking for AppImages");
  const items = await apiJson("/api/appimages");
  if (!items.length) return emptyState(box, "No AppImages found in Applications, Apps, Games or Downloads. Enter a path under Other.");
  box.innerHTML = "";
  for (const a of items) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "row";
    b.setAttribute("role", "option");
    b.setAttribute("aria-selected", "false");
    b.append(Object.assign(document.createElement("span"), { textContent: a.name }),
             Object.assign(document.createElement("small"), { textContent: a.path }));
    b.addEventListener("click", () => selectTarget({ kind: "exec", value: a.path, name: a.name }, b));
    box.append(b);
  }
}

const OTHER = {
  exec: ["Path to the program", "~/Games/Celeste/Celeste"],
  url: ["Website, file or folder", "https://www.openttd.org or ~/Music"],
  command: ["Command", "retroarch -L core.so ~/roms/game.sfc"],
  steam: ["Steam app ID", "1536610"],
};
const isWebsite = (t) => t?.kind === "url" && /^https?:\/\//i.test(t.value || "");
function otherChanged() {
  const kind = $("#otherKind").value, value = $("#otherTarget").value.trim();
  // Websites can open like a game: in their own full-screen window instead of a tab.
  $("#windowField").hidden = !(kind === "url" && /^https?:\/\//i.test(value));
  $("#otherLabel").textContent = OTHER[kind][0];
  $("#otherTarget").placeholder = OTHER[kind][1];
  if (!value) { state.target = null; refresh(); return; }
  const base = value.replace(/\/+$/, "").split("/").pop().replace(/\.(AppImage|sh|x86_64)$/i, "");
  const name = kind === "url" && /^https?:/.test(value) ? new URL(value, location.href).hostname.replace(/^www\./, "")
  : kind === "command" ? value.split(/\s+/)[0].split("/").pop()
  : kind === "steam" ? `Steam game ${value}` : base;
  state.target = { kind, value, name };
  if (!state.nameTouched) { $("#name").value = name; if (!state.labelTouched) $("#label").value = labelFromName(name); }
  refresh();
}

function setupSources() {
  const loaders = { apps: loadApps, steam: loadSteam, appimages: loadAppImages };
  $$(".seg").forEach((seg) => seg.addEventListener("click", () => {
    $$(".seg").forEach((s) => s.setAttribute("aria-selected", String(s === seg)));
    $$(".source").forEach((p) => { p.hidden = p.dataset.panel !== seg.dataset.source; });
    const loader = loaders[seg.dataset.source];
    if (loader && !state.loaded[seg.dataset.source]) { state.loaded[seg.dataset.source] = true; loader(); }
  }));
  $("#appSearch").addEventListener("input", renderApps);
  $("#gamesOnly").addEventListener("change", renderApps);
  $("#otherKind").addEventListener("change", otherChanged);
  $("#otherTarget").addEventListener("input", otherChanged);
  $("#window").addEventListener("change", refresh);
  state.loaded.apps = true;
  loadApps();
}

// ------------------------------------------------------------------ the spec
function spec() {
  const args = $("#args").value.trim();
  return {
    kind: state.target?.kind, target: state.target?.value,
    name: $("#name").value.trim(), subtitle: $("#subtitle").value.trim(),
    label: ($("#label").value.trim() || labelFromName($("#name").value)).toUpperCase(),
    color: $("#color").value,
    countdown: $("#autostart").checked ? Number($("#countdown").value || 0) : null,
    autostart: $("#autostart").checked,
    close_on_eject: $("#closeOnEject").checked,
    args: args && ["app", "exec"].includes(state.target?.kind) ? args.match(/(?:[^\s"]+|"[^"]*")+/g).map((a) => a.replace(/^"|"$/g, "")) : [],
    has_cover: Boolean(state.coverSource),
    window: isWebsite(state.target) ? $("#window").value : null,
  };
}

let checkTimer = 0, lastCheck = { ok: false };
function refresh() {
  const s = spec();
  $("#islandTitle").textContent = s.name || "Your disc";
  $("#islandSub").textContent = s.autostart ? `Inserted · starting in ${s.countdown ?? 5}` : "Ready";
  $("#islandButtons").querySelector(".island-btn").hidden = !s.autostart;
  $("#countdown").disabled = !s.autostart;
  clearTimeout(checkTimer);
  checkTimer = setTimeout(runCheck, 300);
  updateBurnButton();
}

async function runCheck() {
  const note = $("#targetNote");
  if (!state.target) { note.textContent = ""; note.className = "note"; lastCheck = { ok: false }; updateBurnButton(); return; }
  const r = await apiJson("/api/check", spec());
  lastCheck = r;
  if (!r.ok) { note.textContent = r.error; note.className = "note warn"; }
  else if (r.warnings.length) { note.textContent = r.warnings.join(" "); note.className = "note warn"; }
  else {
    const t = state.target;
    note.textContent = ["app", "steam"].includes(t.kind) ? `The disc will open ${t.name}.` : `The disc will run ${r.launch}.`;
    note.className = "note";
  }
  updateBurnButton();
}

// ------------------------------------------------------------------ drive status
function describeDrive(st) {
  const m = st.media || {}, space = st.space;
  const rewrites = space?.rewrites != null ? `About ${space.rewrites} rewrites left` : "";
  switch (m.state) {
    case "burning": return ["busy", "Burning", "Don't eject the disc", "Burning a disc right now."];
    case "no-drive": return ["warn", "No drive found", st.device, `There's no disc drive at ${st.device}.`];
    case "empty": return ["", "No disc", "Insert a blank CD-R", "Insert a blank CD-R, or a DiscBay disc to rewrite."];
    case "spinning": return ["busy", "Reading disc", "", "Reading the disc"];
    case "blank": return ["ok", "Blank disc", space ? `${Math.round(space.free / 1048576)} MB free` : "Ready to burn", "Ready to burn to this blank disc."];
    case "appendable":
      if (m.discbay) return ["ok", st.current || m.label || "DiscBay disc", rewrites || "Can be rewritten",
        `This disc opens ${st.current || m.label}. Burning replaces it. ${rewrites ? rewrites + "." : ""}`];
    return ["ok", "Disc with other files", "DiscBay's files will be added", "This disc already holds other files. They'll be kept, and DiscBay's files added."];
    case "complete": return ["warn", m.label ? `${m.label}, closed` : "Closed disc", "Can't be written again",
      "This disc is closed, so it can't be written again. Insert a blank CD-R or a CD-RW."];
    default: return ["", "Checking the drive", "", ""];
  }
}

async function pollStatus() {
  try {
    const st = await apiJson("/api/status");
    state.media = st.media; state.space = st.space; state.current = st.current;
    if (!state.burning) {
      const [tone, main, sub, long] = describeDrive(st);
      $("#drive").dataset.tone = tone;
      $("#driveMain").textContent = main;
      $("#driveSub").textContent = sub;
      $("#burnStatus").textContent = long;
      $("#ejectBtn").hidden = !st.media?.present || st.media.state === "burning";
    }
  } catch {
    $("#driveMain").textContent = "Studio isn't responding";
    $("#driveSub").textContent = "Is discbay-studio running?";
    $("#drive").dataset.tone = "warn";
  }
  updateBurnButton();
}

function updateBurnButton() {
  const s = spec();
  $("#burnBtn").disabled = state.burning || !state.media?.writable || !state.target || !s.name || !lastCheck.ok;
}

// ------------------------------------------------------------------ burning
function showError(text) { $("#formError").textContent = text || ""; }

function confirmSheet(title, body, ok) {
  const dlg = $("#confirm");
  $("#confirmTitle").textContent = title;
  $("#confirmBody").textContent = body;
  $("#confirmOk").textContent = ok;
  return new Promise((resolve) => {
    dlg.addEventListener("close", () => resolve(dlg.returnValue === "ok"), { once: true });
    dlg.showModal();
  });
}

async function burn(e) {
  e.preventDefault();
  showError("");
  const s = spec(), final = $("#final").checked, m = state.media || {};
  if (m.state === "appendable" && m.discbay) {
    const left = state.space?.rewrites != null ? ` About ${state.space.rewrites} rewrites are left on it.` : "";
    if (!await confirmSheet(`Replace ${state.current || "this disc"}?`,
      `The disc will open ${s.name} instead. This uses about 13 MB of the disc.${left}`, "Replace")) return;
  }
  if (final && !await confirmSheet("Lock this disc?",
    "Once it's locked, the disc can never be changed or rewritten.", "Burn and lock")) return;

  const body = { ...s, final, cover: state.coverSource ? exportCover() : null };
  const res = await api("/api/burn", body);
  const r = await res.json();
  if (!r.ok) { showError(r.error); return; }
  watchJob(r.job, s.name);
}

function setBurningUi(on) {
  state.burning = on;
  $("#disc").classList.toggle("burning", on);
  $("#progressRing").classList.toggle("on", on);
  $$("#form input, #form select, #form .seg, #form .tile, #form .pill").forEach((el) => { el.disabled = on; });
  if (!on) restoreControls();
  updateBurnButton();
}

/** Put back the controls that depend on what's chosen (re-enabling everything would be wrong). */
function restoreControls() {
  const cropping = state.coverSource === "image";
  $("#zoom").disabled = !cropping;
  $("#autoCrop").disabled = !cropping;
  $("#useIcon").disabled = !state.target?.iconUrl;
  $("#countdown").disabled = !$("#autostart").checked;
}

function watchJob(id, name) {
  setBurningUi(true);
  $("#disc").classList.remove("done");
  $("#drive").dataset.tone = "busy";
  $("#driveMain").textContent = "Burning";
  $("#driveSub").textContent = "Don't eject the disc";
  const fill = $("#ringFill"), caption = $("#stageCaption");
  const tick = async () => {
    const job = await apiJson(`/api/jobs/${id}`);
    // Burns take about 40 seconds, mostly writing the session's lead-out, so
    // the ring follows the clock and finishes when the drive reports success.
    const pct = job.state === "running" ? Math.min(94, (job.elapsed / 45) * 94) : 100;
    fill.style.strokeDashoffset = String(100 - pct);
    if (job.state === "running") {
      caption.innerHTML = "";
      caption.append(Object.assign(document.createElement("strong"), { textContent: job.phase }),
                     document.createElement("br"), `${Math.round(job.elapsed)} seconds`);
      setTimeout(tick, 700);
      return;
    }
    setBurningUi(false);
    $("#progressRing").classList.remove("on");
    if (job.state === "done") {
      $("#disc").classList.add("done");
      const r = job.result;
      const more = r.final ? "The disc is locked." : r.rewrites != null ? `About ${r.rewrites} rewrites left on it.` : "";
      caption.innerHTML = "";
      caption.append(Object.assign(document.createElement("strong"), { textContent: `${name} is ready.` }),
                     document.createElement("br"),
                     `${r.ejected ? "Put the disc back in to play. " : ""}${more}`);
      $("#burnBtn").textContent = "Burn again";
    } else {
      caption.textContent = "This is how your disc will look in the drive.";
      showError(job.error);
    }
    pollStatus();
  };
  tick();
}

async function saveIso() {
  showError("");
  if (!state.target) { showError("Choose what the disc should open first."); return; }
  const res = await api("/api/iso", { ...spec(), cover: state.coverSource ? exportCover() : null });
  if (!res.ok) { showError((await res.json()).error); return; }
  const blob = await res.blob();
  const a = Object.assign(document.createElement("a"), {
    href: URL.createObjectURL(blob), download: `${spec().label.toLowerCase()}.iso`,
  });
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 10_000);
}

// ------------------------------------------------------------------ labels
const NUDGE_KEY = "discbay.labelNudge";
function loadNudge() {
  try { return { x: 0, y: 0, ...JSON.parse(localStorage.getItem(NUDGE_KEY) || "{}") }; } catch { return { x: 0, y: 0 }; }
}
function saveNudge(nudge) {
  try { localStorage.setItem(NUDGE_KEY, JSON.stringify(nudge)); } catch { /* storage unavailable: fine */ }
}

let labelSlots = ["top"];
// Exposed so the printable page can be checked without a printer.
window.discbayLabels = {
  html: () => labels.labelsHTML(labelItems(), loadNudge()),
  alignment: () => labels.alignmentHTML(loadNudge()),
};
function labelItems() {
  const s = spec();
  const cover = renderCover(1440);   // about 300 dpi across a 118 mm label
  const disc = $("#labelDisc").checked ? labels.discLabel({
    cover, name: s.name, subtitle: s.subtitle, showName: $("#labelName").checked }) : null;
    const spine = $("#labelSpine").checked ? labels.spineLabel({ name: s.name || "Disc", subtitle: s.subtitle, color: s.color }) : null;
    return labels.layout({ slots: labelSlots, disc, spine });
}
function nudgeFromForm() {
  const nudge = { x: Number($("#nudgeX").value) || 0, y: Number($("#nudgeY").value) || 0 };
  saveNudge(nudge);
  return nudge;
}
function refreshLabelPreview() {
  const nothing = !$("#labelDisc").checked && !$("#labelSpine").checked;
  $("#printLabels").disabled = nothing;
  labels.drawSheetPreview($("#sheetPreview"), nothing ? [] : labelItems(), labelSlots);
}

function setupLabels() {
  const nudge = loadNudge();
  $("#nudgeX").value = nudge.x;
  $("#nudgeY").value = nudge.y;
  $("#labelsBtn").addEventListener("click", () => {
    if (!$("#name").value.trim()) { showError("Give the disc a name before printing labels."); return; }
    showError("");
    $("#labelDialog").showModal();
    refreshLabelPreview();
  });
  $$("#labelDialog .seg").forEach((seg) => seg.addEventListener("click", () => {
    $$("#labelDialog .seg").forEach((s) => s.setAttribute("aria-selected", String(s === seg)));
    labelSlots = seg.dataset.slots.split(",");
    refreshLabelPreview();
  }));
  ["#labelDisc", "#labelSpine", "#labelName"].forEach((sel) => $(sel).addEventListener("change", refreshLabelPreview));
  $("#printLabels").addEventListener("click", () => labels.printLabels(labelItems(), nudgeFromForm()));
  $("#printTest").addEventListener("click", () => labels.printAlignmentTest(nudgeFromForm()));
  $("#closeLabels").addEventListener("click", () => $("#labelDialog").close());
}

// ------------------------------------------------------------------ start
function setup() {
  setupLabels();
  setupCropper();
  setupSources();
  $("#name").addEventListener("input", () => {
    state.nameTouched = true;
    if (!state.labelTouched) $("#label").value = labelFromName($("#name").value);
    refresh();
  });
  $("#label").addEventListener("input", (e) => {
    state.labelTouched = true;
    e.target.value = e.target.value.toUpperCase().replace(/[^A-Z0-9_]/g, "_");
    refresh();
  });
  ["#subtitle", "#countdown", "#autostart", "#closeOnEject", "#args"].forEach((sel) => {
    $(sel).addEventListener("input", refresh);
    $(sel).addEventListener("change", refresh);
  });
  $("#form").addEventListener("submit", burn);
  $("#isoBtn").addEventListener("click", saveIso);
  $("#ejectBtn").addEventListener("click", async () => { await api("/api/eject", {}); pollStatus(); });
  refresh();
  pollStatus();
  setInterval(() => { if (!state.burning) pollStatus(); }, 2000);
}

setup();
