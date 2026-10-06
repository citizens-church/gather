class PCMProcessor extends AudioWorkletProcessor {
  constructor() { super(); this.sum=0; this.weight=0; this.phase=0; this.buffer=new Int16Array(1600); this.index=0; }
  process(inputs) {
    const input=inputs[0]?.[0]; if(!input) return true;
    const ratio=sampleRate/16000;
    // Integrate fractional source samples: no cumulative drift at 44.1 or 48 kHz.
    for(const sample of input){
      let remaining=1;
      while(remaining>1e-7){
        const step=Math.min(remaining,ratio-this.phase);
        this.sum+=sample*step; this.weight+=step; this.phase+=step; remaining-=step;
        if(this.phase>=ratio-1e-7){
          const value=Math.max(-1,Math.min(1,this.sum/this.weight));
          this.buffer[this.index++]=Math.round(value*(value<0?32768:32767));
          this.sum=0;this.weight=0;this.phase=0;
          if(this.index===this.buffer.length){this.port.postMessage(this.buffer.buffer,[this.buffer.buffer]);this.buffer=new Int16Array(1600);this.index=0;}
        }
      }
    }
    return true;
  }
}
registerProcessor('gather-pcm',PCMProcessor);
