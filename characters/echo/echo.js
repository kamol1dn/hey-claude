.pragma library
// Echo, the pocket bat (pixel art). The core states are ported from docs/character-options.html;
// the sleep states, woke, approval_needed and the idle antics are Echo's own.
// make(K) gets the character kit (ui/qml/HeyClaude/Character/kit.js).
//
// Everything sits on one pixel grid: 12 cells across (x 0..11), the head in rows 3..6, the
// body in rows 7..10, the ears above. A frame is a *pose* (statePose, then the antic's pose
// hook) rendered cell by cell, so antics bend the same body the states use.
function make(K) {
const { C, TAU, clamp, lerp, mixc, rgba, easeOutCubic, easeInCubic, easeInOut, rnd, pick } = K;
const EB={B:[150,137,130], D:[104,86,100], F:[196,185,176], K:[16,12,12], perch:[66,60,57], E:[78,62,76]};
const TONGUE=[205,96,86], BLUSH=[222,118,116], MOTH=[234,222,190], MOTHB=[168,146,112], SNOW=[236,242,250],
      RAIN=[146,170,206], STAR=[255,240,214], MOON=[240,226,182], DUST=[124,114,108], CLOUD={l:[172,178,192], c:[128,134,150], s:[98,102,120]};
const GLYPH={
  z:['###','..#','.#.','#..','###'],
  q:['.##.','#..#','..#.','.#..','....','.#..'],
  chk:['....#','...#.','#.#..','.#...'],
  env:['wwwww','wdwdw','wwaww','wwwww'],
  bang:['#','#','#','.','#'],
  moth0:['#.#','.b.'], moth1:['.b.','#.#'],
  flake:['.#.','#w#','.#.'],
  moon:['.##.','#...','#...','.##.'],
  note1:['.#..','.##.','.#.#','##..','##..'],
  note2:['.####','.#..#','.#..#','##.##','##.##'],
  cloud:['..llll.ll..','.lcccclcccl','.sssssssss.'],
  mug:['####.','#..##','#..##','#..#.','####.'],
  plant:['.#.#.','#.#.#','.###.','#####','.###.','.###.','.###.'],
  smoth:['#.#','.#.'],
  d1:['.#.','##.','.#.','.#.','###'], d2:['##.','..#','.#.','#..','###'], d3:['##.','..#','.#.','..#','##.'],
  d4:['#.#','#.#','###','..#','..#'], d5:['###','#..','##.','..#','##.'], d6:['.##','#..','###','#.#','###']
};
// wings, left side (the right one is mirrored, x -> 11 - x); [x, y, colour key = D]
const WING={
  0:[[1,6],[0,7],[1,7],[2,7],[0,8],[2,8],[2,9]],
  1:[[0,5],[1,5],[0,6],[1,6],[1,7],[2,7],[0,7],[2,8]],
  2:[[0,2],[0,3],[1,3],[0,4],[1,4],[1,5],[2,5],[1,6],[2,6],[2,7]],
  3:[[2,7],[1,8],[2,8],[0,9],[1,9],[0,10],[2,9]],
  fold:[[2,7],[1,8],[2,8],[2,9]],
  // wrapped round the body like a cloak (asleep, cupping a moth, hiding)
  // (a 4th 'L' = the left wing only: it closes over the right one, its edge is the seam)
  wrap:[[2,6,'E'],[2,7],[3,7],[4,7],[5,7],[1,8],[2,8],[3,8],[4,8],[5,8],[2,9],[3,9],[4,9],[5,9],[3,10],[4,10],[5,10],
        [6,7,'E','L'],[6,8,'E','L'],[5,9,'E','L'],[5,10,'E','L']],
  wrap2:[[2,6,'E'],[1,7],[2,7],[3,7],[4,7],[5,7],[1,8],[2,8],[3,8],[4,8],[5,8],[1,9],[2,9],[3,9],[4,9],[5,9],[3,10],[4,10],[5,10],
        [6,7,'E','L'],[6,8,'E','L'],[5,9,'E','L'],[5,10,'E','L']],
  hide:[[1,5,'E'],[1,6],[2,6],[3,6],[4,6],[5,6],[1,7],[2,7],[3,7],[4,7],[5,7],[1,8],[2,8],[3,8],[4,8],[5,8],[2,9],[3,9],[4,9],[5,9],[3,10],[4,10],[5,10],
        [6,6,'E','L'],[6,7,'E','L'],[6,8,'E','L'],[5,9,'E','L'],[5,10,'E','L']],
  // brought across the chest to be groomed
  face:[[0,4,'E'],[0,5],[1,5],[1,6],[2,6],[3,6],[2,7],[3,7],[4,7],[3,8]],
  // held up over the head like an umbrella
  umbrella:[[2,6],[1,5],[1,4],[0,3],[0,2],[0,1],[0,0],[1,0],[2,0],[3,0],[4,0],[5,0],[6,0],[7,0],[8,0],[9,0],
            [1,-1],[2,-1],[3,-1],[4,-1],[5,-1],[6,-1],[7,-1],[3,1],[6,1],[9,1],[3,0,'E'],[6,0,'E']]
};
const FRONT={wrap:1, wrap2:1, hide:1, face:1, umbrella:1};
// ears, left side, mirrored likewise; A = the inner ear (it glows)
const EAR={
  up0:[[1,0,'B'],[1,1,'B'],[2,1,'A'],[1,2,'B'],[2,2,'A'],[3,2,'B']],
  up1:[[1,0,'B'],[1,1,'B'],[2,1,'A'],[1,2,'B'],[2,2,'A'],[3,2,'B'],[1,-1,'B'],[2,0,'A']],
  up2:[[1,0,'B'],[1,1,'B'],[2,1,'A'],[1,2,'B'],[2,2,'A'],[3,2,'B'],[1,-1,'B'],[2,0,'A'],[1,-2,'B'],[2,-1,'A']],
  twitch:[[1,2,'B'],[2,2,'A'],[3,2,'B'],[0,1,'B'],[1,1,'B']],
  down:[[0,2,'B'],[1,2,'A'],[2,2,'B'],[0,3,'B']],
  droop:[[0,0,'B'],[0,1,'B'],[1,1,'A'],[1,2,'B'],[2,2,'A'],[3,2,'B']],
  fold:[[1,1,'B'],[0,2,'B'],[1,2,'B'],[2,2,'D'],[3,2,'B']]
};
const seg=(u,a,b)=>clamp((u-a)/(b-a));
const hsh=(n)=>{ const x=Math.sin(n*127.1+311.7)*43758.5453; return x-Math.floor(x); };

// The pixel grid in canvas px. inBox: we're between o.begin() and o.end() (an antic's
// under / draw / exit), so undo the box transform and draw on the same grid as the body.
function grid(o, inBox){
  const ctx=o.ctx, c=Math.max(1,Math.floor((o.box+4*o.dpr)/12));
  const bx=Math.round(o.cx-6*c), by=Math.round(o.cy-5.5*c), half=Math.max(1,Math.floor(c/2));
  if (inBox){ ctx.save(); ctx.scale(1/o.k,1/o.k); ctx.translate(-o.cx,-o.cy); }
  return {
    ctx, c, bx, by, half, small:o.small,
    // one cell at (x, y) in cells (snapped to the grid)
    px(x,y,col,a=1){ if (a<=0.004) return; ctx.fillStyle=rgba(col,a); ctx.fillRect(bx+Math.round(x)*c, by+Math.round(y)*c, c, c); },
    // one half cell (snapped to half cells): sparks, flakes, stars
    hp(x,y,col,a=1){ if (a<=0.004) return; ctx.fillStyle=rgba(col,a); ctx.fillRect(bx+Math.round(Math.round(x*2)*c/2), by+Math.round(Math.round(y*2)*c/2), half, half); },
    // a glyph of full (cell=1) or half (cell=0.5) cells, top-left at (gx, gy) in cells
    glyph(rows,gx,gy,map,a=1,cell=1){
      if (a<=0.004) return;
      const n=cell<1?half:c, x0=bx+Math.round(Math.round(gx*2)*c/2), y0=by+Math.round(Math.round(gy*2)*c/2);
      for (let r=0;r<rows.length;r++) for (let k=0;k<rows[r].length;k++){
        const col=map[rows[r][k]]; if (!col) continue;
        ctx.fillStyle=rgba(col,a); ctx.fillRect(x0+k*n, y0+r*n, n, n);
      }
    },
    done(){ if (inBox) ctx.restore(); }
  };
}
// cells on an arc of radius r (cells) round (cx, cy), from angle a0 to a1
function arcCells(cx,cy,r,a0,a1){
  const out=[], seen={}, n=Math.max(3,Math.ceil(Math.abs(a1-a0)*r*1.6));
  for (let i=0;i<=n;i++){
    const a=a0+(a1-a0)*i/n, x=Math.round(cx+r*Math.cos(a)), y=Math.round(cy+r*Math.sin(a)), key=x+','+y;
    if (!seen[key]){ seen[key]=1; out.push([x,y]); }
  }
  return out;
}
// a sonar ping: the arc on one side (side 1 = right, -1 = left), tilt < 0 aims it up
function sonarArc(g,cx,cy,r,side,spread,col,a,tilt=0){
  const mid = side>0 ? tilt : Math.PI-tilt;
  for (const [x,y] of arcCells(cx,cy,r,mid-spread,mid+spread)) g.px(x,y,col,a);
}
// the shared "caught!": a little ! pops beside the right ear
function bang(g,u){
  const a=1-u*u, lift=u<0.25?0.5:0;
  g.glyph(GLYPH.bang,11.5,-0.5-lift,{'#':C.hot},a,0.5);
}
const pose=(s)=>s.cs._q||{dx:0,dy:0,hdx:0,hdy:0,el:'up0',er:'up0'};

// ---- the pose of a state -------------------------------------------------------------
function statePose(s){
  const p=s.p, cs=s.cs, st=s.state, t=s.t, calm=s.calm;
  const q={dx:0,dy:0,hdx:0,hdy:0,hang:false,perch:false,gone:false,env:false,chk:0,
           wl:0,wr:0,el:'up0',er:'up0',eyes:'open',eyeL:null,eyeR:null,lx:s.look.x,ly:s.look.y,
           mouth:0,glow:0,ash:false,feet:true,blush:0,cheeks:false,ask:0,noBlink:false};
  if (!calm){
    if (st==='happy' && t<1.2) q.dy=-Math.round(2*Math.abs(Math.sin(t*Math.PI*2.5)));
    if (st==='working') q.dy=-Math.round((Math.sin(cs.wph*Math.PI*0.5)+1)*0.5);
    if (st==='confused' && t<0.45) q.dx=(Math.floor(t*22)%2)?1:-1;
    if (st==='idle' && s.notice>0.75) q.dy=-1;
    if (st==='talking' && cs.hop>0) q.dy=-1;
  }
  if (st==='sending'){
    q.env = t>0.08 && t<1.2;
    if (!calm){
      if (t>=0.35 && t<1.15){ const u=(t-0.35)/0.8; q.dx=16*easeInCubic(u); q.dy=-7*easeInCubic(u)-(u<0.2?1:0); }
      else if (t>=1.15 && t<1.5) q.gone=true;
      else if (t>=1.5 && t<2.1){ const u=(t-1.5)/0.6; q.dx=-16*(1-easeOutCubic(u)); q.dy=-3*(1-u); }
    } else q.env = t<1.2;
    if (t>2.0) q.chk=clamp((t-2.0)/0.15)*(1-clamp((t-3.3)/0.3));
  }
  q.hang = q.perch = q.ash = st==='off';
  q.glow = st==='off'? 0 : clamp(0.25+(st==='listening'? s.L*0.95:0)+s.notice*0.5+(st==='talking'? s.A*0.4:0));
  let f=0;
  if (p.flap>0.35) f=[1,2,1,3][Math.floor(cs.wph*4)%4];
  else if (st==='talking' && s.A>0.8) f=1;
  else if (st==='listening' && s.L>0.7) f=1;
  q.wl=q.wr=f;
  const e=p.ear||0;
  if (e<-0.5) q.el=q.er='down';
  else { const up=e>2.1?2: e>0.55?1:0; q.el='up'+up; q.er=cs.twT>0?'twitch':'up'+up; }
  if (st==='thinking'){ q.lx=0.6; q.ly=-1; }
  if (st==='working'){ q.lx=0; q.ly=1; }
  if (st==='sending' && t<1.2){ q.lx=1; q.ly=-0.2; }
  if (st==='off') q.eyes = s.near>0.6 ? 'peek':'closed';
  else if (st==='happy' && t<2.4) q.eyes='happy';
  else if (st==='sending' && t>2.0 && t<3.3) q.eyes='happy';
  else if (st==='confused') q.eyes='confused';
  let m=0;
  if (st==='talking') m = s.A>0.5?2: s.A>0.12?1:0;
  if (q.eyes==='happy') m=3;
  if (st==='confused') m=4;
  if (st==='off') m='none';
  q.mouth=m;
  if (st==='sending' && q.dx!==0) q.feet=false;

  /* drowsy: heavy lids, ears gone soft; every few breaths the lids sink, the head dips, and it catches itself */
  if (st==='drowsy'){
    q.el=q.er='droop'; q.glow=0.1+s.notice*0.6; q.eyes='heavy';
    if (s.notice>0.4){ q.el=q.er='up0'; q.eyes='open'; }
    else if (!calm){
      const P=7, ph=t%P, n=Math.floor(t/P);
      if (ph>3.8 && ph<5.6){ q.eyes='closed'; q.noBlink=true; if (ph>4.7) q.hdy=1; }
      else if (ph>=5.6 && ph<6.1){ q.eyes='open'; q.el=q.er='up0'; }
      if (n%3===1 && ph>0.8 && ph<2.3){ q.mouth='yawn'; q.eyes='squeeze'; q.noBlink=true; }
    }
  }
  /* asleep: tucked into its wings like a cloak, ears folded, breathing slowly; a pointer nearby lifts one ear */
  if (st==='asleep' || (st==='waking' && (calm ? t<0.5 : t<0.22))) sleepPose(q, s, calm || st==='waking' ? 0 : t);
  if (st==='waking' && !(calm ? t<0.5 : t<0.22)){
    q.glow=0.35;
    if (calm){ q.el=q.er='up0'; }
    else if (t<0.38){ q.wl=q.wr='wrap'; q.feet=false; q.eyes='closed'; q.noBlink=true; q.el=q.er='up1'; }    // ears first
    else if (t<1.0){                                                                                          // the big stretch
      q.wl=q.wr=2; q.dy=-1; q.eyes='squeeze'; q.noBlink=true; q.el=q.er='up2';
      q.mouth = t>0.48 && t<0.92 ? 'yawn' : 'o';
    }
    else if (t<1.24){ q.eyes='closed'; q.noBlink=true; q.dx=(Math.floor(t*26)%2)?1:-1; q.el=q.er=(Math.floor(t*26)%2)?'twitch':'up0'; }  // a fur shake
    else { q.eyes='open'; q.lx = t<1.42 ? -0.9 : 0.9; q.ly=0; q.el='up1'; q.er = t<1.42 ? 'up0' : 'up1'; }     // where was I?
  }
  /* woke: the catch. Ears shoot up and light, a hop, eyes wide on you */
  if (st==='woke'){
    q.el=q.er='up2'; q.glow=clamp(1.2-t*0.9); q.eyes = t<0.2 ? 'wide' : 'open'; q.noBlink=true;
    q.lx=s.look.x*0.5; q.ly=0; q.mouth = t<0.3 ? 'o' : 0;
    if (!calm){ if (t<0.16) q.dy=-1; q.wl=q.wr = t<0.3 ? 1 : 0; }
  }
  /* approval_needed: a raised wing ("excuse me?"), a tilt of the ears, a small polite question */
  if (st==='approval_needed'){
    q.el=q.er='up1'; q.wr=2; q.glow=0.5+s.notice*0.4; q.eyes='open'; q.lx=s.look.x*0.6; q.ly=0.2; q.mouth=0; q.ask=1;
    if (!calm){
      const ph=t%4.4;
      if (ph<2.2){ q.el='up2'; q.er='up0'; }
      if (ph>3.5 && ph<3.75) q.er='twitch';
      q.ask = 1 + ((Math.floor(t/0.75)%2) ? 1 : 0);
    }
  }
  return q;
}
function sleepPose(q, s, t){
  q.el=q.er='droop'; q.eyes='closed'; q.noBlink=true; q.mouth='none'; q.hdy=1; q.glow=0; q.ash=true; q.feet=false;
  q.wl=q.wr = t>0 && (t%3.6)<1.6 ? 'wrap2' : 'wrap';
  if (s.near>0.6) q.er='up0';
}

// ---- the idle antics ----------------------------------------------------------------
// Each: start / end (stash what the exit needs in s.cs.ax), pose(s, a, q) bends the body,
// under / draw add props on the same pixel grid, exit is the short "caught!" over the real state.
const hopIn=(q,t)=>{ q.dy=-1; q.wl=q.wr=2; q.el=q.er='up1'; q.eyes='open'; };
function hangPose(s,a,q){
  const t=a.t, D=a.dur;
  if (t<0.2){ hopIn(q,t); return false; }
  if (t>D-0.4){ const k=t-(D-0.4); q.dy = k<0.18 ? -1 : 0; q.wl=q.wr = k<0.22 ? 2 : 0; q.eyes='open'; return false; }
  q.hang=true; q.perch=true; q.ash=false;
  if (t<0.45) q.wl=q.wr=1;
  return true;
}
const zs=(g,t,x,y,n,rise,a)=>{
  for (let i=0;i<n;i++){
    const k=((t/1.7)+i/n)%1;
    g.glyph(GLYPH.z, x+k*1.4+i*0.2, y-k*rise, {'#':C.dim}, a*Math.sin(k*Math.PI)*0.9, 0.5);
  }
};

const antics={
  /* hangs upside down from the island's edge, looks around, stretches a wing, flips back */
  hang:{
    pose(s,a,q){
      if (!hangPose(s,a,q)) return;
      const t=a.t, u=a.u;
      q.lx=[-0.9,0,0.9,0.2][Math.floor(t/1.3)%4]; q.ly=0;
      if (u>0.42 && u<0.58){ q.wl=1; q.mouth = u>0.45 && u<0.53 ? 'yawn' : 0; q.eyes = q.mouth==='yawn' ? 'squeeze' : 'open'; }
      if (u>0.66 && u<0.7) q.er='twitch';
      if (u>0.74 && u<0.86){ q.eyes='happy'; q.mouth=3; }
    },
    end(s,a,caught){ s.cs.ax={}; },
    exitDur:0.28,
    exit(o,s,c,u){
      const g=grid(o,true);
      if (!o.small) for (let i=0;i<3;i++) g.hp([1.5,5.5,9.5][i], -0.4+u*(2.4+i*0.5), DUST, (1-u)*0.9);
      g.hp(-0.5,4+u*2,C.dim,(1-u)*0.6); g.hp(12,4+u*2,C.dim,(1-u)*0.6);
      bang(g,u); g.done();
    }
  },

  /* a moth comes to visit: Echo tracks it, cups it in its wings, peeks, and lets it go */
  moth:{
    start(s,a){ a.m={x:-2,y:0}; },
    update(s,dt,a){
      const t=a.t, u=a.u, m=a.m;
      if (u<0.56){
        const lx=5.5+4.4*Math.sin(t*1.7+0.3), ly=0.4+1.2*Math.sin(t*3.1)+0.6*Math.sin(t*1.3);
        const k=easeInOut(seg(u,0,0.14));
        m.x=lerp(-2.5,lx,k); m.y=lerp(-1.5,ly,k);
      } else if (u<0.6){ const k=seg(u,0.56,0.6); m.x=lerp(m.x,5.5,k); m.y=lerp(m.y,6.5,k); }
      else if (u<0.8){ m.x=5.5; m.y=7.5; }
      else { const k=seg(u,0.8,1); m.x=5.5+k*8+Math.sin(t*9)*0.6; m.y=7-k*9; }
    },
    pose(s,a,q){
      const u=a.u, m=a.m, t=a.t;
      const d=Math.hypot(m.x-5.5,m.y-4);
      q.lx=clamp((m.x-5.5)/3.5,-1,1); q.ly=clamp((m.y-4)/2.5,-1,1);
      q.el=q.er = d<3.5 ? 'up2' : 'up1'; q.glow=0.35+clamp(1-d/7)*0.6;
      if (u>0.3 && u<0.56 && Math.floor(t*2)%3===0) q.mouth='o';
      if (u>=0.56 && u<0.62){ q.dy=-1; q.wl=q.wr=2; q.eyes='wide'; q.mouth='o'; }
      else if (u>=0.62 && u<0.8){ q.wl=q.wr='wrap'; q.feet=false; q.lx=0; q.ly=1; q.el=q.er='up1';
        q.eyes = u>0.7 && u<0.76 ? 'happy' : 'open'; q.mouth = q.eyes==='happy' ? 3 : 0; }
      else if (u>=0.8){ q.wl=q.wr = u<0.86 ? 1 : 0; q.eyes = u<0.9 ? 'open' : 'happy'; q.mouth = u<0.9 ? 0 : 3; q.lx=1; q.ly=-1; }
    },
    draw(o,s,a){
      const u=a.u, m=a.m, g=grid(o,true);
      if (u>=0.62 && u<0.8){ if (Math.floor(a.t*7)%3!==0) g.hp(5.75+(Math.floor(a.t*5)%2)*0.5, 8, MOTH, 0.9); }   // something wriggles inside
      else if (!(u>=0.6 && u<0.62)) g.glyph(Math.floor(a.t*11)%2 ? GLYPH.moth0 : GLYPH.moth1, m.x-0.75, m.y-0.5, {'#':MOTH,'b':MOTHB}, 1, 0.5);
      g.done();
    },
    end(s,a,caught){ s.cs.ax={x:a.m.x, y:a.m.y, hidden:a.u>=0.6 && a.u<0.8}; },
    exitDur:0.3,
    exit(o,s,c,u){
      const g=grid(o,true), ax=s.cs.ax||{x:5.5,y:1};
      const x0 = ax.hidden ? 5.5 : ax.x, y0 = ax.hidden ? 7 : ax.y;
      g.glyph(Math.floor(u*8)%2 ? GLYPH.moth0 : GLYPH.moth1, x0-0.75+u*6, y0-0.5-u*6, {'#':MOTH,'b':MOTHB}, 1-u*u, 0.5);
      g.hp(x0+u*3, y0-u*3, MOTH, 0.4*(1-u));
      g.done();
    }
  },

  /* echolocation: three chirps; the echoes come back into the ears from one side, then the other */
  ping:{
    start(s,a){ a.k=a.dur/5; },
    pose(s,a,q){
      const t=a.t/a.k;
      const P=[0.45,1.85,3.25];
      for (let i=0;i<3;i++){
        const d=t-P[i];
        if (d>-0.25 && d<0.1){ q.mouth='o'; q.el=q.er='up1'; }
        if (d>1.0 && d<1.45){                                   // the echo arrives
          const sd=[0,-1,1][i];
          q.glow=0.9; if (sd<=0) q.el='up2'; if (sd>=0) q.er='up2';
          q.lx=sd*0.9; q.ly=0;
        }
      }
      if (t>4.55){ q.eyes='happy'; q.mouth=3; }
    },
    draw(o,s,a){
      const g=grid(o,true), t=a.t/a.k, P=[0.45,1.85,3.25], sides=[[1,-1],[-1],[1]];
      const q=pose(s), cx=5.5+q.dx, cy=5+q.dy;
      for (let i=0;i<3;i++){
        const d=t-P[i];
        if (d>0 && d<0.7){ const k=d/0.7; for (const sd of [1,-1]) sonarArc(g,cx,cy,4+3*easeOutCubic(k),sd,0.55,C.hot,(1-k*k)*0.95); }
        if (d>0.65 && d<1.1){ const k=(d-0.65)/0.45; for (const sd of sides[i]) sonarArc(g,cx,cy-1.5,7-3.2*k,sd,0.4,C.fg,0.25+0.5*k,-0.55); }
      }
      g.done();
    },
    end(s,a){ s.cs.ax={}; },
    exitDur:0.25,
    exit(o,s,c,u){
      const g=grid(o,true);
      for (const sd of [1,-1]) sonarArc(g,5.5,5,4+u*2,sd,0.45,C.hot,(1-u)*0.6);
      g.done();
    }
  },

  /* chirps once, the echo comes back far too loud, and Echo jumps out of its fur */
  spooked:{
    pose(s,a,q){
      const u=a.u;
      if (u<0.14){ q.el=q.er='up1'; }
      else if (u<0.24){ q.mouth='o'; q.el=q.er='up1'; q.lx=0.9; }
      else if (u<0.4){ q.el=q.er='up2'; q.lx=1; q.glow=0.4+seg(u,0.3,0.4)*0.6; }
      else if (u<0.53){ q.dy = u<0.46 ? -2 : -1; q.el=q.er='up2'; q.eyes='wide'; q.noBlink=true; q.wl=q.wr=2; q.mouth='o'; q.glow=1; }
      else if (u<0.76){ q.wl=q.wr='hide'; q.feet=false; q.el=q.er='down'; q.lx=1; q.ly=0; q.eyes='open'; q.mouth='none'; }
      else { q.lx=-0.6; q.ly=1; q.blush=seg(u,0.76,0.82)*0.9; q.mouth=4; q.el='up0'; q.er = u>0.86 && u<0.9 ? 'twitch':'up0'; }
    },
    draw(o,s,a){
      const g=grid(o,true), u=a.u;
      if (u>0.14 && u<0.28){ const k=seg(u,0.14,0.28); sonarArc(g,5.5,5,4+3*k,1,0.45,C.hot,1-k); }
      if (u>0.3 && u<0.42){ const k=seg(u,0.3,0.42);
        sonarArc(g,5.5,5,7.5-3.5*k,1,0.85,C.whitehot,0.5+0.5*k); sonarArc(g,5.5,5,9-3.5*k,1,0.7,C.hot,0.5*k); }
      if (u>0.4 && u<0.56){ const k=seg(u,0.4,0.56), q=pose(s);
        g.glyph(GLYPH.bang,11.5,-1+q.dy,{'#':C.hot},1-k*k,0.5);
        if (k<0.5){ g.hp(-0.5,3+q.dy,C.fg,0.7*(1-k*2)); g.hp(12,3+q.dy,C.fg,0.7*(1-k*2)); g.hp(-0.5,1+q.dy,C.fg,0.5*(1-k*2)); g.hp(12,1+q.dy,C.fg,0.5*(1-k*2)); } }
      g.done();
    },
    end(s,a){ s.cs.ax={}; },
    exitDur:0.3,
    exit(o,s,c,u){
      const g=grid(o,true);
      g.px(2,6,BLUSH,0.8*(1-u)); g.px(9,6,BLUSH,0.8*(1-u));
      bang(g,u); g.done();
    }
  },

  /* grooms one wing, then the other, then a good fluffy shake */
  groom:{
    pose(s,a,q){
      const u=a.u, t=a.t, lick=Math.floor(t/0.2)%2===0;
      q.el=q.er='up0'; q.glow=0.2;
      if (u<0.06){ q.wl='face'; q.lx=-1; q.ly=1; }
      else if (u<0.42){ q.wl='face'; q.eyes='closed'; q.noBlink=true; q.mouth = lick ? 'lickL' : 0; q.el = Math.floor(t/1.1)%2 ? 'up0' : 'droop'; }
      else if (u<0.48){ q.eyes='open'; q.lx=1; q.ly=1; }
      else if (u<0.84){ q.wr='face'; q.eyes='closed'; q.noBlink=true; q.mouth = lick ? 'lickR' : 0; q.er = Math.floor(t/1.1)%2 ? 'up0' : 'droop'; }
      else if (u<0.93){ const j=Math.floor(t*24)%2; q.dx=j?1:-1; q.eyes='squeeze'; q.noBlink=true; q.el=j?'twitch':'up1'; q.er=j?'up1':'twitch'; q.wl=q.wr=j?1:0; }
      else { q.eyes='happy'; q.mouth=3; q.el=q.er='up1'; }
    },
    draw(o,s,a){
      const u=a.u; if (u<0.84 || u>0.99) return;
      const g=grid(o,true), k=seg(u,0.84,0.99);
      for (let i=0;i<7;i++){
        const ang=-Math.PI*0.95+i*Math.PI*0.32, v=3.2+hsh(i)*2.2;
        const x=5.5+Math.cos(ang)*(3.5+v*k), y=6+Math.sin(ang)*(3+v*k)+k*k*4;
        g.hp(x,y,i%3===0?EB.F:EB.B,(1-k)*0.95);
      }
      g.done();
    },
    end(s,a){ s.cs.ax={}; },
    exitDur:0.25,
    exit(o,s,c,u){
      const g=grid(o,true);
      for (let i=0;i<4;i++) g.hp([0,11.5,0.5,11][i], 5+i*0.8+u*2.5, EB.B, (1-u)*0.9);
      bang(g,u); g.done();
    }
  },

  /* desktop, at night: the stars come out, Echo counts them one by one, then a shooting star */
  stars:{
    pose(s,a,q){
      const t=a.t, u=a.u, ST=STARS;
      q.glow=0.3; q.ly=-1;
      const n=countAt(t);
      if (n>=0){ const st=ST[ORDER[n]]; q.lx=clamp((st[0]-5.5)/4,-1,1); if (Math.floor((t-COUNT0)/COUNT_DT*2)%2===0){ if (st[0]<5.5) q.el='up2'; else q.er='up2'; } }
      if (u>0.66 && u<0.74){ const k=seg(u,0.66,0.74); q.lx=lerp(1,-1,k); q.el=q.er='up2'; q.eyes='wide'; q.mouth='o'; }
      if (u>=0.74 && u<0.82){ q.el=q.er='up1'; q.lx=-0.5; q.mouth=4; }       // where was I?
      if (u>=0.82){ q.eyes='happy'; q.mouth=3; q.el=q.er='up1'; }
    },
    under(o,s,a){
      const g=grid(o,true), t=a.t, u=a.u, fade=1-seg(u,0.9,1);
      const n=countAt(t);
      g.glyph(GLYPH.moon,14,-3.5,{'#':MOON},seg(t,0,1.2)*fade*0.9,0.5);
      for (let i=0;i<STARS.length;i++){
        const [x,y]=STARS[i], on=seg(t,0.2+i*0.17,0.6+i*0.17), tw=0.55+0.45*Math.sin(t*2.3+i*1.7);
        const counted=ORDER.indexOf(i), lit = n>=0 && counted>=0 && counted<=n;
        const hot = n>=0 && ORDER[n]===i;
        const a0=on*fade*(hot?1:lit?0.85:0.45*tw);
        g.hp(x,y,hot?C.whitehot:STAR,a0);
        if (hot || (lit && tw>0.8)){ const ar=a0*(hot?0.75:0.35); g.hp(x-0.5,y,STAR,ar); g.hp(x+0.5,y,STAR,ar); g.hp(x,y-0.5,STAR,ar); g.hp(x,y+0.5,STAR,ar); }
      }
      if (u>0.66 && u<0.76){                                        // a shooting star
        const k=seg(u,0.66,0.76), x=lerp(17,-4,k), y=lerp(-3.5,0.5,k);
        for (let j=0;j<6;j++) g.hp(x+j*0.5*1.05,y-j*0.5*0.2,j===0?C.whitehot:STAR,(1-j/6)*(1-k*0.5));
      }
      g.done();
    },
    draw(o,s,a){
      const g=grid(o,true), t=a.t, u=a.u, n=countAt(t), q=pose(s);
      if (n>=0 && u<0.66) g.glyph(GLYPH['d'+(n+1)],12.5,1+q.dy,{'#':C.dim},0.9,0.5);
      else if (u>=0.7 && u<0.82) g.glyph(GLYPH.q,12.5,0.5+q.dy,{'#':C.dim},0.9,0.5);
      g.done();
    },
    end(s,a){ s.cs.ax={}; },
    exitDur:0.3,
    exit(o,s,c,u){
      const g=grid(o,true);
      for (let i=0;i<STARS.length;i++) g.hp(STARS[i][0],STARS[i][1]-u*0.5,STAR,(1-u)*0.6);
      bang(g,u); g.done();
    }
  },

  /* winter: one snowflake drifts down, lands on an ear, tickles... achoo */
  snow:{
    start(s,a){ a.f={x:9,y:-3}; },
    update(s,dt,a){
      const u=a.u, t=a.t, f=a.f;
      if (u<0.45){ const k=easeInOut(seg(u,0,0.45)); f.x=lerp(8.5,10.25,k)+Math.sin(t*2.4)*0.9*(1-k); f.y=lerp(-3.2,0,k); }
      else if (u<0.76){ f.x=10.25; f.y = (u>0.5 && u<0.54) ? -0.5 : 0; }
      else { const k=seg(u,0.76,1); f.x=10.25+k*3; f.y=-k*2+k*k*5; }
    },
    pose(s,a,q){
      const u=a.u;
      q.er='up0'; q.el='up1'; q.glow=0.3;
      if (u<0.45){ q.lx=0.9; q.ly=-0.5; }
      else if (u<0.66){ q.lx=1; q.ly=0; if (u>0.5 && u<0.54) q.er='twitch'; if (u>0.58) q.mouth='o'; }
      else if (u<0.74){ q.eyes='squeeze'; q.noBlink=true; q.mouth='o'; q.dy=-1; q.hdy=0; }   // ah... ah...
      else if (u<0.8){ q.eyes='squeeze'; q.noBlink=true; q.mouth='yawn'; q.dy=u<0.77?1:0; q.el=q.er='down'; q.wl=q.wr=1; }   // CHOO
      else { q.eyes = u<0.86 ? 'blink' : 'happy'; q.mouth = u<0.86 ? 0 : 3; q.el=q.er='up1'; }
    },
    under(o,s,a){
      const g=grid(o,true), t=a.t;
      for (let i=0;i<6;i++){                                       // a light fall behind
        const x=(hsh(i+3)*13-0.5), sp=0.9+hsh(i+11)*0.7, y=((t*sp+hsh(i+7)*12)%13)-1.5;
        g.hp(x+Math.sin(t*1.3+i)*0.4,y,SNOW,0.55);
      }
      g.done();
    },
    draw(o,s,a){
      const g=grid(o,true), f=a.f, u=a.u;
      if (u<0.76) g.glyph(GLYPH.flake,f.x-0.75,f.y-0.75,{'#':SNOW,'w':[255,255,255]},0.95,0.5);
      else if (u<0.95){                                            // it bursts into glitter
        const k=seg(u,0.76,0.95);
        for (let i=0;i<6;i++){ const ang=i*TAU/6+0.4; g.hp(10.25+Math.cos(ang)*k*2.6,Math.sin(ang)*k*2.2+k*k,SNOW,1-k); }
        g.hp(6+k*1.5,8+k,C.fg,0.5*(1-k)); g.hp(5+k*0.5,8.5+k,C.fg,0.4*(1-k));
      }
      g.done();
    },
    end(s,a){ s.cs.ax={x:a.f.x, y:a.f.y, on:a.u<0.76}; },
    exitDur:0.3,
    exit(o,s,c,u){
      const g=grid(o,true), ax=s.cs.ax||{x:10,y:-1,on:true};
      if (ax.on){ g.glyph(GLYPH.flake,ax.x-0.75+u*1.5,ax.y-0.75+u*2.5,{'#':SNOW,'w':[255,255,255]},(1-u)*0.9,0.5); }
      bang(g,u); g.done();
    }
  },

  /* only after a long quiet spell: hanging from the edge, it nods off, slips a foot, and grabs on again */
  doze:{
    pose(s,a,q){
      if (!hangPose(s,a,q)) return;
      const u=a.u, t=a.t;
      q.lx=0; q.ly=0;
      if (u<0.24){ q.eyes='heavy'; q.el=q.er='up0'; }
      else if (u<0.56){ q.eyes='closed'; q.noBlink=true; q.el=q.er='droop'; q.glow=0.1; q.mouth='none'; }
      else if (u<0.68){ q.eyes='closed'; q.noBlink=true; q.el=q.er='droop'; q.glow=0.1; q.dx=1; q.footL=false; q.wl=3; q.wr=0; q.mouth='none'; }   // one foot lets go
      else if (u<0.74){ q.eyes='wide'; q.noBlink=true; q.el=q.er='up2'; q.wl=q.wr=Math.floor(t*14)%2?2:1; q.mouth='o'; q.glow=1; }            // !
      else { q.lx=Math.floor(t/0.45)%2?0.9:-0.9; q.blush=0.8*(1-seg(u,0.86,0.92)); q.el=q.er='up0'; }
    },
    draw(o,s,a){
      const u=a.u; if (u<0.26 || u>0.68) return;
      const g=grid(o,true), q=pose(s);
      zs(g,a.t,9.5+q.dx,5.5,2,5,seg(u,0.26,0.32)*(1-seg(u,0.64,0.68)));
      g.done();
    },
    end(s,a){ s.cs.ax={z:a.u>0.26 && a.u<0.68}; },
    exitDur:0.28,
    exit(o,s,c,u){
      const g=grid(o,true), ax=s.cs.ax||{};
      if (ax.z) for (let i=0;i<4;i++) g.hp(10+Math.cos(i*1.6)*u*2.5, 3+Math.sin(i*1.6)*u*2.5, C.dim, 1-u);
      bang(g,u); g.done();
    }
  },

  /* desktop: two slow pings paint a sonar map of the dark (a mug, a plant, a moth that moves) */
  sonar:{
    pose(s,a,q){
      const t=a.t, u=a.u;
      q.el=q.er='up2'; q.glow=0.45;
      if (u<0.08) { q.eyes='open'; }
      else if (u<0.72){ q.eyes='closed'; q.noBlink=true; q.mouth = (u>0.08 && u<0.13) || (u>0.46 && u<0.51) ? 'o' : 0;
        const r1=ringR(t-a.dur*0.1), r2=ringR(t-a.dur*0.48); if ((r1>8 && r1<12) || (r2>8 && r2<12)) q.glow=1; }
      else { q.eyes='open'; q.lx=1; q.ly=-0.5; q.mouth = u>0.8 ? 3 : 0; if (u>0.78 && u<0.86) q.eyes='happy'; }
    },
    under(o,s,a){
      const g=grid(o,true), t=a.t, D=a.dur, cx=5.5, cy=4.5;
      const T1=D*0.1, T2=D*0.48;
      for (const T0 of [T1,T2]){
        const r=ringR(t-T0); if (r<=0 || r>14) continue;
        const k=r/14;
        for (const [x,y] of arcCells(cx,cy,r,0,TAU)) if (x>=-6 && x<=17 && y>=-4 && y<=12) g.hp(x+0.25,y+0.25,C.hot,0.5*(1-k));
      }
      const fade=1-seg(a.u,0.9,1);
      const dots=(rows,ox,oy,a0,col)=>{ if (a0<=0) return;
        for (let r=0;r<rows.length;r++) for (let k=0;k<rows[r].length;k++) if (rows[r][k]==='#') g.hp(ox+k+0.25,oy+r+0.25,col,a0); };
      const seen=(x,y)=>{
        const d=Math.hypot(x-cx,y-cy); let best=0;
        for (const T0 of [T1,T2]){ const tt=t-T0-ringT(d); if (tt>=0) best=Math.max(best, 1-clamp(tt/2.6)*0.8); }
        return best*fade;
      };
      for (let x=-5;x<=17;x++) g.hp(x+0.25,11.25,C.hot,seen(x,11)*0.7);
      dots(GLYPH.mug,-5,6,seen(-3,8),C.hot);
      dots(GLYPH.plant,13,4,seen(15,7),C.hot);
      const second=t>T2;
      if (second) dots(GLYPH.smoth,14,-3,seen(15,-2.5),C.fg); else dots(GLYPH.smoth,-4,-3,seen(-3,-2.5),C.fg);
      g.done();
    },
    end(s,a){ s.cs.ax={}; },
    exitDur:0.25,
    exit(o,s,c,u){
      const g=grid(o,true);
      for (let x=-5;x<=17;x+=2) g.hp(x+0.25,11.25,C.hot,0.5*(1-u));
      bang(g,u); g.done();
    }
  },

  /* desktop: pings a little ball of sound and flutters after it, round and round, and catches it */
  chase:{
    start(s,a){ a.b={x:5.5,y:6.5}; a.e={x:0,y:0}; },
    update(s,dt,a){
      const u=a.u, b=a.b, e=a.e;
      const loop=(th)=>({x:5.5+7.5*Math.cos(th), y:3.2+2.6*Math.sin(th)});
      if (u<0.16){ const k=easeOutCubic(seg(u,0.04,0.16)); b.x=lerp(5.5,-2,k); b.y=lerp(6.5,3.2,k)-Math.sin(k*Math.PI)*1.5; e.x=0; e.y=0; }
      else if (u<0.74){
        const th=Math.PI+seg(u,0.16,0.74)*TAU*1.25; const p=loop(th); b.x=p.x; b.y=p.y;
        const f=loop(th-0.85), w=easeInOut(seg(u,0.16,0.3));
        e.x=clamp((f.x-5.5)*0.68,-5,5)*w; e.y=clamp((f.y-4)*0.55,-1,2)*w;
      } else if (u<0.8){ const k=seg(u,0.74,0.8); e.x=lerp(e.x,b.x-5.5,k*0.5); e.y=lerp(e.y,clamp(b.y-6,-1,2),k*0.5); }
      else { const k=easeInOut(seg(u,0.8,0.94)); e.x=lerp(a.ex??e.x,0,k); e.y=lerp(a.ey??e.y,0,k); }
      if (u>=0.8 && a.ex===undefined){ a.ex=e.x; a.ey=e.y; }
    },
    pose(s,a,q){
      const u=a.u, t=a.t, b=a.b, e=a.e;
      q.dx=Math.round(e.x); q.dy=Math.round(e.y); q.el=q.er='up1'; q.glow=0.5;
      q.lx=clamp((b.x-5.5-q.dx)/3,-1,1); q.ly=clamp((b.y-4.5-q.dy)/3,-1,1);
      if (u<0.06){ q.mouth='o'; }
      const flying=(u>0.16 && u<0.95) && (Math.abs(e.x)>0.4 || Math.abs(e.y)>0.4 || (u>0.2 && u<0.9));
      if (flying){ q.wl=q.wr=[1,2,1,3][Math.floor(t*10)%4]; q.feet=false; }
      if (u>=0.74 && u<0.92){ q.eyes='happy'; q.mouth=3; }
      if (u>=0.95){ q.eyes='happy'; q.mouth=3; }
    },
    draw(o,s,a){
      const g=grid(o,true), u=a.u, b=a.b, t=a.t;
      if (u>0.04 && u<0.74){
        const pulse=Math.floor(t*8)%2;
        g.px(Math.round(b.x),Math.round(b.y),C.whitehot,1);
        const ar=pulse?0.75:0.45, X=Math.round(b.x), Y=Math.round(b.y);
        g.hp(X-0.5,Y+0.25,C.hot,ar); g.hp(X+1,Y+0.25,C.hot,ar); g.hp(X+0.25,Y-0.5,C.hot,ar); g.hp(X+0.25,Y+1,C.hot,ar);
      } else if (u>=0.74 && u<0.88){
        const k=seg(u,0.74,0.88);
        for (let i=0;i<8;i++){ const ang=i*TAU/8; g.hp(b.x+Math.cos(ang)*k*3,b.y+Math.sin(ang)*k*3,i%2?C.hot:C.whitehot,1-k); }
      }
      g.done();
    },
    end(s,a){ s.cs.ax={x:a.b.x, y:a.b.y, on:a.u>0.04 && a.u<0.74}; },
    exitDur:0.25,
    exit(o,s,c,u){
      const g=grid(o,true), ax=s.cs.ax||{};
      if (ax.on) for (let i=0;i<4;i++){ const ang=i*TAU/4+0.78; g.hp(ax.x+Math.cos(ang)*u*2,ax.y+Math.sin(ang)*u*2,C.hot,1-u); }
      bang(g,u); g.done();
    }
  },

  /* hiccups, in sonar: each one pops out a little ping. Holds its breath... and one more */
  hiccup:{
    start(s,a){ a.k=a.dur/6; },
    pose(s,a,q){
      const t=a.t/a.k;
      const H=[0.45,1.25,2.0];
      q.el=q.er='up0'; q.glow=0.3;
      for (const h of H){ const d=t-h; if (d>=0 && d<0.14){ q.dy=-1; q.el=q.er='up2'; q.eyes='squeeze'; q.noBlink=true; q.mouth='o'; } else if (d>=0.14 && d<0.4){ q.eyes='wide'; q.noBlink=true; } }
      if (t>=2.6 && t<4.3){ q.cheeks=true; q.eyes='squeeze'; q.noBlink=true; q.mouth='shut'; q.el=q.er='up1'; q.glow=0.3+0.7*seg(t,2.6,4.2); }   // holds its breath
      else if (t>=4.3 && t<4.6){ q.mouth='o'; q.eyes='closed'; q.noBlink=true; }
      else if (t>=4.6 && t<5.25){ q.eyes='happy'; q.mouth=3; q.el=q.er='droop'; }
      else if (t>=5.25 && t<5.4){ q.dy=-1; q.eyes='wide'; q.noBlink=true; q.el=q.er='up1'; q.mouth='o'; }        // ...hic
      else if (t>=5.4){ q.el=q.er='down'; q.mouth=4; q.lx=0; q.ly=0.6; }
    },
    draw(o,s,a){
      const g=grid(o,true), t=a.t/a.k, q=pose(s);
      for (const h of [0.45,1.25,2.0,5.25]){
        const d=t-h; if (d<0 || d>0.7) continue;
        const k=d/0.7, y=2.4-k*3.2+q.dy, small=h>5;
        if (small){ g.hp(5.25,y,C.hot,1-k); g.hp(4.75,y+0.5,C.hot,1-k); g.hp(5.75,y+0.5,C.hot,1-k); }
        else { g.px(5,y,C.hot,1-k); g.px(6,y,C.hot,1-k); g.px(4,y+1,C.hot,(1-k)*0.8); g.px(7,y+1,C.hot,(1-k)*0.8); }
      }
      if (t>4.3 && t<5.0){ const k=seg(t,4.3,5.0); for (let i=0;i<3;i++) g.hp(4.5-k*2.5-i*0.5,7+k*0.5+i*0.5,C.dim,(1-k)*0.7); }
      g.done();
    },
    end(s,a){ s.cs.ax={}; },
    exitDur:0.25,
    exit(o,s,c,u){
      const g=grid(o,true);
      g.hp(5.25,1.5-u*2,C.hot,1-u); g.hp(4.75,2-u*2,C.hot,1-u); g.hp(5.75,2-u*2,C.hot,1-u);
      g.done();
    }
  },

  /* swivels its ears like dishes, finds a faraway song, and bobs along with its eyes shut */
  tune:{
    pose(s,a,q){
      const u=a.u, t=a.t;
      q.glow=0.3;
      if (u<0.08){ q.el='down'; q.er='up2'; q.lx=0.8; }
      else if (u<0.16){ q.el='up2'; q.er='down'; q.lx=-0.8; }
      else if (u<0.22){ q.el='up1'; q.er='twitch'; q.lx=0.5; }
      else if (u<0.28){ q.el=q.er='up2'; q.lx=1; q.glow=0.8; }
      else if (u<0.9){
        const beat=(t-a.dur*0.28)/0.5, b=Math.floor(beat), ph=beat-b;
        q.eyes='happy'; q.mouth = b%4===3 ? 3 : 0; q.el=q.er='up1'; q.glow=0.55+0.35*(1-ph);
        if (ph<0.22) q.dy=-1;
        q.wl = b%2 ? 1 : 0; q.wr = b%2 ? 0 : 1;
      } else { q.eyes='open'; q.el=q.er='up1'; q.lx=0.6; q.mouth=3; }
    },
    draw(o,s,a){
      const u=a.u, t=a.t; if (u<0.22) return;
      const g=grid(o,true);
      for (let i=0;i<4;i++){
        const t0=a.dur*(0.24+i*0.15), d=(t-t0)/1.6; if (d<0 || d>1) continue;
        const x=11.2-d*1.0, y=0.6+Math.sin(d*Math.PI*2+i)*0.7-d*0.4;
        const a0=Math.min(1,d*5)*(1-seg(d,0.7,1));
        g.glyph(i%2?GLYPH.note2:GLYPH.note1, x, y-1.25, {'#':i%2?C.whitehot:C.fg}, a0, 0.5);
      }
      g.done();
    },
    end(s,a){ s.cs.ax={}; },
    exitDur:0.25,
    exit(o,s,c,u){
      const g=grid(o,true);
      g.glyph(GLYPH.note1,11+u*0.5,-0.5+u*2.5,{'#':C.fg},(1-u)*0.8,0.5);
      g.done();
    }
  },

  /* desktop, when it rains: a small cloud parks overhead; up goes a wing umbrella. Then a good shake */
  rain:{
    pose(s,a,q){
      const u=a.u, t=a.t;
      if (u<0.1){ q.lx=0.8; q.ly=-1; q.el=q.er='up1'; }
      else if (u<0.82){ q.wr='umbrella'; q.el=q.er='down'; q.lx=0.2; q.ly=-1; q.glow=0.2;
        if (Math.floor(t*1.3)%5===2) q.eyes='blink';
        if (u>0.5 && u<0.6){ q.eyes='happy'; q.mouth=3; } }
      else if (u<0.9){ const j=Math.floor(t*24)%2; q.dx=j?1:-1; q.eyes='squeeze'; q.noBlink=true; q.el=j?'twitch':'up0'; q.er=j?'up0':'twitch'; q.wl=q.wr=1; }
      else { q.eyes='happy'; q.mouth=3; q.el=q.er='up1'; }
    },
    under(o,s,a){
      const g=grid(o,true), u=a.u, t=a.t;
      const off = u<0.12 ? (1-easeOutCubic(seg(u,0,0.12)))*14 : u>0.8 ? -easeInCubic(seg(u,0.8,0.95))*16 : 0;
      const ca = u>0.95 ? 0 : 1;
      g.glyph(GLYPH.cloud,-4.5+off,-4,{'l':CLOUD.l,'c':CLOUD.c,'s':CLOUD.s},ca,1);
      const raining = u>0.1 && u<0.82, rk=Math.min(seg(u,0.1,0.16),1-seg(u,0.76,0.82));
      if (raining) for (let i=0;i<11;i++){
        const x=Math.round((hsh(i*3.1)*10.5-4.5+off)*2)/2, sp=9+hsh(i+5)*3, ph=hsh(i*7.7)*12;
        const covered = x>=2 && x<=11, floor = covered ? -1.5 : 10.5;
        const L=floor+2.5, y=-1.5+((t*sp+ph)%L);
        if (y>=floor){ g.hp(x-0.5,floor+0.5,RAIN,0.6*rk); g.hp(x+0.5,floor+0.5,RAIN,0.6*rk); }
        else { g.hp(x,y,RAIN,0.85*rk); g.hp(x,y+0.5,RAIN,0.5*rk); }
      }
      const pud=seg(u,0.2,0.8)*(1-seg(u,0.88,1));
      if (pud>0) for (let x=-4;x<=1;x+=0.5) g.hp(x,11,RAIN,0.45*pud*(x>-3.5&&x<0.5?1:0.5));
      g.done();
    },
    draw(o,s,a){
      const u=a.u; if (u<0.82 || u>0.95) return;
      const g=grid(o,true), k=seg(u,0.82,0.95);
      for (let i=0;i<8;i++){ const ang=i*TAU/8+0.3; g.hp(5.5+Math.cos(ang)*(4.5+k*3),5+Math.sin(ang)*(4+k*3)+k*k*2,RAIN,(1-k)*0.85); }
      g.done();
    },
    end(s,a){ s.cs.ax={}; },
    exitDur:0.3,
    exit(o,s,c,u){
      const g=grid(o,true);
      g.glyph(GLYPH.cloud,-4.5,-4-u,{'l':CLOUD.l,'c':CLOUD.c,'s':CLOUD.s},(1-u)*0.7,1);
      for (let i=0;i<3;i++) g.hp(2+i*3,-1+u*3,RAIN,1-u);
      g.done();
    }
  }
};
// the star chart (desktop cells around Echo) and the counting order
const STARS=[[-4,-3],[-1.5,-1],[-4.5,2.5],[2.5,-3.5],[8.5,-3.5],[12.5,-2],[16.5,-3],[15.5,1.5],[-2.5,6],[16,5.5],[5.5,-3.5],[-5,8.5]];
const ORDER=[1,0,2,3,10,4,5];
const COUNT0=1.8, COUNT_DT=0.95;
function countAt(t){ if (t<COUNT0) return -1; return Math.min(ORDER.length-1, 5, Math.floor((t-COUNT0)/COUNT_DT)); }
function ringR(dt){ return dt<0 ? -1 : dt*7.5; }
function ringT(d){ return d/7.5; }

const Echo={
  id:'echo', name:'Echo', kind:'pixel bat',
  line:'A pocket-sized bat with satellite-dish ears. Hears everything, says just enough.',
  sig:'<b>Off:</b> hangs upside down from the island and snoozes. <b>Listening:</b> ears grow with your voice and sound pings fly into them.',
  antics,
  enter(s,st){
    const cs=s.cs; cs.burst=0; cs.wk=0; cs.wa=0;
    if (st==='off') cs.zT=0.4;
    if (st==='asleep') cs.zT=1.2;
  },
  update(s,dt){
    const st=s.state,t=s.t,cs=s.cs,calm=s.calm;
    let ear=0, flap=0, hz=2;
    if (st==='listening') ear=1+s.L*1.4;
    else if (st==='confused') ear=-1;
    else if (st==='talking') ear=0.3+s.A*1.0;
    else if (st==='happy') ear=1.2;
    else if (st==='thinking') ear=0.6;
    else if (st==='working') ear=0.6;
    else if (st==='woke') ear=2.4;
    else if (st==='approval_needed') ear=1.2;
    if (st==='idle'||st==='thinking') ear+=s.notice*1.4;
    s.fo('ear',ear,0.06);
    if (!calm){
      if (st==='working'){ flap=1; hz=2.6; }
      if (st==='happy' && t<1.3){ flap=1; hz=7; }
      if (st==='sending' && t>0.15 && t<2.2){ flap=1; hz=6; }
    }
    s.fo('flap',flap,0.06);
    cs.wph=(cs.wph||0)+dt*hz;
    if (st==='idle' && !calm && !s.antic){
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
    if (st==='asleep'){
      cs.zT=(cs.zT??1)-dt;
      if (cs.zT<=0){ cs.zT=2.9; s.spawn({k:'pz',x:8.5,y:3.5,vx:0.9,vy:-1.5,max:2.7}); }
    }
    if (st==='woke' && !cs.wa){
      cs.wa=1;                                     // the sound that woke it flies into both ears
      s.spawn({k:'parc2',x:-2.5,y:0.5,vx:9,max:0.32,side:1});
      s.spawn({k:'parc2',x:14.5,y:0.5,vx:-9,max:0.32,side:-1});
    }
    if (st==='waking' && !cs.wk && t>1.0){
      cs.wk=1;                                     // a little fluff from the shake
      for (let i=0;i<6;i++) s.spawn({k:'phalf',x:5.5+rnd(-4.5,4.5),y:rnd(3,8),vx:rnd(-5,5),vy:rnd(-6,-2),g:16,max:rnd(0.5,0.8),col:pick([EB.B,EB.F,EB.B])});
    }
    if (st==='happy' && !cs.burst && t>0.05){
      cs.burst=1;
      for (let i=0;i<14;i++) s.spawn({k:'ppix',x:5.5,y:3,vx:rnd(-13,13),vy:rnd(-17,-8),g:34,max:rnd(1.1,1.7),col:pick([C.accent,C.ok,C.fg,C.hot])});
    }
    if (st==='talking' && s.onset) cs.hop=0.12;
    if (cs.hop>0) cs.hop-=dt;
  },
  // approval can wait minutes: its slow bob needs no more than 12 fps
  fps(s,B){
    const f=K.wantFps(s,B);
    if (s.state==='approval_needed' && !s.antic && !s.caught && s.t>1.2) return s.calm ? 0 : Math.min(f,12);
    return f;
  },
  draw(o,s){
    const ctx=o.ctx, p=s.p, cs=s.cs, st=s.state, t=s.t, calm=s.calm;
    const g=grid(o,false), c=g.c, bx=g.bx, by=g.by;
    const q=statePose(s);
    const an=s.antic && antics[s.antic.id];
    if (an && an.pose) an.pose(s,s.antic,q);
    if (calm && s.antic){ q.dx=0; q.dy=0; q.hdy=0; }
    cs._q=q;
    const glyph=(rows,gx,gy,map,a=1,cell=c,orgX=bx,orgY=by)=>{
      for (let r=0;r<rows.length;r++) for (let k=0;k<rows[r].length;k++){
        const col=map[rows[r][k]]; if (!col) continue;
        ctx.fillStyle=rgba(col,a); ctx.fillRect(orgX+Math.round(gx*c)+k*cell, orgY+Math.round(gy*c)+r*cell, cell, cell);
      }
    };
    /* working satellites, back half */
    const sats=[];
    if (st==='working'){
      for (let i=0;i<3;i++){
        const a=s.T*TAU/2.6+i*TAU/3;
        sats.push({x:Math.round(5.5+7.5*Math.cos(a)), y:Math.round(6+2.6*Math.sin(a)), front:Math.sin(a)>0});
      }
      for (const z of sats) if (!z.front){ ctx.fillStyle=rgba(C.banked,0.9); ctx.fillRect(bx+z.x*c, by+z.y*c, c, c); }
    }
    if (!q.gone){
      const hang=q.hang, ox=bx+Math.round(q.dx)*c, oy=by+Math.round(q.dy)*c;
      const P=(x,y,col,a=1)=>{ const yy=hang?11-y:y; ctx.fillStyle=rgba(col,a); ctx.fillRect(ox+x*c,oy+yy*c,c,c); };
      const H=(x,y,col,a=1)=>P(x+q.hdx,y+q.hdy,col,a);
      /* perch (big view only): the island's edge */
      if (q.perch && !o.small){ ctx.fillStyle=rgba(EB.perch,1); ctx.fillRect(bx-1*c, by, 14*c, Math.max(1,Math.round(c*0.6))); }
      const B=EB.B, D=EB.D, F=EB.F, Kc=EB.K, W=C.fg;
      const A = q.ash ? mixc(C.ash,C.banked,0.3) : mixc(C.banked,C.hot,clamp(q.glow));
      const COL={B, D, F, K:Kc, A, E:EB.E, W};
      const wing=(f,right)=>{ for (const w of (WING[f]||WING[0])) if (!(right && w[3]==='L')) P(right?11-w[0]:w[0], w[1], COL[w[2]||'D']); };
      const front=(f)=>!!FRONT[f];
      /* wings behind */
      if (!front(q.wl)) wing(q.wl,false);
      if (!front(q.wr)) wing(q.wr,true);
      /* ears: big, set wide */
      for (const e of (EAR[q.el]||EAR.up0)) H(e[0],e[1],COL[e[2]]);
      for (const e of (EAR[q.er]||EAR.up0)) H(11-e[0],e[1],COL[e[2]]);
      /* head, rounded top corners */
      for (let y=3;y<=6;y++) for (let x=2;x<=9;x++) H(x,y,B);
      if (q.cheeks){ H(1,4,B); H(1,5,B); H(10,4,B); H(10,5,B); }
      /* body: a small furry belly */
      for (let x=3;x<=8;x++) P(x,7,B);
      P(3,8,B); for (let x=4;x<=7;x++) P(x,8,F); P(8,8,B);
      for (let x=4;x<=7;x++) P(x,9,B);
      if (q.feet){ if (q.footL!==false) P(4,10,D); P(7,10,D); }
      /* eyes */
      let lx=q.lx, ly=q.ly;
      if (hang) ly=-ly;
      let mode=q.eyes;
      if (!q.noBlink && s.blink>0.5 && (mode==='open'||mode==='peek'||mode==='confused'||mode==='heavy'||mode==='wide')) mode = mode==='peek'?'closed':'blink';
      /* eyes: dark 2x2 with a glint that follows the pointer */
      const eye=(x0,left,kind)=>{
        const gx = lx>0.3?1: lx<-0.3?0: (left?1:0), gy = ly>0.45?1:0;
        if (kind==='open'){ H(x0,4,Kc); H(x0+1,4,Kc); H(x0,5,Kc); H(x0+1,5,Kc); H(x0+gx,4+gy,W); }
        else if (kind==='closed'||kind==='blink'){ H(x0,5,Kc); H(x0+1,5,Kc); }
        else if (kind==='heavy'){ H(x0,4,EB.E); H(x0+1,4,EB.E); H(x0,5,Kc); H(x0+1,5,Kc); H(x0+gx,5,W,0.75); }
        else if (kind==='wide'){ H(x0,4,W); H(x0+1,4,W); H(x0,5,W); H(x0+1,5,W); H(x0+(left?1:0),4+gy,Kc); }
        else if (kind==='squeeze'){ if (left){ H(x0,4,Kc); H(x0+1,5,Kc); } else { H(x0+1,4,Kc); H(x0,5,Kc); } }
      };
      if (mode==='happy'){ H(2,5,Kc); H(3,4,Kc); H(4,5,Kc); H(7,5,Kc); H(8,4,Kc); H(9,5,Kc); }
      else if (mode==='confused'){ eye(3,true,'open'); H(7,5,Kc); H(8,5,Kc); H(7,3,Kc); H(8,4,D); }
      else if (mode==='peek'){ eye(3,true,'closed'); eye(7,false,'open'); }
      else { eye(3,true,q.eyeL||mode); eye(7,false,q.eyeR||mode); }
      /* mouth, always with one tiny fang */
      const m=q.mouth;
      if (m===0){ H(5,6,Kc); H(6,6,Kc); H(6,7,W); }
      else if (m===1){ H(5,6,W); H(6,6,Kc); H(5,7,Kc); H(6,7,Kc); }
      else if (m===2){ H(5,6,W); H(6,6,Kc); H(5,7,Kc); H(6,7,TONGUE); H(5,8,Kc); H(6,8,Kc); }
      else if (m===3){ H(4,6,Kc); H(5,7,Kc); H(6,7,TONGUE); H(7,6,Kc); H(5,6,Kc); H(6,6,Kc); }
      else if (m===4){ H(4,7,Kc); H(5,6,Kc); H(6,7,Kc); H(7,6,Kc); }
      else if (m==='o'){ H(5,6,Kc); H(6,6,Kc); H(5,7,Kc); H(6,7,Kc); }
      else if (m==='yawn'){ H(5,6,W); H(6,6,Kc); H(4,7,Kc); H(5,7,Kc); H(6,7,TONGUE); H(7,7,Kc); H(5,8,Kc); H(6,8,Kc); }
      else if (m==='lickL'){ H(5,6,Kc); H(6,6,Kc); H(5,7,TONGUE); H(4,7,TONGUE,0.85); }
      else if (m==='lickR'){ H(5,6,Kc); H(6,6,Kc); H(6,7,TONGUE); H(7,7,TONGUE,0.85); }
      else if (m==='shut'){ H(5,6,Kc); H(6,6,Kc); }
      /* wings in front (cloak, grooming, umbrella) */
      if (front(q.wr)) wing(q.wr,true);
      if (front(q.wl)) wing(q.wl,false);
      if (q.blush>0){ H(2,6,BLUSH,q.blush); H(9,6,BLUSH,q.blush); }
      /* envelope */
      if (q.env) glyph(GLYPH.env, 3.5+Math.round(q.dx), 9.5+Math.round(q.dy), {w:C.fg,d:C.dim,a:C.accent},1,c,bx,by);
      /* thinking dots */
      if (st==='thinking'){
        const n=Math.floor(t*2.6)%4;
        [[10,1],[11,0],[12,-1]].forEach(([x,y],i)=>{ ctx.fillStyle=rgba(C.accent,i<n?1:0.18); ctx.fillRect(bx+x*c,by+y*c,c,c); });
      }
      if (st==='confused'){ const a=clamp(t/0.2); glyph(GLYPH.q,9.5,-5,{'#':C.bad},a); }
      /* approval: a small polite question between the ears */
      if (q.ask){ const a=clamp(t/0.25); g.glyph(GLYPH.q, 4.5+q.dx, (q.ask===2?-0.5:0)+q.dy, {'#':C.accent}, a*(q.ask===2?1:0.8), 0.5); }
      /* woke: a spark on each ear tip */
      if (st==='woke' && t<0.35 && !calm){ const a=1-t/0.35; P(1,-3,C.whitehot,a); P(10,-3,C.whitehot,a); }
    }
    for (const z of sats) if (z.front){ ctx.fillStyle=rgba(C.hot,1); ctx.fillRect(bx+z.x*c, by+z.y*c, c, c); }
    if (q.chk>0) glyph(GLYPH.chk,9,-3,{'#':C.ok},q.chk);
    /* pixel particles */
    const half=g.half;
    for (const z of s.parts){
      const u=z.life/z.max, a=1-u*u;
      if (z.k==='parc'||z.k==='parc2'){
        const x=Math.round(z.x), y=Math.round(z.y), sd=z.side;
        ctx.fillStyle = z.k==='parc' ? rgba(C.fg,a*(0.35+0.6*s.L)) : rgba(C.hot,a);
        ctx.fillRect(bx+x*c,by+(y-1)*c,c,c); ctx.fillRect(bx+(x+sd)*c,by+y*c,c,c); ctx.fillRect(bx+x*c,by+(y+1)*c,c,c);
      } else if (z.k==='pz'){
        glyph(GLYPH.z,z.x,z.y,{'#':C.dim},a*0.85,half);
      } else if (z.k==='ppix'){
        ctx.fillStyle=rgba(z.col,a); ctx.fillRect(bx+Math.round(z.x)*c,by+Math.round(z.y)*c,c,c);
      } else if (z.k==='phalf'){
        g.hp(z.x,z.y,z.col,a);
      }
    }
  }
};
return Echo;
}
