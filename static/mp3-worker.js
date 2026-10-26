// Kodiert den Mitschnitt während der Aufnahme zu MP3.
//
// Ein eigener Worker, damit das Kodieren den Audiograph nie aufhält. Er
// bekommt dieselben PCM-Blöcke, die an den Server gehen - Pause und Diktat
// sind also schon herausgefiltert, und Mitschnitt und Transkript zählen
// dieselben Sekunden.
//
// 48 kbit/s mono: gemessen 2026-09-23 erkennt Voxtral daran 99,6 % derselben
// Wörter wie am WAV (32 kbit/s: 98,6 %), bei 22 MB je Stunde statt 115.
//
// lame.min.js ist lamejs 1.2.1 (LGPL-3.0, github.com/zhuker/lamejs),
// unverändert aus dem npm-Paket. Es liegt hier, weil die Desktop-Fassung
// offline laufen muss.

let encoder = null;

self.onmessage = ({ data }) => {
  try {
    if (data.type === 'start') {
      importScripts('lame.min.js');
      encoder = new lamejs.Mp3Encoder(1, data.rate, data.kbps);
      self.postMessage({ type: 'bereit' });
    } else if (data.type === 'pcm') {
      const mp3 = encoder.encodeBuffer(data.samples);
      if (mp3.length) self.postMessage({ type: 'mp3', teil: mp3 }, [mp3.buffer]);
    } else if (data.type === 'ende') {
      const mp3 = encoder.flush();
      self.postMessage({ type: 'fertig', teil: mp3 }, [mp3.buffer]);
    }
  } catch (fehler) {
    self.postMessage({ type: 'fehler', text: String(fehler?.message || fehler) });
  }
};
