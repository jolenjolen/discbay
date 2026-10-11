// Printable labels: disc faces and slim-case spines, laid out for a label sheet.
//
// Sheet: Label Planet LPCD118P (also sold as SL2P), A4, two 118 mm disc labels
// with 41 mm holes, plus two spine strips and two rectangles.
// From Label Planet's template: 24.8 mm top margin, 129.4 mm vertical pitch,
// top row disc -> 127.7 mm -> strip -> 17 mm -> rectangle, bottom row
// rectangle -> 55 mm -> strip -> 16.7 mm -> disc, discs 71.7 mm apart across.
// Strip and rectangle widths aren't published; they were measured from the
// sheet (about 6.5 mm and 45 mm). Use the alignment test before real sheets.

export const SHEET = {
  name: "Label Planet LPCD118P",
  width: 210, height: 297,
  disc: 118, hole: 41,
  spine: { w: 6.5, h: 118 },
  rect: { w: 45, h: 118 },
};

const LEFT = (SHEET.width - SHEET.disc - 71.7) / 2;   // 10.15 mm, discs centred on the sheet
const TOP = 24.8, PITCH = 129.4;

export const SLOTS = {
  top: {
    disc: { x: LEFT, y: TOP },
    spine: { x: LEFT + 127.7, y: TOP },
    rect: { x: LEFT + 127.7 + 17, y: TOP },
  },
  bottom: {
    rect: { x: LEFT, y: TOP + PITCH },
    spine: { x: LEFT + 55, y: TOP + PITCH },
    disc: { x: LEFT + 71.7, y: TOP + PITCH },
  },
};

const DPI = 300, MM = DPI / 25.4;      // print resolution, pixels per mm
const BLEED = 1.5;                       // printed past the edge so small misalignments don't show white
const SAFE_SPINE = 5;                    // a slim case's edge is about 5 mm; keep text inside it

function canvas(wmm, hmm) {
  const c = document.createElement("canvas");
  c.width = Math.round(wmm * MM);
  c.height = Math.round(hmm * MM);
  return c;
}

function fitText(ctx, text, maxWidth, size, weight = 700) {
  const family = getComputedStyle(document.body).fontFamily;
  let px = size;
  ctx.font = `${weight} ${px}px ${family}`;
  while (px > 8 && ctx.measureText(text).width > maxWidth) {
    px -= 1;
    ctx.font = `${weight} ${px}px ${family}`;
  }
  return px;
}

/** The disc face: cover across the ring, optional name below the hole. */
export function discLabel({ cover, name, subtitle, showName }) {
  const d = SHEET.disc + BLEED * 2;
  const c = canvas(d, d), ctx = c.getContext("2d");
  const r = c.width / 2;
  ctx.save();
  ctx.beginPath();
  ctx.arc(r, r, r, 0, Math.PI * 2);
  ctx.clip();
  ctx.drawImage(cover, 0, 0, c.width, c.height);

  if (showName && name) {
    // A soft shade under the text so it reads on any cover.
    const g = ctx.createLinearGradient(0, r + 12 * MM, 0, c.height);
    g.addColorStop(0, "rgba(0,0,0,0)");
    g.addColorStop(0.45, "rgba(0,0,0,0.42)");
    g.addColorStop(1, "rgba(0,0,0,0.55)");
    ctx.fillStyle = g;
    ctx.fillRect(0, r + 12 * MM, c.width, c.height);

    ctx.fillStyle = "#fff";
    ctx.textAlign = "center";
    ctx.textBaseline = "alphabetic";
    ctx.shadowColor = "rgba(0,0,0,0.35)";
    ctx.shadowBlur = 0.8 * MM;
    // Text sits between the hole and the rim, where the label is widest.
    fitText(ctx, name, 72 * MM, 6.2 * MM);
    ctx.fillText(name, r, r + 34 * MM);
    if (subtitle) {
      fitText(ctx, subtitle, 62 * MM, 3.4 * MM, 500);
      ctx.fillStyle = "rgba(255,255,255,0.85)";
      ctx.fillText(subtitle, r, r + 40 * MM);
    }
  }
  ctx.restore();

  // The hole is die-cut, but leave it white in case the sheet sits a little off.
  ctx.globalCompositeOperation = "destination-out";
  ctx.beginPath();
  ctx.arc(r, r, ((SHEET.hole - BLEED * 2) / 2) * MM, 0, Math.PI * 2);
  ctx.fill();
  return c;
}

/** The spine strip, printed upright on the sheet; text reads along the case edge. */
export function spineLabel({ name, subtitle, color }) {
  const w = SHEET.spine.w + BLEED * 2, h = SHEET.spine.h + BLEED * 2;
  const c = canvas(w, h), ctx = c.getContext("2d");
  ctx.fillStyle = color || "#2f6feb";
  ctx.fillRect(0, 0, c.width, c.height);

  ctx.save();
  ctx.translate(c.width / 2, c.height / 2);
  ctx.rotate(-Math.PI / 2);                // reads bottom-to-top, like most case spines
  ctx.fillStyle = "#fff";
  ctx.textBaseline = "middle";
  const length = (SHEET.spine.h - 10) * MM;
  const capHeight = SAFE_SPINE * MM * 0.62;
  const title = subtitle ? `${name}   ${subtitle}` : name;
  ctx.textAlign = "center";
  fitText(ctx, title, length, capHeight);
  ctx.fillText(title, 0, 0);
  ctx.restore();
  return c;
}

/** Everything to print, as [{ img, x, y, w, h }] in millimetres. */
export function layout({ slots, disc, spine }) {
  const items = [];
  for (const slot of slots) {
    const pos = SLOTS[slot];
    if (disc) items.push({ img: disc, x: pos.disc.x - BLEED, y: pos.disc.y - BLEED,
                           w: SHEET.disc + BLEED * 2, h: SHEET.disc + BLEED * 2 });
    if (spine) items.push({ img: spine, x: pos.spine.x - BLEED, y: pos.spine.y - BLEED,
                            w: SHEET.spine.w + BLEED * 2, h: SHEET.spine.h + BLEED * 2 });
  }
  return items;
}

/** Small on-screen picture of the sheet, so you can see which positions print. */
export function drawSheetPreview(target, items, slots) {
  const k = target.width / SHEET.width;
  const ctx = target.getContext("2d");
  target.height = Math.round(SHEET.height * k);
  ctx.fillStyle = "#fff";
  ctx.fillRect(0, 0, target.width, target.height);
  ctx.strokeStyle = "rgba(0,0,0,.18)";
  ctx.lineWidth = 1;
  for (const [slot, pos] of Object.entries(SLOTS)) {
    const active = slots.includes(slot);
    ctx.setLineDash(active ? [] : [3, 3]);
    ctx.beginPath();
    ctx.arc((pos.disc.x + SHEET.disc / 2) * k, (pos.disc.y + SHEET.disc / 2) * k, (SHEET.disc / 2) * k, 0, Math.PI * 2);
    ctx.stroke();
    ctx.strokeRect(pos.spine.x * k, pos.spine.y * k, SHEET.spine.w * k, SHEET.spine.h * k);
    ctx.strokeRect(pos.rect.x * k, pos.rect.y * k, SHEET.rect.w * k, SHEET.rect.h * k);
  }
  ctx.setLineDash([]);
  for (const it of items) {
    ctx.save();
    if (it.w === it.h) {                   // disc: clip to the label so bleed doesn't spill in the preview
      ctx.beginPath();
      ctx.arc((it.x + it.w / 2) * k, (it.y + it.h / 2) * k, (SHEET.disc / 2) * k, 0, Math.PI * 2);
      ctx.clip();
    }
    ctx.drawImage(it.img, it.x * k, it.y * k, it.w * k, it.h * k);
    ctx.restore();
  }
}

/** A complete A4 page with everything at exact millimetre positions. */
export function pageHTML(body) {
  return `<!doctype html><html><head><meta charset="utf-8"><title>DiscBay labels</title><style>
    @page { size: A4 portrait; margin: 0; }
    html, body { margin: 0; padding: 0; width: 210mm; height: 297mm; overflow: hidden; }
    body { position: relative; -webkit-print-color-adjust: exact; print-color-adjust: exact; }
    img, div { position: absolute; }
  </style></head><body>${body}</body></html>`;
}

function printDocument(html) {
  const frame = document.createElement("iframe");
  frame.setAttribute("aria-hidden", "true");
  frame.style.cssText = "position:fixed;right:0;bottom:0;width:0;height:0;border:0;";
  document.body.append(frame);
  const doc = frame.contentDocument;
  doc.open();
  doc.write(html);
  doc.close();
  const images = [...doc.images];
  Promise.all(images.map((img) => img.complete ? null : new Promise((r) => { img.onload = img.onerror = r; })))
    .then(() => {
      frame.contentWindow.focus();
      frame.contentWindow.print();
      setTimeout(() => frame.remove(), 60_000);
    });
}

/** Print the labels at exact positions. nudge = { x, y } in mm, for printers that print a little off. */
export function labelsHTML(items, nudge = { x: 0, y: 0 }) {
  return pageHTML(items.map((it) =>
    `<img src="${it.img.toDataURL("image/png")}" style="left:${(it.x + nudge.x).toFixed(2)}mm;` +
    `top:${(it.y + nudge.y).toFixed(2)}mm;width:${it.w.toFixed(2)}mm;height:${it.h.toFixed(2)}mm">`).join(""));
}

export function printLabels(items, nudge = { x: 0, y: 0 }) {
  printDocument(labelsHTML(items, nudge));
}

/** Outlines only, on plain paper: hold it against a label sheet to check alignment. */
export function alignmentHTML(nudge = { x: 0, y: 0 }) {
  const line = "border:0.3mm solid #000;box-sizing:border-box;";
  let body = "";
  for (const pos of Object.values(SLOTS)) {
    const dx = nudge.x, dy = nudge.y;
    body += `<div style="${line}border-radius:50%;left:${pos.disc.x + dx}mm;top:${pos.disc.y + dy}mm;width:${SHEET.disc}mm;height:${SHEET.disc}mm"></div>`;
    body += `<div style="${line}border-radius:50%;left:${pos.disc.x + dx + (SHEET.disc - SHEET.hole) / 2}mm;` +
            `top:${pos.disc.y + dy + (SHEET.disc - SHEET.hole) / 2}mm;width:${SHEET.hole}mm;height:${SHEET.hole}mm"></div>`;
    body += `<div style="${line}left:${pos.spine.x + dx}mm;top:${pos.spine.y + dy}mm;width:${SHEET.spine.w}mm;height:${SHEET.spine.h}mm"></div>`;
    body += `<div style="${line}left:${pos.rect.x + dx}mm;top:${pos.rect.y + dy}mm;width:${SHEET.rect.w}mm;height:${SHEET.rect.h}mm"></div>`;
  }
  body += `<div style="left:12mm;top:9mm;font:12pt sans-serif">DiscBay alignment test for ${SHEET.name}. ` +
          `Print on plain paper at 100%, hold it over a label sheet against a window, and adjust the nudge until the lines match.</div>`;
  body += `<div style="left:12mm;top:284mm;font:10pt sans-serif">A 100 mm ruler, to check your printer isn't scaling:</div>`;
  body += `<div style="left:12mm;top:289mm;width:100mm;height:3mm;border-left:0.3mm solid #000;border-right:0.3mm solid #000;border-bottom:0.3mm solid #000"></div>`;
  return pageHTML(body);
}

export function printAlignmentTest(nudge = { x: 0, y: 0 }) {
  printDocument(alignmentHTML(nudge));
}
