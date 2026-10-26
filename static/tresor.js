// Gespeicherte Sitzungen, im Browser verschlüsselt.
//
// Der Server bekommt nur Chiffrat. Aus dem Tresor-Passwort entsteht per
// PBKDF2 ein Schlüssel, der einen zufälligen Hauptschlüssel einwickelt; nur
// der eingewickelte liegt auf dem Server. Mit dem Hauptschlüssel werden das
// Projekt-ZIP und die Angaben für die Liste (Titel, Datum) verschlüsselt,
// jeweils AES-GCM mit eigenem Zufalls-IV.
//
// "Auf diesem Gerät merken" legt den Hauptschlüssel als nicht exportierbaren
// CryptoKey in IndexedDB ab: Der Browser kann damit ver- und entschlüsseln,
// aber niemand kann den Schlüssel selbst auslesen - auch kein Skript.
//
// Ohne DOM geschrieben: nur fetch, crypto.subtle und IndexedDB.

const TRESOR_ITERATIONEN = 600_000;
const TRESOR_KENNUNG = new TextEncoder().encode('LPT1');

let tresorSchluessel = null;     // CryptoKey, solange entsperrt

function nachBase64(bytes) {
  let s = '';
  for (let i = 0; i < bytes.length; i += 0x8000) {
    s += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  }
  return btoa(s);
}

function ausBase64(text) {
  const s = atob(text);
  const bytes = new Uint8Array(s.length);
  for (let i = 0; i < s.length; i++) bytes[i] = s.charCodeAt(i);
  return bytes;
}

async function passwortSchluessel(passwort, salz, iterationen) {
  const roh = await crypto.subtle.importKey('raw', new TextEncoder().encode(passwort),
                                            'PBKDF2', false, ['deriveKey']);
  return crypto.subtle.deriveKey(
    { name: 'PBKDF2', salt: salz, iterations: iterationen, hash: 'SHA-256' },
    roh, { name: 'AES-GCM', length: 256 }, false, ['wrapKey', 'unwrapKey']);
}

async function wickleEin(haupt, passwort) {
  const salz = crypto.getRandomValues(new Uint8Array(16));
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const kek = await passwortSchluessel(passwort, salz, TRESOR_ITERATIONEN);
  const gewickelt = new Uint8Array(await crypto.subtle.wrapKey(
    'raw', haupt, kek, { name: 'AES-GCM', iv }));
  const zusammen = new Uint8Array(12 + gewickelt.length);
  zusammen.set(iv);
  zusammen.set(gewickelt, 12);
  return { salt: nachBase64(salz), iterations: TRESOR_ITERATIONEN,
           wrapped: nachBase64(zusammen) };
}

/** Wirft 'falsches Passwort', wenn das Auswickeln scheitert - AES-GCM prüft
 *  die Echtheit, ein falscher Schlüssel fällt also sicher auf. */
async function wickleAus(schluessel, passwort, exportierbar = false) {
  const kek = await passwortSchluessel(passwort, ausBase64(schluessel.salt),
                                       schluessel.iterations);
  const zusammen = ausBase64(schluessel.wrapped);
  try {
    return await crypto.subtle.unwrapKey(
      'raw', zusammen.subarray(12), kek, { name: 'AES-GCM', iv: zusammen.subarray(0, 12) },
      { name: 'AES-GCM' }, exportierbar, ['encrypt', 'decrypt']);
  } catch {
    throw new Error('Das Tresor-Passwort stimmt nicht.');
  }
}

async function verschluessele(bytes) {
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const ct = new Uint8Array(await crypto.subtle.encrypt(
    { name: 'AES-GCM', iv }, tresorSchluessel, bytes));
  const aus = new Uint8Array(4 + 12 + ct.length);
  aus.set(TRESOR_KENNUNG);
  aus.set(iv, 4);
  aus.set(ct, 16);
  return aus;
}

async function entschluessele(bytes) {
  if (bytes.length < 16 || TRESOR_KENNUNG.some((b, i) => bytes[i] !== b)) {
    throw new Error('Unbekanntes Format.');
  }
  try {
    return new Uint8Array(await crypto.subtle.decrypt(
      { name: 'AES-GCM', iv: bytes.subarray(4, 16) }, tresorSchluessel, bytes.subarray(16)));
  } catch {
    throw new Error('Entschlüsseln fehlgeschlagen - anderer Schlüssel?');
  }
}

// -- Merken auf diesem Gerät ------------------------------------------------------

function tresorDb() {
  return new Promise((ok, fehler) => {
    const anfrage = indexedDB.open('live-protocol', 1);
    anfrage.onupgradeneeded = () => anfrage.result.createObjectStore('tresor');
    anfrage.onsuccess = () => ok(anfrage.result);
    anfrage.onerror = () => fehler(anfrage.error);
  });
}

async function tresorGemerkt(benutzer, wert) {
  try {
    const db = await tresorDb();
    return await new Promise((ok, fehler) => {
      const tx = db.transaction('tresor', wert === undefined ? 'readonly' : 'readwrite');
      const store = tx.objectStore('tresor');
      const anfrage = wert === undefined ? store.get(benutzer)
        : wert === null ? store.delete(benutzer) : store.put(wert, benutzer);
      anfrage.onsuccess = () => ok(anfrage.result ?? null);
      anfrage.onerror = () => fehler(anfrage.error);
    });
  } catch {
    return null;   // privates Fenster, blockierter Speicher: dann eben nicht merken
  }
}

// -- Öffentliche Funktionen -------------------------------------------------------

async function tresorStand() {
  const antwort = await fetch('/api/vault');
  if (!antwort.ok) throw new Error(`Tresor nicht erreichbar (HTTP ${antwort.status})`);
  return antwort.json();
}

function tresorOffen() {
  return tresorSchluessel !== null;
}

/** Beim Laden: einen gemerkten Schlüssel wieder aufnehmen, falls er noch
 *  zum Tresor passt (nach dem Leeren passt er nicht mehr). */
async function tresorAusGeraet(benutzer, stand) {
  const gemerkt = await tresorGemerkt(benutzer);
  if (!gemerkt || !stand.key || gemerkt.wrapped !== stand.key.wrapped) return false;
  tresorSchluessel = gemerkt.key;
  return true;
}

async function tresorEinrichten(benutzer, passwort, merken) {
  const haupt = await crypto.subtle.generateKey({ name: 'AES-GCM', length: 256 }, true,
                                                ['encrypt', 'decrypt']);
  const schluessel = await wickleEin(haupt, passwort);
  const antwort = await fetch('/api/vault/key', {
    method: 'PUT', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(schluessel),
  });
  if (!antwort.ok) throw new Error((await antwort.json().catch(() => ({}))).detail
                                   || `HTTP ${antwort.status}`);
  return tresorEntsperren(benutzer, passwort, merken, schluessel);
}

async function tresorEntsperren(benutzer, passwort, merken, schluessel = null) {
  schluessel ??= (await tresorStand()).key;
  if (!schluessel) throw new Error('Es gibt noch keinen Tresor.');
  tresorSchluessel = await wickleAus(schluessel, passwort, false);
  if (merken) await tresorGemerkt(benutzer, { key: tresorSchluessel, wrapped: schluessel.wrapped });
  return true;
}

async function tresorSperren(benutzer) {
  tresorSchluessel = null;
  await tresorGemerkt(benutzer, null);
}

async function tresorPasswortAendern(benutzer, alt, neu) {
  const { key } = await tresorStand();
  const haupt = await wickleAus(key, alt, true);
  const schluessel = await wickleEin(haupt, neu);
  const antwort = await fetch('/api/vault/key', {
    method: 'PUT', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ...schluessel, replace: true }),
  });
  if (!antwort.ok) throw new Error(`HTTP ${antwort.status}`);
  const gemerkt = await tresorGemerkt(benutzer);
  if (gemerkt) await tresorGemerkt(benutzer, { key: gemerkt.key, wrapped: schluessel.wrapped });
}

async function tresorLeeren(benutzer) {
  const antwort = await fetch('/api/vault', { method: 'DELETE' });
  if (!antwort.ok) throw new Error(`HTTP ${antwort.status}`);
  await tresorSperren(benutzer);
}

function neueTresorKennung() {
  return [...crypto.getRandomValues(new Uint8Array(12))]
    .map((b) => b.toString(16).padStart(2, '0')).join('');
}

/** Projekt-ZIP und Listenangaben verschlüsselt ablegen. */
async function tresorSpeichern(kennung, zipBytes, meta) {
  const inhalt = await verschluessele(zipBytes);
  const kopf = nachBase64(await verschluessele(new TextEncoder().encode(JSON.stringify(meta))));
  const antwort = await fetch(`/api/vault/items/${kennung}`, {
    method: 'PUT', headers: { 'Content-Type': 'application/octet-stream', 'X-Meta': kopf },
    body: inhalt,
  });
  if (!antwort.ok) throw new Error((await antwort.json().catch(() => ({}))).detail
                                   || `HTTP ${antwort.status}`);
  return antwort.json();
}

async function tresorListe() {
  const stand = await tresorStand();
  const eintraege = [];
  for (const item of stand.items) {
    let meta = {};
    try {
      meta = JSON.parse(new TextDecoder().decode(await entschluessele(ausBase64(item.meta))));
    } catch { meta = { title: '(nicht lesbar)' }; }
    eintraege.push({ ...item, ...meta });
  }
  return { ...stand, items: eintraege };
}

async function tresorLaden(kennung) {
  const antwort = await fetch(`/api/vault/items/${kennung}`);
  if (!antwort.ok) throw new Error(`HTTP ${antwort.status}`);
  return entschluessele(new Uint8Array(await antwort.arrayBuffer()));
}

async function tresorLoeschen(kennung) {
  const antwort = await fetch(`/api/vault/items/${kennung}`, { method: 'DELETE' });
  if (!antwort.ok) throw new Error(`HTTP ${antwort.status}`);
}
