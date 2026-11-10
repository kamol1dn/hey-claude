.pragma library
// Echo, the pocket bat (pixel art). Ported from docs/character-options.html.
// make(K) gets the character kit (ui/qml/HeyClaude/Character/kit.js).
function make(K) {
const { C, TAU, clamp, lerp, mixc, rgba, easeOutCubic, easeInCubic, rnd, pick } = K;
const EB={B:[150,137,130], D:[104,86,100], F:[196,185,176], K:[16,12,12], perch:[66,60,57]};
const GLYPH={
  z:['###','..#','.#.','#..','###'],
  q:['.##.','#..#','..#.','.#..','....','.#..'],
  chk:['....#','...#.','#.#..','.#...'],
  env:['wwwww','wdwdw','wwaww','wwwww']
};
const Echo={
  id:'echo', name:'Echo', kind:'pixel bat',
  line:'A pocket-sized bat with satellite-dish ears. Hears everything, says just enough.',
  sig:'<b>Off:</b> hangs upside down from the island and snoozes. <b>Listening:</b> ears grow with your voice and sound pings fly into them.',
  enter(s,st){ const cs=s.cs; cs.burst=0; if (st==='off') cs.zT=0.4; },
  update(s,dt){
    const st=s.state,t=s.t,cs=s.cs,calm=s.calm;
    let ear=0, flap=0, hz=2;
    if (st==='listening') ear=1+s.L*1.4;
    else if (st==='confused') ear=-1;
    else if (st==='talking') ear=0.3+s.A*1.0;
    else if (st==='happy') ear=1.2;
    else if (st==='thinking') ear=0.6;
    else if (st==='working') ear=0.6;
    if (st==='idle'||st==='thinking') ear+=s.notice*1.4;
    s.fo('ear',ear,0.06);
    if (!calm){
      if (st==='working'){ flap=1; hz=2.6; }
      if (st==='happy' && t<1.3){ flap=1; hz=7; }
      if (st==='sending' && t>0.15 && t<2.2){ flap=1; hz=6; }
    }
    s.fo('flap',flap,0.06);
    cs.wph=(cs.wph||0)+dt*hz;
    if (st==='idle' && !calm){
      cs.tw=(cs.tw??5)-dt;
      if (cs.tw<0){ cs.twT=0.22; cs.tw=6+K.random()*6; }
    }
    if (cs.twT>0) cs.twT-=dt;
    if (st==='listening'){
      cs.arcT=(cs.arcT||0)-dt;
      if (cs.arcT<=0 && s.L>0.12){
        cs.arcT=0.34-0.16*s.L;
        s.spawn({k:'parc',x:-3.5,y:1.5,vx:8.5,max:0.62,side:1});
        s.spawn({k:'parc',x:15.5,y:1.5,vx:-8.5,max:0.62,side:-1});
      }
    }
    if (st==='off'){
      cs.zT=(cs.zT??0.5)-dt;
      if (cs.zT<=0){ cs.zT=1.7; s.spawn({k:'pz',x:9.5,y:8,vx:1.4,vy:-2.2,max:2.4}); }
    }
    if (st==='happy' && !cs.burst && t>0.05){
      cs.burst=1;
      for (let i=0;i<14;i++) s.spawn({k:'ppix',x:5.5,y:3,vx:rnd(-13,13),vy:rnd(-17,-8),g:34,max:rnd(1.1,1.7),col:pick([C.accent,C.ok,C.fg,C.hot])});
    }
    if (st==='talking' && s.onset) cs.hop=0.12;
    if (cs.hop>0) cs.hop-=dt;
  },
  draw(o,s){
    const ctx=o.ctx, p=s.p, cs=s.cs, st=s.state, t=s.t, calm=s.calm;
    const c=Math.max(1,Math.floor((o.box+4*o.dpr)/12));
    let dx=0, dy=0, env=false, chk=0, gone=false;
    if (!calm){
      if (st==='happy' && t<1.2) dy=-Math.round(2*Math.abs(Math.sin(t*Math.PI*2.5)));
      if (st==='working') dy=-Math.round((Math.sin(cs.wph*Math.PI*0.5)+1)*0.5);
      if (st==='confused' && t<0.45) dx=(Math.floor(t*22)%2)?1:-1;
      if ((st==='idle') && s.notice>0.75) dy=-1;
      if (st==='talking' && cs.hop>0) dy=-1;
    }
    if (st==='sending'){
      env = t>0.08 && t<1.2;
      if (!calm){
        if (t>=0.35 && t<1.15){ const u=(t-0.35)/0.8; dx=16*easeInCubic(u); dy=-7*easeInCubic(u)-(u<0.2?1:0); }
        else if (t>=1.15 && t<1.5) gone=true;
        else if (t>=1.5 && t<2.1){ const u=(t-1.5)/0.6; dx=-16*(1-easeOutCubic(u)); dy=-3*(1-u); }
      } else env = t<1.2;
      if (t>2.0) chk=clamp((t-2.0)/0.15)*(1-clamp((t-3.3)/0.3));
    }
    if (st==='happy') chk=0;
    const hang = st==='off';
    const bx=Math.round(o.cx-6*c), by=Math.round(o.cy-5.5*c);
    const ox=bx+Math.round(dx)*c, oy=by+Math.round(dy)*c;
    const P=(x,y,col,a=1)=>{ const yy=hang?11-y:y; ctx.fillStyle=rgba(col,a); ctx.fillRect(ox+x*c,oy+yy*c,c,c); };
    const glyph=(rows,gx,gy,map,a=1,cell=c,orgX=bx,orgY=by)=>{
      for (let r=0;r<rows.length;r++) for (let q=0;q<rows[r].length;q++){
        const ch=rows[r][q]; const col=map[ch]; if (!col) continue;
        ctx.fillStyle=rgba(col,a); ctx.fillRect(orgX+Math.round(gx*c)+q*cell, orgY+Math.round(gy*c)+r*cell, cell, cell);
      }
    };
    /* working satellites, back half */
    const sats=[];
    if (st==='working'){
      for (let i=0;i<3;i++){
        const a=s.T*TAU/2.6+i*TAU/3;
        sats.push({x:Math.round(5.5+7.5*Math.cos(a)), y:Math.round(6+2.6*Math.sin(a)), front:Math.sin(a)>0});
      }
      for (const q of sats) if (!q.front){ ctx.fillStyle=rgba(C.banked,0.9); ctx.fillRect(bx+q.x*c, by+q.y*c, c, c); }
    }
    if (!gone){
      /* perch (big view only) */
      if (hang && !o.small){ ctx.fillStyle=rgba(EB.perch,1); ctx.fillRect(ox-1*c, oy, 14*c, Math.max(1,Math.round(c*0.6))); }
      const B=EB.B, D=EB.D, F=EB.F, K=EB.K, W=C.fg;
      const earGlow = st==='off'? 0 : clamp(0.25+(st==='listening'? s.L*0.95:0)+s.notice*0.5+(st==='talking'? s.A*0.4:0));
      const A = st==='off'? mixc(C.ash,C.banked,0.3) : mixc(C.banked,C.hot,earGlow);
      /* wings */
      let f=0;
      if (p.flap>0.35){ f=[1,2,1,3][Math.floor(cs.wph*4)%4]; }
      else if (st==='talking' && s.A>0.8) f=1;
      else if (st==='listening' && s.L>0.7) f=1;
      else if (st==='happy' && t>1.3) f=0;
      /* wings: membranes with a scalloped edge, always a little open so the silhouette says "bat" */
      const WL={0:[[1,6],[0,7],[1,7],[2,7],[0,8],[2,8],[2,9]],
                1:[[0,5],[1,5],[0,6],[1,6],[1,7],[2,7],[0,7],[2,8]],
                2:[[0,2],[0,3],[1,3],[0,4],[1,4],[1,5],[2,5],[1,6],[2,6],[2,7]],
                3:[[2,7],[1,8],[2,8],[0,9],[1,9],[0,10],[2,9]]}[f];
      for (const [x,y] of WL){ P(x,y,D); P(11-x,y,D); }
      /* ears: big, set wide */
      const e=p.ear||0, tw=cs.twT>0;
      if (e<-0.5){
        P(0,2,B); P(1,2,A); P(2,2,B); P(0,3,B,1);
        P(11,2,B); P(10,2,A); P(9,2,B); P(11,3,B,1);
      } else {
        const up = e>2.1?2: e>0.55?1:0;
        P(1,0,B); P(1,1,B); P(2,1,A); P(1,2,B); P(2,2,A); P(3,2,B);
        if (up>=1){ P(1,-1,B); P(2,0,A); } if (up>=2){ P(1,-2,B); P(2,-1,A); }
        P(10,2,B); P(9,2,A); P(8,2,B);
        if (!tw){ P(10,0,B); P(10,1,B); P(9,1,A); if (up>=1){ P(10,-1,B); P(9,0,A); } if (up>=2){ P(10,-2,B); P(9,-1,A); } }
        else { P(11,1,B); P(10,1,B); }
      }
      /* head, rounded top corners */
      for (let x=2;x<=9;x++) P(x,3,B);
      for (let y=4;y<=6;y++) for (let x=2;x<=9;x++) P(x,y,B);
      /* body: a small furry belly */
      for (let x=3;x<=8;x++) P(x,7,B);
      P(3,8,B); for (let x=4;x<=7;x++) P(x,8,F); P(8,8,B);
      for (let x=4;x<=7;x++) P(x,9,B);
      if (!(st==='sending' && dx!==0) ) { P(4,10,D); P(7,10,D); }
      /* eyes */
      let lx=s.look.x, ly=s.look.y;
      if (st==='thinking'){ lx=0.6; ly=-1; }
      if (st==='working'){ lx=0; ly=1; }
      if (st==='sending' && t<1.2){ lx=1; ly=-0.2; }
      if (hang) ly=-ly;
      let mode='open';
      if (st==='off') mode = s.near>0.6 ? 'peek':'closed';
      else if (st==='happy' && t<2.4) mode='happy';
      else if (st==='sending' && t>2.0 && t<3.3) mode='happy';
      else if (st==='confused') mode='confused';
      if (mode!=='happy' && s.blink>0.5 && mode!=='closed') mode = mode==='peek'?'closed':'blink';
      /* eyes: dark 2x2 with a glint that follows the pointer */
      const eye=(x0,left,kind)=>{
        if (kind==='open'){
          P(x0,4,K); P(x0+1,4,K); P(x0,5,K); P(x0+1,5,K);
          const gx = lx>0.3?1: lx<-0.3?0: (left?1:0);
          const gy = ly>0.45?1:0;
          P(x0+gx,4+gy,W);
        } else if (kind==='closed'||kind==='blink'){ P(x0,5,K); P(x0+1,5,K); }
      };
      if (mode==='open'){ eye(3,true,'open'); eye(7,false,'open'); }
      else if (mode==='closed'||mode==='blink'){ eye(3,true,'closed'); eye(7,false,'closed'); }
      else if (mode==='peek'){ eye(3,true,'closed'); eye(7,false,'open'); }
      else if (mode==='happy'){ P(2,5,K); P(3,4,K); P(4,5,K); P(7,5,K); P(8,4,K); P(9,5,K); }
      else if (mode==='confused'){ eye(3,true,'open'); P(7,5,K); P(8,5,K); P(7,3,K); P(8,4,D); }
      /* mouth, always with one tiny fang */
      let m=0;
      if (st==='talking') m = s.A>0.5?2: s.A>0.12?1:0;
      if (mode==='happy') m=3;
      if (st==='confused') m=4;
      const tongue=[205,96,86];
      if (m===0){ if (st!=='off'){ P(5,6,K); P(6,6,K); P(6,7,W); } }
      else if (m===1){ P(5,6,W); P(6,6,K); P(5,7,K); P(6,7,K); }
      else if (m===2){ P(5,6,W); P(6,6,K); P(5,7,K); P(6,7,tongue); P(5,8,K); P(6,8,K); }
      else if (m===3){ P(4,6,K); P(5,7,K); P(6,7,tongue); P(7,6,K); P(5,6,K); P(6,6,K); }
      else if (m===4){ P(4,7,K); P(5,6,K); P(6,7,K); P(7,6,K); }
      /* envelope */
      if (env) glyph(GLYPH.env, 3.5+Math.round(dx), 9.5+Math.round(dy), {w:C.fg,d:C.dim,a:C.accent},1,c,bx,by);
      /* thinking dots */
      if (st==='thinking'){
        const n=Math.floor(t*2.6)%4;
        [[10,1],[11,0],[12,-1]].forEach(([x,y],i)=>{ ctx.fillStyle=rgba(C.accent,i<n?1:0.18); ctx.fillRect(bx+x*c,by+y*c,c,c); });
      }
      if (st==='confused'){ const a=clamp(t/0.2); glyph(GLYPH.q,9.5,-5,{'#':C.bad},a); }
    }
    for (const q of sats) if (q.front){ ctx.fillStyle=rgba(C.hot,1); ctx.fillRect(bx+q.x*c, by+q.y*c, c, c); }
    if (chk>0) glyph(GLYPH.chk,9,-3,{'#':C.ok},chk);
    /* pixel particles */
    const half=Math.max(1,Math.floor(c/2));
    for (const q of s.parts){
      const u=q.life/q.max, a=1-u*u;
      if (q.k==='parc'){
        const x=Math.round(q.x), y=Math.round(q.y), sd=q.side;
        ctx.fillStyle=rgba(C.fg,a*(0.35+0.6*s.L));
        ctx.fillRect(bx+x*c,by+(y-1)*c,c,c); ctx.fillRect(bx+(x+sd)*c,by+y*c,c,c); ctx.fillRect(bx+x*c,by+(y+1)*c,c,c);
      } else if (q.k==='pz'){
        glyph(GLYPH.z,q.x,q.y,{'#':C.dim},a*0.85,half);
      } else if (q.k==='ppix'){
        ctx.fillStyle=rgba(q.col,a); ctx.fillRect(bx+Math.round(q.x)*c,by+Math.round(q.y)*c,c,c);
      }
    }
  }
};
return Echo;
}
