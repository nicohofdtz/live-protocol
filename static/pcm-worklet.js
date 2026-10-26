// Wandelt die Float32-Frames des Audiographen in 16-bit-PCM und schickt sie an
// den Hauptthread. Der AudioContext läuft bereits mit 16 kHz, das Resampling
// erledigt also der Browser - besser als eine eigene Dezimierung.
class PCMWorklet extends AudioWorkletProcessor {
  process(inputs) {
    const input = inputs[0];
    if (!input || input.length === 0) return true;
    const channel = input[0];
    if (!channel) return true;

    const pcm = new Int16Array(channel.length);
    for (let i = 0; i < channel.length; i++) {
      const s = Math.max(-1, Math.min(1, channel[i]));
      pcm[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
    }
    this.port.postMessage(pcm.buffer, [pcm.buffer]);
    return true;
  }
}
registerProcessor('pcm-worklet', PCMWorklet);
