(function(){
'use strict';

/* ================= helpers ================= */
const $=(s,r=document)=>r.querySelector(s);
const $$=(s,r=document)=>Array.from(r.querySelectorAll(s));
const clamp=(v,a,b)=>Math.min(b,Math.max(a,v));

const APP_VERSION='4.2';
const NAME={vco1:'VCO1',vco2:'VCO2',lfo:'LFO',env:'ENV',noise:'NOISE',filter:'VCF',vca:'VCA',mix:'MIX',master:'MASTER'};
const PALETTE={
  vco1:'#ff5db1', vco2:'#59e3ff', lfo:'#ffd166', env:'#7cffb2',
  noise:'#c39bff', filter:'#ff9f5d', vca:'#ff6b6b', mix:'#6bffb8', master:'#59e3ff'
};
const WAVE_LABELS={sine:'SIN',triangle:'TRI',sawtooth:'SAW',square:'SQR'};
const fmtHz=v=>v<10?v.toFixed(2):v<100?v.toFixed(1):String(Math.round(v));
const fmtT=v=>v<1?(v*1000).toFixed(0)+'ms':v.toFixed(2)+'s';
const fmt2=v=>v.toFixed(2);
const fmtOct=v=>(v>0?'+':'')+v;
const fmtC=v=>(v>0?'+':'')+Math.round(v)+'¢';

/* ================= module specs ================= */
const MODULES=[
  {id:'vco1',name:'VCO 1',waves:1,
   knobs:[
     {id:'freq',label:'FREQ',min:30,max:1500,value:110,curve:'exp',fmt:fmtHz},
     {id:'oct',label:'OCT',min:-2,max:2,value:0,step:1,fmt:fmtOct},
     {id:'det',label:'DETUNE',min:-50,max:50,value:0,fmt:fmtC}
   ],
   ins:[{id:'fm',label:'FM'}],
   outs:[{id:'out',label:'OUT'}]},
  {id:'vco2',name:'VCO 2',waves:1,
   knobs:[
     {id:'freq',label:'FREQ',min:30,max:1500,value:110,curve:'exp',fmt:fmtHz},
     {id:'oct',label:'OCT',min:-2,max:2,value:0,step:1,fmt:fmtOct},
     {id:'det',label:'DETUNE',min:-50,max:50,value:8,fmt:fmtC}
   ],
   ins:[{id:'fm',label:'FM'}],
   outs:[{id:'out',label:'OUT'}]},
  {id:'lfo',name:'LFO',waves:1,
   knobs:[
     {id:'rate',label:'RATE',min:0.05,max:12,value:2,curve:'exp',fmt:fmtHz},
     {id:'depth',label:'DEPTH',min:0,max:1,value:0.35,fmt:fmt2}
   ],
   outs:[{id:'out',label:'OUT'}]},
  {id:'env',name:'ENVELOPE',
   knobs:[
     {id:'a',label:'ATK',min:0.005,max:2,value:0.02,curve:'exp',fmt:fmtT},
     {id:'d',label:'DEC',min:0.02,max:2,value:0.25,curve:'exp',fmt:fmtT},
     {id:'s',label:'SUS',min:0,max:1,value:0.5,fmt:fmt2},
     {id:'r',label:'REL',min:0.02,max:4,value:0.5,curve:'exp',fmt:fmtT}
   ],
   outs:[{id:'out',label:'OUT'}]},
  {id:'noise',name:'NOISE',
   knobs:[{id:'level',label:'LEVEL',min:0,max:1,value:0.4,fmt:fmt2}],
   outs:[{id:'out',label:'OUT'}]},
  {id:'filter',name:'VCF · LP',
   knobs:[
     {id:'cutoff',label:'CUTOFF',min:60,max:9000,value:900,curve:'exp',fmt:fmtHz},
     {id:'res',label:'RES',min:0.3,max:18,value:4,fmt:fmt2}
   ],
   ins:[{id:'in',label:'IN'},{id:'cutoff',label:'CUT'},{id:'res',label:'RES'}],
   outs:[{id:'out',label:'OUT'}]},
  {id:'vca',name:'VCA',
   knobs:[{id:'level',label:'LEVEL',min:0,max:1.5,value:0.5,fmt:fmt2}],
   ins:[{id:'in',label:'IN'},{id:'gain',label:'GAIN'}],
   outs:[{id:'out',label:'OUT'}]},
  {id:'mix',name:'MIX',
   knobs:[
     {id:'m1',label:'L1',min:0,max:1,value:0.7,fmt:fmt2},
     {id:'m2',label:'L2',min:0,max:1,value:0.7,fmt:fmt2},
     {id:'m3',label:'L3',min:0,max:1,value:0.7,fmt:fmt2}
   ],
   ins:[{id:'in1',label:'IN1'},{id:'in2',label:'IN2'},{id:'in3',label:'IN3'}],
   outs:[{id:'out',label:'OUT'}]},
  {id:'master',name:'MASTER OUT',span:2,meter:1,
   knobs:[{id:'vol',label:'VOLUME',min:0,max:1,value:0.8,fmt:fmt2}],
   ins:[{id:'in',label:'IN'}]}
];

/* ================= state ================= */
const kState={};          // 'mid.knob' -> value
const knobs={};           // 'mid.knob' -> Knob
const KSPEC={};           // 'mid.knob' -> spec
const waveState={vco1:'sawtooth',vco2:'sawtooth',lfo:'sine'};
let ctx=null,ready=false,holdMode=false,kbdOct=0,lastEff=60;
const activeNotes=new Map(); // pointerId -> effective midi
const cables=[];          // {out,in,color,el,hit}
let drag=null,tempEl=null,tdata=null,toastT=null;

/* ================= Knob ================= */
class Knob{
  constructor(el,spec,onInput){
    this.el=el;this.spec=spec;this.onInput=onInput;
    this.dial=el.querySelector('.knob-dial');
    this.valEl=el.querySelector('.knob-val');
  }
  normToVal(n){
    const s=this.spec;
    if(s.curve==='exp')return s.min*Math.pow(s.max/s.min,n);
    let v=s.min+(s.max-s.min)*n;
    if(s.step)v=Math.round((v-s.min)/s.step)*s.step+s.min;
    return clamp(v,s.min,s.max);
  }
  valToNorm(v){
    const s=this.spec;
    const n=s.curve==='exp'?Math.log(v/s.min)/Math.log(s.max/s.min):(v-s.min)/(s.max-s.min);
    return clamp(n,0,1);
  }
  set(v){
    v=clamp(v,this.spec.min,this.spec.max);
    this.v=v;
    const n=this.valToNorm(v);
    this.dial.style.transform='rotate('+(-135+n*270)+'deg)';
    this.valEl.textContent=(this.spec.fmt||String)(v);
    this.onInput(v);
  }
  init(){
    this.set(this.spec.value);
    let startY=0,startN=0,lastTap=0,capturing=false;
    this.el.addEventListener('pointerdown',e=>{
      e.preventDefault();
      try{this.el.setPointerCapture(e.pointerId);}catch(_){/* noop */}
      capturing=true;
      const now=performance.now();
      if(now-lastTap<300)this.set(this.spec.value); // double tap -> reset
      lastTap=now;
      startY=e.clientY;startN=this.valToNorm(this.v);
    });
    this.el.addEventListener('pointermove',e=>{
      if(!capturing)return;
      const n=clamp(startN+(startY-e.clientY)/140,0,1);
      this.set(this.normToVal(n));
    });
    const off=()=>{capturing=false;};
    this.el.addEventListener('pointerup',off);
    this.el.addEventListener('pointercancel',off);
  }
}

/* ================= build rack DOM ================= */
const rack=$('#rack');
const rackWrap=$('#rackWrap');
const svg=$('#cables');

function wavesHtml(mid){
  return '<div class="waves">'+Object.keys(WAVE_LABELS).map(w=>
    '<button class="wave-btn'+(waveState[mid]===w?' on':'')+'" data-module="'+mid+'" data-wave="'+w+'">'+WAVE_LABELS[w]+'</button>'
  ).join('')+'</div>';
}
function makeJackEl(kind,key,label){
  const el=document.createElement('div');
  el.className='jack jack-'+kind;
  el.dataset[kind]=key;
  if(kind==='out'){
    const c=PALETTE[key.split('.')[0]]||'#fff';
    el.style.setProperty('--c',c);
    el.style.setProperty('--c-glow',c+'99');
  }
  el.innerHTML='<i></i><span>'+label+'</span>';
  return el;
}
function makeKnobEl(mid,spec){
  const el=document.createElement('div');
  el.className='knob';
  el.innerHTML='<div class="knob-dial"><div class="knob-dot"></div></div>'+
    '<div class="knob-label">'+spec.label+'</div><div class="knob-val"></div>';
  const k=new Knob(el,spec,v=>applyKnob(mid,spec.id,v));
  knobs[mid+'.'+spec.id]=k;
  KSPEC[mid+'.'+spec.id]=spec;
  kState[mid+'.'+spec.id]=spec.value;
  k.init();
  return el;
}

for(const m of MODULES){
  const el=document.createElement('section');
  el.className='module'+(m.span===2?' module--master':'');
  el.dataset.module=m.id;
  el.style.setProperty('--mc',PALETTE[m.id]);
  let html='<h3>'+m.name+'</h3>';
  if(m.waves)html+=wavesHtml(m.id);
  if(m.knobs&&m.knobs.length)
    html+='<div class="knobs'+(m.knobs.length!==3?' k'+m.knobs.length:'')+'"></div>';
  if(m.meter)
    html+='<div class="mrow"><div class="mslot"></div>'+
          '<div class="master-meter"><div class="meter-fill"></div></div>'+
          '<div class="master-note">→ OUTPUT</div></div>';
  el.innerHTML=html;
  if(m.knobs&&m.knobs.length)
    for(const s of m.knobs)el.querySelector('.knobs').appendChild(makeKnobEl(m.id,s));
  const jrow=document.createElement('div');
  jrow.className='jacks';
  (m.ins||[]).forEach(i=>jrow.appendChild(makeJackEl('in',m.id+'.'+i.id,i.label)));
  (m.outs||[]).forEach(o=>jrow.appendChild(makeJackEl('out',m.id+'.'+o.id,o.label)));
  if(m.meter)el.querySelector('.mslot').appendChild(jrow.firstChild);
  else if(jrow.children.length)el.appendChild(jrow);
  rack.appendChild(el);
}

/* ================= keyboard ================= */
const keysEl=$('#keys');
const KB_START=48,KB_N=25; // C3 .. C4
const isBlack=m=>[1,3,6,8,10].indexOf(m%12)>=0;
(function buildKeys(){
  let nW=0;
  for(let m=KB_START;m<KB_START+KB_N;m++){
    if(!isBlack(m)){
      const k=document.createElement('div');
      k.className='key key-w';k.dataset.midi=m;
      keysEl.appendChild(k);nW++;
    }
  }
  const w=100/nW;
  for(let m=KB_START;m<KB_START+KB_N;m++){
    if(!isBlack(m))continue;
    let wc=0;for(let x=KB_START;x<m;x++)if(!isBlack(x))wc++;
    const k=document.createElement('div');
    k.className='key key-b';k.dataset.midi=m;
    k.style.left=(wc*w-w*0.31)+'%';
    k.style.width=(w*0.62)+'%';
    keysEl.appendChild(k);
  }
})();
function markActive(){
  $$('.key',keysEl).forEach(k=>k.classList.remove('active'));
  for(const m of activeNotes.values()){
    const base=m-kbdOct*12;
    $$('.key[data-midi="'+base+'"]',keysEl).forEach(k=>k.classList.add('active'));
  }
}
function pressKey(pid,midi){
  const wasEmpty=activeNotes.size===0;
  const eff=midi+kbdOct*12;
  activeNotes.set(pid,eff);
  lastEff=eff;
  markActive();
  if(!ready)return;
  const t=ctx.currentTime;
  N.osc1.frequency.setTargetAtTime(vcoFreq('vco1',eff),t,0.012);
  N.osc2.frequency.setTargetAtTime(vcoFreq('vco2',eff),t,0.012);
  if(wasEmpty)envAttack();
}
function releaseMidi(midi){
  for(const [p,m] of activeNotes)if(m===midi)activeNotes.delete(p);
  markActive();
  if(ready&&activeNotes.size===0&&!holdMode)envRelease();
}
keysEl.addEventListener('pointerdown',e=>{
  const k=e.target.closest('.key');
  if(!k)return;
  e.preventDefault();
  pressKey(e.pointerId,+k.dataset.midi);
});

/* ================= audio engine ================= */
const N={};
const JACK_SCALE={'filter.cutoff':5000,'filter.res':10,'vca.gain':1.4,'vco1.fm':1200,'vco2.fm':1200};

function initAudio(){
  const AC=window.AudioContext||window.webkitAudioContext;
  ctx=new AC();

  N.osc1=ctx.createOscillator();N.osc1.type=waveState.vco1;N.osc1.frequency.value=110;
  N.osc2=ctx.createOscillator();N.osc2.type=waveState.vco2;N.osc2.frequency.value=110;
  N.vco1g=ctx.createGain();N.vco1g.gain.value=0.7;N.osc1.connect(N.vco1g);
  N.vco2g=ctx.createGain();N.vco2g.gain.value=0.7;N.osc2.connect(N.vco2g);

  const nb=ctx.createBuffer(1,ctx.sampleRate*2,ctx.sampleRate);
  {const d=nb.getChannelData(0);for(let i=0;i<d.length;i++)d[i]=Math.random()*2-1;}
  N.noiseSrc=ctx.createBufferSource();N.noiseSrc.buffer=nb;N.noiseSrc.loop=true;
  N.noiseg=ctx.createGain();N.noiseg.gain.value=0.4;N.noiseSrc.connect(N.noiseg);

  N.lfo=ctx.createOscillator();N.lfo.type=waveState.lfo;N.lfo.frequency.value=2;
  N.lfog=ctx.createGain();N.lfog.gain.value=0.35;N.lfo.connect(N.lfog);

  const cb=ctx.createBuffer(1,ctx.sampleRate,ctx.sampleRate);
  cb.getChannelData(0).fill(1);
  N.envSrc=ctx.createBufferSource();N.envSrc.buffer=cb;N.envSrc.loop=true;
  N.envg=ctx.createGain();N.envg.gain.value=0;N.envSrc.connect(N.envg);

  N.bq=ctx.createBiquadFilter();N.bq.type='lowpass';N.bq.frequency.value=900;N.bq.Q.value=4;
  N.fg=ctx.createGain();N.fg.gain.value=1;N.bq.connect(N.fg);

  N.vca=ctx.createGain();N.vca.gain.value=0.5;

  N.m1=ctx.createGain();N.m2=ctx.createGain();N.m3=ctx.createGain();
  N.msum=ctx.createGain();
  N.m1.connect(N.msum);N.m2.connect(N.msum);N.m3.connect(N.msum);
  N.mg=ctx.createGain();N.mg.gain.value=0.6;N.msum.connect(N.mg);

  N.master=ctx.createGain();N.master.gain.value=0.8;
  N.comp=ctx.createDynamicsCompressor();
  N.comp.threshold.value=-16;N.comp.knee.value=24;N.comp.ratio.value=5;
  N.comp.attack.value=0.003;N.comp.release.value=0.2;
  N.analyser=ctx.createAnalyser();N.analyser.fftSize=1024;
  N.master.connect(N.comp);N.comp.connect(N.analyser);N.analyser.connect(ctx.destination);

  N.osc1.start();N.osc2.start();N.lfo.start();N.noiseSrc.start();N.envSrc.start();

  // patch bay: every input jack is a gain (0 or scale) permanently wired to its destination
  N.jacks={};
  const j=(id,dest)=>{const g=ctx.createGain();g.gain.value=0;g.connect(dest);N.jacks[id]=g;};
  j('filter.in',N.bq);
  j('filter.cutoff',N.bq.frequency);
  j('filter.res',N.bq.Q);
  j('vca.in',N.vca);
  j('vca.gain',N.vca.gain);
  j('vco1.fm',N.osc1.detune);
  j('vco2.fm',N.osc2.detune);
  j('mix.in1',N.m1);
  j('mix.in2',N.m2);
  j('mix.in3',N.m3);
  j('master.in',N.master);

  ready=true;
  for(const key in KSPEC){
    const i=key.indexOf('.');
    applyKnob(key.slice(0,i),key.slice(i+1),kState[key]);
  }
  cables.forEach(c=>{setJack(c.in,true);wireCable(c,true);});
  setVcoFreqs(lastEff);
  if(ctx.state!=='running')ctx.resume();
}

function vcoFreq(mid,eff){
  return kState[mid+'.freq']*Math.pow(2,kState[mid+'.oct'])*Math.pow(2,((eff==null?lastEff:eff)-60)/12);
}
function setVcoFreqs(eff){
  if(!ready)return;
  const t=ctx.currentTime;
  N.osc1.frequency.setTargetAtTime(vcoFreq('vco1',eff),t,0.015);
  N.osc2.frequency.setTargetAtTime(vcoFreq('vco2',eff),t,0.015);
}
function applyKnob(mid,kid,v){
  kState[mid+'.'+kid]=v;
  if(!ready)return;
  const t=ctx.currentTime;
  if(mid==='vco1'||mid==='vco2'){
    if(kid==='det')(mid==='vco1'?N.osc1:N.osc2).detune.setTargetAtTime(v,t,0.01);
    else setVcoFreqs(lastEff);
  }else if(mid==='lfo'){
    if(kid==='rate')N.lfo.frequency.setTargetAtTime(v,t,0.01);
    else N.lfog.gain.setTargetAtTime(v,t,0.01);
  }else if(mid==='filter'){
    if(kid==='cutoff')N.bq.frequency.setTargetAtTime(v,t,0.01);
    else N.bq.Q.setTargetAtTime(v,t,0.01);
  }else if(mid==='vca'){
    N.vca.gain.setTargetAtTime(v,t,0.01);
  }else if(mid==='noise'){
    N.noiseg.gain.setTargetAtTime(v,t,0.01);
  }else if(mid==='mix'){
    (kid==='m1'?N.m1:kid==='m2'?N.m2:N.m3).gain.setTargetAtTime(v,t,0.01);
  }else if(mid==='master'){
    N.master.gain.setTargetAtTime(v,t,0.01);
  }
}
function envAttack(){
  if(!ready)return;
  const t=ctx.currentTime,g=N.envg.gain;
  const a=clamp(kState['env.a'],0.002,10),d=clamp(kState['env.d'],0.01,10),s=clamp(kState['env.s'],0,1);
  g.cancelScheduledValues(t);
  g.setValueAtTime(Math.max(0,g.value),t);
  g.linearRampToValueAtTime(1,t+a);
  g.setTargetAtTime(s,t+a,Math.max(d/3,0.012));
}
function envRelease(){
  if(!ready)return;
  const t=ctx.currentTime,g=N.envg.gain;
  const r=clamp(kState['env.r'],0.01,10);
  g.cancelScheduledValues(t);
  g.setValueAtTime(Math.max(0,g.value),t);
  g.setTargetAtTime(0,t,Math.max(r/4,0.012));
}
function setJack(inId,on){
  if(!ready)return;
  const scale=JACK_SCALE[inId]||1;
  N.jacks[inId].gain.setTargetAtTime(on?scale:0,ctx.currentTime,0.012);
}
// id вывода модуля -> его аудио-узел (источник сигнала для провода)
const OUT_NODE={
  'vco1.out':()=>N.vco1g,'vco2.out':()=>N.vco2g,'lfo.out':()=>N.lfog,
  'env.out':()=>N.envg,'noise.out':()=>N.noiseg,'filter.out':()=>N.fg,
  'vca.out':()=>N.vca,'mix.out':()=>N.mg
};
function wireCable(c,on){
  if(!ready)return;
  const src=OUT_NODE[c.out]?OUT_NODE[c.out]():null;
  const dst=N.jacks[c.in];
  if(!src||!dst)return;
  if(on)src.connect(dst);else src.disconnect(dst);
}
function setWave(mid,w){
  waveState[mid]=w;
  $$('.wave-btn[data-module="'+mid+'"]').forEach(x=>x.classList.toggle('on',x.dataset.wave===w));
  if(ready)(mid==='lfo'?N.lfo:mid==='vco1'?N.osc1:N.osc2).type=w;
}
rackWrap.addEventListener('click',e=>{
  const b=e.target.closest('.wave-btn');
  if(b)setWave(b.dataset.module,b.dataset.wave);
});

/* ================= cables ================= */
const NS='http://www.w3.org/2000/svg';
function mkPath(cls){
  const p=document.createElementNS(NS,'path');
  p.setAttribute('class',cls);
  svg.appendChild(p);
  return p;
}
function jackPos(attr,val){
  const el=document.querySelector('['+attr+'="'+val+'"]');
  if(!el)return{x:0,y:0};
  const wr=rackWrap.getBoundingClientRect(),r=el.getBoundingClientRect();
  return{x:r.left+r.width/2-wr.left,y:r.top+r.height/2-wr.top};
}
function pathD(a,b){
  const dx=Math.abs(b.x-a.x),dy=Math.abs(b.y-a.y);
  const sag=Math.min(170,34+Math.max(dx,dy)*0.38);
  return'M '+a.x+' '+a.y+' C '+a.x+' '+(a.y+sag)+', '+b.x+' '+(b.y+sag)+', '+b.x+' '+b.y;
}
function updateCable(c){
  const d=pathD(jackPos('data-out',c.out),jackPos('data-in',c.in));
  c.el.setAttribute('d',d);
  c.hit.setAttribute('d',d);
}
// явный пиксельный размер канваса — без % и inset, работает в любом браузере
function sizeSvg(){
  const w=Math.round(rackWrap.clientWidth);
  const h=Math.round(rackWrap.scrollHeight);
  if(w>0&&h>0){
    svg.setAttribute('width',w);
    svg.setAttribute('height',h);
  }
}
function updateAll(){
  sizeSvg();
  cables.forEach(updateCable);
  if(drag)updateTemp();
}
// текстовая копия списка соединений (если SVG вдруг не рисуется — соединения всё равно видны)
function refreshPatchlist(){
  const el=document.getElementById('patchlist');
  if(!el)return;
  if(!cables.length){el.textContent='— провода не подключены —';return;}
  el.innerHTML=cables.map(c=>{
    const om=c.out.split('.')[0],im=c.in.split('.')[0],ip=c.in.split('.')[1];
    return '<b>'+NAME[om]+'</b>→'+NAME[im]+'.'+String(ip).toUpperCase();
  }).join('&nbsp;&nbsp;·&nbsp;&nbsp;');
}
// самопроверка: измеряет канвас и первый провод, при проблеме пишет на экран
function selfCheck(){
  let msg=null;
  const r=svg.getBoundingClientRect();
  const part='SVG-слой: '+Math.round(r.width)+'×'+Math.round(r.height)+', проводов: '+cables.length;
  if(r.width<10||r.height<10){
    msg=part+'. Канвас нулевой — обнови страницу (Safari: значок ⟳) или пересоздай иконку приложения.';
  }else if(cables.length>0){
    const p=cables[0].el.getBoundingClientRect();
    if(p.width<1&&p.height<1)
      msg=part+'. Провода есть в данных, но не отображаются — пришли скриншот этого сообщения.';
  }
  let b=document.getElementById('diagbox');
  if(msg){
    if(!b){b=document.createElement('div');b.id='diagbox';document.body.appendChild(b);}
    b.textContent='⚠ '+msg;b.style.display='block';
  }else if(b){b.style.display='none';}
}
// «лечебный» цикл: несколько раз перерисовывает провода, пока iOS/Safari не стабилизируют вёрстку
function heal(n){
  if(n<=0)return;
  updateAll();
  selfCheck();
  setTimeout(function(){heal(n-1);},600);
}
function connect(outId,inId,color){
  const old=cables.find(c=>c.in===inId);
  if(old)removeCable(old);
  const c={out:outId,in:inId,color:color||PALETTE[outId.split('.')[0]]||'#fff'};
  c.el=mkPath('cable');
  c.hit=mkPath('cable-hit');
  c.el.style.stroke=c.color;
  c.el.style.color=c.color;
  c.hit.dataset.in=inId;
  c.hit.dataset.out=outId;
  cables.push(c);
  updateCable(c);
  if(ready){setJack(inId,true);wireCable(c,true);}
  refreshPatchlist();
}
function removeCable(c){
  if(ready){setJack(c.in,false);wireCable(c,false);}
  c.el.remove();c.hit.remove();
  const i=cables.indexOf(c);
  if(i>=0)cables.splice(i,1);
  refreshPatchlist();
}
function updateTemp(){
  if(!tempEl)tempEl=mkPath('cable temp');
  tempEl.style.stroke=drag.color;
  tempEl.style.color=drag.color;
  tempEl.setAttribute('d',pathD(jackPos('data-out',drag.out),drag.p));
}
function clearTemp(){
  if(tempEl){tempEl.remove();tempEl=null;}
}
function nearestJackOf(sel,cx,cy,rad){
  let best=null,bd=rad*rad;
  for(const el of $$(sel)){
    const r=el.getBoundingClientRect();
    const dx=cx-(r.left+r.width/2),dy=cy-(r.top+r.height/2);
    const d=dx*dx+dy*dy;
    if(d<bd){bd=d;best=el;}
  }
  return best;
}
const nearestIn=(cx,cy)=>nearestJackOf('.jack-in',cx,cy,30);
const nearestOut=(cx,cy)=>nearestJackOf('.jack-out',cx,cy,30);
const nearestJack=(cx,cy,rad)=>nearestJackOf('.jack',cx,cy,rad);
const elOfOut=id=>document.querySelector('[data-out="'+id+'"]');

function setDragSrc(el){
  $$('.jack.drag-src').forEach(j=>j.classList.remove('drag-src'));
  if(el)el.classList.add('drag-src');
}
function clearDragHl(){
  setDragSrc(null);
  $$('.jack.hilite').forEach(j=>j.classList.remove('hilite'));
}
// позиция указателя относительно стеллажа (для черчения временного провода)
function wrapPos(e){
  const wr=rackWrap.getBoundingClientRect();
  return{x:e.clientX-wr.left,y:e.clientY-wr.top};
}
// новый провод от вывода
function startNewDrag(outId,srcEl,e){
  drag={mode:'new',out:outId,in:null,color:PALETTE[outId.split('.')[0]]||'#fff',
        p:wrapPos(e),sx:e.clientX,sy:e.clientY,removed:false};
  setDragSrc(srcEl);
  updateTemp();
}
// перетяжка существующего провода (от входа, от провода или от занятого вывода)
function startMoveDrag(c,srcEl,e){
  drag={mode:'move',out:c.out,in:c.in,color:c.color,
        p:wrapPos(e),sx:e.clientX,sy:e.clientY,removed:false};
  setDragSrc(srcEl);
  updateTemp();
}
rackWrap.addEventListener('pointerdown',e=>{
  if(e.target.closest('.knob'))return;
  let jackEl=e.target.closest('.jack');
  if(!jackEl){
    const hitEl=e.target.closest('.cable-hit');
    if(!hitEl)return;
    jackEl=nearestJack(e.clientX,e.clientY,24);
    if(jackEl){
      // палец на проводе, но над сокетом — действуем как на сокете
    }else{
      const c=cables.find(x=>x.in===hitEl.dataset.in);
      if(c){e.preventDefault();startMoveDrag(c,elOfOut(c.out),e);}
      return;
    }
  }
  if(jackEl.classList.contains('jack-out')){
    e.preventDefault();
    startNewDrag(jackEl.dataset.out,jackEl,e);
  }else{
    const c=cables.find(x=>x.in===jackEl.dataset.in);
    if(c){e.preventDefault();startMoveDrag(c,jackEl,e);}
  }
});
window.addEventListener('pointermove',e=>{
  if(drag){
    if(!drag.removed){
      const dx=e.clientX-drag.sx,dy=e.clientY-drag.sy;
      if(dx*dx+dy*dy>100){
        drag.removed=true;
        if(drag.mode==='move'){
          const c=cables.find(x=>x.in===drag.in&&x.out===drag.out);
          if(c)removeCable(c);
        }
      }
    }
    drag.p=wrapPos(e);
    updateTemp();
    const hiIn=nearestIn(e.clientX,e.clientY);
    const hiOut=hiIn?null:nearestOut(e.clientX,e.clientY);
    $$('.jack-in').forEach(j=>j.classList.toggle('hilite',j===hiIn));
    $$('.jack-out').forEach(j=>j.classList.toggle('hilite',j===hiOut));
    return;
  }
  // keyboard glide
  if(activeNotes.has(e.pointerId)){
    const el=document.elementFromPoint(e.clientX,e.clientY);
    const k=el&&el.closest?el.closest('.key'):null;
    if(k){
      const midi=+k.dataset.midi;
      if(midi!==activeNotes.get(e.pointerId)){
        releaseMidi(activeNotes.get(e.pointerId));
        pressKey(e.pointerId,midi);
      }
    }
  }
});
function endDrag(e){
  if(!drag)return;
  clearDragHl();
  const hiIn=nearestIn(e.clientX,e.clientY);
  const hiOut=hiIn?null:nearestOut(e.clientX,e.clientY);
  let connected=false;
  if(drag.mode==='new'){
    if(hiIn){connect(drag.out,hiIn.dataset.in,drag.color);connected=true;}
    // отпустили в воздухе — провода нет
  }else if(drag.removed){
    if(hiIn){connect(drag.out,hiIn.dataset.in,drag.color);connected=true;}
    else if(hiOut){connect(hiOut.dataset.out,drag.in,drag.color);connected=true;}
    // воздух — провод снят
  }
  // короткий тап по проводу — ничего не происходит
  if(connected&&navigator.vibrate)navigator.vibrate(12);
  drag=null;
  clearTemp();
}
window.addEventListener('pointerup',e=>{
  endDrag(e);
  if(activeNotes.has(e.pointerId))releaseMidi(activeNotes.get(e.pointerId));
});
window.addEventListener('pointercancel',e=>{
  endDrag(e);
  if(activeNotes.has(e.pointerId))releaseMidi(activeNotes.get(e.pointerId));
});

/* ================= octave / hold ================= */
$('#octDn').addEventListener('click',()=>setOct(kbdOct-1));
$('#octUp').addEventListener('click',()=>setOct(kbdOct+1));
function setOct(o){
  o=clamp(o,-2,2);
  const delta=o-kbdOct;
  kbdOct=o;
  $('#octVal').textContent=fmtOct(o);
  if(ready&&delta!==0){lastEff+=delta*12;setVcoFreqs(lastEff);}
}
$('#btnHold').addEventListener('click',()=>{
  holdMode=!holdMode;
  $('#btnHold').classList.toggle('on',holdMode);
  if(!holdMode&&ready&&activeNotes.size===0)envRelease();
});

/* ================= presets / save / load / random ================= */
const PRESETS={
  'classic':{
    waves:{vco1:'sawtooth',vco2:'sawtooth',lfo:'sine'},
    knobs:{'vco1.freq':110,'vco1.oct':0,'vco1.det':0,'vco2.freq':110,'vco2.oct':0,'vco2.det':9,
      'lfo.rate':3,'lfo.depth':0.12,'env.a':0.01,'env.d':0.2,'env.s':0.6,'env.r':0.4,
      'filter.cutoff':900,'filter.res':5,'vca.level':0.3,
      'mix.m1':0.7,'mix.m2':0.7,'mix.m3':0.7,'noise.level':0.4,'master.vol':0.8},
    cables:[['vco1.out','filter.in'],['env.out','vca.gain'],['filter.out','vca.in'],['vca.out','master.in']]
  },
  'witch-pad':{
    waves:{vco1:'triangle',vco2:'sine',lfo:'sine'},
    knobs:{'vco1.freq':174,'vco2.freq':220,'vco2.det':0,
      'lfo.rate':0.4,'lfo.depth':0.3,'env.a':0.8,'env.d':0.9,'env.s':0.75,'env.r':2,
      'filter.cutoff':1500,'filter.res':1,'vca.level':0.25,
      'mix.m1':0.8,'mix.m2':0.8,'mix.m3':0.7,'noise.level':0.4,'master.vol':0.8},
    cables:[['vco1.out','mix.in1'],['vco2.out','mix.in2'],['mix.out','vca.in'],
      ['env.out','vca.gain'],['lfo.out','vco1.fm'],['vca.out','master.in']]
  },
  'dark-bass':{
    waves:{vco1:'sawtooth',vco2:'square',lfo:'triangle'},
    knobs:{'vco1.freq':55,'vco2.freq':55,'vco2.det':4,
      'lfo.rate':6,'lfo.depth':0.06,'env.a':0.005,'env.d':0.3,'env.s':0.5,'env.r':0.2,
      'filter.cutoff':500,'filter.res':12,'vca.level':0.3,
      'mix.m1':0.7,'mix.m2':0.7,'mix.m3':0.7,'noise.level':0.4,'master.vol':0.85},
    cables:[['vco1.out','filter.in'],['env.out','filter.cutoff'],['env.out','vca.gain'],
      ['filter.out','vca.in'],['vca.out','master.in']]
  },
  'noise-lead':{
    waves:{vco1:'sine',vco2:'square',lfo:'sine'},
    knobs:{'vco1.freq':110,'vco2.freq':330,'vco2.det':0,
      'lfo.rate':0.8,'lfo.depth':0.35,'env.a':0.01,'env.d':0.4,'env.s':0.4,'env.r':0.6,
      'filter.cutoff':900,'filter.res':4,'vca.level':0.3,
      'mix.m1':0.9,'mix.m2':0.7,'mix.m3':0.7,'noise.level':0.5,'master.vol':0.75},
    cables:[['noise.out','mix.in1'],['vco2.out','mix.in2'],['mix.out','vca.in'],
      ['env.out','vca.gain'],['lfo.out','vco2.fm'],['vca.out','master.in']]
  }
};
function applyPreset(name){
  const p=PRESETS[name];
  if(!p)return;
  $('#preset').value=name;
  Object.keys(p.knobs).forEach(k=>{if(knobs[k])knobs[k].set(p.knobs[k]);});
  Object.keys(p.waves).forEach(m=>setWave(m,p.waves[m]));
  cables.slice().forEach(removeCable);
  p.cables.forEach(pair=>connect(pair[0],pair[1]));
}
$('#preset').addEventListener('change',e=>applyPreset(e.target.value));

function toast(msg,ms){
  const t=$('#toast');
  t.textContent=msg;
  t.classList.add('show');
  clearTimeout(toastT);
  toastT=setTimeout(()=>t.classList.remove('show'),ms||1500);
}
$('#btnSave').addEventListener('click',()=>{
  try{
    localStorage.setItem('ww-patch',JSON.stringify({
      k:kState,w:waveState,c:cables.map(c=>[c.out,c.in])
    }));
    toast('Патч сохранён 💾');
  }catch(e){toast('Не удалось сохранить');}
});
$('#btnLoad').addEventListener('click',()=>{
  try{
    const s=localStorage.getItem('ww-patch');
    if(!s){toast('Ничего не сохранено');return;}
    const d=JSON.parse(s);
    Object.keys(d.k||{}).forEach(k=>{if(knobs[k])knobs[k].set(d.k[k]);});
    Object.keys(d.w||{}).forEach(m=>{if(WAVE_LABELS[d.w[m]])setWave(m,d.w[m]);});
    cables.slice().forEach(removeCable);
    (d.c||[]).forEach(pair=>{
      if(pair.length===2&&
         document.querySelector('[data-out="'+pair[0]+'"]')&&
         document.querySelector('[data-in="'+pair[1]+'"]'))connect(pair[0],pair[1]);
    });
    toast('Патч загружен');
  }catch(e){toast('Ошибка загрузки');}
});
$('#btnRand').addEventListener('click',()=>{
  const rr=(a,b)=>a+Math.random()*(b-a);
  const ex=(a,b)=>a*Math.pow(b/a,Math.random());
  knobs['vco1.freq'].set(ex(40,1500));
  knobs['vco2.freq'].set(kState['vco1.freq']*(Math.random()<0.5?1:Math.pow(2,rr(-0.3,0.3))));
  knobs['vco2.det'].set(rr(-15,15));
  knobs['lfo.rate'].set(ex(0.08,10));
  knobs['lfo.depth'].set(rr(0,0.5));
  knobs['env.a'].set(ex(0.005,1.5));
  knobs['env.d'].set(ex(0.02,2));
  knobs['env.s'].set(rr(0.15,0.9));
  knobs['env.r'].set(ex(0.03,3));
  knobs['filter.cutoff'].set(ex(120,7000));
  knobs['filter.res'].set(rr(1,15));
  knobs['vca.level'].set(rr(0.2,0.6));
  knobs['master.vol'].set(rr(0.6,0.9));
  const W=Object.keys(WAVE_LABELS);
  setWave('vco1',W[Math.random()*W.length|0]);
  setWave('vco2',W[Math.random()*W.length|0]);
  setWave('lfo',W[Math.random()*W.length|0]);
  toast('Случайный патч ✨');
});

/* ================= boot / meter ================= */
function boot(){
  if(ready)return;
  initAudio();
  $('#start').classList.add('hide');
  updateAll();selfCheck();
  heal(8);
  toast('Соедини модули: нажми и потяни светящуюся точку OUT в любой сокет',3800);
}
$('#startBtn').addEventListener('click',e=>{e.stopPropagation();boot();});
$('#start').addEventListener('click',boot);

const meterFill=$('.meter-fill');
function frame(){
  if(ready&&N.analyser){
    if(!tdata)tdata=new Uint8Array(N.analyser.fftSize);
    N.analyser.getByteTimeDomainData(tdata);
    let sum=0;
    for(let i=0;i<tdata.length;i++){const v=(tdata[i]-128)/128;sum+=v*v;}
    const rms=Math.sqrt(sum/tdata.length);
    meterFill.style.width=(Math.min(1,rms*2.8)*100).toFixed(1)+'%';
  }
  if(drag)updateTemp();
  requestAnimationFrame(frame);
}
requestAnimationFrame(frame);

window.addEventListener('resize',updateAll);
window.addEventListener('orientationchange',()=>setTimeout(()=>{updateAll();selfCheck();},250));
if(document.fonts&&document.fonts.ready)document.fonts.ready.then(()=>updateAll());
document.addEventListener('visibilitychange',()=>{
  if(!document.hidden){updateAll();heal(3);}
});
window.addEventListener('load',()=>{
  updateAll();selfCheck();
  heal(10);
});
document.addEventListener('contextmenu',e=>{
  if(e.target.closest&&e.target.closest('.rack-wrap,.kbd'))e.preventDefault();
});

applyPreset('classic');
{const vc=$('#verchip');if(vc)vc.textContent='v'+APP_VERSION;}
})();
