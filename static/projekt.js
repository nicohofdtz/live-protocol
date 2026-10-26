// Projekt als Datei beim Nutzer: Audio und Arbeitsstand in einem ZIP.
//
// Der Server hält nichts. Wer später weiterarbeiten will -
// zusammenfassen, Sprecher zuordnen, anders zusammenfassen -, öffnet diese
// Datei wieder. Deshalb steht alles darin, was die Nachbearbeitung braucht,
// und ein Versionsfeld, damit eine spätere Fassung ältere Dateien erkennt.
//
// Eigenes ZIP statt Bibliothek: Das Audio ist schon komprimiert (MP3/M4A),
// also genügt "stored" ohne Kompression - das sind wenige Zeilen. Beim Lesen
// wird auch "deflate" verstanden, falls jemand die Datei neu gepackt hat.
//
// Ohne DOM geschrieben, damit ein Test es unter Node prüfen kann.

const PROJEKT_FORMAT = 'live-protocol-projekt';
const PROJEKT_VERSION = 1;

const CRC_TABELLE = (() => {
  const t = new Uint32Array(256);
  for (let n = 0; n < 256; n++) {
    let c = n;
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    t[n] = c >>> 0;
  }
  return t;
})();

function crc32(bytes) {
  let c = 0xffffffff;
  for (let i = 0; i < bytes.length; i++) c = CRC_TABELLE[(c ^ bytes[i]) & 0xff] ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
}

/** [{name, daten: Uint8Array}] -> Uint8Array eines ZIP ohne Kompression. */
function packeZip(dateien) {
  const kodierer = new TextEncoder();
  const teile = [];
  const verzeichnis = [];
  let versatz = 0;
  for (const { name, daten } of dateien) {
    const nameBytes = kodierer.encode(name);
    const crc = crc32(daten);
    const kopf = new DataView(new ArrayBuffer(30));
    kopf.setUint32(0, 0x04034b50, true);
    kopf.setUint16(4, 20, true);
    kopf.setUint16(6, 0x0800, true);          // Namen in UTF-8
    kopf.setUint16(8, 0, true);               // stored
    kopf.setUint32(14, crc, true);
    kopf.setUint32(18, daten.length, true);
    kopf.setUint32(22, daten.length, true);
    kopf.setUint16(26, nameBytes.length, true);
    teile.push(new Uint8Array(kopf.buffer), nameBytes, daten);

    const eintrag = new DataView(new ArrayBuffer(46));
    eintrag.setUint32(0, 0x02014b50, true);
    eintrag.setUint16(4, 20, true);
    eintrag.setUint16(6, 20, true);
    eintrag.setUint16(8, 0x0800, true);
    eintrag.setUint32(16, crc, true);
    eintrag.setUint32(20, daten.length, true);
    eintrag.setUint32(24, daten.length, true);
    eintrag.setUint16(28, nameBytes.length, true);
    eintrag.setUint32(42, versatz, true);
    verzeichnis.push(new Uint8Array(eintrag.buffer), nameBytes);
    versatz += 30 + nameBytes.length + daten.length;
  }
  const groesse = verzeichnis.reduce((s, t) => s + t.length, 0);
  const ende = new DataView(new ArrayBuffer(22));
  ende.setUint32(0, 0x06054b50, true);
  ende.setUint16(8, dateien.length, true);
  ende.setUint16(10, dateien.length, true);
  ende.setUint32(12, groesse, true);
  ende.setUint32(16, versatz, true);
  const alles = [...teile, ...verzeichnis, new Uint8Array(ende.buffer)];
  const ergebnis = new Uint8Array(alles.reduce((s, t) => s + t.length, 0));
  let pos = 0;
  for (const t of alles) { ergebnis.set(t, pos); pos += t.length; }
  return ergebnis;
}

/** Uint8Array eines ZIP -> Map name -> Uint8Array. Liest das zentrale
 *  Verzeichnis, versteht "stored" und "deflate". */
async function entpackeZip(bytes) {
  const sicht = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  let ende = -1;
  for (let i = bytes.length - 22; i >= Math.max(0, bytes.length - 65557); i--) {
    if (sicht.getUint32(i, true) === 0x06054b50) { ende = i; break; }
  }
  if (ende < 0) throw new Error('Keine ZIP-Datei.');
  const anzahl = sicht.getUint16(ende + 10, true);
  let pos = sicht.getUint32(ende + 16, true);
  const dekodierer = new TextDecoder();
  const dateien = new Map();
  for (let n = 0; n < anzahl; n++) {
    if (sicht.getUint32(pos, true) !== 0x02014b50) throw new Error('ZIP-Verzeichnis beschädigt.');
    const methode = sicht.getUint16(pos + 10, true);
    const gepackt = sicht.getUint32(pos + 20, true);
    const nameLaenge = sicht.getUint16(pos + 28, true);
    const extraLaenge = sicht.getUint16(pos + 30, true);
    const kommentarLaenge = sicht.getUint16(pos + 32, true);
    const kopfVersatz = sicht.getUint32(pos + 42, true);
    const name = dekodierer.decode(bytes.subarray(pos + 46, pos + 46 + nameLaenge));
    pos += 46 + nameLaenge + extraLaenge + kommentarLaenge;

    const lokal = kopfVersatz + 30 + sicht.getUint16(kopfVersatz + 26, true)
      + sicht.getUint16(kopfVersatz + 28, true);
    const roh = bytes.subarray(lokal, lokal + gepackt);
    if (name.endsWith('/')) continue;
    if (methode === 0) {
      dateien.set(name, roh.slice());
    } else if (methode === 8) {
      const strom = new Blob([roh]).stream().pipeThrough(new DecompressionStream('deflate-raw'));
      dateien.set(name, new Uint8Array(await new Response(strom).arrayBuffer()));
    } else {
      throw new Error(`Packverfahren ${methode} wird nicht unterstützt.`);
    }
  }
  return dateien;
}

/** Arbeitsstand + Audio -> ZIP-Bytes. `stand` ist das, was projekt.json
 *  aufnimmt; `audio` ist {name, daten} oder null. */
function baueProjekt(stand, audio) {
  const json = {
    format: PROJEKT_FORMAT,
    version: PROJEKT_VERSION,
    created: stand.created ?? new Date().toISOString(),
    app_version: stand.app_version ?? '',
    output_type: stand.output_type ?? 'meeting',
    context: stand.context ?? {},
    // Stabile Kennungen je Zeile: Die Sprecherzuordnung (M3) korrigiert
    // einzelne Zeilen und muss sie über Speichern und Öffnen wiederfinden.
    transcript: (stand.transcript ?? []).map((e, i) => ({
      id: e.id ?? `z${i + 1}`, seconds: Number(e.seconds) || 0, text: String(e.text ?? ''),
      ...(e.gap ? { gap: true } : {}),
      ...(e.speaker ? { speaker: String(e.speaker) } : {}),
    })),
    // Namen je Sprecherkennung aus der Zuordnung, dazu "mehrere Personen".
    speakers: stand.speakers ?? {},
    feedback: stand.feedback ?? [],
    summary: stand.summary ?? null,
    live_protocol: stand.live_protocol ?? null,
    audio: audio ? audio.name : null,
  };
  const dateien = [{ name: 'projekt.json',
                     daten: new TextEncoder().encode(JSON.stringify(json, null, 2)) }];
  if (audio) dateien.push({ name: audio.name, daten: audio.daten });
  return packeZip(dateien);
}

/** ZIP-Bytes -> {stand, audio}. Wirft mit einer lesbaren Meldung, wenn die
 *  Datei kein Projekt ist oder aus einer neueren Fassung stammt. */
async function leseProjekt(bytes) {
  const dateien = await entpackeZip(bytes);
  const roh = dateien.get('projekt.json');
  if (!roh) throw new Error('Das ist kein Projekt dieser Anwendung (projekt.json fehlt).');
  let stand;
  try {
    stand = JSON.parse(new TextDecoder().decode(roh));
  } catch {
    throw new Error('projekt.json ist beschädigt.');
  }
  if (stand.format !== PROJEKT_FORMAT) throw new Error('Das ist kein Projekt dieser Anwendung.');
  if (!Number.isInteger(stand.version) || stand.version > PROJEKT_VERSION) {
    throw new Error(`Das Projekt stammt aus einer neueren Fassung (Format ${stand.version}). `
                    + 'Bitte die Anwendung aktualisieren.');
  }
  if (!Array.isArray(stand.transcript)) throw new Error('Im Projekt fehlt das Transkript.');
  const audio = stand.audio && dateien.has(stand.audio)
    ? { name: stand.audio, daten: dateien.get(stand.audio) } : null;
  return { stand, audio };
}

if (typeof module !== 'undefined') {
  module.exports = { crc32, packeZip, entpackeZip, baueProjekt, leseProjekt,
                     PROJEKT_FORMAT, PROJEKT_VERSION };
}
