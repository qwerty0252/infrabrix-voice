/* Resamples mono microphone Float32 frames to AssemblyAI's PCM16/24 kHz input. */
class PcmResamplerProcessor extends AudioWorkletProcessor {
  constructor(options) {
    super();
    this.targetSampleRate = options.processorOptions?.targetSampleRate || 24000;
    this.position = 0;
    this.remainder = new Float32Array(0);
  }

  process(inputs) {
    const input = inputs[0]?.[0];
    if (!input || input.length === 0) return true;
    const samples = new Float32Array(this.remainder.length + input.length);
    samples.set(this.remainder);
    samples.set(input, this.remainder.length);
    const step = sampleRate / this.targetSampleRate;
    const output = [];
    while (this.position + 1 < samples.length) {
      const index = Math.floor(this.position);
      const fraction = this.position - index;
      const value = samples[index] * (1 - fraction) + samples[index + 1] * fraction;
      output.push(Math.max(-1, Math.min(1, value)) * 0x7fff);
      this.position += step;
    }
    const consumed = Math.floor(this.position);
    this.position -= consumed;
    this.remainder = samples.slice(consumed);
    if (output.length) {
      const pcm = Int16Array.from(output);
      this.port.postMessage(pcm.buffer, [pcm.buffer]);
    }
    return true;
  }
}

registerProcessor("pcm-resampler", PcmResamplerProcessor);
