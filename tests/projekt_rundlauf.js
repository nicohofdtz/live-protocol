// Wird von test_projekt.py aufgerufen: node projekt_rundlauf.js <modus> <datei>
const fs = require('fs');
const path = require('path');
const p = require(path.join(__dirname, '..', 'static', 'projekt.js'));

const STAND = {
  created: '2026-09-23T10:00:00Z', app_version: '0.3.0', output_type: 'meeting',
  context: { topic: 'Sommerfest', participants: 'Martina, Jens' },
  transcript: [{ seconds: 0, text: 'Wir fangen an.' },
               { seconds: 60, text: '[Aufnahme unterbrochen - etwa 20 s fehlen]', gap: true },
               { seconds: 95, text: 'Die Hüpfburg übernimmt Martina.', speaker: 'speaker_2' }],
  speakers: { speaker_2: { name: 'Martina', mehrere: false } },
  feedback: ['Namen ausschreiben'],
  summary: { markdown: '# Sommerfest\n\n| Was | Wer |\n|---|---|\n| Hüpfburg | Martina |',
             model: { id: 'eu', name: 'GLM 5.3' }, options: { detail: 'knapp' } },
};

(async () => {
  const [modus, datei] = process.argv.slice(2);
  if (modus === 'schreiben') {
    const audio = { name: 'aufnahme.mp3', daten: new Uint8Array(300000).map((_, i) => i % 251) };
    fs.writeFileSync(datei, p.baueProjekt(STAND, audio));
  } else if (modus === 'lesen') {
    const { stand, audio } = await p.leseProjekt(new Uint8Array(fs.readFileSync(datei)));
    console.log(JSON.stringify({ stand, audio: audio && { name: audio.name, laenge: audio.daten.length,
      pruef: audio.daten.reduce((s, b) => (s + b) % 1000003, 0) } }));
  } else if (modus === 'fehler') {
    try { await p.leseProjekt(new Uint8Array(fs.readFileSync(datei))); console.log('kein Fehler'); }
    catch (e) { console.log(e.message); }
  }
})();
