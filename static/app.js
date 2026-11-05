// Aufnahme im Browser: Mikrofon (und auf Wunsch Systemton) -> AudioWorklet
// -> 16-bit-PCM -> WebSocket.
// Bewusst kein MediaRecorder: dessen webm/mp4-Fragmente sind einzeln nicht
// dekodierbar, der Server müsste die wachsende Datei jedes Mal komplett neu
// dekodieren. Rohes PCM ist auf localhost billig und serverseitig beliebig
// schneidbar.

const SAMPLE_RATE = 16000;
const SETTINGS_KEY = 'live-protocol.settings';
const HINT_KEY = 'live-protocol.hint-dismissed';
const UPDATE_KEY = 'live-protocol.update-dismissed';

const el = (id) => document.getElementById(id);
const ui = {
  toggle: el('toggle'), stop: el('stop'), status: el('status'),
  transcript: el('transcript'), partial: el('partial'), protocol: el('protocol'),
  outputType: el('output-type'),
  cycle: el('cycle-badge'), transcribing: el('transcribing'), meta: el('meta'),
  protocolTitle: el('protocol-title'),
  level: el('level'), levelBars: el('level-bars'), export: el('export'),
  toggleLabel: el('toggle-label'), toggleIcon: el('toggle-icon'),
  settings: el('settings'), settingsOpen: el('settings-open'),
  setEngine: el('set-engine'), glossaryList: el('glossary-list'),
  engineNote: el('engine-note'), setLlm: el('set-llm'), setLive: el('set-live'),
  liveOptions: el('live-options'), speakersNote: el('speakers-note'),
  llmNote: el('llm-note'), vocabularyNote: el('vocabulary-note'),
  feedbackArea: el('feedback-area'), feedbackOpen: el('feedback-open'),
  feedbackPanel: el('feedback-panel'), feedbackText: el('feedback-text'),
  feedbackSend: el('feedback-send'), feedbackCancel: el('feedback-cancel'),
  feedbackDictate: el('feedback-dictate'), feedbackNotes: el('feedback-notes'),
  hint: el('local-hint'), hintClose: el('local-hint-close'), hintLink: el('local-hint-link'),
  version: el('version'), downloadLink: el('download-link'),
  audioWarning: el('audio-warning'), audioWarningTitle: el('audio-warning-title'),
  audioWarningText: el('audio-warning-text'), audioWarningResume: el('audio-warning-resume'),
  updateHint: el('update-hint'), updateText: el('update-text'),
  updateLink: el('update-link'), updateClose: el('update-close'),
  firstRun: el('first-run'), firstRunStart: el('first-run-start'),
  firstRunSkip: el('first-run-skip'), firstRunProgress: el('first-run-progress'),
  firstRunFill: el('first-run-fill'), firstRunStatus: el('first-run-status'),
  firstRunText: el('first-run-text'), firstRunTitle: el('first-run-title'),
  contextOpen: el('context-open'), contextDialog: el('context-dialog'),
  ctxTopic: el('ctx-topic'), ctxAgenda: el('ctx-agenda'),
  ctxParticipants: el('ctx-participants'), ctxGoal: el('ctx-goal'),
  ctxSave: el('ctx-save'),
  uploadOpen: el('upload-open'), audioFile: el('audio-file'),
  downloadAudio: el('download-audio'),
  credentials: el('credentials'), setMistralKey: el('set-mistral-key'),
  compatPreset: el('set-compat-preset'), compatNote: el('compat-note'),
  compatUrl: el('set-compat-url'), compatKey: el('set-compat-key'),
  compatLive: el('set-compat-live'), compatSummary: el('set-compat-summary'),
  compatTranscription: el('set-compat-transcription'), compatBox: el('compat-box'),
  openDialog: el('open-dialog'), openFile: el('open-file'), openDiarize: el('open-diarize'),
  openDiarizeRow: el('open-diarize-row'), openDiarizeNote: el('open-diarize-note'),
  vaultOpen: el('vault-open'), vaultDialog: el('vault-dialog'), vaultBody: el('vault-body'),
  openVault: el('open-vault'), openVaultHint: el('open-vault-hint'),
  usageOpen: el('usage-open'), usageDialog: el('usage-dialog'),
  newsOpen: el('news-open'), newsDialog: el('news-dialog'), newsBody: el('news-body'),
  usageMonth: el('usage-month'), usageBody: el('usage-body'),
  transcriptEdit: el('transcript-edit'), transcriptEditing: el('transcript-editing'),
  backToSummary: el('back-to-summary'), speakerSplit: el('speaker-split'),
  liveProtocol: el('live-protocol'), phoneHint: el('phone-hint'),
  phoneHintUpload: el('phone-hint-upload'),
  summaryOpen: el('summary-open'), summaryDialog: el('summary-dialog'),
  sumModels: el('sum-models'), sumDetail: el('sum-detail'),
  sumDetailNote: el('sum-detail-note'), sumBlocks: el('sum-blocks'),
  sumFreitext: el('sum-freitext'), sumFeedback: el('sum-feedback'),
  exportMenu: el('export-menu'), retranscribe: el('retranscribe'),
  speakersOpen: el('speakers-open'), speakerFilter: el('speaker-filter'),
  speakerDialog: el('speaker-dialog'), speakerTabs: el('speaker-tabs'),
  speakerName: el('speaker-name'), speakerMixed: el('speaker-mixed'),
  speakerSamples: el('speaker-samples'), setSpeakersAfter: el('set-speakers-after'),
  setSystemAudio: el('set-system-audio'), systemAudioNote: el('system-audio-note'),
  sumPersons: el('sum-persons'), sumSpeakersNote: el('sum-speakers-note'),
  sumSpeakersEdit: el('sum-speakers-edit'),
  protocolEdit: el('protocol-edit'), protocolEditing: el('protocol-editing'),
  protocolEditor: el('protocol-editor'), annotate: el('annotate'),
  annotations: el('annotations'), annotationList: el('annotation-list'),
  changeList: el('change-list'), annotationDialog: el('annotation-dialog'),
  annotationQuote: el('annotation-quote'), annotationText: el('annotation-text'),
};

// Der Modus-Schalter ist eine Radiogruppe, kein <select> - .value und
// .disabled bleiben trotzdem in der gewohnten Form ansprechbar, damit die
// bestehenden Aufrufstellen unverändert bleiben.
Object.defineProperties(ui.outputType, {
  value: { get: () => ui.outputType.querySelector('input:checked')?.value ?? '' },
  disabled: {
    get: () => [...ui.outputType.querySelectorAll('input')].every((i) => i.disabled),
    set: (value) => { for (const i of ui.outputType.querySelectorAll('input')) i.disabled = value; },
  },
});

let localSettings = {};

let config = null;
let settings = {};
let ws = null, audioContext = null, workletNode = null, mediaStream = null, systemStream = null, systemRegler = null;
let liveLaufOffen = false, liveNachlauf = null;
let sessionId = null;
let recording = false, paused = false, firstSegment = true;
// Der Browser hält den Audiograph an, sobald die Seite in den Hintergrund
// gerät oder der Bildschirm ausgeht. Ohne diese Buchführung liefe die
// Oberfläche weiter, als würde aufgezeichnet - und niemand merkte, dass
// Minuten fehlen.
let audioSuspended = false, suspendedAt = null, closingIntentionally = false;
let wakeLock = null;
// Terminrahmen: Agenda, Beteiligte, Ziel. Ueberlebt die Sitzung im Browser,
// damit eine Folgeaufnahme desselben Termins ihn nicht neu braucht.
let terminRahmen = {};
let dictating = false, diktatSeit = 0;
let dictationChunks = [];
// Mitschnitt der laufenden Aufnahme - nur im Arbeitsspeicher. Ein Neuladen
// oder Schließen des Fensters wirft ihn weg, und genau so ist er gemeint:
// eine Rückfallebene für den Fall, dass die Erkennung enttäuscht, kein Archiv.
let recordedChunks = [];
let recordedUrl = null;
// Der Mitschnitt als MP3, blockweise im Worker kodiert. Bis der Worker
// meldet, dass er bereit ist, bleibt das PCM in recordedChunks - scheitert
// er, wird daraus wie bisher ein WAV: Die Rückfallebene darf nicht selbst
// ausfallen.
let mp3Worker = null, mp3Bereit = false, mp3Kaputt = false, mp3Teile = [];
let mp3Fertig = null, aufgenommeneSamples = 0;
const MP3_KBPS = 48;
let lastTranscriptText = '';
// Das Transkript als Einträge, wie es an die Zusammenfassung geht - samt
// Lückenvermerken, damit das Modell sieht, wo etwas fehlt.
let transkriptEintraege = [];
let gesammelteHinweise = [];
let aufnahmeBeginn = null;
// Mit welchem Modell die rechte Spalte zuletzt entstanden ist.
let zusammenfassungVon = null;
// Die Audiodatei zum Projekt: die hochgeladene Datei oder der eigene
// Mitschnitt als Blob. Geht ins Projekt-ZIP.
let projektAudio = null;
// Ob seit dem letzten Speichern etwas entstanden ist, das ein Neuladen
// verlöre - gehostet ist es dann endgültig weg.
let ungesichert = false;
// Zuordnung der Sprecherkennungen aus der Erkennung: {speaker_1: {name, mehrere}}.
let sprecher = {};
// Reißt die Audioverbindung ab, sammeln sich die Frames hier, bis sie wieder
// steht - statt still verloren zu gehen. Gedeckelt, damit ein langer Ausfall
// den Speicher nicht füllt; was darüber hinausgeht, wird als Lücke vermerkt.
let wartendeFrames = [], wartendeSamples = 0, verworfeneSamples = 0;
let neuVerbinden = null;
const PUFFER_SEKUNDEN = 120;
// Für den Hinweis, dass Sprache ankommt, aber kein Text mehr.
let letzteSprache = 0, letztesTranskript = 0, stilleWache = null;

function setStatus(text, isError = false) {
  ui.status.textContent = text;
  ui.status.classList.toggle('error', isError);
}

// -- Einstellungen ----------------------------------------------------------

// Die Auswahl liegt beim Server, nicht im Browser: Wer den Dienst am Telefon
// und am Rechner benutzt, findet sie an beiden Stellen wieder. Der Browser
// bleibt als Rückfall für den Fall, dass der Server gerade nicht antwortet.
async function loadStored() {
  try {
    const antwort = await fetch('/api/prefs');
    if (antwort.ok) return await antwort.json();
  } catch { /* offline oder noch nicht angemeldet */ }
  try {
    return JSON.parse(localStorage.getItem(SETTINGS_KEY)) || {};
  } catch {
    return {};   // privates Fenster, gelöschte Daten, blockierter Speicher
  }
}

function storeSettings() {
  try {
    localStorage.setItem(SETTINGS_KEY, JSON.stringify(settings));
  } catch { /* nicht kritisch */ }
  fetch('/api/prefs', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(settings),
  }).catch(() => { /* die lokale Kopie steht schon */ });
}

function fillSettingsDialog() {
  ui.setEngine.value = settings.engine;
  ui.setLlm.value = settings.llm_endpoint;
  glossarEntwurf = structuredClone(settings.glossaries ?? []);
  aktivEntwurf = new Set(settings.active_glossaries ?? []);
  zeichneGlossare();
  ui.setLive.checked = settings.live_protocol === true;
  ui.liveOptions.hidden = !ui.setLive.checked;
  ui.setSpeakersAfter.checked = settings.speakers_after_stop === true;
  ui.setSystemAudio.checked = settings.system_audio === true && systemtonMoeglich();
  ui.setSystemAudio.disabled = !systemtonMoeglich();
  ui.systemAudioNote.textContent = systemtonMoeglich()
    ? 'Nimmt zusätzlich auf, was der Rechner abspielt, etwa die Gegenseite in Teams '
      + 'oder Zoom. Beim Start fragt der Browser nach einer Bildschirmfreigabe: Dort '
      + 'den Bildschirm wählen und „Systemton teilen“ einschalten. Das Bild wird '
      + 'nicht aufgezeichnet. Ohne Kopfhörer landet die Gegenseite doppelt in der '
      + 'Aufnahme.'
    : 'Geht nur am Rechner in Chrome oder Edge, unter macOS ab Version 14.2.';
  const dienst = sprecherDienst();
  ui.setSpeakersAfter.disabled = !dienst;
  ui.speakersNote.textContent = dienst
    ? `Die Aufnahme geht dafür noch einmal ganz an ${dienst.name}`
      + (dienst.cost ? ` (${dienst.cost.split(',')[0]})` : '') + '.'
    : 'Dafür ist kein Dienst eingerichtet.';
  updateEngineNote();
  updateLlmNote();
  if (config.mode === 'desktop') fillCredentials();
}

/** Eine Zeile je Auswahl: Kosten und Datenschutz, knapp. Der ausführliche
 *  Datenschutztext steht im Titel, für wer es genau wissen will. */
function beschreibe(eintrag, lokalText) {
  if (!eintrag) return '';
  if (eintrag.local) return lokalText;
  const teile = [eintrag.cost, eintrag.configured === false ? 'kein API-Schlüssel hinterlegt' : ''];
  return teile.filter(Boolean).join(' · ');
}

function updateEngineNote() {
  const engine = config.engines.find((e) => e.id === ui.setEngine.value);
  if (!engine) return;
  ui.engineNote.textContent = beschreibe(engine, 'Läuft auf diesem Rechner, das Audio bleibt hier.');
  ui.engineNote.title = engine.privacy || '';
  ui.engineNote.className = engine.configured === false ? 'note warn' : 'note';
  // Ob ein Vokabular-Hinweis überhaupt ankommt, weiß der Server.
  ui.vocabularyNote.textContent = 'Angekreuzte Glossare gehen an die Erkennung und die '
    + 'Zusammenfassung. Wörter im Transkript markieren und korrigieren nimmt sie auf.'
    + (engine.vocabulary ? '' : ` ${engine.name} nimmt selbst keine Begriffsliste an – `
       + 'Korrekturen werden dort nur ersetzt.');
}

function updateLlmNote() {
  const value = ui.setLlm.value;
  if (value === 'auto') {
    ui.llmNote.textContent = 'Der erste erreichbare: '
      + config.llm_endpoints.map((e) => e.label).join(' → ');
    ui.llmNote.title = '';
    ui.llmNote.className = 'note';
    return;
  }
  const endpoint = config.llm_endpoints.find((e) => e.name === value);
  ui.llmNote.textContent = beschreibe(endpoint, 'Läuft im eigenen Netz.');
  ui.llmNote.title = endpoint?.privacy || '';
  ui.llmNote.className = endpoint?.configured === false ? 'note warn' : 'note';
}

const METER_SEGMENTS = 8;

function buildMeter() {
  ui.levelBars.innerHTML = '';
  for (let i = 0; i < METER_SEGMENTS; i++) ui.levelBars.append(document.createElement('span'));
}

async function loadConfig() {
  try {
    config = await (await fetch('/api/config')).json();
  } catch (error) {
    setStatus(`Konfiguration nicht ladbar: ${error.message}`, true);
    return;
  }

  buildOutputTypeControl();
  ui.setEngine.innerHTML = '';
  for (const engine of config.engines) {
    ui.setEngine.append(new Option(engine.name, engine.id));
  }
  ui.setLlm.innerHTML = '';
  ui.setLlm.append(new Option('Automatisch', 'auto'));
  for (const endpoint of config.llm_endpoints) {
    ui.setLlm.append(new Option(endpoint.label || endpoint.name, endpoint.name));
  }

  settings = { ...config.defaults, ...(await loadStored()) };
  // Aus der einen Begriffsliste von früher wird das erste Glossar.
  if (!Array.isArray(settings.glossaries)) {
    const alt = String(settings.vocabulary ?? '').split(/[,\n]/).map((b) => b.trim()).filter(Boolean);
    settings.glossaries = alt.length ? [{ id: 'g1', name: 'Allgemein', terms: alt }] : [];
    settings.active_glossaries = alt.length ? ['g1'] : [];
  }
  if (!config.engines.some((e) => e.id === settings.engine)) {
    settings.engine = config.defaults.engine;
  }
  if (settings.llm_endpoint !== 'auto'
      && !config.llm_endpoints.some((e) => e.name === settings.llm_endpoint)) {
    settings.llm_endpoint = 'auto';
  }
  fillSettingsDialog();
  ui.liveProtocol.checked = settings.live_protocol === true;
  zeigeTelefonHinweis(true);
  ui.outputType.addEventListener('change', () => {
    positionThumb();
    benenneProtokollspalte();
  });
  benenneProtokollspalte();

  buildMeter();
  showLocalHint();
  zeigeDownloadlink();
  zeigeNutzungshinweis();
  zeigeSeiten();
  ui.usageOpen.hidden = !config.admin;
  zeigeKontingent(config.quota);
  ui.exportMenu.querySelector('[data-export="tresor"]').hidden = !config.vault;
  ui.vaultOpen.hidden = !config.vault;
  ladeRahmen();
  renderMeta();
  ui.version.textContent = `Version ${config.version}`;
  if (config.mode === 'desktop') {
    await loadLocalSettings();
    checkModel();
    checkUpdate();
  }
  ui.settingsOpen.disabled = false;
  setStatus('bereit');
  if (settings.live_protocol) loadHealth();
  if (config.vault) ladeTresorAusGeraet();
  zeigeNeuigkeiten();
}

// Getrennt von der Konfiguration: der Health-Check wartet je Endpunkt bis zu
// 4 s auf eine Verbindung. Zusammen mit /api/config blockierte das die
// Oberfläche, und ein Klick aufs Zahnrad in dieser Zeit lief ins Leere.
async function loadHealth() {
  try {
    const { endpoints } = await (await fetch('/api/health')).json();
    // Nur wichtig, wenn das Live-Protokoll läuft - und dann nur, wenn es hakt.
    if (!endpoints.some((e) => e.ok)) {
      setStatus('Das Modell fürs Live-Protokoll ist gerade nicht erreichbar.', true);
    }
  } catch { /* kein Grund, die Startzeile zu füllen */ }
}

// Nur im gehosteten Betrieb, und nur solange nicht weggeklickt.
function showLocalHint() {
  if (config.mode !== 'hosted' || !config.download_url) return;
  let dismissed = false;
  try {
    dismissed = localStorage.getItem(HINT_KEY) === '1';
  } catch { /* privates Fenster o. Ä. - dann eben anzeigen */ }
  if (dismissed) return;
  ui.hintLink.href = config.download_url;
  ui.hint.hidden = false;
}

// Der Hinweis oben lässt sich dauerhaft wegklicken — der Weg zur lokalen
// Fassung darf daran nicht hängen. Sonst kommt nur noch hin, wer die
// Adresse kennt.
function zeigeDownloadlink() {
  if (config.mode !== 'hosted' || !config.download_url) return;
  ui.downloadLink.href = config.download_url;
  ui.downloadLink.hidden = false;
}

// -- Update-Prüfung ---------------------------------------------------------

// Läuft im Backend, nicht hier: der Browser käme wegen CORS nicht an eine
// fremde Adresse heran, und offline soll es folgenlos bleiben.
async function checkUpdate() {
  let stand;
  try {
    stand = await (await fetch('/api/update')).json();
  } catch {
    return;                       // offline ist kein Fehlerfall
  }
  if (!stand.available) return;

  let weggeklickt = null;
  try {
    weggeklickt = localStorage.getItem(UPDATE_KEY);
  } catch { /* privates Fenster */ }
  if (weggeklickt === stand.latest) return;   // diese Fassung schon abgelehnt

  ui.updateText.textContent =
    `Version ${stand.latest} steht bereit, installiert ist ${stand.current}.`;
  if (stand.download_url) ui.updateLink.href = stand.download_url;
  ui.updateHint.hidden = false;
  ui.updateClose.onclick = () => {
    ui.updateHint.hidden = true;
    try {
      localStorage.setItem(UPDATE_KEY, stand.latest);
    } catch { /* nicht kritisch */ }
  };
}

// -- Erstinstallation: Modell nachladen -------------------------------------

async function checkModel() {
  let state;
  try {
    state = await (await fetch('/api/model')).json();
  } catch {
    return;
  }
  if (state.present || !state.needed) return;
  ui.firstRun.hidden = false;
  if (state.running) startModelPolling();
}

async function downloadModel() {
  ui.firstRunStart.disabled = true;
  ui.firstRunSkip.disabled = true;
  ui.firstRunProgress.hidden = false;
  ui.firstRunStatus.textContent = 'wird gestartet …';
  try {
    await fetch('/api/model', { method: 'POST' });
  } catch (error) {
    ui.firstRunStatus.textContent = `Start fehlgeschlagen: ${error.message}`;
    ui.firstRunStart.disabled = false;
    return;
  }
  startModelPolling();
}

function startModelPolling() {
  ui.firstRunProgress.hidden = false;
  const timer = setInterval(async () => {
    let state;
    try {
      state = await (await fetch('/api/model')).json();
    } catch {
      return;
    }
    if (state.error) {
      clearInterval(timer);
      ui.firstRunStatus.textContent = state.error;
      ui.firstRunStart.disabled = false;
      ui.firstRunSkip.disabled = false;
      return;
    }
    const megabyte = (n) => (n / 1e6).toFixed(0);
    ui.firstRunFill.style.width = `${(state.downloaded / state.size) * 100}%`;
    ui.firstRunStatus.textContent =
      `${megabyte(state.downloaded)} von ${megabyte(state.size)} MB`;
    if (state.present) {
      clearInterval(timer);
      ui.firstRunTitle.textContent = 'Fertig';
      ui.firstRunText.textContent =
        'Das Modell liegt bereit. Bitte das Programm einmal neu starten, damit '
        + 'die lokale Transkription verfügbar wird.';
      ui.firstRunProgress.hidden = true;
      ui.firstRunSkip.disabled = false;
      ui.firstRunSkip.textContent = 'Schließen';
    }
  }, 1000);
}

// -- Lokale Zugangsdaten ----------------------------------------------------

async function loadLocalSettings() {
  try {
    localSettings = await (await fetch('/api/settings')).json();
  } catch {
    localSettings = {};
  }
  ui.credentials.hidden = false;
}

const COMPAT_FELDER = { compatLive: 'live_model', compatSummary: 'summary_model',
                       compatTranscription: 'transcription_model' };

function fillCredentials() {
  // Schlüssel kommen nie zurück - das Feld zeigt nur, ob einer hinterlegt ist.
  ui.setMistralKey.value = '';
  ui.setMistralKey.placeholder = localSettings.mistral_api_key_set
    ? 'hinterlegt – zum Ersetzen neu eingeben' : 'nicht hinterlegt';
  const vorlagen = config.provider_presets || {};
  ui.compatPreset.innerHTML = '';
  for (const [id, vorlage] of Object.entries(vorlagen)) {
    ui.compatPreset.append(new Option(vorlage.name, id));
  }
  const eigen = localSettings.openai_compat || altAnbieter(vorlagen);
  ui.compatPreset.value = eigen.preset || 'custom';
  ui.compatUrl.value = eigen.base_url || '';
  for (const [feld, schluessel] of Object.entries(COMPAT_FELDER)) {
    ui[feld].value = eigen[schluessel] || '';
  }
  ui.compatKey.value = '';
  ui.compatKey.placeholder = eigen.api_key_set
    ? 'hinterlegt – zum Ersetzen neu eingeben' : 'nicht hinterlegt';
  ui.compatBox.open = Boolean(eigen.base_url);
  beschreibeVorlage();
}

/** Ältere Fassungen kannten einen OpenRouter-Schlüssel und einen eigenen
 *  Endpunkt fürs Live-Protokoll. Beide erscheinen als Anbieter; der Server
 *  übernimmt den Schlüssel beim Speichern. */
function altAnbieter(vorlagen) {
  if (localSettings.openrouter_api_key_set && vorlagen.openrouter) {
    const v = vorlagen.openrouter;
    return { preset: 'openrouter', base_url: v.base_url, live_model: v.live_model,
             summary_model: v.summary_model, transcription_model: v.transcription_model,
             api_key_set: true };
  }
  const alt = localSettings.custom_llm;
  if (alt?.url) {
    return { preset: 'custom', base_url: alt.url, live_model: alt.model,
             summary_model: alt.model, api_key_set: alt.api_key_set };
  }
  return {};
}

/** Vorlage gewechselt: Adresse und Modelle vorfüllen. Was schon drinsteht,
 *  wird ersetzt - die Vorlage ist genau dafür da. */
function waehleVorlage() {
  const vorlage = config.provider_presets?.[ui.compatPreset.value];
  if (!vorlage) return;
  ui.compatUrl.value = vorlage.base_url;
  for (const [feld, schluessel] of Object.entries(COMPAT_FELDER)) {
    ui[feld].value = vorlage[schluessel];
  }
  beschreibeVorlage();
}

/** Platzhalter der Modellfelder: Eine Vorlage ohne Modellnamen (OpenAI)
 *  bittet ausdrücklich darum, sie einzutragen. */
function modellPlatzhalter() {
  const vorlage = config.provider_presets?.[ui.compatPreset.value];
  const fehlt = vorlage && !vorlage.live_model && vorlage.base_url;
  ui.compatLive.placeholder = fehlt ? 'Modellnamen eintragen' : '';
  ui.compatSummary.placeholder = fehlt ? 'Modellnamen eintragen' : '';
  ui.compatTranscription.placeholder = fehlt ? 'Modellnamen eintragen (optional)'
    : 'leer = keine';
}

function beschreibeVorlage() {
  const vorlage = config.provider_presets?.[ui.compatPreset.value];
  if (!vorlage) return;
  ui.compatNote.textContent = [vorlage.note, `Datenschutz (ohne Gewähr): ${vorlage.privacy}`]
    .filter(Boolean).join(' ');
  ui.compatNote.className = vorlage.note ? 'note warn' : 'note';
  modellPlatzhalter();
}

async function saveCredentials() {
  const patch = {};
  if (ui.setMistralKey.value.trim()) patch.mistral_api_key = ui.setMistralKey.value.trim();

  const url = ui.compatUrl.value.trim();
  const eigen = localSettings.openai_compat || {};
  if (url) {
    const neu = { preset: ui.compatPreset.value, base_url: url };
    for (const [feld, schluessel] of Object.entries(COMPAT_FELDER)) {
      neu[schluessel] = ui[feld].value.trim();
    }
    // Ohne neuen Schlüssel bleibt der hinterlegte; der Server kennt ihn.
    if (ui.compatKey.value.trim()) neu.api_key = ui.compatKey.value.trim();
    const geaendert = ui.compatKey.value.trim()
      || Object.entries(neu).some(([k, v]) => (eigen[k] || '') !== v);
    if (geaendert) patch.openai_compat = neu;
  } else if (eigen.base_url) {
    patch.openai_compat = null;   // geleert = entfernen
  }
  if (Object.keys(patch).length === 0) return;

  try {
    const response = await fetch('/api/settings', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(patch),
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    localSettings = await response.json();
    await loadConfig();   // neue Endpunkte in die Auswahl übernehmen
  } catch (error) {
    setStatus(`Zugangsdaten nicht gespeichert: ${error.message}`, true);
  }
}

function renderMeta() {
  const engine = config.engines.find((e) => e.id === settings.engine);
  const llm = config.llm_endpoints.find((e) => e.name === settings.llm_endpoint);
  const live = settings.live_protocol
    ? `Live-Protokoll: ${llm ? llm.label : 'automatisch'}`
    : 'Zusammenfassung nach dem Beenden';
  const ton = systemtonAktiv() ? ' · mit Systemton' : '';
  ui.meta.textContent = `${engine ? engine.name : settings.engine} · ${live}${ton}`;
}

/** Am Telefon hält kein Browser das Mikrofon im Hintergrund an. Der
 *  Hinweis steht vor dem Start und verschwindet, sobald es losgeht. */
function zeigeTelefonHinweis(sichtbar) {
  const telefon = window.matchMedia('(pointer: coarse)').matches;
  // Nur vor dem Start - liegt schon ein Transkript da, kostet er nur Platz.
  const leer = !transkriptEintraege.some((e) => !e.gap);
  ui.phoneHint.hidden = !(sichtbar && telefon && leer);
}

/** Baut den Modus-Schalter aus der Konfiguration.
 *
 * Feste Segmentbreite statt gemessener Label-Breite: der gleitende
 * Hintergrund ist dann ein reiner CSS-Ausdruck (translateX um Vielfache der
 * Eigenbreite), unabhängig davon, wie viele Segmente es gerade sind. */
function buildOutputTypeControl() {
  const container = ui.outputType;
  container.innerHTML = '<span class="segmented-thumb" aria-hidden="true"></span>';
  container.style.setProperty('--segments', config.output_types.length);
  for (const type of config.output_types) {
    const item = document.createElement('span');
    item.className = 'segmented-item';
    const input = document.createElement('input');
    input.type = 'radio';
    input.name = 'output-type';
    input.id = `output-type-${type.id}`;
    input.value = type.id;
    const label = document.createElement('label');
    label.htmlFor = input.id;
    label.textContent = type.short_name || type.name;
    label.title = type.description;
    item.append(input, label);
    container.append(item);
  }
  const first = container.querySelector('input');
  if (first) first.checked = true;
  positionThumb();
}

function positionThumb() {
  const inputs = [...ui.outputType.querySelectorAll('input')];
  const index = Math.max(0, inputs.findIndex((i) => i.checked));
  ui.outputType.style.setProperty('--index', index);
}

// -- Aufnahme ---------------------------------------------------------------

/** Die tatsächlich benutzte Strategie.
 *
 * "auto" heißt: die des gewählten Modus. Deshalb ist es die Vorgabe - für
 * einen Modus mit fester Zielgliederung ist Delta prinzipbedingt die falsche
 * Wahl, und diese Kopplung soll niemand von Hand herstellen müssen. */
/** Die rechte Spalte heißt, was dort entsteht - „Protokoll“ über einem
 *  Konzept wäre schlicht falsch beschriftet. */
function benenneProtokollspalte() {
  const modus = config.output_types.find((t) => t.id === ui.outputType.value);
  ui.protocolTitle.textContent = modus?.short_name || 'Protokoll';
}

function strategieFuerModus() {
  const modus = config.output_types.find((t) => t.id === ui.outputType.value);
  return modus?.default_strategy ?? config.strategies[0];
}

/** Sitzung anlegen. Wirft mit der Meldung des Servers, wenn es nicht geht. */
async function starteSitzung(engineId) {
  const response = await fetch('/api/sessions', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      output_type: ui.outputType.value,
      strategy: strategieFuerModus(),
      engine: engineId,
      llm_endpoint: settings.llm_endpoint,
      vocabulary: aktiveBegriffe(),
      context: terminRahmen,
      live_protocol: settings.live_protocol === true,
    }),
  });
  if (!response.ok) {
    const detail = await response.json().catch(() => ({}));
    throw new Error(detail.detail || `Sitzung fehlgeschlagen (HTTP ${response.status})`);
  }
  sessionId = (await response.json()).id;
}

/** Audioverbindung aufbauen und auf das Öffnen warten - mit Abbruch.
 *
 * Ohne den Abbruch hängt der Start stumm, wenn die Verbindung scheitert:
 * Mikrofon freigegeben, sonst nichts. Auf HTTPS-Seiten muss es wss sein, ein
 * ws:// gilt als Mixed Content - lokal auf http fällt das nie auf. */
async function oeffneAudioverbindung() {
  const schema = location.protocol === 'https:' ? 'wss:' : 'ws:';
  ws = new WebSocket(`${schema}//${location.host}/api/sessions/${sessionId}/stream`);
  ws.binaryType = 'arraybuffer';
  ws.addEventListener('message', (event) => handleEvent(JSON.parse(event.data)));
  ws.addEventListener('close', (event) => {
    if (!recording) return;
    if (event.code === 4410) {
      // Der Server hat die Sitzung inzwischen selbst beendet - neu verbinden
      // bringt nichts mehr. Der Mitschnitt im Browser läuft trotzdem weiter.
      setzeWarnung('Sitzung auf dem Server beendet',
        'Die Verbindung war zu lange weg. Beende die Aufnahme - der Mitschnitt '
        + 'lässt sich herunterladen und später erneut verarbeiten.');
      return;
    }
    verbindeNeu();
  });
  await new Promise((resolve, reject) => {
    ws.addEventListener('open', resolve, { once: true });
    ws.addEventListener('error', () => reject(new Error('Verbindung abgelehnt')),
                        { once: true });
    setTimeout(() => reject(new Error('Zeitüberschreitung')), 10000);
  });
}

/** Nach einem Abriss neu verbinden, mit wachsenden Abständen.
 *
 * Solange es dauert, sammelt onAudioFrame die Frames; danach gehen sie in
 * einem Schwung hinterher. Der Server wartet zwei Minuten auf die neue
 * Verbindung, bevor er die Sitzung selbst beendet - die Versuche hier bleiben
 * darunter. */
function verbindeNeu() {
  if (neuVerbinden) return;
  setStatus('Verbindung getrennt – neuer Versuch …', true);
  neuVerbinden = (async () => {
    for (const pause of [1, 2, 5, 10, 15, 20, 30]) {
      await new Promise((r) => setTimeout(r, pause * 1000));
      if (!recording) break;
      try {
        await oeffneAudioverbindung();
        sendeWartendeFrames();
        setStatus(paused ? 'pausiert' : 'Aufnahme läuft');
        neuVerbinden = null;
        return;
      } catch { /* nächster Versuch */ }
    }
    neuVerbinden = null;
    if (recording) {
      setzeWarnung('Verbindung verloren',
        'Der Server ist nicht mehr erreichbar. Beende die Aufnahme - der '
        + 'Mitschnitt lässt sich herunterladen und später erneut verarbeiten.');
    }
  })();
}

function puffereFrame(buffer) {
  wartendeFrames.push(buffer);
  wartendeSamples += buffer.byteLength / 2;
  while (wartendeSamples > PUFFER_SEKUNDEN * SAMPLE_RATE) {
    const alt = wartendeFrames.shift();
    wartendeSamples -= alt.byteLength / 2;
    verworfeneSamples += alt.byteLength / 2;
  }
}

function sendeWartendeFrames() {
  const verloren = Math.round(verworfeneSamples / SAMPLE_RATE);
  if (verloren >= 3) markiereLuecke(verloren);
  for (const frame of wartendeFrames) ws.send(frame);
  wartendeFrames = [];
  wartendeSamples = 0;
  verworfeneSamples = 0;
}

/** Sprache kommt an, aber seit einer Weile kein Text mehr: Die Transkription
 *  hängt, ohne dass ein Fehler gemeldet wurde. Der Pegel allein sähe dann aus,
 *  als liefe alles. */
function pruefeTranskriptFluss() {
  if (!recording || paused || audioSuspended || dictating || neuVerbinden) return;
  const dienst = config.engines.find((e) => e.id === settings.engine);
  const schwelle = (dienst?.streaming ? 30 : 90) * 1000;
  const jetzt = Date.now();
  if (jetzt - letzteSprache < 10_000 && jetzt - letztesTranskript > schwelle) {
    setStatus(`Seit ${Math.round((jetzt - letztesTranskript) / 1000)} s kommt kein `
              + 'Text an, obwohl gesprochen wird.', true);
  }
}

const NUTZUNG_KEY = 'live-protocol.nutzung-hinweis';

/** Kurzer Fingerabdruck des Hinweistexts: Ein geänderter Text soll wieder
 *  erscheinen, auch wenn der alte weggeklickt wurde. */
function fingerabdruck(text) {
  let h = 0;
  for (let i = 0; i < text.length; i++) h = (h * 31 + text.charCodeAt(i)) | 0;
  return String(h);
}

/** Hinweisleiste der Instanz, bis sie weggeklickt wird. Der Inhalt kommt vom
 *  Server (BANNER_TEXT oder banner.html aus dem Overlay). */
function zeigeNutzungshinweis() {
  if (!config.banner) return;
  try {
    if (localStorage.getItem(NUTZUNG_KEY) === fingerabdruck(config.banner)) return;
  } catch { /* dann eben jedes Mal */ }
  el('use-banner-text').innerHTML = config.banner;
  el('use-banner').hidden = false;
}

el('use-banner-close').addEventListener('click', () => {
  el('use-banner').hidden = true;
  try {
    localStorage.setItem(NUTZUNG_KEY, fingerabdruck(config.banner || ''));
  } catch { /* nicht kritisch */ }
});

/** Links auf die zusätzlichen Seiten aus dem Overlay - nur, was es gibt. */
function zeigeSeiten() {
  const ziel = el('pages');
  ziel.innerHTML = '';
  for (const seite of config.pages || []) {
    const a = document.createElement('a');
    a.href = seite.path;
    a.textContent = seite.title;
    ziel.append(a);
  }
}

const ZUSTIMMUNG_KEY = 'live-protocol.zustimmung-hinweis';

/** Vor jeder Aufnahme: Aufnehmen braucht die Zustimmung aller (§ 201 StGB).
 *  Mit "Nicht mehr anzeigen" fragt es auf diesem Gerät nicht wieder, mit
 *  CONSENT_HINT=false gar nicht. */
function zustimmungBestaetigt() {
  if (config.consent_hint === false) return Promise.resolve(true);
  try {
    if (localStorage.getItem(ZUSTIMMUNG_KEY) === '1') return Promise.resolve(true);
  } catch { /* privates Fenster - dann eben jedes Mal */ }
  const dialog = el('consent-dialog');
  el('consent-hide').checked = false;
  dialog.returnValue = '';
  dialog.showModal();
  return new Promise((fertig) => {
    dialog.addEventListener('close', () => {
      const ok = dialog.returnValue === 'ok';
      if (ok && el('consent-hide').checked) {
        try { localStorage.setItem(ZUSTIMMUNG_KEY, '1'); } catch { /* nicht kritisch */ }
      }
      fertig(ok);
    }, { once: true });
  });
}

/** Systemton gibt es nur über die Bildschirmfreigabe von Chromium-Browsern am
 *  Rechner. Firefox und Safari kennen getDisplayMedia, liefern aber keinen
 *  Systemton; suppressLocalAudioPlayback gibt es nur in Chromium. */
function systemtonMoeglich() {
  const geraete = navigator.mediaDevices;
  return Boolean(geraete?.getDisplayMedia)
    && 'suppressLocalAudioPlayback' in geraete.getSupportedConstraints()
    && !window.matchMedia('(pointer: coarse)').matches;
}

function systemtonAktiv() {
  return settings.system_audio === true && systemtonMoeglich();
}

/** Fragt die Bildschirmfreigabe ab. Null heißt: nicht starten - wer den
 *  Systemton eingeschaltet hat, erwartet die Gegenseite in der Aufnahme, und
 *  eine halbe Besprechung fiele erst hinterher auf. */
async function holeSystemton() {
  let stream;
  try {
    stream = await navigator.mediaDevices.getDisplayMedia({
      // Das Bild verlangt der Standard; es geht nirgends hin.
      video: { frameRate: 1 },
      audio: {
        echoCancellation: false, noiseSuppression: false, autoGainControl: false,
        suppressLocalAudioPlayback: false,
      },
      systemAudio: 'include',
      windowAudio: 'system',
      selfBrowserSurface: 'exclude',
      surfaceSwitching: 'exclude',
    });
  } catch (error) {
    // Abbrechen im Dialog: "Permission denied"; Sperre durchs System:
    // "Permission denied by system" oder NotReadableError.
    const vomSystem = error.name === 'NotReadableError' || /system/i.test(error.message);
    setStatus(vomSystem
      ? 'Keine Bildschirmfreigabe: Das System verweigert sie. Unter macOS in den '
        + 'Systemeinstellungen bei Datenschutz & Sicherheit die Bildschirmaufnahme '
        + 'erlauben und das Programm neu starten.'
      : 'Aufnahme nicht gestartet: Bildschirmfreigabe abgebrochen.', true);
    return null;
  }
  if (!stream.getAudioTracks().length) {
    stream.getTracks().forEach((track) => track.stop());
    setStatus('Aufnahme nicht gestartet, die Freigabe enthielt keinen Ton: Bitte den '
              + 'Bildschirm wählen und „Systemton teilen“ einschalten. Unter macOS '
              + 'braucht das Version 14.2 und Chrome oder Edge ab Version 141.', true);
    return null;
  }
  return stream;
}

function stoppeEingaenge() {
  mediaStream?.getTracks().forEach((track) => track.stop());
  systemStream?.getTracks().forEach((track) => track.stop());
  systemStream = null;
}

async function start() {
  if (!(await zustimmungBestaetigt())) return;
  // Vor dem Mikrofon: Die Freigabe verlangt einen frischen Klick, und die
  // Mikrofon-Abfrage kann ihn aufbrauchen.
  systemStream = null;
  if (systemtonAktiv()) {
    systemStream = await holeSystemton();
    if (!systemStream) return;
  }
  try {
    mediaStream = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
    });
  } catch (error) {
    setStatus(`Kein Mikrofonzugriff: ${error.message}`, true);
    mediaStream = null;
    stoppeEingaenge();
    return;
  }

  try {
    await starteSitzung(settings.engine);
  } catch (error) {
    setStatus(error.message, true);
    stoppeEingaenge();
    return;
  }

  // Der Browser resampled selbst auf 16 kHz - besser als eigene Dezimierung.
  audioContext = new AudioContext({ sampleRate: SAMPLE_RATE });
  // iOS startet den Graphen angehalten; ohne resume() fliesst kein einziges
  // Frame, obwohl die Mikrofonfreigabe erteilt wurde.
  if (audioContext.state === 'suspended') await audioContext.resume();
  audioContext.onstatechange = onAudioContextState;
  if (audioContext.sampleRate !== SAMPLE_RATE) {
    // Manche Geraete erzwingen die Hardware-Rate. Ungefiltert kaeme das Audio
    // in falscher Geschwindigkeit beim Server an.
    setStatus(`Gerät liefert ${audioContext.sampleRate} Hz statt ${SAMPLE_RATE} Hz, `
              + 'wird umgerechnet.');
  }
  await audioContext.audioWorklet.addModule('/static/pcm-worklet.js');

  try {
    await oeffneAudioverbindung();
  } catch (error) {
    setStatus(`Audioverbindung fehlgeschlagen: ${error.message}`, true);
    stoppeEingaenge();
    await audioContext.close();
    audioContext = null;
    ws.close();
    return;
  }

  // Ein eingehender Anruf oder eine andere App, die das Mikrofon greift,
  // schaltet den Track stumm. Der AudioContext bleibt dabei 'running' - dieser
  // Fall geht also an onstatechange vorbei und braucht seinen eigenen Melder.
  for (const track of mediaStream.getAudioTracks()) {
    track.onmute = () => { if (recording) beginneStillstand(); };
    track.onunmute = () => { if (recording) beendeStillstand(); };
    track.onended = () => {
      if (!recording) return;
      nullePegel();
      setzeWarnung('Mikrofon nicht mehr verfügbar',
        'Das Gerät hat die Aufnahme beendet. Die Sitzung lässt sich abschließen, '
        + 'weiter mitgeschrieben wird nicht.');
    };
  }

  // Die Freigabe beenden lässt sich jederzeit über die Leiste des Browsers.
  for (const track of systemStream?.getAudioTracks() ?? []) {
    track.onended = () => {
      if (!recording) return;
      setzeWarnung('Systemton nicht mehr verfügbar',
        'Die Bildschirmfreigabe wurde beendet. Das Mikrofon läuft weiter, die '
        + 'Gegenseite fehlt ab hier in der Aufnahme.', { fortsetzbar: false });
    };
  }

  // Der Worklet liest nur einen Kanal, der Systemton ist Stereo: Der Mischer
  // rechnet alles auf Mono herunter. Mit zwei Quellen etwas leiser, damit die
  // Summe nicht übersteuert.
  const mischer = audioContext.createGain();
  mischer.channelCount = 1;
  mischer.channelCountMode = 'explicit';
  mischer.channelInterpretation = 'speakers';
  mischer.gain.value = systemStream ? 0.7 : 1;
  audioContext.createMediaStreamSource(mediaStream).connect(mischer);
  systemRegler = null;
  if (systemStream) {
    systemRegler = audioContext.createGain();
    audioContext.createMediaStreamSource(new MediaStream(systemStream.getAudioTracks()))
      .connect(systemRegler).connect(mischer);
  }
  workletNode = new AudioWorkletNode(audioContext, 'pcm-worklet');
  workletNode.port.onmessage = (event) => onAudioFrame(event.data);
  mischer.connect(workletNode);
  // Ohne Ziel läuft der Graph in manchen Browsern nicht an; stummer Ausgang.
  const silent = audioContext.createGain();
  silent.gain.value = 0;
  workletNode.connect(silent).connect(audioContext.destination);

  starteMitschnitt();
  recording = true;
  paused = false;
  firstSegment = true;
  setzeSitzungsinhaltZurueck();
  wartendeFrames = []; wartendeSamples = 0; verworfeneSamples = 0;
  letztesTranskript = Date.now();
  clearInterval(stilleWache);
  stilleWache = setInterval(pruefeTranskriptFluss, 5000);
  ui.toggle.dataset.recording = 'true';
  ui.stop.disabled = false;
  setPauseIcon(false);
  ui.level.hidden = false;
  ui.outputType.disabled = true;
  ui.settingsOpen.disabled = true;
  ui.liveProtocol.disabled = true;
  zeigeTelefonHinweis(false);
  clearTranscript();
  ui.protocol.innerHTML = settings.live_protocol
    ? '<p class="empty">Wartet auf den ersten Protokolllauf.</p>'
    : '<p class="empty">Die Zusammenfassung entsteht nach dem Beenden.</p>';
  // Ohne Live-Protokoll gibt es kein Protokoll, an dem ein Hinweis hängen
  // könnte - gesammelt wird trotzdem von Anfang an, für die Zusammenfassung.
  zeigeFeedback(!settings.live_protocol);
  ui.feedbackNotes.innerHTML = '';
  verbergeWarnung();
  audioSuspended = false;
  suspendedAt = null;
  await fordereWakeLock();
  setStatus('Aufnahme läuft');
}

// -- Terminrahmen ----------------------------------------------------------
//
// Agenda, Beteiligte und Ziel vorab zu kennen hebt das Protokoll spürbar -
// ein Kaltstart erkennt Abkürzungen nicht und findet keine Gliederung. Der
// Rahmen ist trotzdem freiwillig und jederzeit nachreichbar: Wer fünf
// Minuten zu spät in den Termin stolpert, darf kein Formular vorfinden.

const RAHMEN_KEY = 'live-protocol.termin-rahmen';

function ladeRahmen() {
  try {
    terminRahmen = JSON.parse(localStorage.getItem(RAHMEN_KEY) || '{}');
  } catch { terminRahmen = {}; }
  markiereRahmen();
}

function speichereRahmen() {
  try {
    localStorage.setItem(RAHMEN_KEY, JSON.stringify(terminRahmen));
  } catch { /* privates Fenster - der Rahmen gilt dann nur für diese Sitzung */ }
}

function markiereRahmen() {
  const gefüllt = Object.values(terminRahmen).some((wert) => wert && wert.trim());
  ui.contextOpen.classList.toggle('has-context', gefüllt);
  ui.contextOpen.title = gefüllt
    ? 'Terminrahmen hinterlegt - zum Ändern antippen'
    : 'Terminrahmen: Agenda, Beteiligte, Ziel';
}

function fuelleRahmenDialog() {
  ui.ctxTopic.value = terminRahmen.topic || '';
  ui.ctxAgenda.value = terminRahmen.agenda || '';
  ui.ctxParticipants.value = terminRahmen.participants || '';
  ui.ctxGoal.value = terminRahmen.goal || '';
}

async function uebernimmRahmen() {
  terminRahmen = {
    topic: ui.ctxTopic.value.trim(),
    agenda: ui.ctxAgenda.value.trim(),
    participants: ui.ctxParticipants.value.trim(),
    goal: ui.ctxGoal.value.trim(),
  };
  speichereRahmen();
  markiereRahmen();
  // Laeuft bereits eine Sitzung, gilt der Rahmen ab dem nächsten Lauf -
  // genau dafür ist er nachreichbar.
  if (!sessionId || !recording) return;
  try {
    await fetch(`/api/sessions/${sessionId}/context`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(terminRahmen),
    });
    setStatus('Terminrahmen übernommen - gilt ab dem nächsten Protokolllauf');
  } catch (error) {
    setStatus(`Terminrahmen nicht übernommen: ${error.message}`, true);
  }
}

ui.contextOpen.addEventListener('click', () => {
  fuelleRahmenDialog();
  ui.contextDialog.showModal();
});

ui.ctxSave.addEventListener('click', () => { uebernimmRahmen(); });

// -- Stillstand des Audiographen -------------------------------------------
//
// Safari hält den AudioContext an, sobald die Seite unsichtbar wird oder der
// Bildschirm ausgeht. Der WebSocket bleibt dabei offen, der Pegel steht im
// letzten Ausschlag - die Anwendung sah aus, als liefe sie weiter, und das
// Material dieser Minuten war still verloren. Verhindern lässt sich der
// Stillstand nicht (Tab- und App-Wechsel gehören der Plattform), also wird
// er benannt.

function setzeWarnung(titel, text, { fortsetzbar = true } = {}) {
  ui.audioWarningResume.hidden = !fortsetzbar;
  ui.audioWarningTitle.textContent = titel;
  ui.audioWarningText.textContent = text;
  ui.audioWarning.hidden = false;
}

function verbergeWarnung() {
  ui.audioWarning.hidden = true;
}

function onAudioContextState() {
  if (!audioContext) return;
  const zustand = audioContext.state;
  if (zustand === 'running') {
    if (audioSuspended) beendeStillstand();
    return;
  }
  // Ein gewolltes close() beim Beenden ist kein Fehler.
  if (closingIntentionally || !recording) return;
  beginneStillstand();
}

function beginneStillstand() {
  if (audioSuspended) return;
  audioSuspended = true;
  suspendedAt = Date.now();
  nullePegel();
  setzeWarnung('Aufnahme unterbrochen',
    'Der Browser hat die Tonaufnahme angehalten - das passiert, wenn die Seite '
    + 'in den Hintergrund gerät oder der Bildschirm ausgeht. Seitdem wird '
    + 'nichts mitgeschrieben.');
  setStatus('Aufnahme unterbrochen - kein Ton', true);
}

async function beendeStillstand() {
  if (!audioSuspended) return;
  const sekunden = Math.round((Date.now() - suspendedAt) / 1000);
  audioSuspended = false;
  suspendedAt = null;
  verbergeWarnung();
  if (sekunden >= 3) markiereLuecke(sekunden);
  setStatus(paused ? 'pausiert' : 'Aufnahme läuft');
  await fordereWakeLock();
}

/** Die fehlenden Sekunden sind unwiederbringlich weg. Ohne Markierung
 *  entstünde ein nahtlos wirkendes Transkript mit einem Loch - und das
 *  Protokollmodell formulierte darüber hinweg. */
function markiereLuecke(sekunden, grund = 'unterbrochen') {
  const text = grund === 'diktat'
    ? `Diktat - etwa ${sekunden} s der Aufnahme fehlen`
    : `Aufnahme unterbrochen - etwa ${sekunden} s fehlen`;
  const p = document.createElement('p');
  p.className = 'seg gap';
  p.textContent = text;
  ui.transcript.insertBefore(p, ui.partial);
  ui.transcript.scrollTop = ui.transcript.scrollHeight;
  // Auch beim Kopieren gehört das Loch dazu, nicht nur in der Anzeige.
  lastTranscriptText += (lastTranscriptText ? '\n\n' : '') + `[${text}]`;
  transkriptEintraege.push({ text: `[${text}]`, seconds: aufgenommeneSekunden(), gap: true });
  fetch(`/api/sessions/${sessionId}/gap`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ seconds: sekunden, reason: grund }),
  }).catch(() => { /* Die Anzeige steht auch ohne den Servereintrag. */ });
}

/** Nach dem Suspend liegt im Worklet nichts mehr an; der Pegel bliebe sonst
 *  auf dem letzten Wert stehen. */
function nullePegel() {
  for (const balken of ui.levelBars.children) {
    balken.classList.remove('on', 'peak');
  }
  ui.level.classList.remove('live');
}

/** Hält den Bildschirm an. Deckt den häufigsten Fall ab - die Sperre nach
 *  Untätigkeit - und nur den: Ein Tab- oder App-Wechsel ist davon
 *  unberührt. Das Lock wird beim Sichtbarkeitsverlust automatisch
 *  freigegeben und muss danach neu angefordert werden. */
async function fordereWakeLock() {
  if (!recording || !('wakeLock' in navigator)) return;
  try {
    wakeLock = await navigator.wakeLock.request('screen');
  } catch { /* Akku zu schwach, Gerät verweigert - kein Grund zu stören. */ }
}

async function gibWakeLockFrei() {
  try { await wakeLock?.release(); } catch { /* schon weg */ }
  wakeLock = null;
}

document.addEventListener('visibilitychange', async () => {
  if (document.visibilityState !== 'visible' || !recording) return;
  await fordereWakeLock();
  if (audioContext?.state === 'suspended') {
    // Klappt meistens von selbst. iOS verlangt gelegentlich eine echte
    // Geste - dafür steht der Knopf in der Warnung.
    try { await audioContext.resume(); } catch { /* Knopf bleibt */ }
  }
});

ui.audioWarningResume.addEventListener('click', async () => {
  try {
    await audioContext?.resume();
    await fordereWakeLock();
  } catch (error) {
    setStatus(`Fortsetzen fehlgeschlagen: ${error.message}`, true);
  }
});

/** Nur für Geräte, die 16 kHz verweigern. Gemittelt über das Fenster, das
 *  auf einen Zielwert fällt, statt jeden n-ten Wert zu nehmen: Ohne diese
 *  einfache Tiefpass-Wirkung fielen hohe Anteile als Störgeräusch in den
 *  Sprachbereich (Aliasing) - in Mitschnitt und Transkription. */
function resampleTo16k(samples, vonRate) {
  const faktor = vonRate / SAMPLE_RATE;
  const laenge = Math.floor(samples.length / faktor);
  const out = new Int16Array(laenge);
  for (let i = 0; i < laenge; i++) {
    const von = Math.floor(i * faktor), bis = Math.max(von + 1, Math.floor((i + 1) * faktor));
    let summe = 0;
    for (let j = von; j < bis; j++) summe += samples[j];
    out[i] = Math.round(summe / (bis - von));
  }
  return out;
}

function onAudioFrame(buffer) {
  let samples = new Int16Array(buffer);
  if (audioContext && audioContext.sampleRate !== SAMPLE_RATE) {
    samples = resampleTo16k(samples, audioContext.sampleRate);
    buffer = samples.buffer;
  }
  showLevel(samples);
  if (dictating) {
    dictationChunks.push(samples.slice());
    return;   // Diktat gehört nicht ins Sitzungstranskript
  }
  if (paused) return;
  nimmAuf(samples.slice());
  if (ws?.readyState === WebSocket.OPEN && !neuVerbinden) ws.send(buffer);
  else if (recording) puffereFrame(buffer);
}

function showLevel(samples) {
  let sum = 0;
  for (let i = 0; i < samples.length; i++) sum += samples[i] * samples[i];
  const rms = Math.sqrt(sum / samples.length);
  // Logarithmisch, damit leise Sprache sichtbar ist statt am Boden zu kleben.
  // -60 dBFS bis 0 dBFS auf die Segmente verteilt.
  const db = 20 * Math.log10((rms || 1) / 32768);
  const level = Math.min(1, Math.max(0, (db + 60) / 60));
  const lit = Math.round(level * METER_SEGMENTS);
  const bars = ui.levelBars.children;
  for (let i = 0; i < bars.length; i++) {
    bars[i].classList.toggle('on', i < lit);
    bars[i].classList.toggle('peak', i < lit && i >= METER_SEGMENTS - 2);
  }
  ui.level.classList.toggle('live', lit > 0);
  if (lit >= 3) letzteSprache = Date.now();
}

/** Beschriftung des großen Knopfes. Er ist Start, Pause und Fortsetzen in
 *  einem - beendet wird daneben, damit beides nicht am selben Griff hängt. */
function setPauseIcon(isPaused) {
  const text = !recording ? 'Aufzeichnung starten' : isPaused ? 'Fortsetzen' : 'Pause';
  const symbol = !recording ? '#i-mic' : isPaused ? '#i-play' : '#i-pause';
  ui.toggleLabel.textContent = text;
  ui.toggleIcon.setAttribute('href', symbol);
  ui.toggle.title = text;
}

async function togglePause() {
  paused = !paused;
  await fetch(`/api/sessions/${sessionId}/${paused ? 'pause' : 'resume'}`, { method: 'POST' });
  setPauseIcon(paused);
  setStatus(paused ? 'pausiert' : 'Aufnahme läuft');
}

/** Direkt nach dem Stopp kommt der Dialog zur Zusammenfassung, und die entsteht
 *  allein aus dem Transkript. Ein letzter Lauf des Live-Modells kostete dann nur
 *  Wartezeit und Geld. */
function ohneLetztenLiveLauf() {
  return settings.live_protocol === true && config.summary_models?.length > 0;
}

/** „Später“ statt Zusammenfassung: Dann bleibt das Live-Protokoll das Ergebnis
 *  und bekommt die letzten Sätze doch noch. Eine Zusammenfassung, die inzwischen
 *  gestartet wurde, gewinnt: Das Ergebnis wird dann verworfen. */
async function holeLiveLaufNach() {
  if (!liveLaufOffen || zusammenfassungVon) return;
  liveLaufOffen = false;
  const lauf = {};
  liveNachlauf = lauf;
  ui.cycle.hidden = false;
  try {
    const antwort = await fetch(`/api/sessions/${sessionId}/live-pass`, { method: 'POST' });
    const { protocol } = await antwort.json();
    if (lauf === liveNachlauf && antwort.ok && protocol) renderProtocol(protocol);
  } catch {
    // Das Transkript ist vollständig, es fehlt nur der letzte Lauf.
  } finally {
    if (lauf === liveNachlauf) {
      ui.cycle.hidden = true;
      liveNachlauf = null;
    }
  }
}

async function schliesseSitzung() {
  const ohneLauf = ohneLetztenLiveLauf();
  liveLaufOffen = ohneLauf;
  if (ws?.readyState === WebSocket.OPEN) ws.send(ohneLauf ? 'stop-without-live-pass' : 'stop');
  const ziel = `/api/sessions/${sessionId}/stop${ohneLauf ? '?live_pass=false' : ''}`;
  const antwort = await (await fetch(ziel, { method: 'POST' })).json();
  // Ein abgebrochener Lauf meldet sich nicht mehr zurück.
  ui.cycle.hidden = true;
  return antwort;
}

async function stop() {
  recording = false;
  clearInterval(stilleWache);
  // Das folgende close() feuert statechange ebenfalls. Ohne diese Marke
  // meldete jedes normale Beenden einen Stillstand.
  closingIntentionally = true;
  audioSuspended = false;
  suspendedAt = null;
  verbergeWarnung();
  await gibWakeLockFrei();
  ui.toggle.disabled = true;
  ui.stop.disabled = true;
  setStatus(ohneLetztenLiveLauf() ? 'Aufnahme wird abgeschlossen …'
    : 'Aufnahme wird abgeschlossen, letzter Protokolllauf …');

  workletNode?.disconnect();
  stoppeEingaenge();
  await audioContext?.close();
  ui.level.hidden = true;

  ui.transcribing.hidden = true;
  ui.cycle.hidden = true;
  await biteMitschnittAn();

  try {
    const result = await schliesseSitzung();
    if (result.protocol) renderProtocol(result.protocol);
    setStatus('fertig');
  } catch (error) {
    setStatus(`Abschluss fehlgeschlagen: ${error.message}`, true);
  }
  ws?.close();
  zeigeTelefonHinweis(true);
  ui.transcriptEdit.hidden = !transkriptEintraege.length;
  merkeVor();
  ui.retranscribe.hidden = !projektAudio;
  ui.speakersOpen.hidden = !projektAudio || !sprecherDienst();
  if (settings.speakers_after_stop && projektAudio && sprecherDienst()
      && transkriptEintraege.some((e) => !e.gap)) {
    await unterscheideSprecher({ fragen: false });
  } else {
    bieteZusammenfassungAn();
  }

  ui.toggle.disabled = false;
  ui.toggle.dataset.recording = 'false';
  paused = false;
  setPauseIcon(false);
  ui.outputType.disabled = false;
  ui.settingsOpen.disabled = false;
  ui.liveProtocol.disabled = false;
  closingIntentionally = false;
}

// -- Ereignisse -------------------------------------------------------------

function clearTranscript() {
  // Alles bis auf die Zeile für den noch laufenden Satz - die gehört zum
  // Transkript, ist aber kein Absatz.
  for (const kind of [...ui.transcript.children]) {
    if (kind !== ui.partial) kind.remove();
  }
  ui.partial.hidden = true;
  ui.partial.textContent = '';
}

function handleEvent(event) {
  if (event.type === 'transcribing') {
    ui.transcribing.hidden = false;
  } else if (event.type === 'partial') {
    // Strom-Dienste liefern wortweise; der noch offene Satz steht als
    // vorläufige Zeile unter dem Transkript, bis er fertig ist.
    if (event.text && firstSegment) clearTranscript();
    ui.partial.textContent = event.text;
    ui.partial.hidden = !event.text;
    ui.transcript.scrollTop = ui.transcript.scrollHeight;
  } else if (event.type === 'transcript') {
    letztesTranskript = Date.now();
    ui.transcribing.hidden = true;
    if (firstSegment) { clearTranscript(); firstSegment = false; }
    lastTranscriptText += (lastTranscriptText ? '\n\n' : '') + event.entry.text;
    transkriptEintraege.push({ text: event.entry.text, seconds: event.entry.seconds });
    ui.export.disabled = false;
    ungesichert = true;
    const p = document.createElement('p');
    p.className = 'seg fresh';
    const time = document.createElement('time');
    time.textContent = formatSeconds(event.entry.seconds);
    p.append(time, document.createTextNode(event.entry.text));
    ui.transcript.insertBefore(p, ui.partial);
    ui.partial.hidden = true;
    ui.partial.textContent = '';
    ui.transcript.scrollTop = ui.transcript.scrollHeight;
  } else if (event.type === 'protocol_pending') {
    ui.cycle.hidden = false;
  } else if (event.type === 'protocol') {
    // Der Spinner verschwindet, sobald der Lauf durch ist. Welcher Lauf es war,
    // steht in der Fußzeile - im Kopf wäre es eine Zahl ohne Bedeutung.
    ui.cycle.hidden = true;
    renderProtocol(event.markdown);
    ui.export.disabled = false;
    zeigeFeedback(true);
    if (event.prompt_tokens) {
      ui.meta.textContent =
        `Lauf ${event.cycle} über ${event.endpoint} · ${event.prompt_tokens} Prompt-Tokens, ` +
        `${event.completion_tokens} Ausgabe-Tokens` +
        (event.ops ? ` · ${event.ops} Operationen` : '');
    }
  } else if (event.type === 'feedback_pending') {
    ui.cycle.hidden = false;
    setStatus('Hinweis wird eingearbeitet …');
  } else if (event.type === 'limit_reached') {
    setStatus(`Zeitgrenze von ${event.minutes} Minuten erreicht – Aufnahme wird beendet.`, true);
    if (recording) stop();
  } else if (event.type === 'paused') {
    setStatus('pausiert');
  } else if (event.type === 'error') {
    // Kein Spinner darf einen Fehler überleben - sonst dreht er sich ewig.
    ui.transcribing.hidden = true;
    ui.cycle.hidden = true;
    setStatus(`${event.stage}: ${event.message}`, true);
  }
}

function formatSeconds(seconds) {
  return `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, '0')}`;
}


// -- Mitschnitt und vorhandenes Audio ---------------------------------------

function verwerfeMitschnitt() {
  recordedChunks = [];
  mp3Teile = [];
  aufgenommeneSamples = 0;
  mp3Worker?.terminate();
  mp3Worker = null;
  mp3Bereit = false;
  mp3Kaputt = false;
  if (recordedUrl) URL.revokeObjectURL(recordedUrl);
  recordedUrl = null;
  ui.downloadAudio.hidden = true;
}

/** Den MP3-Worker für eine neue Aufnahme starten. */
function starteMitschnitt() {
  verwerfeMitschnitt();
  try {
    mp3Worker = new Worker('/static/mp3-worker.js');
  } catch {
    mp3Kaputt = true;
    return;
  }
  mp3Worker.onmessage = ({ data }) => {
    if (data.type === 'bereit') {
      mp3Bereit = true;
      // Was bis hierher nur als PCM vorliegt, geht nachträglich hinein.
      for (const stueck of recordedChunks) mp3Worker.postMessage({ type: 'pcm', samples: stueck });
      recordedChunks = [];
    } else if (data.type === 'mp3') {
      mp3Teile.push(data.teil);
    } else if (data.type === 'fertig') {
      mp3Teile.push(data.teil);
      mp3Fertig?.();
    } else if (data.type === 'fehler') {
      mp3Scheitert(data.text);
    }
  };
  mp3Worker.onerror = (ereignis) => {
    ereignis.preventDefault();
    mp3Scheitert(ereignis.message || 'Kodierer nicht ladbar');
  };
  mp3Worker.postMessage({ type: 'start', rate: SAMPLE_RATE, kbps: MP3_KBPS });
}

function mp3Scheitert(grund) {
  if (mp3Kaputt) return;
  mp3Kaputt = true;
  mp3Worker?.terminate();
  mp3Worker = null;
  mp3Fertig?.();
  // Sichtbar, aber kein Abbruch: ab hier sammelt sich das PCM für ein WAV.
  setStatus(`Mitschnitt als MP3 nicht möglich (${grund}) – er läuft als WAV weiter.`, true);
}

function nimmAuf(samples) {
  aufgenommeneSamples += samples.length;
  if (mp3Bereit && !mp3Kaputt) {
    mp3Worker.postMessage({ type: 'pcm', samples }, [samples.buffer]);
  } else {
    recordedChunks.push(samples);
  }
}

/** Mitschnitt abschließen: MP3 fertig kodieren, sonst WAV. Ist der Worker
 *  mitten in der Aufnahme ausgefallen, gibt es beides - vorne MP3, hinten
 *  WAV - und beides wird zusammen heruntergeladen. */
async function biteMitschnittAn() {
  if (mp3Bereit && !mp3Kaputt && mp3Worker) {
    await Promise.race([
      new Promise((fertig) => { mp3Fertig = fertig; mp3Worker.postMessage({ type: 'ende' }); }),
      new Promise((fertig) => setTimeout(fertig, 5000)),
    ]);
    mp3Fertig = null;
    mp3Worker?.terminate();
    mp3Worker = null;
  }
  const stempel = new Date().toISOString().slice(0, 16).replace('T', '-').replace(':', '');
  const dateien = [];
  if (mp3Teile.length) {
    dateien.push(new File(mp3Teile, `aufnahme-${stempel}.mp3`, { type: 'audio/mpeg' }));
  }
  if (recordedChunks.length) {
    dateien.push(new File([encodeWav(recordedChunks)], `aufnahme-${stempel}.wav`,
                          { type: 'audio/wav' }));
  }
  recordedChunks = [];
  mp3Teile = [];
  mitschnittDateien = dateien;
  projektAudio = dateien[0] ?? null;
  ui.downloadAudio.hidden = !dateien.length;
}

let mitschnittDateien = [];

function ladeMitschnittHerunter() {
  const dateien = mitschnittDateien.length ? mitschnittDateien
    : projektAudio ? [projektAudio] : [];
  for (const datei of dateien) {
    const url = URL.createObjectURL(datei);
    const a = document.createElement('a');
    a.href = url;
    a.download = datei.name;
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
}

/** Erster Dienst, der eine fertige Datei entgegennimmt.
 *
 * Ein Strom-Dienst scheidet hier aus: Er transkribiert, was in Echtzeit
 * hineinläuft, und eine Stunde Aufnahme bräuchte dort eine Stunde. */
function dateiFaehigerDienst() {
  const gewaehlt = config.engines.find((e) => e.id === settings.engine);
  if (gewaehlt && !gewaehlt.streaming && gewaehlt.configured) return gewaehlt;
  return config.engines.find((e) => !e.streaming && e.configured) || null;
}

/** Datei zu 16-kHz-Mono-PCM machen - im Browser, nicht auf dem Server.
 *
 * Der Browser dekodiert mp3, m4a, wav, ogg und flac ohnehin; auf dem Server
 * bräuchte es dafür ffmpeg im Bündel. Das Umrechnen erledigt ein
 * OfflineAudioContext gleich mit. */
async function alsPcm(datei) {
  const roh = await datei.arrayBuffer();
  const ctx = new AudioContext();
  let dekodiert;
  try {
    dekodiert = await ctx.decodeAudioData(roh);
  } finally {
    ctx.close();
  }
  const laenge = Math.ceil(dekodiert.duration * SAMPLE_RATE);
  const offline = new OfflineAudioContext(1, laenge, SAMPLE_RATE);
  const quelle = offline.createBufferSource();
  quelle.buffer = dekodiert;
  quelle.connect(offline.destination);
  quelle.start();
  const fertig = await offline.startRendering();

  const kanal = fertig.getChannelData(0);
  const pcm = new Int16Array(kanal.length);
  for (let i = 0; i < kanal.length; i += 1) {
    const wert = Math.max(-1, Math.min(1, kanal[i]));
    pcm[i] = wert < 0 ? wert * 0x8000 : wert * 0x7fff;
  }
  return pcm;
}

async function transkribiereDatei(datei, { mitSprechern = false } = {}) {
  if (recording) {
    setStatus('Erst die laufende Aufnahme beenden.', true);
    return;
  }
  // Mit Sprechern gleich beim ersten Mal - sonst liefe dieselbe Datei
  // zweimal durch die Erkennung.
  if (mitSprechern && sprecherDienst()) {
    return transkribiereAmStueck(datei, sprecherDienst(), { mitSprechern: true });
  }
  const dienst = dateiFaehigerDienst();
  if (!dienst) {
    setStatus('Kein Dienst verfügbar, der eine fertige Datei annimmt.', true);
    return;
  }
  // Cloud-Dienste bekommen die Datei am Stück. Nur der lokale
  // whisper-server nimmt keine ganzen Dateien und bleibt beim alten Weg.
  if (!dienst.local) return transkribiereAmStueck(datei, dienst);

  ui.uploadOpen.disabled = true;
  ui.toggle.disabled = true;
  setStatus(`${datei.name} wird gelesen …`);

  let pcm;
  try {
    pcm = await alsPcm(datei);
  } catch (error) {
    setStatus(`Audio nicht lesbar: ${error.message}`, true);
    ui.uploadOpen.disabled = false;
    ui.toggle.disabled = false;
    return;
  }

  const dauer = pcm.length / SAMPLE_RATE;
  try {
    await starteSitzung(dienst.id);
    await oeffneAudioverbindung();
    verwerfeMitschnitt();
    setzeSitzungsinhaltZurueck();
    clearTranscript();
    ui.transcript.querySelector('.empty')?.remove();
    ui.protocol.innerHTML = '<p class="empty">Die Zusammenfassung entsteht nach der Erkennung.</p>';
    zeigeFeedback(false);

    // Schneller als in Echtzeit, aber nicht beliebig schnell: Der Server
    // schneidet und transkribiert nebenher, und alles, was er nicht abholt,
    // liegt in seinem Speicher. Etwa das Sechsfache entspricht dem, was die
    // Erkennung schafft.
    const stueck = SAMPLE_RATE * 5;
    for (let ab = 0; ab < pcm.length; ab += stueck) {
      if (ws?.readyState !== WebSocket.OPEN) throw new Error('Verbindung getrennt');
      // slice und nicht subarray: subarray teilt sich den Puffer mit dem
      // Original, `.buffer` wäre also jedes Mal die ganze Datei.
      ws.send(pcm.slice(ab, Math.min(ab + stueck, pcm.length)).buffer);
      const anteil = Math.min(1, (ab + stueck) / pcm.length);
      setStatus(`${datei.name}: ${Math.round(anteil * 100)} % übertragen `
                + `(${formatSeconds(dauer)} Aufnahme)`);
      await new Promise((r) => setTimeout(r, 800));
    }
    setStatus('Übertragen – die Erkennung läuft noch …');
    const ergebnis = await schliesseSitzung();
    if (ergebnis.protocol) renderProtocol(ergebnis.protocol);
    setStatus(`fertig – ${datei.name}`);
    bieteZusammenfassungAn();
  } catch (error) {
    setStatus(`Verarbeitung fehlgeschlagen: ${error.message}`, true);
  } finally {
    ws?.close();
    ui.uploadOpen.disabled = false;
    ui.toggle.disabled = false;
  }
}

/** Die Datei unverändert an den Server, der sie an den Dienst durchreicht.
 *  Eine Stunde dauert so Sekunden bis wenige Minuten statt einer Stunde. */
async function transkribiereAmStueck(datei, dienst, { mitSprechern = false } = {}) {
  const grenze = (config.upload_mb ?? 150) * 1024 * 1024;
  if (datei.size > grenze) {
    setStatus(`${datei.name} ist größer als ${config.upload_mb} MB. Als MP3 oder M4A `
              + 'gespeichert wird dieselbe Aufnahme deutlich kleiner.', true);
    return;
  }
  ui.uploadOpen.disabled = true;
  ui.toggle.disabled = true;
  const beginn = Date.now();
  const uhr = setInterval(() => {
    setStatus(`${datei.name} wird mit ${dienst.name} transkribiert`
              + (mitSprechern ? ', Sprecher werden unterschieden' : '')
              + ` … ${Math.round((Date.now() - beginn) / 1000)} s`);
  }, 1000);
  setStatus(`${datei.name} wird übertragen …`);
  let sperre = null;
  try { sperre = await navigator.wakeLock?.request('screen'); } catch { /* egal */ }
  try {
    const ziel = mitSprechern ? '/api/transcribe?diarize=true'
      : `/api/transcribe?engine=${encodeURIComponent(dienst.id)}`;
    const antwort = await fetch(ziel, {
      method: 'POST',
      headers: { 'Content-Type': datei.type || 'application/octet-stream',
                 'X-Filename': encodeURIComponent(datei.name) },
      body: datei,
    });
    const daten = await antwort.json().catch(() => ({}));
    if (!antwort.ok) throw new Error(daten.detail || `HTTP ${antwort.status}`);

    verwerfeMitschnitt();
    setzeSitzungsinhaltZurueck();
    clearTranscript();
    ui.transcript.querySelector('.empty')?.remove();
    firstSegment = false;
    for (const eintrag of daten.transcript) handleEvent({ type: 'transcript', entry: eintrag });
    // Die Datei selbst ist der Mitschnitt: fürs Projekt und fürs erneute
    // Durchlaufen.
    projektAudio = datei;
    mitschnittDateien = [];
    ui.retranscribe.hidden = false;
    ui.downloadAudio.hidden = false;
    ui.speakersOpen.hidden = !sprecherDienst();
    ui.protocol.innerHTML = '<p class="empty">Die Zusammenfassung folgt.</p>';
    zeigeFeedback(false);
    ui.transcriptEdit.hidden = false;
    zeigeTelefonHinweis(false);
    setStatus(`fertig – ${datei.name} in ${daten.seconds} s transkribiert`
              + ` (${formatSeconds(daten.duration)} Aufnahme)`);
    tresorId = null;
    if (mitSprechern && daten.transcript.some((e) => e.speaker)) {
      transkriptEintraege = daten.transcript.map((e) => ({ ...e }));
      sprecher = Object.fromEntries([...new Set(daten.transcript.map((e) => e.speaker))]
        .filter(Boolean).map((id) => [id, { name: '', mehrere: false }]));
      zeichneTranskript();
      oeffneSprecherDialog();
    } else {
      bieteZusammenfassungAn();
    }
  } catch (error) {
    setStatus(`Verarbeitung fehlgeschlagen: ${error.message}`, true);
  } finally {
    clearInterval(uhr);
    try { await sperre?.release(); } catch { /* schon weg */ }
    ui.uploadOpen.disabled = false;
    ui.toggle.disabled = false;
  }
}

// -- Feedback ---------------------------------------------------------------

async function sendFeedback() {
  const text = ui.feedbackText.value.trim();
  if (!text) return;
  ui.feedbackSend.disabled = true;
  try {
    const response = await fetch(`/api/sessions/${sessionId}/feedback`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text }),
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const result = await response.json();
    if (result.protocol) renderProtocol(result.protocol);
    gesammelteHinweise = result.feedback;
    renderNotes(result.feedback);
    ui.feedbackText.value = '';
    showFeedbackPanel(false);
    setStatus(!settings.live_protocol ? 'Hinweis gemerkt – er geht in die Zusammenfassung'
      : recording ? 'Aufnahme läuft' : 'Hinweis eingearbeitet');
  } catch (error) {
    setStatus(`Hinweis fehlgeschlagen: ${error.message}`, true);
  } finally {
    ui.feedbackSend.disabled = false;
  }
}

function renderNotes(notes) {
  ui.feedbackNotes.innerHTML = '';
  for (const note of notes) {
    const div = document.createElement('div');
    div.className = 'note-chip';
    div.textContent = note;
    ui.feedbackNotes.append(div);
  }
}

async function toggleDictation() {
  if (!dictating) {
    if (!audioContext) {
      setStatus('Diktieren geht nur während einer laufenden Aufzeichnung.', true);
      return;
    }
    dictationChunks = [];
    dictating = true;
    // Das Diktat ist die eigene Stimme, nicht die Gegenseite.
    if (systemRegler) systemRegler.gain.value = 0;
    diktatSeit = Date.now();
    ui.feedbackDictate.dataset.recording = 'true';
    ui.feedbackDictate.lastChild.textContent = ' Diktat beenden';
    ui.feedbackDictate.querySelector('use').setAttribute('href', '#i-stop');
    return;
  }

  dictating = false;
  if (systemRegler) systemRegler.gain.value = 1;
  // Während des Diktats ging die Besprechung weiter, aber nicht in die
  // Aufnahme - dieselbe Art Loch wie ein Aussetzer, also auch so vermerkt.
  const diktatSekunden = Math.round((Date.now() - diktatSeit) / 1000);
  if (recording && !paused && diktatSekunden >= 3) markiereLuecke(diktatSekunden, 'diktat');
  ui.feedbackDictate.dataset.recording = 'false';
  ui.feedbackDictate.lastChild.textContent = ' Diktieren';
  ui.feedbackDictate.querySelector('use').setAttribute('href', '#i-mic');
  ui.feedbackDictate.disabled = true;
  try {
    const wav = encodeWav(dictationChunks);
    const response = await fetch(`/api/sessions/${sessionId}/transcribe`, {
      method: 'POST',
      headers: { 'Content-Type': 'audio/wav' },
      body: wav,
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const { text } = await response.json();
    ui.feedbackText.value = (ui.feedbackText.value + ' ' + text).trim();
  } catch (error) {
    setStatus(`Diktat fehlgeschlagen: ${error.message}`, true);
  } finally {
    ui.feedbackDictate.disabled = false;
    dictationChunks = [];
  }
}

function encodeWav(chunks) {
  const total = chunks.reduce((sum, c) => sum + c.length, 0);
  const buffer = new ArrayBuffer(44 + total * 2);
  const view = new DataView(buffer);
  const ascii = (offset, text) => {
    for (let i = 0; i < text.length; i++) view.setUint8(offset + i, text.charCodeAt(i));
  };
  ascii(0, 'RIFF');
  view.setUint32(4, 36 + total * 2, true);
  ascii(8, 'WAVEfmt ');
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);            // PCM
  view.setUint16(22, 1, true);            // mono
  view.setUint32(24, SAMPLE_RATE, true);
  view.setUint32(28, SAMPLE_RATE * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  ascii(36, 'data');
  view.setUint32(40, total * 2, true);

  let offset = 44;
  for (const chunk of chunks) {
    for (let i = 0; i < chunk.length; i++, offset += 2) view.setInt16(offset, chunk[i], true);
  }
  return buffer;
}

// -- Markdown ---------------------------------------------------------------

// Bewusst minimaler Renderer: Überschriften, Listen, Absätze. Das Modell
// liefert nur diese Formen.
function renderProtocol(markdown) {
  if (!markdown) {
    ui.protocol.innerHTML = '<p class="empty">Noch kein Protokoll.</p>';
    return;
  }
  ui.protocol.dataset.markdown = markdown;
  const html = [];
  let inList = false;
  let tabelle = null;
  const schliesseTabelle = () => {
    if (!tabelle) return;
    const [kopf, ...zeilen] = tabelle;
    const zelle = (tag) => (z) => `<${tag}>${inline(z)}</${tag}>`;
    html.push('<div class="table-wrap"><table><thead><tr>'
      + kopf.map(zelle('th')).join('') + '</tr></thead><tbody>'
      + zeilen.map((z) => `<tr>${z.map(zelle('td')).join('')}</tr>`).join('')
      + '</tbody></table></div>');
    tabelle = null;
  };
  for (const raw of markdown.split('\n')) {
    const line = raw.trimEnd();
    // Tabellen, etwa die Aufgabentabelle der Zusammenfassung.
    if (/^\s*\|.*\|\s*$/.test(line)) {
      if (inList) { html.push('</ul>'); inList = false; }
      const zellen = line.trim().slice(1, -1).split('|').map((z) => z.trim());
      if (zellen.every((z) => /^:?-{2,}:?$/.test(z))) continue;   // Trennzeile
      (tabelle ??= []).push(zellen);
      continue;
    }
    schliesseTabelle();
    const bullet = line.match(/^\s*[-*]\s+(.*)$/);
    if (bullet) {
      if (!inList) { html.push('<ul>'); inList = true; }
      html.push(`<li>${inline(bullet[1])}</li>`);
      continue;
    }
    if (inList) { html.push('</ul>'); inList = false; }
    const heading = line.match(/^(#{1,4})\s+(.*)$/);
    if (heading) {
      const level = Math.min(heading[1].length + 1, 4);
      html.push(`<h${level}>${inline(heading[2])}</h${level}>`);
    } else if (line.trim()) {
      html.push(`<p>${inline(line)}</p>`);
    }
  }
  if (inList) html.push('</ul>');
  schliesseTabelle();
  ui.protocol.innerHTML = html.join('');
}

function inline(text) {
  const escaped = text
    .replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;');
  return escaped.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
                .replace(/(^|\W)\*(.+?)\*(?=\W|$)/g, '$1<em>$2</em>');
}

// -- Verdrahtung ------------------------------------------------------------

ui.toggle.addEventListener('click', () => (recording ? togglePause() : start()));
ui.stop.addEventListener('click', () => { if (recording) stop(); });
ui.settingsOpen.disabled = true;   // bis loadConfig() durch ist
// Auf dem Telefon liegen die Bereiche untereinander und teilen sich die Höhe.
// Ein Tipp auf eine Überschrift gibt dem Bereich das Doppelte - nebeneinander
// am Schreibtisch gibt es dagegen nichts zu verteilen.
const schmalerBildschirm = window.matchMedia('(max-width: 760px)');
document.querySelectorAll('.pane > h2').forEach((kopf, i) => {
  kopf.addEventListener('click', (ereignis) => {
    if (!schmalerBildschirm.matches || ereignis.target.closest('button')) return;
    const haupt = document.querySelector('main');
    const gross = i === 0 ? 'gross-transkript' : 'gross-protokoll';
    haupt.classList.remove(i === 0 ? 'gross-protokoll' : 'gross-transkript');
    haupt.classList.toggle(gross);
  });
});

// Auf schmalen Bildschirmen zieht der Feedback-Auslöser in die Überschrift
// der Protokollspalte. Als eigene Leiste kostete er dort ein Fünftel der
// sichtbaren Höhe - für eine Schaltfläche, die selten gebraucht wird.
const protokollKopf = document.querySelector('main .pane:last-child > h2');
function zeigeFeedback(sichtbar) {
  ui.feedbackArea.hidden = !sichtbar;
  ui.feedbackOpen.hidden = !sichtbar;
}

function ordneFeedbackEin() {
  const inDenKopf = schmalerBildschirm.matches;
  if (inDenKopf && ui.feedbackOpen.parentElement !== protokollKopf) {
    ui.feedbackOpen.classList.add('icon-button', 'small');
    ui.feedbackOpen.classList.remove('ghost', 'wide');
    protokollKopf.insertBefore(ui.feedbackOpen, protokollKopf.querySelector('.icon-button'));
  } else if (!inDenKopf && ui.feedbackOpen.parentElement === protokollKopf) {
    ui.feedbackOpen.classList.remove('icon-button', 'small');
    ui.feedbackOpen.classList.add('ghost', 'wide');
    ui.feedbackPanel.before(ui.feedbackOpen);
  }
}
ordneFeedbackEin();
schmalerBildschirm.addEventListener('change', ordneFeedbackEin);

ui.uploadOpen.addEventListener('click', oeffneOeffnen);
ui.openFile.addEventListener('click', () => ui.audioFile.click());
ui.audioFile.addEventListener('change', () => {
  const datei = ui.audioFile.files?.[0];
  ui.audioFile.value = '';        // damit dieselbe Datei erneut gewählt werden kann
  if (!datei) return;
  const mitSprechern = ui.openDiarize.checked && Boolean(sprecherDienst());
  if (settings.upload_diarize !== ui.openDiarize.checked) {
    settings = { ...settings, upload_diarize: ui.openDiarize.checked };
    storeSettings();
  }
  if (ui.openDialog.open) ui.openDialog.close();
  if (/\.zip$/i.test(datei.name) || datei.type === 'application/zip') oeffneProjekt(datei);
  else transkribiereDatei(datei, { mitSprechern });
});

/** Ein Dialog für alles, was man öffnen kann: eine Datei vom Gerät oder eine
 *  gespeicherte Sitzung. Ob Sprecher unterschieden werden, fragt er vor dem
 *  Hochladen - danach hieße es, die Datei ein zweites Mal zu transkribieren. */
function oeffneOeffnen() {
  if (!config) return;
  const dienst = sprecherDienst();
  ui.openDiarizeRow.hidden = !dienst;
  ui.openDiarize.checked = Boolean(dienst) && settings.upload_diarize === true;
  ui.openDiarizeNote.hidden = !dienst;
  ui.openDiarizeNote.textContent = dienst
    ? `Über ${dienst.name}${dienst.cost ? ` (${dienst.cost.split(',')[0]})` : ''}. `
      + 'Danach ordnest du die Stimmen Namen zu.' : '';
  ui.openVaultHint.hidden = !config.vault;
  ui.openDialog.showModal();
}

function oeffneTresor() {
  if (!config?.vault) return;
  if (ui.openDialog.open) ui.openDialog.close();
  zeichneTresor();
  ui.vaultDialog.showModal();
}
ui.vaultOpen.addEventListener('click', oeffneTresor);
ui.openVault.addEventListener('click', oeffneTresor);
ui.downloadAudio.addEventListener('click', ladeMitschnittHerunter);
ui.settingsOpen.addEventListener('click', () => {
  if (!config) return;
  fillSettingsDialog();
  ui.settings.showModal();
});
ui.setEngine.addEventListener('change', updateEngineNote);
ui.compatPreset.addEventListener('change', waehleVorlage);
ui.setLlm.addEventListener('change', updateLlmNote);
ui.setLive.addEventListener('change', () => { ui.liveOptions.hidden = !ui.setLive.checked; });

// Erklärtext je Einstellung: zweiter Klick auf denselben Schalter klappt ihn
// wieder zu. Kein Tooltip, damit es auch am Telefon lesbar bleibt.
for (const button of document.querySelectorAll('.help-toggle')) {
  button.addEventListener('click', () => {
    const text = el(button.getAttribute('aria-controls'));
    const open = button.getAttribute('aria-expanded') !== 'true';
    button.setAttribute('aria-expanded', String(open));
    text.hidden = !open;
  });
}
ui.settings.addEventListener('close', () => {
  if (ui.settings.returnValue !== 'save') return;
  settings = {
    ...settings,
    engine: ui.setEngine.value,
    llm_endpoint: ui.setLlm.value,
    live_protocol: ui.setLive.checked,
    glossaries: glossarEntwurf.map((g) => ({ ...g, terms: [...new Set(g.terms)] })),
    active_glossaries: [...aktivEntwurf].filter((id) => glossarEntwurf.some((g) => g.id === id)),
    speakers_after_stop: ui.setSpeakersAfter.checked,
    system_audio: ui.setSystemAudio.checked,
  };
  storeSettings();
  zeigeLiveSchalter();
  renderMeta();
  if (config.mode === 'desktop') saveCredentials();
});

ui.firstRunStart.addEventListener('click', downloadModel);
ui.firstRunSkip.addEventListener('click', () => { ui.firstRun.hidden = true; });

function showFeedbackPanel(open) {
  ui.feedbackPanel.hidden = !open;
  ui.feedbackOpen.hidden = open;   // sonst steht der Knopf sinnlos über dem Feld
  if (open) ui.feedbackText.focus();
}

ui.feedbackOpen.addEventListener('click', () => showFeedbackPanel(true));
ui.feedbackCancel.addEventListener('click', () => showFeedbackPanel(false));
ui.feedbackSend.addEventListener('click', sendFeedback);
ui.feedbackDictate.addEventListener('click', toggleDictation);

ui.hintClose.addEventListener('click', () => {
  ui.hint.hidden = true;
  try {
    localStorage.setItem(HINT_KEY, '1');
  } catch { /* nicht kritisch */ }
});

ui.export.addEventListener('click', (ereignis) => {
  ereignis.stopPropagation();
  const offen = ui.exportMenu.hidden;
  ui.exportMenu.hidden = !offen;
  ui.export.setAttribute('aria-expanded', String(offen));
});
document.addEventListener('click', () => {
  ui.exportMenu.hidden = true;
  ui.export.setAttribute('aria-expanded', 'false');
});
for (const eintrag of ui.exportMenu.querySelectorAll('[data-export]')) {
  eintrag.addEventListener('click', () => exportiere(eintrag.dataset.export,
                                                     eintrag.dataset.format || 'md'));
}

for (const button of document.querySelectorAll('[data-copy]')) {
  button.addEventListener('click', async () => {
    const text = button.dataset.copy === 'transcript'
      ? lastTranscriptText
      : (ui.protocol.dataset.markdown || '');
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
      const use = button.querySelector('use');
      use.setAttribute('href', '#i-check');
      button.classList.add('copied');
      setTimeout(() => {
        use.setAttribute('href', '#i-copy');
        button.classList.remove('copied');
      }, 1200);
    } catch {
      setStatus('Kopieren wurde vom Browser abgelehnt.', true);
    }
  });
}

/** Schalter im Kopf und Häkchen in den Einstellungen sind dieselbe Wahl. */
function zeigeLiveSchalter() {
  ui.liveProtocol.checked = settings.live_protocol === true;
  if (!recording && !ui.protocol.dataset.markdown) {
    ui.protocol.innerHTML = settings.live_protocol
      ? '<p class="empty">Entsteht während der Aufnahme.</p>'
      : '<p class="empty">Entsteht nach dem Beenden.</p>';
  }
}

ui.liveProtocol.addEventListener('change', () => {
  settings = { ...settings, live_protocol: ui.liveProtocol.checked };
  storeSettings();
  zeigeLiveSchalter();
  renderMeta();
  if (settings.live_protocol) loadHealth();
});
ui.phoneHintUpload.addEventListener('click', oeffneOeffnen);
ui.summaryOpen.addEventListener('click', oeffneZusammenfassung);
ui.retranscribe.addEventListener('click', transkribiereErneut);
ui.speakersOpen.addEventListener('click', () => (hatSprecher()
  ? oeffneSprecherDialog() : unterscheideSprecher({ fragen: true })));
ui.sumSpeakersEdit.addEventListener('click', () => {
  ui.summaryDialog.close('speakers');
  if (hatSprecher()) oeffneSprecherDialog();
  else unterscheideSprecher({ fragen: true });
});
ui.speakerFilter.addEventListener('change', filtereTranskript);
ui.speakerName.addEventListener('input', () => {
  if (!aktiverSprecher) return;
  sprecher[aktiverSprecher] = { ...(sprecher[aktiverSprecher] || {}), name: ui.speakerName.value };
  zeichneSprecherReiter();
});
ui.speakerMixed.addEventListener('change', () => {
  if (!aktiverSprecher) return;
  sprecher[aktiverSprecher] = { ...(sprecher[aktiverSprecher] || {}), mehrere: ui.speakerMixed.checked };
  ui.speakerSplit.hidden = !ui.speakerMixed.checked;
  zeichneSprecherReiter();
});
ui.speakerSplit.addEventListener('click', () => ui.speakerDialog.close('split'));
ui.speakerDialog.addEventListener('close', () => {
  hoerprobe?.pause();
  ungesichert = true;
  const wert = ui.speakerDialog.returnValue;
  if (wert === 'split' && aktiverSprecher) {
    transkriptBearbeiten = true;
    zeichneTranskript();
    ui.speakerFilter.value = aktiverSprecher;
    filtereTranskript();
  } else {
    zeichneTranskript();
  }
  merkeVor();
  if (wert === 'summary') oeffneZusammenfassung();
  else {
    zeigeZurueckZurZusammenfassung(true);
    // Wer das Transkript prüft, kommt gleich zur Zusammenfassung zurück.
    if (wert !== 'check' && wert !== 'split') holeLiveLaufNach();
  }
});

// -- Sprecher ------------------------------------------------------

function sprecherDienst() {
  return config.engines.find((e) => e.diarize && e.configured) ?? null;
}

function hatSprecher() {
  return transkriptEintraege.some((e) => e.speaker);
}

function sprecherLabel(id) {
  const eintrag = sprecher[id] || {};
  if (eintrag.mehrere) return eintrag.name?.trim() ? `${eintrag.name.trim()} u. a.` : 'mehrere Sprecher';
  return eintrag.name?.trim() || `Sprecher ${String(id).replace(/\D+/g, '') || '?'}`;
}

/** Wie der Sprecher beim Modell ankommt. „Martina u. a.“ sähe dort aus wie
 *  ein Name - dann schriebe es Martina Sätze zu, die jemand anderes sagte. */
function sprecherFuerModell(id) {
  const eintrag = sprecher[id] || {};
  if (!eintrag.mehrere) return sprecherLabel(id);
  return eintrag.name?.trim() ? `mehrere Sprecher (darunter ${eintrag.name.trim()})` : 'mehrere Sprecher';
}

/** Wie lange die Audiodatei läuft - aus den Metadaten, ohne sie zu dekodieren. */
function audioDauer(datei) {
  return new Promise((fertig) => {
    const a = new Audio();
    const url = URL.createObjectURL(datei);
    a.preload = 'metadata';
    a.onloadedmetadata = () => { URL.revokeObjectURL(url); fertig(a.duration); };
    a.onerror = () => { URL.revokeObjectURL(url); fertig(NaN); };
    a.src = url;
  });
}

/** Die ganze Aufnahme mit Sprechern neu transkribieren. Das Ergebnis ersetzt
 *  das bisherige Transkript; Lückenvermerke werden nach ihrer Zeit wieder
 *  eingefügt, sonst formulierte die Zusammenfassung über das Loch. */
async function unterscheideSprecher({ fragen }) {
  const dienst = sprecherDienst();
  if (!projektAudio || !dienst) {
    setStatus('Dafür braucht es die Aufnahme und einen Dienst, der Sprecher unterscheidet.', true);
    return;
  }
  const dauer = await audioDauer(projektAudio);
  if (dauer > 3 * 3600) {
    setStatus('Über drei Stunden kann der Dienst Sprecher nicht in einem Stück '
              + 'unterscheiden. Das Transkript bleibt ohne Sprecher.', true);
    bieteZusammenfassungAn();
    return;
  }
  const minuten = Number.isFinite(dauer) ? Math.max(1, Math.round(dauer / 60)) : null;
  if (fragen && !confirm('Sprecher unterscheiden?\n\nDie ganze Aufnahme geht dafür an '
      + `${dienst.name} und wird ein zweites Mal transkribiert`
      + (minuten ? ` – bei ${minuten} min rund ${Math.max(1, Math.round(minuten * 0.3))} ct` : '')
      + '. Das bisherige Transkript wird ersetzt, Lücken bleiben vermerkt.')) return;

  ui.transcribing.hidden = false;
  ui.speakersOpen.disabled = true;
  const beginn = Date.now();
  const uhr = setInterval(() => setStatus(
    `Sprecher werden unterschieden … ${Math.round((Date.now() - beginn) / 1000)} s`), 1000);
  // Der Bildschirm soll während der Minute nicht ausgehen - am Telefon
  // bräche sonst die Anfrage ab.
  let sperre = null;
  try { sperre = await navigator.wakeLock?.request('screen'); } catch { /* egal */ }
  try {
    const antwort = await fetch('/api/transcribe?diarize=true', {
      method: 'POST',
      headers: { 'Content-Type': projektAudio.type || 'audio/mpeg',
                 'X-Filename': encodeURIComponent(projektAudio.name) },
      body: projektAudio,
    });
    const daten = await antwort.json().catch(() => ({}));
    if (!antwort.ok) throw new Error(daten.detail || `HTTP ${antwort.status}`);
    const luecken = transkriptEintraege.filter((e) => e.gap);
    transkriptEintraege = [...daten.transcript, ...luecken]
      .sort((a, b) => a.seconds - b.seconds || (a.gap ? 1 : -1));
    sprecher = Object.fromEntries([...new Set(daten.transcript.map((e) => e.speaker))]
      .filter(Boolean).map((id) => [id, sprecher[id] ?? { name: '', mehrere: false }]));
    zeichneTranskript();
    ungesichert = true;
    setStatus(`${Object.keys(sprecher).length} Sprecher erkannt – jetzt zuordnen.`);
    oeffneSprecherDialog();
  } catch (error) {
    // Das bisherige Transkript bleibt stehen; ohne Sprecher geht es weiter.
    setStatus(`Sprecher unterscheiden fehlgeschlagen: ${error.message}. `
              + 'Das Transkript bleibt ohne Sprecher.', true);
    bieteZusammenfassungAn();
  } finally {
    clearInterval(uhr);
    try { await sperre?.release(); } catch { /* schon weg */ }
    ui.transcribing.hidden = true;
    ui.speakersOpen.disabled = false;
  }
}

/** Das ganze Transkript neu zeichnen - mit Sprecher vor jeder Zeile, wenn
 *  es welche gibt. Auch lastTranscriptText (Kopieren) folgt. */
function zeichneTranskript() {
  clearTranscript();
  ui.transcript.querySelector('.empty')?.remove();
  firstSegment = false;
  lastTranscriptText = '';
  ui.transcriptEditing.hidden = !transkriptBearbeiten;
  ui.transcript.classList.toggle('editing-transcript', transkriptBearbeiten);
  ui.transcriptEdit.hidden = recording || !transkriptEintraege.length || transkriptBearbeiten;
  if (transkriptBearbeiten) {
    zeichneBearbeitung();
    fuelleSprecherFilter();
    return;
  }
  transkriptEintraege.forEach((eintrag, index) => {
    const p = document.createElement('p');
    p.className = eintrag.gap ? 'seg gap' : 'seg';
    p.dataset.index = index;
    if (eintrag.gap) {
      p.textContent = eintrag.text.replace(/^\[|\]$/g, '');
    } else {
      const time = document.createElement('time');
      time.textContent = formatSeconds(eintrag.seconds);
      p.append(time);
      if (eintrag.speaker) {
        const knopf = document.createElement('button');
        knopf.type = 'button';
        knopf.className = sprecher[eintrag.speaker]?.mehrere ? 'spk mixed' : 'spk';
        knopf.dataset.speaker = eintrag.speaker;
        knopf.textContent = sprecherLabel(eintrag.speaker);
        knopf.title = sprecher[eintrag.speaker]?.mehrere
          ? 'Mehrere Personen – antippen, um diese Zeile jemandem zuzuordnen'
          : 'Diese Zeile einem anderen Sprecher zuordnen';
        knopf.addEventListener('click', () => waehleSprecherFuer(knopf, index));
        p.append(knopf);
      }
      p.append(document.createTextNode(eintrag.text));
    }
    ui.transcript.insertBefore(p, ui.partial);
    lastTranscriptText += (lastTranscriptText ? '\n\n' : '')
      + (eintrag.speaker && !eintrag.gap ? `${sprecherLabel(eintrag.speaker)}: ` : '') + eintrag.text;
  });
  ui.export.disabled = !transkriptEintraege.length;
  fuelleSprecherFilter();
}

/** Eine einzelne Zeile umhängen: eine Auswahl an Stelle des Namens. */
function waehleSprecherFuer(knopf, index) {
  const auswahl = sprecherAuswahl(transkriptEintraege[index].speaker);
  knopf.replaceWith(auswahl);
  auswahl.focus();
  let erledigt = false;
  const fertig = () => {
    if (erledigt) return;
    erledigt = true;
    const id = neuerSprecherFallsGewuenscht(auswahl.value);
    if (id !== null) transkriptEintraege[index].speaker = id || undefined;
    ungesichert = true;
    zeichneTranskript();
    filtereTranskript();
    merkeVor();
  };
  auswahl.addEventListener('change', fertig);
  auswahl.addEventListener('blur', fertig);
}

const NEUE_PERSON = '__neu';

/** Auswahl aller Sprecher, dazu „ohne“ und „neue Person“. Mit „neue Person“
 *  lassen sich Stimmen trennen, die die Erkennung zusammengeworfen hat. */
function sprecherAuswahl(aktuell) {
  const auswahl = document.createElement('select');
  auswahl.className = 'small-select';
  auswahl.append(new Option('– ohne –', ''));
  for (const id of Object.keys(sprecher)) auswahl.append(new Option(sprecherLabel(id), id));
  auswahl.append(new Option('Neue Person …', NEUE_PERSON));
  auswahl.value = aktuell || '';
  return auswahl;
}

/** Liefert die Kennung für den gewählten Wert, legt bei „neue Person“ einen
 *  Sprecher an. null heißt abgebrochen. */
function neuerSprecherFallsGewuenscht(wert) {
  if (wert !== NEUE_PERSON) return wert;
  const name = prompt('Name der Person:')?.trim();
  if (!name) return null;
  const vorhanden = Object.keys(sprecher).find((id) => sprecher[id].name?.trim() === name
                                                    && !sprecher[id].mehrere);
  if (vorhanden) return vorhanden;
  let n = 1;
  while (sprecher[`person_${n}`]) n += 1;
  sprecher[`person_${n}`] = { name, mehrere: false };
  return `person_${n}`;
}

function fuelleSprecherFilter() {
  const mit = hatSprecher() || (transkriptBearbeiten && Object.keys(sprecher).length > 0);
  ui.speakerFilter.hidden = !mit;
  if (!mit) return;
  const vorher = ui.speakerFilter.value;
  ui.speakerFilter.innerHTML = '';
  ui.speakerFilter.append(new Option('Alle Sprecher', ''));
  for (const id of Object.keys(sprecher)) ui.speakerFilter.append(new Option(sprecherLabel(id), id));
  ui.speakerFilter.value = [...ui.speakerFilter.options].some((o) => o.value === vorher) ? vorher : '';
  filtereTranskript();
}

function filtereTranskript() {
  const nur = ui.speakerFilter.value;
  for (const p of ui.transcript.querySelectorAll('.seg[data-index]')) {
    const eintrag = transkriptEintraege[Number(p.dataset.index)];
    p.classList.toggle('hidden-by-filter', Boolean(nur) && !eintrag.gap && eintrag.speaker !== nur);
  }
}

let aktiverSprecher = null, hoerprobe = null;

function sprechzeiten() {
  const zeiten = {};
  transkriptEintraege.forEach((e, i) => {
    if (!e.speaker || e.gap) return;
    const naechste = transkriptEintraege.slice(i + 1).find((x) => !x.gap);
    const ende = naechste ? naechste.seconds : e.seconds + e.text.length / 15;
    zeiten[e.speaker] = (zeiten[e.speaker] || 0) + Math.max(0, ende - e.seconds);
  });
  return zeiten;
}

function oeffneSprecherDialog() {
  const zeiten = sprechzeiten();
  const ids = Object.keys(sprecher).sort((a, b) => (zeiten[b] || 0) - (zeiten[a] || 0));
  aktiverSprecher = ids.includes(aktiverSprecher) ? aktiverSprecher : ids[0];
  zeichneSprecherReiter();
  zeigeSprecher(aktiverSprecher);
  ui.speakerDialog.showModal();
}

function zeichneSprecherReiter() {
  const zeiten = sprechzeiten();
  const gesamt = Object.values(zeiten).reduce((s, z) => s + z, 0) || 1;
  const ids = Object.keys(sprecher).sort((a, b) => (zeiten[b] || 0) - (zeiten[a] || 0));
  ui.speakerTabs.innerHTML = '';
  for (const id of ids) {
    const knopf = document.createElement('button');
    knopf.type = 'button';
    knopf.role = 'tab';
    knopf.setAttribute('aria-selected', String(id === aktiverSprecher));
    knopf.textContent = `${sprecherLabel(id)} · ${Math.round((zeiten[id] || 0) / gesamt * 100)} %`;
    knopf.addEventListener('click', () => { zeigeSprecher(id); zeichneSprecherReiter(); });
    ui.speakerTabs.append(knopf);
  }
}

/** Die Ausschnitte eines Sprechers: die längsten, damit man die Stimme
 *  erkennt - ein „Ja.“ hilft dabei nicht. */
function zeigeSprecher(id) {
  aktiverSprecher = id;
  hoerprobe?.pause();
  const eintrag = sprecher[id] || {};
  ui.speakerName.value = eintrag.name || '';
  ui.speakerMixed.checked = Boolean(eintrag.mehrere);
  ui.speakerSplit.hidden = !eintrag.mehrere;
  const zeilen = transkriptEintraege.map((e, i) => ({ e, i }))
    .filter(({ e }) => e.speaker === id && !e.gap)
    .sort((a, b) => b.e.text.length - a.e.text.length).slice(0, 5)
    .sort((a, b) => a.e.seconds - b.e.seconds);
  ui.speakerSamples.innerHTML = '';
  for (const { e, i } of zeilen) {
    const zeile = document.createElement('div');
    zeile.className = 'sample';
    const knopf = document.createElement('button');
    knopf.type = 'button';
    knopf.className = 'icon-button small';
    knopf.title = 'Anhören';
    knopf.setAttribute('aria-label', 'Anhören');
    knopf.innerHTML = '<svg class="icon"><use href="#i-play"/></svg>';
    const naechste = transkriptEintraege.slice(i + 1).find((x) => !x.gap);
    const ende = Math.min(naechste ? naechste.seconds : e.seconds + 15, e.seconds + 15);
    knopf.addEventListener('click', () => spieleAb(e.seconds, ende));
    const zeit = document.createElement('time');
    zeit.textContent = formatSeconds(e.seconds);
    const text = document.createElement('span');
    text.textContent = e.text;
    zeile.append(knopf, zeit, text);
    ui.speakerSamples.append(zeile);
  }
  ui.speakerName.focus();
}

let hoerprobeUrl = null;
function spieleAb(von, bis) {
  if (!projektAudio) return;
  if (!hoerprobe) {
    hoerprobeUrl = URL.createObjectURL(projektAudio);
    hoerprobe = new Audio(hoerprobeUrl);
  }
  hoerprobe.pause();
  hoerprobe.currentTime = Math.max(0, von);
  hoerprobe.ontimeupdate = () => { if (hoerprobe.currentTime >= bis) hoerprobe.pause(); };
  hoerprobe.play().catch((error) => setStatus(`Abspielen nicht möglich: ${error.message}`, true));
}
ui.summaryDialog.addEventListener('close', () => {
  const wert = ui.summaryDialog.returnValue;
  if (wert === 'run') fuehreZusammenfassungAus();
  // Wer den Dialog verlässt, um erst das Transkript anzusehen, soll ohne
  // Umweg über die Sprecher wieder hineinfinden.
  else if (wert !== 'speakers') {
    zeigeZurueckZurZusammenfassung(true);
    holeLiveLaufNach();
  }
});

// -- Zusammenfassung nach dem Termin ---------------------------

function setzeSitzungsinhaltZurueck() {
  liveLaufOffen = false;
  liveNachlauf = null;
  lastTranscriptText = '';
  transkriptEintraege = [];
  gesammelteHinweise = [];
  zusammenfassungVon = null;
  projektAudio = null;
  ungesichert = false;
  sprecher = {};
  ui.speakersOpen.hidden = true;
  ui.speakerFilter.hidden = true;
  anmerkungen = [];
  aenderungen = [];
  ui.protocolEdit.hidden = true;
  ui.annotations.hidden = true;
  transkriptBearbeiten = false;
  ui.transcriptEditing.hidden = true;
  ui.transcriptEdit.hidden = true;
  zeigeZurueckZurZusammenfassung(false);
  tresorId = null;
  tresorAudioId = null;
  tresorAudioVon = null;
  aufnahmeBeginn = new Date();
  ui.summaryOpen.hidden = true;
  ui.export.disabled = true;
  benenneProtokollspalte();
}

function aufgenommeneSekunden() {
  return Math.round(aufgenommeneSamples / SAMPLE_RATE);
}

const DETAIL_TEXT = {
  knapp: ['Knapp', 'Nur Ergebnisse, Entscheidungen und Aufgaben.'],
  standard: ['Standard', 'Je Thema Kernaussagen und Ergebnis, mit Begründung.'],
  ausfuehrlich: ['Ausführlich', 'Mit dem Verlauf der Diskussion und den Argumenten.'],
};

/** Nach dem Stopp oder Upload: gleich den Dialog anbieten, wenn es etwas
 *  zusammenzufassen gibt. Wer ihn schließt, findet den Knopf in der Spalte. */
function bieteZusammenfassungAn() {
  const inhalt = transkriptEintraege.some((e) => !e.gap);
  ui.summaryOpen.hidden = !inhalt;
  if (!inhalt) return;
  if (!config.summary_models?.length) {
    setStatus('Kein Modell für die Zusammenfassung eingerichtet.', true);
    return;
  }
  oeffneZusammenfassung();
}

function oeffneZusammenfassung() {
  if (!config.summary_models?.length) return;
  const modus = config.output_types.find((t) => t.id === ui.outputType.value);
  const gewaehltesModell = config.summary_models.some((m) => m.id === settings.summary_model)
    ? settings.summary_model : config.summary_models[0].id;

  ui.sumModels.innerHTML = '';
  // Gleicher Datenschutz für alle Modelle: einmal darunter statt je Eintrag.
  // Am Telefon standen sonst zwei identische Absätze übereinander.
  const gemeinsam = new Set(config.summary_models.map((m) => m.privacy)).size === 1;
  for (const modell of config.summary_models) {
    const label = document.createElement('label');
    label.className = 'choice';
    const input = document.createElement('input');
    input.type = 'radio';
    input.name = 'sum-model';
    input.value = modell.id;
    input.checked = modell.id === gewaehltesModell;
    const text = document.createElement('span');
    const titel = document.createElement('strong');
    titel.textContent = `${modell.label} – ${modell.name}`;
    const angaben = document.createElement('p');
    angaben.className = 'note';
    angaben.textContent = [modell.speed && `Dauer ${modell.speed}`,
      modell.cost && `Kosten ${modell.cost}`].filter(Boolean).join(' · ');
    const datenschutz = document.createElement('p');
    datenschutz.className = 'note';
    datenschutz.textContent = `Datenschutz (ohne Gewähr): ${modell.privacy}`;
    text.append(titel, angaben);
    if (!gemeinsam) text.append(datenschutz);
    label.append(input, text);
    ui.sumModels.append(label);
  }
  if (gemeinsam && config.summary_models[0]?.privacy) {
    const datenschutz = document.createElement('p');
    datenschutz.className = 'note';
    datenschutz.textContent = `Datenschutz (ohne Gewähr): ${config.summary_models[0].privacy}`;
    ui.sumModels.append(datenschutz);
  }

  const detail = config.summary_details.includes(settings.summary_detail)
    ? settings.summary_detail : 'standard';
  ui.sumDetail.innerHTML = '';
  for (const stufe of config.summary_details) {
    const label = document.createElement('label');
    label.className = 'choice';
    const input = document.createElement('input');
    input.type = 'radio';
    input.name = 'sum-detail';
    input.value = stufe;
    input.checked = stufe === detail;
    input.addEventListener('change', () => {
      ui.sumDetailNote.textContent = DETAIL_TEXT[stufe]?.[1] ?? '';
    });
    label.append(input, document.createTextNode(DETAIL_TEXT[stufe]?.[0] ?? stufe));
    ui.sumDetail.append(label);
  }
  ui.sumDetailNote.textContent = DETAIL_TEXT[detail]?.[1] ?? '';

  const gemerkt = settings.summary_blocks?.[modus?.id];
  ui.sumBlocks.innerHTML = '';
  for (const baustein of modus?.summary_blocks ?? []) {
    const label = document.createElement('label');
    const input = document.createElement('input');
    input.type = 'checkbox';
    input.value = baustein.id;
    input.checked = Array.isArray(gemerkt) ? gemerkt.includes(baustein.id) : baustein.default;
    label.append(input, document.createTextNode(baustein.label));
    ui.sumBlocks.append(label);
  }
  ui.sumBlocks.closest('fieldset').hidden = !(modus?.summary_blocks ?? []).length;

  const mitSprechern = hatSprecher();
  const benannt = Object.values(sprecher).some((s) => s.name?.trim());
  ui.sumPersons.checked = mitSprechern && benannt;
  ui.sumPersons.disabled = !mitSprechern;
  ui.sumPersons.closest('label').hidden = !mitSprechern;
  ui.sumSpeakersNote.textContent = mitSprechern
    ? (benannt ? '' : 'Noch niemand benannt – dann stünde dort nur „Sprecher 1“, „Sprecher 2“.')
    : 'Das Transkript unterscheidet noch keine Sprecher.';
  ui.sumSpeakersEdit.textContent = mitSprechern ? 'Sprecher bearbeiten' : 'Sprecher unterscheiden …';
  ui.sumSpeakersEdit.closest('fieldset').hidden = !(mitSprechern || (projektAudio && sprecherDienst()));
  ui.sumFeedback.textContent = gesammelteHinweise.length
    ? `Deine Hinweise aus dem Termin gehen mit: ${gesammelteHinweise.join(' · ')}` : '';
  ui.summaryDialog.showModal();
}

async function fuehreZusammenfassungAus() {
  const modus = ui.outputType.value;
  const modellId = ui.sumModels.querySelector('input:checked')?.value;
  const modell = config.summary_models.find((m) => m.id === modellId);
  const detail = ui.sumDetail.querySelector('input:checked')?.value ?? 'standard';
  const bausteine = [...ui.sumBlocks.querySelectorAll('input:checked')].map((i) => i.value);
  const personen = hatSprecher() && ui.sumPersons.checked;
  if (!modell) return;

  settings = {
    ...settings, summary_model: modell.id, summary_detail: detail,
    summary_blocks: { ...(settings.summary_blocks || {}), [modus]: bausteine },
  };
  storeSettings();

  liveNachlauf = null;
  ui.cycle.hidden = false;
  ui.summaryOpen.disabled = true;
  zeigeZurueckZurZusammenfassung(false);
  setStatus(`Zusammenfassung mit ${modell.name} läuft …`
            + (modell.speed ? ` (etwa ${modell.speed})` : ''));
  try {
    const antwort = await fetch('/api/summarize', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        model: modell.id, output_type: modus,
        // Namen gehen nur mit, wenn sie zugeordnet werden sollen.
        transcript: transkriptEintraege.map(({ speaker, ...e }) => (
          personen && speaker ? { ...e, speaker: sprecherFuerModell(speaker) } : e)),
        context: terminRahmen, feedback: gesammelteHinweise, glossary: aktiveBegriffe(),
        options: { detail, blocks: bausteine, freitext: ui.sumFreitext.value.trim(),
                   persons: personen },
      }),
    });
    const daten = await antwort.json().catch(() => ({}));
    if (!antwort.ok) throw new Error(daten.detail || `HTTP ${antwort.status}`);
    renderProtocol(daten.markdown);
    zusammenfassungVon = daten.model;
    liveLaufOffen = false;
    ui.protocolTitle.textContent = 'Zusammenfassung';
    ui.export.disabled = false;
    aenderungen = [];
    zeigeEditor();
    letzteOptionen = { detail, blocks: bausteine, freitext: ui.sumFreitext.value.trim() };
    ungesichert = true;
    merkeVor();
    ui.meta.textContent = `Zusammenfassung: ${daten.model.name} (${daten.model.label}) · `
      + `${daten.seconds} s · ${daten.prompt_tokens} Prompt-, `
      + `${daten.completion_tokens} Ausgabe-Tokens`;
    setStatus('Zusammenfassung fertig');
  } catch (error) {
    setStatus(`Zusammenfassung fehlgeschlagen: ${error.message}`, true);
  } finally {
    ui.cycle.hidden = true;
    ui.summaryOpen.disabled = false;
  }
}

// -- Projekt -------------------------------------------------------------

let letzteOptionen = null;

/** Der Arbeitsstand, wie er ins Projekt geht - für die Datei und den Tresor. */
function projektStand() {
  return {
    created: (aufnahmeBeginn ?? new Date()).toISOString(), app_version: config.version,
    output_type: ui.outputType.value, context: terminRahmen, transcript: transkriptEintraege,
    feedback: gesammelteHinweise, speakers: sprecher,
    summary: zusammenfassungVon ? { markdown: ui.protocol.dataset.markdown,
                                    model: zusammenfassungVon, options: letzteOptionen } : null,
  };
}

function audioName(datei) {
  return `aufnahme${(datei.name.match(/\.[a-z0-9]+$/i) ?? ['.mp3'])[0].toLowerCase()}`;
}

function projektTitel() {
  const eigene = (ui.protocol.dataset.markdown || '').match(/^#\s+(.+)$/m)?.[1];
  return (terminRahmen.topic || eigene || '').trim()
    || `Aufnahme vom ${(aufnahmeBeginn ?? new Date()).toLocaleDateString('de-DE')}`;
}

async function speichereProjekt() {
  const audio = projektAudio
    ? { name: audioName(projektAudio), daten: new Uint8Array(await projektAudio.arrayBuffer()) }
    : null;
  const bytes = baueProjekt(projektStand(), audio);
  const tag = (aufnahmeBeginn ?? new Date()).toISOString().slice(0, 10);
  const titel = (terminRahmen.topic || 'projekt').trim().toLowerCase()
    .replace(/[^a-z0-9äöüß]+/g, '-').replace(/^-|-$/g, '').slice(0, 40) || 'projekt';
  const url = URL.createObjectURL(new Blob([bytes], { type: 'application/zip' }));
  const a = document.createElement('a');
  a.href = url;
  a.download = `${tag}-${titel}.zip`;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
  ungesichert = false;
  setStatus(`Projekt gespeichert${audio ? ' – mit Audio' : ' – ohne Audio'}`);
}

async function oeffneProjekt(datei) {
  if (recording) {
    setStatus('Erst die laufende Aufnahme beenden.', true);
    return;
  }
  if (ungesichert && !confirm('Der aktuelle Stand ist nicht gespeichert und geht verloren. '
                              + 'Trotzdem öffnen?')) return;
  let projekt;
  try {
    projekt = await leseProjekt(new Uint8Array(await datei.arrayBuffer()));
  } catch (error) {
    setStatus(`Projekt nicht lesbar: ${error.message}`, true);
    return;
  }
  zeigeProjekt(projekt, datei.name);
  tresorId = null;
}

/** Einen gelesenen Projektstand anzeigen - aus einer Datei oder dem Tresor. */
function zeigeProjekt({ stand, audio }, woher) {
  verwerfeMitschnitt();
  setzeSitzungsinhaltZurueck();
  aufnahmeBeginn = stand.created ? new Date(stand.created) : new Date();

  const modus = el(`output-type-${stand.output_type}`);
  if (modus) { modus.checked = true; positionThumb(); benenneProtokollspalte(); }
  terminRahmen = stand.context ?? {};
  speichereRahmen();
  markiereRahmen();

  clearTranscript();
  ui.transcript.querySelector('.empty')?.remove();
  firstSegment = false;
  for (const eintrag of stand.transcript) {
    if (eintrag.gap) {
      const p = document.createElement('p');
      p.className = 'seg gap';
      p.textContent = eintrag.text.replace(/^\[|\]$/g, '');
      ui.transcript.insertBefore(p, ui.partial);
      lastTranscriptText += (lastTranscriptText ? '\n\n' : '') + eintrag.text;
      transkriptEintraege.push({ text: eintrag.text, seconds: eintrag.seconds, gap: true });
    } else {
      handleEvent({ type: 'transcript', entry: { text: eintrag.text, seconds: eintrag.seconds } });
    }
  }
  gesammelteHinweise = stand.feedback ?? [];
  renderNotes(gesammelteHinweise);
  zeigeFeedback(false);
  sprecher = stand.speakers ?? {};
  if (stand.transcript.some((e) => e.speaker)) {
    transkriptEintraege = stand.transcript.map((e) => ({ ...e }));
    zeichneTranskript();
  }

  if (stand.summary?.markdown) {
    renderProtocol(stand.summary.markdown);
    zusammenfassungVon = stand.summary.model ?? null;
    letzteOptionen = stand.summary.options ?? null;
    ui.protocolTitle.textContent = 'Zusammenfassung';
    zeigeEditor();
  } else {
    ui.protocol.innerHTML = '<p class="empty">Noch nicht zusammengefasst.</p>';
  }
  if (audio) {
    const typ = /\.wav$/i.test(audio.name) ? 'audio/wav'
      : /\.m4a$/i.test(audio.name) ? 'audio/mp4' : 'audio/mpeg';
    projektAudio = new File([audio.daten], audio.name, { type: typ });
    mitschnittDateien = [projektAudio];
    ui.downloadAudio.hidden = false;
  }
  ui.summaryOpen.hidden = !transkriptEintraege.some((e) => !e.gap);
  ui.retranscribe.hidden = !projektAudio;
  ui.speakersOpen.hidden = !projektAudio || !sprecherDienst();
  ui.transcriptEdit.hidden = !transkriptEintraege.length;
  zeigeTelefonHinweis(false);
  ungesichert = false;
  setStatus(`Geöffnet: ${woher}`);
}

/** Den Mitschnitt oder die Projektdatei noch einmal durch die Erkennung
 *  schicken - wenn mittendrin etwas abbrach oder ein anderer Dienst besser
 *  passt. Das bisherige Transkript wird ersetzt. */
async function transkribiereErneut() {
  if (!projektAudio || recording) return;
  const dienst = dateiFaehigerDienst();
  if (!dienst || dienst.local) {
    setStatus('Dafür braucht es einen Cloud-Dienst, der ganze Dateien annimmt.', true);
    return;
  }
  if (!confirm(`Das Transkript mit ${dienst.name} neu erstellen? `
               + 'Das bisherige Transkript wird ersetzt.')) return;
  // Die Hinweise aus dem Termin gelten weiter, das neue Transkript ersetzt
  // nur das alte.
  const hinweise = gesammelteHinweise;
  await transkribiereAmStueck(projektAudio, dienst);
  gesammelteHinweise = hinweise;
  renderNotes(hinweise);
}

window.addEventListener('beforeunload', (ereignis) => {
  // Gehostet hält der Server nichts: Ein Neuladen verliert Aufnahme und
  // Ergebnis endgültig. Solange etwas ungesichert ist, fragt der Browser.
  if (recording || ungesichert) ereignis.preventDefault();
});

// -- Protokoll-Editor mit Anmerkungen ---------------------------------------

let anmerkungen = [], aenderungen = [], markierung = '';

/** Bearbeiten und Anmerken gibt es, sobald ein fertiges Protokoll dasteht. */
function zeigeEditor() {
  const da = Boolean(ui.protocol.dataset.markdown) && !recording;
  ui.protocolEdit.hidden = !da;
  zeichneAnmerkungen();
}

function bearbeite(an) {
  ui.protocol.hidden = an;
  ui.protocolEditing.hidden = !an;
  ui.annotations.hidden = an || (!anmerkungen.length && !aenderungen.length);
  if (an) {
    ui.protocolEditor.value = ui.protocol.dataset.markdown || '';
    ui.protocolEditor.focus();
  }
}

function zeichneAnmerkungen() {
  ui.annotationList.innerHTML = '';
  anmerkungen.forEach((a, i) => {
    const chip = document.createElement('div');
    chip.className = 'annotation-chip';
    const text = document.createElement('span');
    if (a.quote) {
      const zitat = document.createElement('span');
      zitat.className = 'zitat';
      zitat.textContent = `„${a.quote.length > 60 ? a.quote.slice(0, 57) + '…' : a.quote}“ – `;
      text.append(zitat);
    }
    text.append(document.createTextNode(a.comment));
    const weg = document.createElement('button');
    weg.type = 'button';
    weg.className = 'icon-button small';
    weg.title = 'Anmerkung entfernen';
    weg.setAttribute('aria-label', 'Anmerkung entfernen');
    weg.innerHTML = '<svg class="icon"><use href="#i-x"/></svg>';
    weg.addEventListener('click', () => { anmerkungen.splice(i, 1); zeichneAnmerkungen(); });
    const platz = document.createElement('span');
    platz.className = 'spacer';
    chip.append(text, platz, weg);
    ui.annotationList.append(chip);
  });

  ui.changeList.innerHTML = '';
  aenderungen.forEach((c, i) => {
    const chip = document.createElement('div');
    chip.className = 'annotation-chip change-chip';
    const text = document.createElement('span');
    if (!c.gefunden) {
      text.textContent = `Nicht gefunden, nicht geändert: „${c.alt.slice(0, 60)}“`;
    } else {
      const alt = document.createElement('del');
      alt.textContent = c.alt.slice(0, 80);
      const neu = document.createElement('ins');
      neu.textContent = c.neu.slice(0, 80);
      text.append(alt, document.createTextNode(' → '), neu);
      if (c.alle) text.append(document.createTextNode(` (${c.anzahl}×)`));
    }
    const platz = document.createElement('span');
    platz.className = 'spacer';
    chip.append(text, platz);
    if (c.gefunden) {
      const zurueck = document.createElement('button');
      zurueck.type = 'button';
      zurueck.className = 'ghost small-button';
      zurueck.textContent = 'zurücknehmen';
      zurueck.addEventListener('click', () => nimmZurueck(i));
      chip.append(zurueck);
    }
    ui.changeList.append(chip);
  });

  const n = anmerkungen.length;
  el('annotations-apply').textContent = n ? `Anmerkungen einarbeiten (${n})` : 'Anmerkungen einarbeiten';
  el('annotations-apply').disabled = !n;
  // Ohne Anmerkung gibt es nichts einzuarbeiten - der Knopf kostete am
  // Telefon nur eine Zeile.
  el('annotations-apply').hidden = !n;
  ui.annotations.hidden = !ui.protocol.dataset.markdown || recording
    || !ui.protocolEditing.hidden;
}

function nimmZurueck(i) {
  const c = aenderungen[i];
  const md = ui.protocol.dataset.markdown || '';
  const stelle = md.indexOf(c.neu);
  if (stelle < 0) {
    setStatus('Die Stelle hat sich inzwischen geändert, zurücknehmen geht nicht mehr.', true);
    return;
  }
  renderProtocol(c.alle ? md.replaceAll(c.neu, c.alt)
    : md.slice(0, stelle) + c.alt + md.slice(stelle + c.neu.length));
  aenderungen.splice(i, 1);
  ungesichert = true;
  zeichneAnmerkungen();
}

function neueAnmerkung(zitat) {
  markierung = zitat;
  ui.annotationQuote.textContent = zitat ? `Zur Stelle: „${zitat}“` : 'Allgemein, ohne bestimmte Stelle.';
  ui.annotationText.value = '';
  ui.annotationDialog.showModal();
  ui.annotationText.focus();
}

/** Alle gesammelten Anmerkungen in einem kleinen Lauf. Das Modell liefert
 *  nur Ersetzungen; eingesetzt werden sie hier, der Rest bleibt Zeichen für
 *  Zeichen, wie er war. */
async function arbeiteAnmerkungenEin() {
  if (!anmerkungen.length) return;
  const knopf = el('annotations-apply');
  knopf.disabled = true;
  ui.cycle.hidden = false;
  setStatus('Anmerkungen werden eingearbeitet …');
  try {
    const antwort = await fetch('/api/revise', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ markdown: ui.protocol.dataset.markdown,
                             annotations: anmerkungen, model: zusammenfassungVon?.id }),
    });
    const daten = await antwort.json().catch(() => ({}));
    if (!antwort.ok) throw new Error(daten.detail || `HTTP ${antwort.status}`);
    let md = ui.protocol.dataset.markdown;
    const neu = [];
    for (const { alt, neu: ersatz, alle } of daten.replacements) {
      const stelle = md.indexOf(alt);
      if (stelle < 0) { neu.push({ alt, neu: ersatz, gefunden: false }); continue; }
      const anzahl = alle ? md.split(alt).length - 1 : 1;
      md = alle ? md.replaceAll(alt, ersatz)
        : md.slice(0, stelle) + ersatz + md.slice(stelle + alt.length);
      neu.push({ alt, neu: ersatz, gefunden: true, alle: anzahl > 1, anzahl });
    }
    renderProtocol(md);
    aenderungen = neu;
    anmerkungen = [];
    ungesichert = true;
    const ok = neu.filter((c) => c.gefunden).length;
    setStatus(`${ok} Änderung${ok === 1 ? '' : 'en'} eingearbeitet`
              + (neu.length > ok ? `, ${neu.length - ok} Stelle(n) nicht gefunden` : '')
              + ` · ${daten.seconds} s`);
  } catch (error) {
    setStatus(`Einarbeiten fehlgeschlagen: ${error.message}`, true);
  } finally {
    ui.cycle.hidden = true;
    zeichneAnmerkungen();
  }
}

ui.protocolEdit.addEventListener('click', () => bearbeite(true));
el('protocol-edit-cancel').addEventListener('click', () => { bearbeite(false); zeichneAnmerkungen(); });
el('protocol-edit-save').addEventListener('click', () => {
  renderProtocol(ui.protocolEditor.value);
  aenderungen = [];
  ungesichert = true;
  bearbeite(false);
  zeichneAnmerkungen();
  setStatus('Protokoll geändert');
});
el('annotation-general').addEventListener('click', () => neueAnmerkung(''));
el('annotations-apply').addEventListener('click', arbeiteAnmerkungenEin);
ui.annotationDialog.addEventListener('close', () => {
  const text = ui.annotationText.value.trim();
  if (ui.annotationDialog.returnValue !== 'save' || !text) return;
  anmerkungen.push({ quote: markierung, comment: text });
  zeichneAnmerkungen();
});
// Markieren im fertigen Protokoll bietet eine Anmerkung an.
document.addEventListener('selectionchange', () => {
  const auswahl = document.getSelection();
  const text = auswahl?.toString().trim() ?? '';
  const drin = auswahl?.rangeCount && ui.protocol.contains(auswahl.getRangeAt(0).commonAncestorContainer);
  if (!text || !drin || recording || !ui.protocol.dataset.markdown || text.length > 500) {
    ui.annotate.hidden = true;
    return;
  }
  const rahmen = auswahl.getRangeAt(0).getBoundingClientRect();
  ui.annotate.style.left = `${Math.max(8, Math.min(window.innerWidth - 130, rahmen.left))}px`;
  ui.annotate.style.top = `${Math.max(8, rahmen.bottom + 6)}px`;
  ui.annotate.hidden = false;
});
// Mit mousedown/touchstart statt click: Ein Klick hebt die Markierung sonst
// schon auf, bevor er ankommt.
for (const art of ['mousedown', 'touchstart']) {
  ui.annotate.addEventListener(art, (ereignis) => {
    ereignis.preventDefault();
    const zitat = document.getSelection()?.toString().trim() ?? '';
    ui.annotate.hidden = true;
    neueAnmerkung(zitat);
  }, { passive: false });
}

// -- Export --------------------------------------------------------------

async function exportiere(was, format = 'md') {
  ui.exportMenu.hidden = true;
  if (was === 'projekt') { speichereProjekt(); return; }
  if (was === 'tresor') { sichereImTresor(); return; }
  const modus = config.output_types.find((t) => t.id === ui.outputType.value);
  const datum = aufnahmeBeginn ?? new Date();
  const tag = datum.toISOString().slice(0, 10);
  const wann = datum.toLocaleString('de-DE', { dateStyle: 'medium', timeStyle: 'short' });
  const rahmen = [['Thema', terminRahmen.topic], ['Beteiligte', terminRahmen.participants],
                  ['Ziel', terminRahmen.goal], ['Agenda', terminRahmen.agenda]]
    .filter(([, wert]) => wert && String(wert).trim())
    .map(([name, wert]) => `- **${name}:** ${String(wert).trim().replaceAll('\n', ' · ')}`);

  let titel, inhalt, name;
  if (was === 'transkript') {
    titel = 'Transkript';
    inhalt = transkriptEintraege.map((e) => `**${formatSeconds(e.seconds)}** `
      + (e.speaker && !e.gap ? `${sprecherLabel(e.speaker)}: ` : '') + e.text).join('\n\n');
    name = `${tag}-transkript.md`;
  } else {
    titel = terminRahmen.topic?.trim() || modus?.name || 'Protokoll';
    inhalt = ui.protocol.dataset.markdown || '_Noch kein Protokoll._';
    // Bringt die Zusammenfassung ihre eigene Überschrift mit, ist das der
    // Titel - sonst stünden zwei übereinander.
    const eigene = inhalt.match(/^#\s+(.+)\n+/);
    if (eigene) {
      titel = eigene[1].trim();
      inhalt = inhalt.slice(eigene[0].length);
    }
    name = `${tag}-${(modus?.short_name || 'protokoll').toLowerCase()}.md`;
  }
  const herkunft = `Aufgenommen am ${wann}`
    + (zusammenfassungVon && was !== 'transkript'
      ? ` · zusammengefasst mit ${zusammenfassungVon.name}` : '');

  let blob;
  if (format === 'docx') {
    // Word baut der Server - python-docx steckt auch im Desktop-Bündel.
    name = name.replace(/\.md$/, '.docx');
    try {
      const antwort = await fetch('/api/export/docx', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ markdown: inhalt, title: titel, filename: name,
                               meta: [herkunft, ...rahmen.map((z) => z.replace(/^- /, ''))] }),
      });
      if (!antwort.ok) throw new Error((await antwort.json().catch(() => ({}))).detail
                                       || `HTTP ${antwort.status}`);
      blob = await antwort.blob();
    } catch (error) {
      setStatus(`Word-Export fehlgeschlagen: ${error.message}`, true);
      return;
    }
  } else {
    const kopf = [`# ${titel}`, '', herkunft];
    if (rahmen.length) kopf.push('', ...rahmen);
    blob = new Blob([[...kopf, '', '---', '', inhalt, ''].join('\n')],
                    { type: 'text/markdown;charset=utf-8' });
  }
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}


// -- Transkript bearbeiten -------------------------------------------------------
//
// Vor allem für Sprecher, die die Erkennung zusammengeworfen hat: Zeilen
// teilen, jede Hälfte der richtigen Person geben. Text korrigieren geht dabei
// mit - das Audio bleibt, wie es war.

let transkriptBearbeiten = false;

function endeVon(index) {
  const e = transkriptEintraege[index];
  const naechste = transkriptEintraege.slice(index + 1).find((x) => !x.gap);
  return naechste ? naechste.seconds : e.seconds + e.text.length / 15;
}

function zeichneBearbeitung() {
  transkriptEintraege.forEach((eintrag, index) => {
    const zeile = document.createElement('div');
    zeile.className = eintrag.gap ? 'seg gap' : 'seg edit-row';
    zeile.dataset.index = index;
    if (eintrag.gap) {
      zeile.textContent = eintrag.text.replace(/^\[|\]$/g, '');
      ui.transcript.insertBefore(zeile, ui.partial);
      return;
    }
    const kopf = document.createElement('div');
    kopf.className = 'edit-head';
    if (projektAudio) {
      const hoeren = document.createElement('button');
      hoeren.type = 'button';
      hoeren.className = 'icon-button small';
      hoeren.title = 'Anhören';
      hoeren.setAttribute('aria-label', 'Anhören');
      hoeren.innerHTML = '<svg class="icon"><use href="#i-play"/></svg>';
      hoeren.addEventListener('click', () => spieleAb(eintrag.seconds, endeVon(index)));
      kopf.append(hoeren);
    }
    const zeit = document.createElement('time');
    zeit.textContent = formatSeconds(eintrag.seconds);
    const auswahl = sprecherAuswahl(eintrag.speaker);
    auswahl.addEventListener('change', () => {
      const id = neuerSprecherFallsGewuenscht(auswahl.value);
      if (id !== null) eintrag.speaker = id || undefined;
      ungesichert = true;
      zeichneTranskript();
      filtereTranskript();
    });
    kopf.append(zeit, auswahl);
    const text = document.createElement('textarea');
    text.value = eintrag.text;
    text.rows = 1;
    text.spellcheck = true;
    text.addEventListener('input', () => {
      eintrag.text = text.value;
      ungesichert = true;
      passeHoeheAn(text);
    });
    text.addEventListener('keydown', (ereignis) => bearbeitungsTaste(ereignis, index, text));
    zeile.append(kopf, text);
    ui.transcript.insertBefore(zeile, ui.partial);
    passeHoeheAn(text);
    lastTranscriptText += (lastTranscriptText ? '\n\n' : '') + eintrag.text;
  });
}

function passeHoeheAn(feld) {
  feld.style.height = 'auto';
  feld.style.height = `${feld.scrollHeight}px`;
}

function fokussiere(index, position) {
  const feld = ui.transcript.querySelector(`.edit-row[data-index="${index}"] textarea`);
  if (!feld) return;
  feld.focus();
  feld.setSelectionRange(position, position);
}

function bearbeitungsTaste(ereignis, index, feld) {
  const eintrag = transkriptEintraege[index];
  if (ereignis.key === 'Enter' && !ereignis.shiftKey) {
    // Teilen an der Schreibmarke. Die Zeitmarke der zweiten Hälfte ist
    // geschätzt - nach dem Anteil des Texts bis zur nächsten Zeile.
    ereignis.preventDefault();
    const pos = feld.selectionStart;
    const vorn = feld.value.slice(0, pos).trimEnd();
    const hinten = feld.value.slice(pos).trimStart();
    if (!vorn || !hinten) return;
    const anteil = pos / Math.max(1, feld.value.length);
    const zeitpunkt = eintrag.seconds + (endeVon(index) - eintrag.seconds) * anteil;
    eintrag.text = vorn;
    transkriptEintraege.splice(index + 1, 0, {
      text: hinten, seconds: Math.round(zeitpunkt * 10) / 10,
      ...(eintrag.speaker ? { speaker: eintrag.speaker } : {}),
    });
    ungesichert = true;
    zeichneTranskript();
    filtereTranskript();
    fokussiere(index + 1, 0);
  } else if (ereignis.key === 'Backspace' && feld.selectionStart === 0 && feld.selectionEnd === 0) {
    // An die vorige Zeile anhängen - nicht über einen Lückenvermerk hinweg.
    const vorige = index - 1;
    if (vorige < 0 || transkriptEintraege[vorige].gap) return;
    ereignis.preventDefault();
    const davor = transkriptEintraege[vorige];
    const naht = davor.text.length + 1;
    davor.text = `${davor.text} ${eintrag.text}`.trim();
    transkriptEintraege.splice(index, 1);
    ungesichert = true;
    zeichneTranskript();
    filtereTranskript();
    fokussiere(vorige, naht);
  }
}

ui.transcriptEdit.addEventListener('click', () => {
  transkriptBearbeiten = true;
  zeichneTranskript();
  filtereTranskript();
});
ui.transcriptEditDone = el('transcript-edit-done');
ui.transcriptEditDone.addEventListener('click', () => {
  transkriptBearbeiten = false;
  // Sprecher, denen keine Zeile mehr gehört, fallen aus Filter und Dialog.
  const benutzt = new Set(transkriptEintraege.map((e) => e.speaker).filter(Boolean));
  for (const id of Object.keys(sprecher)) if (!benutzt.has(id)) delete sprecher[id];
  zeichneTranskript();
  filtereTranskript();
  merkeVor();
});

// -- Zurück zur Zusammenfassung ----------------------------------------------------

function zeigeZurueckZurZusammenfassung(sichtbar) {
  ui.backToSummary.hidden = !(sichtbar && !recording && config?.summary_models?.length
                              && transkriptEintraege.some((e) => !e.gap));
  document.body.classList.toggle('zurueck-sichtbar', !ui.backToSummary.hidden);
}
ui.backToSummary.addEventListener('click', () => {
  zeigeZurueckZurZusammenfassung(false);
  oeffneZusammenfassung();
});

// -- Meine Sitzungen (Tresor) -----------------------------------------------------

let tresorId = null, tresorAudioId = null, tresorAudioVon = null;
let tresorKette = Promise.resolve();
let merkeVorTimer = null;
let tresorEingerichtet = null;   // null: unbekannt

async function ladeTresorAusGeraet() {
  try {
    const stand = await tresorStand();
    tresorEingerichtet = Boolean(stand.key);
    await tresorAusGeraet(config.user, stand);
  } catch { /* dann eben gesperrt */ }
}

/** Nach jedem Arbeitsschritt still sichern - wenn der Tresor offen ist. */
function merkeVor() {
  if (!config?.vault || !tresorOffen()) return;
  clearTimeout(merkeVorTimer);
  merkeVorTimer = setTimeout(() => sichereImTresor({ leise: true }), 1500);
}

function sichereImTresor({ leise = false } = {}) {
  tresorKette = tresorKette.then(() => sichereJetzt(leise));
  return tresorKette;
}

async function sichereJetzt(leise) {
  if (!tresorOffen()) {
    if (!leise) {
      oeffneTresor();
      setStatus('Zum Speichern „Meine Sitzungen“ entsperren oder einrichten.', true);
    }
    return;
  }
  if (!transkriptEintraege.some((e) => !e.gap)) {
    if (!leise) setStatus('Noch nichts zum Speichern.', true);
    return;
  }
  const neu = !tresorId;
  try {
    tresorId ??= neueTresorKennung();
    // Das Audio liegt als eigener Eintrag: Es geht einmal hoch, jede weitere
    // Sicherung schickt nur den kleinen Arbeitsstand.
    if (projektAudio && tresorAudioVon !== projektAudio) {
      setStatus('Aufnahme wird verschlüsselt gespeichert …');
      tresorAudioId ??= neueTresorKennung();
      await tresorSpeichern(tresorAudioId, new Uint8Array(await projektAudio.arrayBuffer()),
                            { kind: 'audio', of: tresorId });
      tresorAudioVon = projektAudio;
    }
    const bytes = baueProjekt(projektStand(), null);
    await tresorSpeichern(tresorId, bytes, {
      kind: 'projekt', title: projektTitel(), created: projektStand().created,
      audio: projektAudio ? tresorAudioId : null,
      audio_name: projektAudio ? audioName(projektAudio) : null,
      audio_type: projektAudio?.type || null,
      summary: Boolean(zusammenfassungVon),
    });
    ungesichert = false;
    setStatus(neu ? 'In „Meine Sitzungen“ gespeichert – verschlüsselt'
                  : 'In „Meine Sitzungen“ gesichert');
  } catch (error) {
    if (neu) tresorId = null;
    setStatus(`Speichern in „Meine Sitzungen“ fehlgeschlagen: ${error.message}`, true);
  }
}

async function oeffneAusTresor(eintrag) {
  if (recording) { setStatus('Erst die laufende Aufnahme beenden.', true); return; }
  if (ungesichert && !confirm('Der aktuelle Stand ist nicht gespeichert und geht verloren. '
                              + 'Trotzdem öffnen?')) return;
  ui.vaultDialog.close();
  setStatus(`„${eintrag.title}“ wird geladen und entschlüsselt …`);
  try {
    const projekt = await leseProjekt(await tresorLaden(eintrag.id));
    if (eintrag.audio) {
      projekt.audio = { name: eintrag.audio_name || 'aufnahme.mp3',
                        daten: await tresorLaden(eintrag.audio) };
    }
    zeigeProjekt(projekt, `„${eintrag.title}“`);
    tresorId = eintrag.id;
    tresorAudioId = eintrag.audio || null;
    tresorAudioVon = projektAudio;
  } catch (error) {
    setStatus(`Öffnen fehlgeschlagen: ${error.message}`, true);
  }
}

function knopf(text, klasse, beiKlick) {
  const b = document.createElement('button');
  b.type = 'button';
  b.className = klasse;
  b.textContent = text;
  b.addEventListener('click', beiKlick);
  return b;
}

function passwortFeld(platzhalter) {
  const f = document.createElement('input');
  f.type = 'password';
  f.autocomplete = 'new-password';
  f.placeholder = platzhalter;
  return f;
}

function megabyte(bytes) {
  return `${(bytes / 1024 / 1024).toFixed(bytes < 10 * 1024 * 1024 ? 1 : 0)} MB`;
}

async function zeichneTresor() {
  const ziel = ui.vaultBody;
  ziel.innerHTML = '<p class="note">wird geladen …</p>';
  let stand;
  try {
    stand = tresorOffen() ? await tresorListe() : await tresorStand();
  } catch (error) {
    ziel.innerHTML = '';
    ziel.append(Object.assign(document.createElement('p'),
                              { className: 'note warn', textContent: error.message }));
    return;
  }
  ziel.innerHTML = '';
  const hinweis = (text, klasse = 'note') =>
    ziel.append(Object.assign(document.createElement('p'), { className: klasse, textContent: text }));
  const merken = document.createElement('label');
  merken.className = 'checks-row';
  const merkenBox = Object.assign(document.createElement('input'), { type: 'checkbox', checked: true });
  merken.append(merkenBox, ' Auf diesem Gerät merken');
  const fehler = (error) => hinweis(error.message, 'note warn');

  if (!stand.key) {
    hinweis('Speichert Aufnahme und Arbeitsstand auf dem Server – verschlüsselt mit einem '
            + 'eigenen Passwort. Nur du kannst es öffnen, auch der Betreiber nicht. '
            + 'So nimmst du am Telefon auf und machst am Rechner weiter.');
    hinweis('Vergisst du das Passwort, sind die Sitzungen verloren.', 'note warn');
    const eins = passwortFeld('Tresor-Passwort (mind. 8 Zeichen)');
    const zwei = passwortFeld('wiederholen');
    ziel.append(eins, zwei, merken, knopf('Einrichten', 'primary', async () => {
      if (eins.value.length < 8) return fehler(new Error('Mindestens 8 Zeichen.'));
      if (eins.value !== zwei.value) return fehler(new Error('Die Passwörter weichen ab.'));
      try {
        await tresorEinrichten(config.user, eins.value, merkenBox.checked);
        await zeichneTresor();
      } catch (error) { fehler(error); }
    }));
    return;
  }

  if (!tresorOffen()) {
    const pw = passwortFeld('Tresor-Passwort');
    pw.autocomplete = 'current-password';
    const entsperren = async () => {
      try {
        await tresorEntsperren(config.user, pw.value, merkenBox.checked);
        await zeichneTresor();
      } catch (error) { fehler(error); }
    };
    pw.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); entsperren(); } });
    ziel.append(pw, merken, knopf('Entsperren', 'primary', entsperren));
    ziel.append(knopf('Passwort vergessen – alles löschen …', 'linklike', async () => {
      if (!confirm('Alle gespeicherten Sitzungen endgültig löschen? Ohne das Passwort '
                   + 'lassen sie sich nicht mehr öffnen.')) return;
      try { await tresorLeeren(config.user); await zeichneTresor(); } catch (error) { fehler(error); }
    }));
    return;
  }

  const projekte = stand.items.filter((i) => i.kind !== 'audio');
  const groesse = Object.fromEntries(stand.items.map((i) => [i.id, i.size]));
  if (!projekte.length) hinweis('Noch keine Sitzung gespeichert. Nach dem Beenden oder '
                                + 'Zusammenfassen wird automatisch gesichert.');
  const liste = document.createElement('div');
  liste.className = 'vault-list';
  for (const eintrag of projekte) {
    const zeile = document.createElement('div');
    zeile.className = 'vault-item' + (eintrag.id === tresorId ? ' current' : '');
    const text = document.createElement('button');
    text.type = 'button';
    text.className = 'vault-open';
    const titel = document.createElement('strong');
    titel.textContent = eintrag.title || 'ohne Titel';
    const angaben = document.createElement('span');
    angaben.className = 'note';
    const wann = eintrag.created ? new Date(eintrag.created)
      .toLocaleString('de-DE', { dateStyle: 'medium', timeStyle: 'short' }) : '';
    angaben.textContent = [wann, megabyte((eintrag.size || 0) + (groesse[eintrag.audio] || 0)),
                           eintrag.summary ? 'zusammengefasst' : ''].filter(Boolean).join(' · ');
    text.append(titel, angaben);
    text.addEventListener('click', () => oeffneAusTresor(eintrag));
    const weg = document.createElement('button');
    weg.type = 'button';
    weg.className = 'icon-button small';
    weg.title = 'Löschen';
    weg.setAttribute('aria-label', 'Löschen');
    weg.innerHTML = '<svg class="icon"><use href="#i-x"/></svg>';
    weg.addEventListener('click', async () => {
      if (!confirm(`„${eintrag.title}“ endgültig löschen?`)) return;
      try {
        await tresorLoeschen(eintrag.id);
        if (eintrag.audio) await tresorLoeschen(eintrag.audio).catch(() => {});
        if (eintrag.id === tresorId) { tresorId = null; tresorAudioId = null; tresorAudioVon = null; }
        await zeichneTresor();
      } catch (error) { fehler(error); }
    });
    zeile.append(text, weg);
    liste.append(zeile);
  }
  ziel.append(liste);
  hinweis(`${megabyte(stand.used)} von ${megabyte(stand.quota)} belegt.`);
  const leiste = document.createElement('div');
  leiste.className = 'feedback-buttons';
  if (transkriptEintraege.some((e) => !e.gap)) {
    leiste.append(knopf(tresorId ? 'Aktuellen Stand sichern' : 'Aktuelle Sitzung speichern',
                        'primary small-button', async () => {
      await sichereImTresor(); await zeichneTresor();
    }));
  }
  const platz = document.createElement('span');
  platz.className = 'spacer';
  leiste.append(platz);
  leiste.append(knopf('Passwort ändern', 'ghost small-button', () => {
    const alt = passwortFeld('bisheriges Passwort');
    const neu = passwortFeld('neues Passwort (mind. 8 Zeichen)');
    const nochmal = passwortFeld('neues wiederholen');
    ziel.append(alt, neu, nochmal, knopf('Ändern', 'primary', async () => {
      if (neu.value.length < 8 || neu.value !== nochmal.value) {
        return fehler(new Error('Das neue Passwort ist zu kurz oder weicht ab.'));
      }
      try {
        await tresorPasswortAendern(config.user, alt.value, neu.value);
        await zeichneTresor();
        setStatus('Tresor-Passwort geändert');
      } catch (error) { fehler(error); }
    }));
    alt.focus();
  }));
  leiste.append(knopf('Sperren', 'ghost small-button', async () => {
    await tresorSperren(config.user); await zeichneTresor();
  }));
  ziel.append(leiste);
}

/** Nur wer eine Obergrenze hat, sieht seinen Stand. */
function zeigeKontingent(stand) {
  const feld = el('quota');
  feld.hidden = !stand;
  if (!stand) return;
  feld.textContent = `Kontingent $${stand.used.toFixed(2)} von $${stand.limit.toFixed(2)}`;
  feld.classList.toggle('warn', stand.used >= stand.limit * 0.8);
}

// -- Verbrauch (nur Gruppe admins) --------------------------------------------------

async function zeigeVerbrauch(monat = '') {
  ui.usageBody.innerHTML = '<p class="note">wird geladen …</p>';
  let daten;
  try {
    const antwort = await fetch(`/api/usage?month=${encodeURIComponent(monat)}`);
    daten = await antwort.json();
    if (!antwort.ok) throw new Error(daten.detail || `HTTP ${antwort.status}`);
  } catch (error) {
    ui.usageBody.innerHTML = '';
    ui.usageBody.append(Object.assign(document.createElement('p'),
                                      { className: 'note warn', textContent: error.message }));
    return;
  }
  ui.usageMonth.innerHTML = '';
  for (const m of daten.months.length ? daten.months : [daten.month]) {
    ui.usageMonth.append(new Option(m, m));
  }
  ui.usageMonth.value = daten.month;
  const dollar = (x) => `$${x.toFixed(x < 1 ? 3 : 2)}`;
  const minuten = (s) => `${Math.round(s / 60)} min`;
  const tokens = (u) => `${Math.round((u.prompt_tokens + u.completion_tokens) / 1000)}k Tokens`;
  ui.usageBody.innerHTML = '';
  if (!daten.users.length) {
    ui.usageBody.append(Object.assign(document.createElement('p'),
                                      { className: 'note', textContent: 'Kein Verbrauch in diesem Monat.' }));
    return;
  }
  const summe = document.createElement('p');
  summe.innerHTML = `<strong>Gesamt: ${dollar(daten.total)}</strong>`;
  ui.usageBody.append(summe);
  for (const u of daten.users) {
    const block = document.createElement('details');
    block.className = 'usage-user';
    const kopf = document.createElement('summary');
    kopf.textContent = `${u.user} – ${dollar(u.cost)} · ${minuten(u.seconds)} Audio · ${tokens(u)}`;
    block.append(kopf);
    const tabelle = document.createElement('table');
    tabelle.innerHTML = '<thead><tr><th>Wofür</th><th>Aufrufe</th><th>Menge</th><th>Kosten</th></tr></thead>';
    const rumpf = document.createElement('tbody');
    for (const r of u.resources) {
      const tr = document.createElement('tr');
      for (const wert of [r.name, r.calls, r.seconds ? minuten(r.seconds) : tokens(r), dollar(r.cost)]) {
        const td = document.createElement('td');
        td.textContent = wert;
        tr.append(td);
      }
      rumpf.append(tr);
    }
    tabelle.append(rumpf);
    const huelle = document.createElement('div');
    huelle.className = 'table-wrap';
    huelle.append(tabelle);
    block.append(huelle);
    ui.usageBody.append(block);
  }
}

el('me-delete').addEventListener('click', async () => {
  if (!confirm('Einstellungen, Glossare und alle gespeicherten Sitzungen endgültig löschen?')) return;
  try {
    const antwort = await fetch('/api/me', { method: 'DELETE' });
    if (!antwort.ok) throw new Error(`HTTP ${antwort.status}`);
    if (config.vault) await tresorSperren(config.user).catch(() => {});
    try { localStorage.removeItem(SETTINGS_KEY); } catch { /* egal */ }
    ungesichert = false;
    location.reload();
  } catch (error) {
    setStatus(`Löschen fehlgeschlagen: ${error.message}`, true);
  }
});

ui.usageOpen.addEventListener('click', () => { ui.usageDialog.showModal(); zeigeVerbrauch(); });

// -- Neuigkeiten ---------------------------------------------------------------------

// Nach jeder neuen Version einmal zeigen, was sich geändert hat. Die Texte
// stehen in static/neuigkeiten.json, je Version ein Abschnitt; welche Version
// jemand zuletzt gesehen hat, liegt in seinen Einstellungen (news_seen) und
// gilt damit auf allen Geräten.

function versionsTeile(v) {
  return String(v || '0').replace(/^v/, '').split('.').map((t) => parseInt(t, 10) || 0);
}

function neuerAls(a, b) {
  const x = versionsTeile(a), y = versionsTeile(b);
  for (let i = 0; i < Math.max(x.length, y.length); i++) {
    if ((x[i] || 0) !== (y[i] || 0)) return (x[i] || 0) > (y[i] || 0);
  }
  return false;
}

/** Punkte, die für diesen Nutzer gelten: "hosted"/"desktop" nach Betriebsart,
 *  "admin" nur für Admins. */
function giltFuerMich(punkt) {
  if (!punkt.only) return true;
  if (punkt.only === 'admin') return Boolean(config.admin);
  return punkt.only === config.mode;
}

async function ladeNeuigkeiten() {
  const antwort = await fetch('/static/neuigkeiten.json', { cache: 'no-cache' });
  if (!antwort.ok) throw new Error(`HTTP ${antwort.status}`);
  return (await antwort.json())
    .map((a) => ({ ...a, items: a.items.filter(giltFuerMich) }))
    .filter((a) => a.items.length);
}

function zeichneNeuigkeiten(abschnitte) {
  ui.newsBody.innerHTML = '';
  for (const abschnitt of abschnitte) {
    const kopf = document.createElement('h4');
    kopf.className = 'news-version';
    kopf.textContent = `Version ${abschnitt.version}`;
    if (abschnitt.date) {
      const datum = document.createElement('span');
      datum.className = 'note';
      datum.textContent = new Date(abschnitt.date)
        .toLocaleDateString('de-DE', { dateStyle: 'medium' });
      kopf.append(' ', datum);
    }
    const liste = document.createElement('ul');
    liste.className = 'news-list';
    for (const punkt of abschnitt.items) {
      const li = document.createElement('li');
      li.append(Object.assign(document.createElement('strong'), { textContent: punkt.title }),
                Object.assign(document.createElement('p'), { textContent: punkt.text }));
      liste.append(li);
    }
    ui.newsBody.append(kopf, liste);
  }
}

/** Beim Start: nur, was seit der zuletzt gesehenen Version dazugekommen ist. */
async function zeigeNeuigkeiten() {
  let abschnitte;
  try { abschnitte = await ladeNeuigkeiten(); } catch { return; }
  const neu = abschnitte.filter((a) => neuerAls(a.version, settings.news_seen));
  if (!neu.length) return;
  zeichneNeuigkeiten(neu);
  ui.newsDialog.showModal();
}

ui.newsDialog.addEventListener('close', async () => {
  let abschnitte;
  try { abschnitte = await ladeNeuigkeiten(); } catch { return; }
  const neueste = abschnitte[0]?.version;
  if (neueste && neuerAls(neueste, settings.news_seen)) {
    settings = { ...settings, news_seen: neueste };
    storeSettings();
  }
});

ui.newsOpen.addEventListener('click', async () => {
  try { zeichneNeuigkeiten(await ladeNeuigkeiten()); } catch (error) {
    ui.newsBody.textContent = `Nicht geladen: ${error.message}`;
  }
  ui.newsDialog.showModal();
});
ui.usageMonth.addEventListener('change', () => zeigeVerbrauch(ui.usageMonth.value));


// -- Glossare und Korrekturen ----------------------------------------
//
// Ein Glossar ist eine Liste von Schreibweisen, die die Erkennung sonst
// verfremdet. Mehrere lassen sich anlegen und ankreuzen; die angekreuzten
// gehen zusammen an die Erkennung (Whisper als Prompt, Voxtral als
// context_bias) und an die Zusammenfassung. Sie liegen in den Einstellungen
// des Nutzers auf dem Server, also auf jedem Gerät gleich.

let glossarEntwurf = [], aktivEntwurf = new Set();

function aktiveBegriffe() {
  const aktiv = new Set(settings.active_glossaries ?? []);
  const begriffe = (settings.glossaries ?? []).filter((g) => aktiv.has(g.id)).flatMap((g) => g.terms);
  return [...new Set(begriffe.map((b) => b.trim()).filter(Boolean))].join(', ');
}

function neueGlossarKennung(liste) {
  let n = 1;
  while (liste.some((g) => g.id === `g${n}`)) n += 1;
  return `g${n}`;
}

function zeichneGlossare() {
  ui.glossaryList.innerHTML = '';
  if (!glossarEntwurf.length) {
    ui.glossaryList.append(Object.assign(document.createElement('p'), {
      className: 'note', textContent: 'Noch kein Glossar.' }));
  }
  glossarEntwurf.forEach((glossar, i) => {
    const block = document.createElement('details');
    block.className = 'glossary';
    const kopf = document.createElement('summary');
    const an = Object.assign(document.createElement('input'), {
      type: 'checkbox', checked: aktivEntwurf.has(glossar.id), title: 'Bei der Aufnahme verwenden' });
    an.addEventListener('click', (e) => e.stopPropagation());
    an.addEventListener('change', () => {
      if (an.checked) aktivEntwurf.add(glossar.id); else aktivEntwurf.delete(glossar.id);
    });
    const titel = document.createElement('span');
    titel.textContent = `${glossar.name || 'ohne Namen'} · ${glossar.terms.length} Begriffe`;
    kopf.append(an, titel);
    const name = Object.assign(document.createElement('input'), {
      type: 'text', value: glossar.name, placeholder: 'Name, z. B. Verein' });
    name.addEventListener('input', () => {
      glossar.name = name.value;
      titel.textContent = `${glossar.name || 'ohne Namen'} · ${glossar.terms.length} Begriffe`;
    });
    const begriffe = Object.assign(document.createElement('textarea'), {
      rows: 4, value: glossar.terms.join('\n'), placeholder: 'Ein Begriff je Zeile' });
    begriffe.addEventListener('input', () => {
      glossar.terms = begriffe.value.split(/[\n,]/).map((b) => b.trim()).filter(Boolean);
      titel.textContent = `${glossar.name || 'ohne Namen'} · ${glossar.terms.length} Begriffe`;
    });
    const weg = Object.assign(document.createElement('button'), {
      type: 'button', className: 'linklike', textContent: 'Glossar löschen' });
    weg.addEventListener('click', () => {
      if (glossar.terms.length && !confirm(`„${glossar.name}“ mit ${glossar.terms.length} Begriffen löschen?`)) return;
      glossarEntwurf.splice(i, 1);
      aktivEntwurf.delete(glossar.id);
      zeichneGlossare();
    });
    block.append(kopf, name, begriffe, weg);
    ui.glossaryList.append(block);
  });
}

el('glossary-add').addEventListener('click', () => {
  const id = neueGlossarKennung(glossarEntwurf);
  glossarEntwurf.push({ id, name: glossarEntwurf.length ? '' : 'Allgemein', terms: [] });
  aktivEntwurf.add(id);
  zeichneGlossare();
  ui.glossaryList.lastElementChild.open = true;
  ui.glossaryList.lastElementChild.querySelector('input[type="text"]').focus();
});

/** Das Wort als ganzes Wort, auch mit Umlauten - "Meier" trifft nicht "Meierhof". */
function wortRegex(wort) {
  const sicher = wort.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  return new RegExp(`(?<![\\p{L}\\p{N}_])${sicher}(?![\\p{L}\\p{N}_])`, 'gu');
}

let korrekturZeile = null, korrekturAlt = '';

function oeffneKorrektur(alt, zeile) {
  korrekturAlt = alt;
  korrekturZeile = zeile;
  el('correct-quote').textContent = `Im Transkript: „${alt}“`;
  el('correct-text').value = alt;
  el('correct-all').checked = true;
  el('correct-glossary').checked = true;
  const ziel = el('correct-target');
  ziel.innerHTML = '';
  const glossare = settings.glossaries ?? [];
  for (const g of glossare) ziel.append(new Option(g.name || 'ohne Namen', g.id));
  if (!glossare.length) ziel.append(new Option('Allgemein (neu)', ''));
  ziel.value = glossare.some((g) => g.id === settings.glossary_target)
    ? settings.glossary_target : (settings.active_glossaries?.[0] ?? glossare[0]?.id ?? '');
  el('correct-target-row').hidden = glossare.length < 2;
  const dienst = config.engines.find((e) => e.id === settings.engine);
  el('correct-note').textContent = recording
    ? (dienst?.vocabulary ? 'Gilt sofort auch für die laufende Aufnahme.'
      : `${dienst?.name ?? 'Der Dienst'} nimmt keine Begriffsliste an – in der laufenden `
        + 'Aufnahme wird das Wort trotzdem überall ersetzt.')
    : 'Das Glossar gilt ab der nächsten Aufnahme und für die Zusammenfassung.';
  el('correct-dialog').showModal();
  el('correct-text').select();
}

async function uebernimmKorrektur() {
  const neu = el('correct-text').value.trim();
  const alt = korrekturAlt;
  if (!neu) return;
  const ueberall = el('correct-all').checked;
  let anzahl = 0;
  if (neu !== alt) {
    const muster = wortRegex(alt);
    const ziele = ueberall ? transkriptEintraege
      : [transkriptEintraege[korrekturZeile]].filter(Boolean);
    for (const e of ziele) {
      if (e.gap) continue;
      const treffer = e.text.match(muster)?.length ?? 0;
      if (!treffer) continue;
      // Ohne "überall" nur die erste Stelle in der markierten Zeile.
      e.text = ueberall ? e.text.replace(muster, neu) : e.text.replace(new RegExp(muster.source, 'u'), neu);
      anzahl += ueberall ? treffer : 1;
    }
  }
  if (el('correct-glossary').checked) {
    let glossare = settings.glossaries ?? [];
    let id = el('correct-target').value;
    if (!glossare.some((g) => g.id === id)) {
      id = neueGlossarKennung(glossare);
      glossare = [...glossare, { id, name: 'Allgemein', terms: [] }];
    }
    glossare = glossare.map((g) => (g.id === id && !g.terms.includes(neu)
      ? { ...g, terms: [...g.terms, neu] } : g));
    const aktiv = new Set(settings.active_glossaries ?? []);
    aktiv.add(id);
    settings = { ...settings, glossaries: glossare, active_glossaries: [...aktiv], glossary_target: id };
    storeSettings();
  }
  if (recording && sessionId) {
    // Der Server ersetzt in seinem Transkript und in allem, was noch kommt,
    // und gibt der Erkennung die neue Begriffsliste.
    fetch(`/api/sessions/${sessionId}/correct`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ alt: ueberall ? alt : '', neu: ueberall ? neu : '',
                             vocabulary: aktiveBegriffe() }),
    }).catch(() => { /* die Anzeige steht auch so */ });
  }
  if (anzahl) {
    ungesichert = true;
    zeichneTranskript();
    filtereTranskript();
    merkeVor();
  }
  setStatus(`${anzahl ? `${anzahl}× ersetzt` : 'Nichts ersetzt'}`
            + (el('correct-glossary').checked ? ` · „${neu}“ im Glossar` : ''));
}

el('correct-dialog').addEventListener('close', () => {
  if (el('correct-dialog').returnValue === 'save') uebernimmKorrektur();
});

// Markieren im Transkript bietet eine Korrektur an - nicht im Bearbeiten-
// Modus, dort schreibt man direkt.
const korrekturKnopf = el('correct-word');
document.addEventListener('selectionchange', () => {
  const auswahl = document.getSelection();
  const text = auswahl?.toString().trim() ?? '';
  const knoten = auswahl?.rangeCount ? auswahl.getRangeAt(0).commonAncestorContainer : null;
  const zeile = knoten && (knoten.nodeType === 1 ? knoten : knoten.parentElement)?.closest('.seg[data-index], .seg');
  const drin = knoten && ui.transcript.contains(knoten) && !transkriptBearbeiten;
  if (!text || !drin || text.length > 60 || text.includes('\n')) {
    korrekturKnopf.hidden = true;
    return;
  }
  const rahmen = auswahl.getRangeAt(0).getBoundingClientRect();
  korrekturKnopf.style.left = `${Math.max(8, Math.min(window.innerWidth - 130, rahmen.left))}px`;
  korrekturKnopf.style.top = `${Math.max(8, rahmen.bottom + 6)}px`;
  korrekturKnopf.dataset.index = zeile?.dataset.index ?? '';
  korrekturKnopf.hidden = false;
});
for (const art of ['mousedown', 'touchstart']) {
  korrekturKnopf.addEventListener(art, (ereignis) => {
    ereignis.preventDefault();
    const text = document.getSelection()?.toString().trim() ?? '';
    const index = korrekturKnopf.dataset.index;
    korrekturKnopf.hidden = true;
    if (!text) return;
    // Während der Aufnahme tragen die Zeilen noch keine Nummer - dann die
    // Zeile über ihre Position suchen.
    let zeile = index !== '' ? Number(index) : null;
    if (zeile === null) zeile = transkriptEintraege.findIndex((e) => !e.gap && e.text.includes(text));
    oeffneKorrektur(text, zeile);
  }, { passive: false });
}
loadConfig();
