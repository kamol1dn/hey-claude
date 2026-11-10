.pragma library
// Wren, a folded-paper bird. Ported from docs/character-options.html (const Wren), plus
// woke, approval_needed, the sleep progression (drowsy, asleep, waking) and twelve idle
// antics. make(K) gets the character kit (ui/qml/HeyClaude/Character/kit.js).
//
// How the antics move the bird: every frame update() builds a pose P (targets for the
// followers in s.p) for the state; an antic's pose(s, a, P) then edits it. Values that
// must snap (cs.fold: the origami squash, cs.hide: the bird is a boat or a plane right
// now, cs.jit: crumple jitter, cs.eye: an eye override) are reset every frame, so an
// interrupted antic lets go of the bird at once.
function make(K) {
const { C, TAU, clamp, lerp, mixc, mul, rgba, easeOutCubic, easeInCubic, easeInOut, easeOutBack, hash, breath,
        poly, drawQ, drawCheck, drawParts, rnd, pick } = K;
const PAPER={light:[241,235,227], mid:[218,209,198], shade:[180,170,160], line:[120,110,100]};
const INK=[30,26,24], RAIN=[150,172,196], OCHRE=[214,158,84], STAR=[255,238,214];
const seg=(t,a,b)=>clamp((t-a)/(b-a));
const bell=u=>Math.sin(Math.PI*clamp(u));

// ---- small drawing helpers (box units) ------------------------------------------
function oval(ctx,x,y,rx,ry,rot){
  rot=rot||0; ctx.beginPath(); ctx.moveTo(x+rx*Math.cos(rot), y+rx*Math.sin(rot));
  K.ellipse(ctx,x,y,rx,ry,rot,0,TAU); ctx.closePath();
}
function line(ctx,pts){ ctx.beginPath(); ctx.moveTo(pts[0][0],pts[0][1]); for (let i=1;i<pts.length;i++) ctx.lineTo(pts[i][0],pts[i][1]); }
function lerp2(a,b,u){ return [lerp(a[0],b[0],u), lerp(a[1],b[1],u)]; }
function sparkle(ctx,x,y,r,col,a){
  if (a<=0.01) return;
  const q=r*0.26; ctx.fillStyle=rgba(col,a);
  poly(ctx,[[x,y-r],[x+q,y-q],[x+r,y],[x+q,y+q],[x,y+r],[x-q,y+q],[x-r,y],[x-q,y-q]]); ctx.fill();
}

// Body coordinates (before the face flip) -> box units, the way drawBird transforms them.
function toWorld(s,bx,by){
  const p=s.p, cs=s.cs, fs=(Math.sign(p.face)||1)*Math.max(Math.abs(p.face)*(cs.fold??1),0.07);
  let X=bx*fs-2, Y=by-16;
  X*=1+0.06*p.puff; Y*=p.sy*(1+0.05*p.puff);
  const c=Math.cos(p.tilt), sn=Math.sin(p.tilt);
  return [-4+p.x+2+X*c-Y*sn, -2+p.by+16+X*sn+Y*c];
}
// A point in the head's frame (beak tip ~ (21, -0.8)) -> box units.
function headPt(s,lx,ly){
  const p=s.p, tk=p.tuck, hs=(Math.sign(p.hs)||1)*Math.max(Math.abs(p.hs),0.08);
  const c=Math.cos(p.hr), sn=Math.sin(p.hr), X=lx*hs;
  return toWorld(s, lerp(9,1,tk)+X*c-ly*sn, -7+p.dark*4+lerp(0,3,tk)+X*sn+ly*c);
}
// "caught!": three little alarm ticks above the head
function startle(o,s,u){
  const ctx=o.ctx, a=1-u; if (a<=0.01) return;
  const H=headPt(s,3,-13), r0=3+4*easeOutCubic(u), r1=r0+5;
  ctx.strokeStyle=rgba(C.accent,a); ctx.lineWidth=o.lw(1.5,0.9); ctx.lineCap='round';
  for (const g of [-2.25,-1.57,-0.9]){ ctx.beginPath(); ctx.moveTo(H[0]+r0*Math.cos(g),H[1]+r0*Math.sin(g)); ctx.lineTo(H[0]+r1*Math.cos(g),H[1]+r1*Math.sin(g)); ctx.stroke(); }
}
function fleck(s,x,y,vx,vy,col){
  s.spawn({k:'tri',x:x,y:y,vx:vx,vy:vy,g:60,drag:1.2,max:rnd(1.0,1.5),r:rnd(1.3,2.1),rot:rnd(0,6),vr:rnd(-9,9),col:col||PAPER.light});
}
function ripple(ctx,o,x,y,age,dur,amax){
  if (age<0||age>dur) return;
  const u=age/dur, r=2+11*easeOutCubic(u);
  ctx.strokeStyle=rgba(PAPER.mid,(1-u)*amax); ctx.lineWidth=o.lw(0.9,0.6); oval(ctx,x,y,r,r*0.28,0); ctx.stroke();
}

// ---- props ------------------------------------------------------------------------
function rod(ctx,o,G,R,bend,a){
  const dx=R[0]-G[0], dy=R[1]-G[1], l=Math.hypot(dx,dy)||1;
  const cx=(G[0]+R[0])/2-dy/l*bend*6, cy=(G[1]+R[1])/2+dx/l*bend*6;
  ctx.lineCap='round';
  ctx.strokeStyle=rgba(PAPER.shade,a); ctx.lineWidth=o.lw(2.1,1.0);
  ctx.beginPath(); ctx.moveTo(G[0],G[1]); ctx.quadraticCurveTo(cx,cy,R[0],R[1]); ctx.stroke();
  ctx.strokeStyle=rgba(PAPER.light,a); ctx.lineWidth=o.lw(0.9,0.5);
  ctx.beginPath(); ctx.moveTo(G[0],G[1]); ctx.quadraticCurveTo(cx,cy,R[0],R[1]); ctx.stroke();
}
function bob(ctx,o,x,y,a){
  const r=Math.max(2.2,o.mw(1.2));
  ctx.fillStyle=rgba(PAPER.light,a); ctx.beginPath(); ctx.arc(x,y,r,0,TAU); ctx.fill();
  ctx.fillStyle=rgba(C.accent,a); ctx.beginPath(); ctx.moveTo(x-r,y); ctx.arc(x,y,r,Math.PI,TAU); ctx.closePath(); ctx.fill();
}
function fish(ctx,o,x,y,rot,sc,a){
  if (a<=0.01) return;
  ctx.save(); ctx.translate(x,y); ctx.rotate(rot); ctx.scale(sc,sc);
  ctx.fillStyle=rgba(C.accent,a); poly(ctx,[[0,0],[3.6,4.4],[0,8.8],[-3.6,4.4]]); ctx.fill();
  ctx.fillStyle=rgba(C.hot,a); poly(ctx,[[0,0],[3.6,4.4],[0,8.8]]); ctx.fill();
  ctx.fillStyle=rgba(C.banked,a); poly(ctx,[[0,8],[3.8,12.6],[0,11.2],[-3.8,12.6]]); ctx.fill();
  ctx.fillStyle=rgba(INK,a); ctx.beginPath(); ctx.arc(1.3,2.9,Math.max(0.7,o.mw(0.55)),0,TAU); ctx.fill();
  ctx.restore();
}
function leafShape(ctx,o,x,y,rot,sc,col,a){
  if (a<=0.01) return;
  ctx.save(); ctx.translate(x,y); ctx.rotate(rot); ctx.scale(sc,sc);
  ctx.fillStyle=rgba(col,a);
  ctx.beginPath(); ctx.moveTo(-5.5,0); ctx.quadraticCurveTo(-1,-4.2,5.5,0); ctx.quadraticCurveTo(-1,4.2,-5.5,0); ctx.closePath(); ctx.fill();
  ctx.fillStyle=rgba(mul(col,0.78),a);
  ctx.beginPath(); ctx.moveTo(-5.5,0); ctx.quadraticCurveTo(-1,4.2,5.5,0); ctx.closePath(); ctx.fill();
  ctx.strokeStyle=rgba(mul(col,0.6),a); ctx.lineWidth=o.lw(0.6,0.45); ctx.lineCap='round';
  ctx.beginPath(); ctx.moveTo(-7.2,0.6); ctx.lineTo(4.6,0); ctx.stroke();
  ctx.restore();
}
function envelope(ctx,o,x,bot,w,h,sy,a,marks){
  ctx.save(); ctx.translate(x,bot); ctx.scale(1,sy);
  ctx.fillStyle=rgba([0,0,0],0.25*a); oval(ctx,0,1.2,w*0.55,1.6,0); ctx.fill();
  ctx.fillStyle=rgba(PAPER.light,a); poly(ctx,[[-w/2,-h],[w/2,-h],[w/2,0],[-w/2,0]]); ctx.fill();
  ctx.fillStyle=rgba(PAPER.mid,a); poly(ctx,[[-w/2,0],[0,-h*0.42],[w/2,0]]); ctx.fill();
  ctx.strokeStyle=rgba(PAPER.line,0.55*a); ctx.lineWidth=o.lw(0.8,0.5); ctx.lineJoin='round';
  line(ctx,[[-w/2,-h],[0,-h*0.38],[w/2,-h]]); ctx.stroke();
  ctx.fillStyle=rgba(C.accent,a); poly(ctx,[[w/2-6.5,-h+1.6],[w/2-1.6,-h+1.6],[w/2-1.6,-h+7],[w/2-6.5,-h+7]]); ctx.fill();
  if (marks){
    const inkA=a*0.9;
    if (marks.ring>0){
      const k=easeOutBack(marks.ring), cx=-w/2+6.5, cy=-h*0.62;
      ctx.strokeStyle=rgba(C.banked,inkA*Math.min(1,marks.ring*3)); ctx.lineWidth=o.lw(1.0,0.6);
      ctx.beginPath(); ctx.arc(cx,cy,4.2*k,0,TAU); ctx.stroke();
      ctx.beginPath(); ctx.moveTo(cx-2.4*k,cy); ctx.lineTo(cx+2.4*k,cy); ctx.stroke();
    }
    if (marks.wave>0){
      ctx.strokeStyle=rgba(C.banked,inkA*Math.min(1,marks.wave*3)); ctx.lineWidth=o.lw(0.9,0.55);
      const x0=-w/2+11.5, len=9*easeOutCubic(marks.wave);
      for (let r=0;r<3;r++){
        const yy=-h*0.82+r*2.6; ctx.beginPath(); ctx.moveTo(x0,yy);
        for (let i=1;i<=8;i++){ const xx=x0+len*i/8; ctx.lineTo(xx, yy+0.8*Math.sin(i*1.6)); }
        ctx.stroke();
      }
    }
  }
  ctx.restore();
}
function letterCard(ctx,o,cx,bot,w,h,a,read){
  if (a<=0.01 || w<0.5 || h<0.5) return;
  ctx.fillStyle=rgba(PAPER.light,a); poly(ctx,[[cx-w/2,bot-h],[cx+w/2,bot-h],[cx+w/2,bot],[cx-w/2,bot]]); ctx.fill();
  ctx.fillStyle=rgba(PAPER.mid,a); poly(ctx,[[cx-w/2,bot-h*0.5],[cx+w/2,bot-h*0.5],[cx+w/2,bot],[cx-w/2,bot]]); ctx.fill();
  if (h>9){
    const k=clamp((h-9)/6)*a;
    ctx.lineWidth=o.lw(0.75,0.5); ctx.lineCap='round';
    for (let r=0;r<3;r++){
      const yy=bot-h+3.2+r*3.6, x1=cx+w/2-2.4-(r===2?4:0);
      ctx.strokeStyle=rgba(r<read?INK:PAPER.line,(r<read?0.7:0.45)*k);
      ctx.beginPath(); ctx.moveTo(cx-w/2+2.2,yy); ctx.lineTo(x1,yy); ctx.stroke();
    }
    ctx.fillStyle=rgba(C.accent,k); ctx.beginPath(); ctx.arc(cx+w/2-2.8,bot-2.8,Math.max(1.1,o.mw(0.8)),0,TAU); ctx.fill();
  }
}
function boatShape(ctx,o,s,x,y,rot,kx,a){
  ctx.save(); ctx.translate(x,y); ctx.rotate(rot); ctx.scale(Math.max(kx,0.06)*0.9,0.9);
  // the sail: two facets and a centre crease
  ctx.fillStyle=rgba(PAPER.mid,a); poly(ctx,[[-13,-4],[0,-26],[0,-4]]); ctx.fill();
  ctx.fillStyle=rgba(PAPER.light,a); poly(ctx,[[0,-26],[13,-4],[0,-4]]); ctx.fill();
  // Wren's eye on the sail
  const ey=-12;
  ctx.fillStyle=rgba(INK,a); ctx.strokeStyle=rgba(INK,a); ctx.lineCap='round';
  if (s.blink>0.5){ ctx.lineWidth=o.lw(1.4,0.9); ctx.beginPath(); ctx.moveTo(2.6,ey); ctx.lineTo(6.4,ey); ctx.stroke(); }
  else { ctx.beginPath(); ctx.arc(4.5+s.look.x*0.8,ey,Math.max(1.8,o.mw(1)),0,TAU); ctx.fill(); }
  // the hull, terracotta side out, its points turned up
  ctx.fillStyle=rgba(C.accent,a); poly(ctx,[[-31,-10],[31,-10],[19,6],[-19,6]]); ctx.fill();
  ctx.fillStyle=rgba(C.banked,a); poly(ctx,[[-31,-10],[-19,6],[-12,-3]]); ctx.fill();
  ctx.fillStyle=rgba(C.hot,a); poly(ctx,[[31,-10],[19,6],[12,-3]]); ctx.fill();
  ctx.strokeStyle=rgba(PAPER.line,0.35*a); ctx.lineWidth=o.lw(0.8,0.5);
  line(ctx,[[-12,-3],[12,-3]]); ctx.stroke();
  ctx.restore();
}
function planeShape(ctx,o,s,x,y,rot,flipY,kx,a){
  ctx.save(); ctx.translate(x,y); ctx.rotate(rot); ctx.scale(Math.max(kx,0.06), flipY);
  ctx.fillStyle=rgba(C.accent,a); poly(ctx,[[16,0.4],[-12,2.6],[-14,5.2]]); ctx.fill();
  ctx.fillStyle=rgba(PAPER.mid,a); poly(ctx,[[16,0],[-9,0],[-14,4.2]]); ctx.fill();
  ctx.fillStyle=rgba(PAPER.light,a); poly(ctx,[[16,0],[-15,-7],[-9,0]]); ctx.fill();
  ctx.strokeStyle=rgba(PAPER.line,0.4*a); ctx.lineWidth=o.lw(0.7,0.45); line(ctx,[[16,0],[-12,-3.2]]); ctx.stroke();
  ctx.fillStyle=rgba(INK,a);
  if (s.blink>0.5){ ctx.fillRect(1.6,1.0,3,Math.max(0.6,o.mw(0.8))); }
  else { ctx.beginPath(); ctx.arc(3.5,1.6,Math.max(1.3,o.mw(1)),0,TAU); ctx.fill(); }
  ctx.restore();
}
function canopy(ctx,o,cx,cy,R,k,a){
  if (k<=0.01||a<=0.01) return;
  const n=6, apex=[cx, cy-14*(0.45+0.55*k)], rim=[];
  for (let i=0;i<=n;i++){ const f=-1+2*i/n; rim.push([cx+R*k*f, cy+3*f*f+(i%2?-1.8:0)]); }
  for (let i=0;i<n;i++){
    const left=i<n/2, base=(i%2)? C.accent : PAPER.light;
    ctx.fillStyle=rgba(left? mul(base,0.86): base, a); poly(ctx,[apex,rim[i],rim[i+1]]); ctx.fill();
  }
  ctx.strokeStyle=rgba(PAPER.line,0.35*a); ctx.lineWidth=o.lw(0.7,0.45);
  for (let i=1;i<n;i++){ ctx.beginPath(); ctx.moveTo(apex[0],apex[1]); ctx.lineTo(rim[i][0],rim[i][1]); ctx.stroke(); }
  ctx.strokeStyle=rgba(PAPER.shade,a); ctx.lineWidth=o.lw(1.4,0.8); ctx.lineCap='round';
  ctx.beginPath(); ctx.moveTo(apex[0],apex[1]); ctx.lineTo(apex[0],apex[1]-4); ctx.stroke();
}
function craneShape(ctx,o,x,y,sc,flap,a){
  if (a<=0.01) return;
  ctx.save(); ctx.translate(x,y); ctx.scale(sc,sc);
  ctx.fillStyle=rgba(C.banked,a*0.9); poly(ctx,[[-1.5,-1],[2.5,-1],[1+1.5*flap,-1-9*flap]]); ctx.fill();
  ctx.fillStyle=rgba(PAPER.mid,a); poly(ctx,[[-3,-1],[-5.8,0.6],[-12,-8.5]]); ctx.fill();
  poly(ctx,[[3,-0.6],[5.8,0.6],[10,-10]]); ctx.fill();
  ctx.fillStyle=rgba(PAPER.shade,a); poly(ctx,[[10,-10],[14,-8.4],[9.4,-8.6]]); ctx.fill();
  ctx.fillStyle=rgba(PAPER.light,a); poly(ctx,[[-5.8,0.6],[0,-2.6],[5.8,0.6],[0,3.6]]); ctx.fill();
  ctx.fillStyle=rgba(flap>=0?C.accent:C.banked,a); poly(ctx,[[-2.4,-0.8],[3,-0.8],[-0.5-2*flap,-1-12*flap]]); ctx.fill();
  ctx.restore();
}
function paperStar(ctx,o,x,y,r,rot,a){
  if (a<=0.01) return;
  const pts=[];
  for (let i=0;i<10;i++){ const g=rot-Math.PI/2+i*Math.PI/5, rr=i%2? r*0.45: r; pts.push([x+rr*Math.cos(g),y+rr*Math.sin(g)]); }
  ctx.fillStyle=rgba(C.accent,a); poly(ctx,pts); ctx.fill();
  ctx.fillStyle=rgba(C.hot,a);
  for (let i=0;i<10;i+=2){ poly(ctx,[[x,y],pts[i],pts[(i+1)%10]]); ctx.fill(); }
}

// ---- the bird --------------------------------------------------------------------
function drawBird(o,s,fx,fy,an){
  const ctx=o.ctx, p=s.p, cs=s.cs, st=s.state, t=s.t, calm=s.calm;
  const dk=k=>mixc(k,mul(k,0.42),p.dark);
  const lineW=o.lw(0.9,0.5);
  ctx.save();
  ctx.translate(-4+p.x+fx, -2+p.by+fy);
  ctx.translate(2,16); ctx.rotate(p.tilt); ctx.scale(1+0.06*p.puff, p.sy*(1+0.05*p.puff)); ctx.translate(-2,-16);
  const fs=(Math.sign(p.face)||1)*Math.max(Math.abs(p.face)*cs.fold,0.07);
  ctx.scale(fs,1);
  if (st==='sending' && fx!==0) ctx.rotate(fx>0? -0.25: 0.15);
  // crumple jitter (confused, a shake-out)
  const cj = cs.jit>0? cs.jit : (st==='confused' && !calm)? Math.exp(-t/0.25)*3.2 : 0;
  const seed=Math.floor(s.T*30);
  const J=(pt,i)=>cj? [pt[0]+(hash(seed+i*7.1)-0.5)*cj*2, pt[1]+(hash(seed+i*3.3+1)-0.5)*cj*2] : pt;
  const T0=J([-16,2],1), Kp=J([-3,-9],2), N2=J([11,-4],3), Cc=J([17,5],4), Bb=J([1,16],5);
  /* legs */
  const lg=p.legs;
  ctx.strokeStyle=rgba(dk(C.dim),1); ctx.lineWidth=o.lw(1.6,0.9); ctx.lineCap='round'; ctx.lineJoin='round';
  ctx.beginPath(); ctx.moveTo(2,14); ctx.lineTo(1,14+9*lg); ctx.lineTo(4,14+9*lg);
  ctx.moveTo(7,12); ctx.lineTo(8,12+11*lg); ctx.lineTo(11,12+11*lg); ctx.stroke();
  /* head (drawn before the body when it's tucked away under the wing) */
  const head=()=>{
    ctx.save(); ctx.translate(lerp(9,1,p.tuck), -7+p.dark*4+lerp(0,3,p.tuck)); ctx.rotate(p.hr);
    ctx.scale((Math.sign(p.hs)||1)*Math.max(Math.abs(p.hs),0.08),1);
    const H=[J([-4,3],8),J([0,-12],9),J([11,-5],10),J([6,3],11)];
    ctx.fillStyle=rgba(dk(PAPER.light),1); poly(ctx,H); ctx.fill();
    ctx.fillStyle=rgba(dk(PAPER.mid),1); poly(ctx,[H[0],H[1],H[3]]); ctx.fill();
    /* beak */
    const bo=p.bo;
    ctx.fillStyle=rgba(dk(C.banked),1); ctx.save(); ctx.translate(10,-1.5); ctx.rotate(bo*0.55); poly(ctx,[[0,-0.5],[8,0.5],[-1,2.5]]); ctx.fill(); ctx.restore();
    ctx.fillStyle=rgba(dk(C.accent),1); ctx.save(); ctx.translate(10,-1.5); ctx.rotate(-bo*0.25); poly(ctx,[[0,-4.5],[11,-0.8],[0,0.5]]); ctx.fill(); ctx.restore();
    if (cs.note){ ctx.save(); ctx.translate(18,0); ctx.rotate(0.2); ctx.fillStyle=rgba(C.fg,1); poly(ctx,[[-1,-3],[9,-3],[9,4],[-1,4]]); ctx.fill();
      ctx.strokeStyle=rgba(C.accent,1); ctx.lineWidth=o.lw(1,0.6); ctx.beginPath(); ctx.moveTo(-1,-3); ctx.lineTo(4,1); ctx.lineTo(9,-3); ctx.stroke(); ctx.restore(); }
    if (cs.card>0){
      /* approval: a little card held up to you, a box waiting for its tick */
      const pu=calm?1:0.6+0.4*Math.sin(s.T*3.2);
      ctx.save(); ctx.translate(18,-1); ctx.rotate(-0.32);
      ctx.fillStyle=rgba(C.fg,1); poly(ctx,[[-1,-6],[12,-6],[12,6],[-1,6]]); ctx.fill();
      ctx.fillStyle=rgba(PAPER.mid,1); poly(ctx,[[-1,1.5],[12,1.5],[12,6],[-1,6]]); ctx.fill();
      ctx.strokeStyle=rgba(C.accent,pu); ctx.lineWidth=o.lw(1.1,0.7); ctx.lineJoin='round';
      line(ctx,[[2.6,-3.6],[7.4,-3.6],[7.4,1.2],[2.6,1.2]]); ctx.closePath(); ctx.stroke();
      ctx.restore();
    }
    if (an && an.held) an.held(o,s,s.antic);
    /* eye */
    const ly = cs.ly!=null? cs.ly : (st==='thinking'? -1 : clamp(s.look.y,-1,1));
    const ex=4.5+cs.lx*0.9, ey=-5+ly*0.8;
    ctx.fillStyle=rgba(INK,1); ctx.strokeStyle=rgba(INK,1); ctx.lineCap='round';
    const elw=o.lw(1.5,0.9);
    let mode=cs.eye;
    if (!mode){
      if (p.happy>0.5) mode='happy';
      else if (st==='off' || st==='asleep' || p.lid>0.86) mode='closed';
      else if (s.blink>0.5 && st!=='drowsy') mode='closed';
      else mode='open';
    }
    if (mode==='happy'){ ctx.lineWidth=elw; ctx.beginPath(); ctx.arc(ex,ey+1.2,2.2,Math.PI*1.15,Math.PI*1.85); ctx.stroke(); }
    else if (mode==='closed'){ ctx.lineWidth=elw; ctx.beginPath(); ctx.moveTo(ex-2,ey+0.5); ctx.lineTo(ex+2,ey+0.5); ctx.stroke(); }
    else if (mode==='wide'){
      ctx.beginPath(); ctx.arc(ex,ey,Math.max(2.6,o.mw(1.2)),0,TAU); ctx.fill();
      if (!o.small){ ctx.fillStyle=rgba(PAPER.light,0.9); ctx.beginPath(); ctx.arc(ex+0.8,ey-0.9,0.8,0,TAU); ctx.fill(); }
    } else {
      const r=Math.max(st==='confused'?1.4:2,o.mw(1)), lid=p.lid;
      if (lid>0.12){
        const cut=ey-r+2*r*lid*0.85;
        ctx.save(); ctx.beginPath(); ctx.rect(ex-r-1,cut,2*r+2,2*r+2); ctx.clip();
        ctx.beginPath(); ctx.arc(ex,ey,r,0,TAU); ctx.fill(); ctx.restore();
        ctx.lineWidth=o.lw(1.1,0.7); ctx.beginPath(); ctx.moveTo(ex-r-0.5,cut); ctx.lineTo(ex+r+0.5,cut); ctx.stroke();
      } else { ctx.beginPath(); ctx.arc(ex,ey,r,0,TAU); ctx.fill(); }
    }
    ctx.restore();
  };
  if (p.tuck>0.5) head();
  /* tail */
  const tl=p.tl;
  ctx.fillStyle=rgba(dk(PAPER.shade),1); poly(ctx,[T0,J([-37,-11-tl*7],6),J([-31,5-tl*3],7)]); ctx.fill();
  ctx.strokeStyle=rgba(PAPER.line,0.45); ctx.lineWidth=lineW; ctx.beginPath(); ctx.moveTo(T0[0],T0[1]); ctx.lineTo(-33,-2-tl*5); ctx.stroke();
  /* body facets */
  ctx.fillStyle=rgba(dk(PAPER.mid),1); poly(ctx,[T0,Kp,Bb]); ctx.fill();
  ctx.fillStyle=rgba(dk(PAPER.light),1); poly(ctx,[Kp,N2,Cc,Bb]); ctx.fill();
  ctx.strokeStyle=rgba(PAPER.line,0.4); ctx.lineWidth=lineW; ctx.beginPath(); ctx.moveTo(Kp[0],Kp[1]); ctx.lineTo(Bb[0],Bb[1]); ctx.stroke();
  if (p.sq>0.05){
    ctx.strokeStyle=rgba(PAPER.line,0.55*p.sq); ctx.beginPath(); ctx.moveTo(-10,0); ctx.lineTo(-4,5); ctx.lineTo(1,0); ctx.lineTo(6,6); ctx.lineTo(12,2); ctx.stroke();
  }
  if (p.tuck<=0.5) head();
  /* wing: shows its terracotta inside when raised; drapes over a tucked head */
  const ang=(170+80*p.w)*Math.PI/180, W1=J([-6,-5],12), W2=J([8,-6],13);
  const tip=[W1[0]+28*Math.cos(ang), W1[1]+28*Math.sin(ang)];
  const u=clamp((W1[1]-tip[1])/18);
  ctx.fillStyle=rgba(dk(mixc(PAPER.mid,C.accent,u)),1);
  if (p.drape>0.02){ const D=[lerp(W2[0],5,p.drape),lerp(W2[1],5,p.drape)]; poly(ctx,[W1,W2,D,tip]); }
  else poly(ctx,[W1,W2,tip]);
  ctx.fill();
  ctx.strokeStyle=rgba(PAPER.line,0.4); ctx.lineWidth=lineW; ctx.beginPath(); ctx.moveTo((W1[0]+W2[0])/2,(W1[1]+W2[1])/2); ctx.lineTo(tip[0],tip[1]); ctx.stroke();
  /* a fresh crease catching the light (refolds) */
  if (cs.crease>0.02){
    ctx.strokeStyle=rgba(C.whitehot,0.9*cs.crease); ctx.lineWidth=o.lw(1.3,0.7);
    line(ctx,[T0,Kp,N2]); ctx.stroke();
    ctx.beginPath(); ctx.moveTo(Kp[0],Kp[1]); ctx.lineTo(Bb[0],Bb[1]); ctx.stroke();
  }
  if (an && an.body) an.body(o,s,s.antic);
  ctx.restore();
}

// ---- idle antics -------------------------------------------------------------------
// Each: pose(s, a, P) edits the bird's pose targets; update / under / draw / held as the
// README says; exit(o, s, c, u) the short "caught!" flourish over the real state.
const ANTICS={

  /* Fishing off the island's edge: a folded rod, a float, nibbles, a bite, a paper fish. */
  fishing:{
    start(s,a){ s.cs.prop={}; a.sp=0; },
    pose(s,a,P){
      const t=a.t, D=a.dur, tb=D-4.4, ty=D-3.6, cs=s.cs;
      P.face=1; P.legs=0.72; P.by=2.6; P.hr=0.14; cs.lx=0.7; cs.ly=0.9; P.tau.hr=0.12;
      if (t<0.9){ P.hr=-0.12; cs.ly=-0.2; }
      let nib=0;
      for (const x of [5.0,7.4]) if (x<tb-0.7) nib=Math.max(nib,bell(seg(t,x,x+0.45)));
      P.hr+=0.12*nib; if (nib>0.3) cs.eye='wide';
      if (t>=tb && t<ty){ cs.eye='wide'; P.by=1.2; P.hr=0.24; P.w=0.25; }
      if (t>=ty && t<ty+0.75){ P.tilt=-0.13*bell(seg(t,ty,ty+0.6)); P.hr=-0.3; P.w=0.5+0.4*Math.sin(TAU*7*t); P.tau.w=0.02; cs.ly=-0.6; }
      if (t>=ty+0.75 && t<D-0.45){ P.happy=1; P.hr=-0.1; cs.ly=0; }
    },
    geo(s,a){
      const t=a.t, D=a.dur, tb=D-4.4, ty=D-3.6;
      const L=30*easeOutBack(seg(t,0.1,0.9))*(1-easeInCubic(seg(t,D-0.75,D-0.1)));
      const G=headPt(s,15,0.4);
      let th=-0.95, bend=0;
      if (t>=tb && t<ty) bend=0.55+0.3*Math.sin(TAU*5*t);
      if (t>=ty) th=-0.95-0.42*bell(seg(t,ty,ty+0.75));
      const R=[G[0]+L*Math.cos(th+bend*0.45), G[1]+L*Math.sin(th+bend*0.45)];
      const Wl=50;
      let B=null, F=null, frot=0, line2='slack';
      if (t>=0.9){
        const fl=[R[0]+4+1.5*Math.sin(s.T*0.9), Wl+0.8*Math.sin(s.T*2.4)];
        let nib=0; for (const x of [5.0,7.4]) if (x<tb-0.7) nib=Math.max(nib,bell(seg(t,x,x+0.45)));
        fl[1]+=2.4*nib;
        if (t<1.8){ const u=seg(t,0.9,1.8); B=[lerp(R[0],fl[0],u)+6*bell(u), lerp(R[1],fl[1],easeInCubic(u))]; }
        else if (t<tb) B=fl;
        else if (t<ty){ B=[fl[0], fl[1]+5*easeOutCubic(seg(t,tb,tb+0.25))]; line2='taut'; }
        else {
          const u=seg(t,ty,ty+0.6), hang=[R[0],R[1]+10];
          const from=[fl[0],fl[1]+5];
          if (u<1){ F=lerp2(from,hang,easeOutCubic(u)); F[1]-=16*bell(u); frot=TAU*easeOutCubic(u); }
          else { const tt=t-ty-0.6, sw=0.55*Math.exp(-tt*1.4)*Math.sin(TAU*1.2*tt); F=[R[0]+10*Math.sin(sw), R[1]+10*Math.cos(sw)]; frot=-sw; }
          line2='taut';
        }
      }
      return {G,R,bend,B,F,frot,line2,Wl,L};
    },
    update(s,dt,a){
      const t=a.t, ty=a.dur-3.6;
      if (!a.sp && t>=ty+0.05){ a.sp=1; const g=this.geo(s,a); const x=g.R[0]+4;
        for (let i=0;i<6;i++) s.spawn({k:'dot',x:x+rnd(-3,3),y:g.Wl+2,vx:rnd(-16,16),vy:rnd(-42,-24),g:120,max:rnd(0.5,0.8),r:rnd(0.7,1.2),col:PAPER.light}); }
    },
    draw(o,s,a){
      const ctx=o.ctx, t=a.t, D=a.dur, g=this.geo(s,a), fade=1-seg(t,D-0.5,D-0.05);
      s.cs.prop={G:g.G,R:g.R,B:g.B||g.F};
      if (g.L<0.5) return;
      // the water off the edge: a faint surface and rings where the float lands
      const fx=g.R[0]+4, wa=clamp(t-0.9)*fade;
      if (wa>0){
        ctx.strokeStyle=rgba(PAPER.shade,0.32*wa); ctx.lineWidth=o.lw(0.8,0.5);
        ctx.beginPath(); for (let i=0;i<=10;i++){ const xx=fx-16+3.2*i, yy=g.Wl+2+0.7*Math.sin(xx*0.5+s.T*2.2); if (i) ctx.lineTo(xx,yy); else ctx.moveTo(xx,yy); } ctx.stroke();
        for (const te of [1.8,5.0,7.4,D-4.4,D-3.6]) ripple(ctx,o,fx,g.Wl+2,t-te,1.3,0.7*wa);
      }
      // line
      const end=g.F||g.B;
      if (end){
        ctx.strokeStyle=rgba(PAPER.mid,0.75*fade); ctx.lineWidth=o.lw(0.55,0.5);
        ctx.beginPath(); ctx.moveTo(g.R[0],g.R[1]);
        if (g.line2==='slack') ctx.quadraticCurveTo((g.R[0]+end[0])/2+3,(g.R[1]+end[1])/2+6,end[0],end[1]);
        else ctx.lineTo(end[0],end[1]);
        ctx.stroke();
      }
      if (g.B && !g.F){ const under=seg(g.B[1],g.Wl+1.5,g.Wl+5); bob(ctx,o,g.B[0],g.B[1],fade*(1-0.7*under)); }
      rod(ctx,o,g.G,g.R,g.bend,fade);
      if (g.F) fish(ctx,o,g.F[0],g.F[1],g.frot,1,fade);
    },
    exitDur:0.28,
    exit(o,s,c,u){
      const ctx=o.ctx, pr=s.cs.prop||{};
      if (pr.G && pr.R){
        ctx.save(); ctx.translate(pr.G[0]+6*u, pr.G[1]+26*u*u); ctx.rotate(1.1*u); ctx.translate(-pr.G[0],-pr.G[1]);
        rod(ctx,o,pr.G,pr.R,0,1-u); ctx.restore();
        if (pr.B){ bob(ctx,o,pr.B[0],pr.B[1]+10*u*u,1-u); }
      }
      startle(o,s,u);
    }
  },

  /* Preening: the head flips round, nibbles under a lifted wing, then a fluffy shake. */
  preen:{
    start(s,a){ a.f=0; },
    pose(s,a,P){
      const t=a.t, D=a.dur, cs=s.cs;
      const back=t>0.35 && t<D-1.45;
      P.hs=back?-1:1; P.tau.hs=0.07;
      if (back){
        const pk=Math.pow(Math.max(0,Math.sin(TAU*2.1*(t-0.35))),3);
        P.tuck=0.3; P.hr=-0.78+0.22*pk; P.w=0.06+0.14*pk; P.drape=0.25; P.bo=0.35*pk; P.tl=0.35; P.puff=0.5; P.tau.hr=0.035; P.tau.w=0.12;
        cs.eye=(Math.floor((t-0.35)*0.9)%2)?'closed':null;
      }
      const u=seg(t,D-1.3,D-0.6);
      if (u>0 && u<1){ cs.jit=1.8*bell(u); P.x=1.4*Math.sin(TAU*12*t)*bell(u); P.tau.x=0.012; P.puff=1.2*bell(u); P.w=0.18*Math.sin(TAU*10*t); P.tau.w=0.02; P.tl=0.4*Math.sin(TAU*8*t); P.tau.tl=0.02; }
      if (t>D-0.55) P.happy=1;
    },
    update(s,dt,a){
      const t=a.t, D=a.dur, f=Math.sign(s.p.face)||1;
      const marks=[1.55,2.75,D-1.15,D-1.0];
      while (a.f<marks.length && t>=marks[a.f]){
        const W=toWorld(s,-8,-5), many=a.f>=2;
        for (let i=0;i<(many?2:1);i++) fleck(s,W[0],W[1],rnd(-18,8)*f,rnd(-26,-12),pick([PAPER.light,PAPER.mid,C.accent]));
        a.f++;
      }
    },
    exitDur:0.25,
    exit(o,s,c,u){ startle(o,s,u); }
  },

  /* Folds itself into a paper boat and bobs on little waves, then unfolds. */
  boat:{
    start(s,a){ s.cs.prop={}; },
    pose(s,a,P){
      const t=a.t, D=a.dur, cs=s.cs;
      P.face=1;
      if (t<0.85){ const c=seg(t,0,0.45); P.by=3*c; P.legs=1-0.35*c; P.sy=1-0.08*c; cs.fold=1-0.93*easeInCubic(seg(t,0.5,0.85)); cs.crease=seg(t,0.4,0.85); }
      else if (t<D-0.95) cs.hide=1;
      else { const u=seg(t,D-0.95,D-0.6); cs.fold=lerp(0.07,1,easeOutBack(u)); cs.crease=1-u; const v=seg(t,D-0.55,D-0.15); cs.jit=1.2*bell(v); if (t>D-0.5) P.happy=1; }
    },
    k(a){ const t=a.t, D=a.dur; return t<0.85?0: t<1.25? easeOutBack(seg(t,0.85,1.25)) : t<D-1.35? 1 : t<D-0.95? 1-easeInCubic(seg(t,D-1.35,D-0.95)) : 0; },
    waves(o,s,a,back){
      const ctx=o.ctx, t=a.t, D=a.dur, wa=seg(t,0.6,1.2)*(1-seg(t,D-1.1,D-0.5));
      if (wa<=0.01) return;
      ctx.strokeStyle=rgba(back?PAPER.shade:PAPER.mid,(back?0.35:0.75)*wa); ctx.lineWidth=o.lw(back?1.0:1.3,0.7); ctx.lineCap='round';
      const y0=back?16:21, sp=back?-2.2:3.0, amp=back?1.2:1.7;
      ctx.beginPath();
      for (let i=0;i<=24;i++){ const x=-46+92*i/24, y=y0+amp*Math.sin(x*0.32+s.T*sp); if (i) ctx.lineTo(x,y); else ctx.moveTo(x,y); }
      ctx.stroke();
    },
    under(o,s,a){ this.waves(o,s,a,true); },
    draw(o,s,a){
      const k=this.k(a);
      if (k>0.001){
        const x=-4+3*Math.sin(s.T*0.5), y=17+1.2*Math.sin(s.T*2.1), rot=0.08*Math.sin(s.T*1.6);
        boatShape(o.ctx,o,s,x,y,rot,k,1);
      }
      this.waves(o,s,a,false);
    },
    exitDur:0.28,
    exit(o,s,c,u){
      const ctx=o.ctx, a=1-u;
      ctx.strokeStyle=rgba(PAPER.mid,0.7*a); ctx.lineWidth=o.lw(1.3,0.7); ctx.lineCap='round';
      ctx.beginPath(); for (let i=0;i<=16;i++){ const x=-40+80*i/16, y=21+1.7*Math.sin(x*0.32); if (i) ctx.lineTo(x,y); else ctx.moveTo(x,y); } ctx.stroke();
      startle(o,s,u);
    }
  },

  /* Folds into a paper plane and loops the desk (desktop companion only). */
  plane:{
    start(s,a){ s.cs.prop={}; },
    pose(s,a,P){
      const t=a.t, D=a.dur, cs=s.cs;
      P.face=1;
      if (t<0.7){ const c=seg(t,0,0.4); P.by=3*c; P.legs=1-0.35*c; P.sy=1-0.08*c; cs.fold=1-0.93*easeInCubic(seg(t,0.4,0.7)); cs.crease=seg(t,0.35,0.7); }
      else if (t<D-0.45) cs.hide=1;
      else { const u=seg(t,D-0.45,D-0.15); cs.fold=lerp(0.07,1,easeOutBack(u)); cs.crease=1-u; P.by=-6*bell(seg(t,D-0.35,D)); P.tau.by=0.02; if (t>D-0.3) P.happy=1; }
    },
    path(u){ const g=TAU*u; return [80*Math.sin(g), -24*(1-Math.cos(g))+7*Math.sin(3*g)*Math.sin(g/2)]; },
    state(s,a){
      const t=a.t, D=a.dur, f0=1.3, f1=D-1.05;
      let k=1, pos=[-2,8], rot=-0.05, flip=1, u=-1;
      if (t<1.0) k=easeOutBack(seg(t,0.7,1.0));
      if (t>=0.95 && t<f0){ rot=-0.05-0.18*bell(seg(t,0.95,f0)); pos=[-2-2*bell(seg(t,0.95,f0)),8]; }
      if (t>=f0 && t<f1){
        u=easeInOut(seg(t,f0,f1)); const P0=this.path(u), P1=this.path(Math.min(1,u+0.004));
        pos=[-2+P0[0],8+P0[1]];
        rot=Math.atan2(P1[1]-P0[1],P1[0]-P0[0]);
        if (Math.cos(rot)<0) flip=-1;
      }
      if (t>=f1){ pos=[-2+5*easeOutCubic(seg(t,f1,f1+0.5))-5,8]; rot=0.06*bell(seg(t,f1,f1+0.4)); }
      if (t>=D-0.75) k=1-easeInCubic(seg(t,D-0.75,D-0.45));
      return {k,pos,rot,flip,u};
    },
    under(o,s,a){
      const g=this.state(s,a); if (g.k<=0.01) return;
      const ctx=o.ctx, alt=clamp((8-g.pos[1])/60);
      ctx.fillStyle=rgba([0,0,0],0.28*(1-alt)); oval(ctx,g.pos[0],25,15*(1-alt*0.5),2.6,0); ctx.fill();
    },
    draw(o,s,a){
      const ctx=o.ctx, g=this.state(s,a);
      s.cs.prop={pos:g.pos};
      if (g.u>0){
        for (let i=1;i<=14;i++){
          const uu=g.u-i*0.011; if (uu<=0) break;
          const q=this.path(uu);
          ctx.fillStyle=rgba(PAPER.light,0.45*(1-i/15)); ctx.beginPath(); ctx.arc(-2+q[0],8+q[1],Math.max(0.9,o.mw(0.8))*(1-i/20),0,TAU); ctx.fill();
        }
      }
      if (g.k>0.01) planeShape(ctx,o,s,g.pos[0],g.pos[1],g.rot,g.flip,g.k,1);
    },
    exitDur:0.28,
    exit(o,s,c,u){
      const ctx=o.ctx, a=1-u;
      ctx.strokeStyle=rgba(PAPER.light,0.6*a); ctx.lineWidth=o.lw(1.2,0.7); ctx.lineCap='round';
      for (const yy of [-6,0,6]){ ctx.beginPath(); ctx.moveTo(-44+20*u,yy); ctx.lineTo(-30+10*u,yy); ctx.stroke(); }
      startle(o,s,u);
    }
  },

  /* Reads a tiny folded letter: out from under the wing, unfolded, read, tucked away. */
  letter:{
    start(s,a){ s.cs.prop={}; },
    geo(s,a){
      const t=a.t, D=a.dur, home=[34,21], wing=toWorld(s,-6,-3);
      let pos=home, w=14, h=18, fold=0, alpha=1, spin=0;
      if (t<0.8){ const u=seg(t,0.2,0.8); pos=lerp2(wing,home,easeInOut(u)); pos[1]-=12*bell(u); fold=1; spin=TAU*easeInOut(u); alpha=seg(t,0.15,0.3); }
      else if (t<1.3) fold=1-easeOutBack(seg(t,0.8,1.3));
      else if (t<D-1.8) fold=0;
      else if (t<D-1.3) fold=easeInCubic(seg(t,D-1.8,D-1.3));
      else { const u=seg(t,D-1.3,D-0.75); pos=lerp2(home,wing,easeInOut(u)); pos[1]-=10*bell(u); fold=1; spin=-TAU*easeInOut(u); alpha=1-seg(t,D-0.9,D-0.75); }
      w=lerp(14,8,clamp(fold)); h=lerp(18,5,clamp(fold));
      const rd=Math.floor(clamp((t-1.4)/(D-3.4))*3.999);
      return {pos,w,h,alpha,spin,read:t>1.4&&t<D-1.8?rd:t>=D-1.8?3:0};
    },
    pose(s,a,P){
      const t=a.t, D=a.dur, cs=s.cs;
      P.face=1;
      if (t<0.5 || (t>D-1.4 && t<D-0.6)) P.w=0.42;
      if (t>=0.5 && t<D-1.4){
        P.hr=0.32; P.legs=0.85; P.by=1.4; P.tilt=0.05;
        if (t>1.4 && t<D-1.8){
          const span=(D-3.4)/3, i=Math.floor((t-1.4)/span), f=((t-1.4)/span)%1;
          cs.lx=-0.7+1.4*easeInOut(f); cs.ly=0.4+0.25*i; P.hr=0.28+0.06*i;
          if (i===2 && f>0.75){ P.happy=1; P.hr=0.15; }
        } else { cs.ly=0.6; cs.lx=0.4; }
      }
      if (t>D-0.55) P.happy=1;
    },
    draw(o,s,a){
      const ctx=o.ctx, g=this.geo(s,a);
      s.cs.prop={pos:g.pos,w:g.w,h:g.h};
      ctx.save(); ctx.translate(g.pos[0],g.pos[1]-g.h/2); ctx.rotate(g.spin); ctx.translate(-g.pos[0],-(g.pos[1]-g.h/2));
      letterCard(ctx,o,g.pos[0],g.pos[1],g.w,g.h,g.alpha,g.read);
      ctx.restore();
    },
    exitDur:0.28,
    exit(o,s,c,u){
      const pr=s.cs.prop||{};
      if (pr.pos){ const ctx=o.ctx; ctx.save(); ctx.translate(pr.pos[0]+4*u,pr.pos[1]-2+8*u*u); ctx.rotate(0.9*u); letterCard(ctx,o,0,2,8,5,1-u,0); ctx.restore(); }
      startle(o,s,u);
    }
  },

  /* Folds a paper crane, peck by peck; the crane flaps and flies away. */
  crane:{
    start(s,a){ s.cs.prop={}; },
    SH:[ [[0,-9],[9,0],[0,9],[-9,0]], [[0,0],[9,0],[0,9],[-9,0]], [[0,-6],[6.5,2],[0,9],[-6.5,2]] ],
    COL:[PAPER.light, C.accent, PAPER.mid],
    pecks:[1.3,2.3,3.3],
    pose(s,a,P){
      const t=a.t, D=a.dur, cs=s.cs;
      P.face=1; P.by=1.4; P.legs=0.86; cs.lx=0.8; cs.ly=t<0.9? lerp(-0.8,0.7,seg(t,0.2,0.9)) : 0.7; P.hr=t<0.9?lerp(-0.3,0.2,seg(t,0.2,0.9)):0.2;
      for (const tp of this.pecks){ const k=bell(seg(t,tp-0.24,tp+0.08)); P.hr+=0.5*k; P.tilt+=0.12*k; P.by+=1.6*k; if (k>0.6) P.bo=0.4; }
      P.tau.hr=0.04; P.tau.tilt=0.04;
      if (t>4.2){ const u=seg(t,4.6,6.2); P.hr=lerp(0.2,-0.55,easeInOut(u)); cs.ly=lerp(0.6,-1,u); P.tau.hr=0.12; }
      if (t>6.6 && t<D-0.8){ P.happy=1; P.w=0.3+0.22*Math.sin(TAU*3*t); P.tau.w=0.03; }
    },
    draw(o,s,a){
      const ctx=o.ctx, t=a.t, D=a.dur, c=[38,13];
      // the sheet, step by step
      const pop=easeOutBack(seg(t,3.3,3.65));
      if (t<3.6){
        let i=0; while (i<2 && t>=this.pecks[i]+0.0) i++;
        let sh=this.SH[0], col=this.COL[0], sq=1, from=0, e=1;
        if (t<this.pecks[0]) { sh=this.SH[0]; }
        else {
          const k=t>=this.pecks[1]?1:0; from=k;
          e=easeOutBack(seg(t,this.pecks[k],this.pecks[k]+0.35));
          sh=this.SH[k].map((v,j)=>lerp2(v,this.SH[k+1][j],e)); col=mixc(this.COL[k],this.COL[k+1],clamp(e));
          sq=1-0.45*bell(seg(t,this.pecks[k],this.pecks[k]+0.35));
        }
        const drop=t<1.0? (1-easeOutCubic(seg(t,0.15,1.0)))*-34 : 0, al=seg(t,0.15,0.45)*(1-pop);
        const sc=1-pop;
        if (al>0.01){
          ctx.save(); ctx.translate(c[0],c[1]+drop); ctx.rotate(t<1?0.5*(1-seg(t,0.15,1.0)):0); ctx.scale(sc,sq*sc);
          ctx.fillStyle=rgba([0,0,0],0.2*al); oval(ctx,0,10,9,1.4,0); ctx.fill();
          ctx.fillStyle=rgba(col,al); poly(ctx,sh); ctx.fill();
          ctx.strokeStyle=rgba(PAPER.line,0.45*al); ctx.lineWidth=o.lw(0.7,0.45);
          ctx.beginPath(); ctx.moveTo(sh[3][0],sh[3][1]); ctx.lineTo(sh[1][0],sh[1][1]); ctx.moveTo(sh[0][0],sh[0][1]); ctx.lineTo(sh[2][0],sh[2][1]); ctx.stroke();
          ctx.restore();
        }
      }
      if (t>=3.3){
        let pos=[c[0],c[1]+4], sc=pop, al=1, flap=0.35;
        if (t>3.8 && t<4.9) flap=0.35+0.4*bell(seg(t,4.1,4.5));
        if (t>=4.9){
          const u=seg(t,4.9,D-0.6);
          pos=[c[0]+14*u+5*Math.sin(u*7), c[1]+4-62*easeInOut(u)];
          sc=1-0.55*u; flap=Math.sin(TAU*3.6*t); al=1-seg(u,0.82,1);
        }
        s.cs.prop={pos};
        craneShape(ctx,o,pos[0],pos[1],sc*1.5,flap,al);
      } else s.cs.prop={pos:c};
    },
    exitDur:0.26,
    exit(o,s,c,u){
      const pr=s.cs.prop||{}, ctx=o.ctx;
      if (pr.pos){ ctx.save(); ctx.translate(pr.pos[0],pr.pos[1]+6*u); ctx.scale(1,1-0.8*u); ctx.fillStyle=rgba(PAPER.light,1-u); poly(ctx,[[-8,0],[8,0],[6,3],[-6,3]]); ctx.fill(); ctx.restore(); }
      startle(o,s,u);
    }
  },

  /* Autumn: a leaf twirls down, Wren tracks it, hops, and catches it in its beak. */
  leaf:{
    start(s,a){ s.cs.prop={}; a.col=pick([C.accent,OCHRE,C.banked]); a.side=K.random()<0.5?-1:1; },
    tc:4.3,
    pos(s,a){
      const t=a.t, tc=this.tc, B=headPt(s,22,0.5);
      const u=clamp(t/tc), start=[-14*a.side-2,-60];
      let x=lerp(start[0],B[0],easeInOut(u))+13*a.side*Math.sin(2.3*t)*Math.pow(1-u,0.8);
      let y=lerp(start[1],B[1],Math.pow(u,1.1));
      return [x,y, 0.75*Math.cos(2.3*t)*(1-u)];
    },
    pose(s,a,P){
      const t=a.t, D=a.dur, tc=this.tc, cs=s.cs;
      P.face=1;
      if (t<tc){
        const L=this.pos(s,a);
        cs.lx=clamp((L[0]-6)/22,-1,1); cs.ly=clamp((L[1]+14)/22,-1,1);
        P.hr=clamp((L[1]+8)/80,-0.6,0.15); P.tau.hr=0.15;
        const h=seg(t,tc-0.42,tc+0.05); P.by=-6*bell(h); P.legs=1-0.35*bell(h); P.tau.by=0.03;
        if (t>tc-0.35) P.bo=0.9;
      } else {
        P.hr=-0.22; cs.ly=-0.2;
        if (t<tc+0.9){ P.w=0.3+0.25*Math.sin(TAU*6*t); P.tau.w=0.02; }
        if (t>tc+0.3 && t<D-0.5) P.happy=1;
      }
    },
    held(o,s,a){
      if (a.t<this.tc) return;
      const al=1-seg(a.t,a.dur-0.45,a.dur-0.05), wig=0.25*Math.exp(-(a.t-this.tc)*3)*Math.sin(TAU*3*(a.t-this.tc));
      leafShape(o.ctx,o,24,3,0.9+wig,1,a.col,al);
    },
    draw(o,s,a){
      if (a.t>=this.tc) { s.cs.prop={pos:headPt(s,24,3),col:a.col}; return; }
      const L=this.pos(s,a);
      s.cs.prop={pos:[L[0],L[1]],col:a.col};
      leafShape(o.ctx,o,L[0],L[1],L[2],1,a.col,seg(a.t,0,0.4));
    },
    exitDur:0.3,
    exit(o,s,c,u){
      const pr=s.cs.prop||{};
      if (pr.pos) leafShape(o.ctx,o,pr.pos[0]-4*u,pr.pos[1]+18*u*u,2.4*u,1,pr.col||C.accent,1-u);
      startle(o,s,u);
    }
  },

  /* Rain: opens a paper umbrella and stays snug while the drops bead off it. */
  umbrella:{
    start(s,a){ s.cs.prop={}; a.shook=0; },
    cx:-6, cy:-34, R:33,
    k(a){ const t=a.t, D=a.dur; return easeOutBack(seg(t,0.2,0.85))*(1-easeInCubic(seg(t,D-1.0,D-0.45))); },
    pose(s,a,P){
      const t=a.t, D=a.dur, cs=s.cs;
      P.face=1; P.by=1.5; P.legs=0.86; P.puff=0.45; P.tl=0.15;
      if (t<0.9){ P.hr=-0.4; cs.ly=-1; }
      else if (t>2.2 && t<4.2){ cs.ly=-0.7; cs.lx=-0.4; P.hr=-0.25; }
      else if (t>5.4 && t<7.2) P.happy=1;
      const v=seg(t,D-0.42,D-0.05);
      if (v>0 && v<1){ cs.jit=1.4*bell(v); P.x=1.2*Math.sin(TAU*12*t)*bell(v); P.tau.x=0.012; }
    },
    update(s,dt,a){
      if (!a.shook && a.t>a.dur-0.4){ a.shook=1;
        for (let i=0;i<6;i++) s.spawn({k:'dot',x:rnd(-24,16),y:rnd(-12,6),vx:rnd(-40,40),vy:rnd(-30,-10),g:110,max:rnd(0.4,0.7),r:rnd(0.7,1.1),col:RAIN}); }
    },
    under(o,s,a){
      const ctx=o.ctx, k=this.k(a), al=clamp(k*2);
      if (al<=0.01) return;
      ctx.strokeStyle=rgba(PAPER.shade,al); ctx.lineWidth=o.lw(1.4,0.8); ctx.lineCap='round';
      const x=this.cx-6;
      ctx.beginPath(); ctx.moveTo(this.cx,this.cy-12); ctx.lineTo(x,17); ctx.arc(x+2.6,17,2.6,Math.PI,0,true); ctx.stroke();
    },
    draw(o,s,a){
      const ctx=o.ctx, t=a.t, D=a.dur, k=this.k(a), cx=this.cx, cy=this.cy, R=this.R;
      const kr=seg(t,0,0.6)*(1-seg(t,D-0.5,D));
      const half=R*k, apexY=cy-14*(0.45+0.55*k);
      const roof=x=>{ const f=Math.abs(x-cx)/Math.max(half,0.01); return f<1? lerp(apexY,cy+3*f*f,f) : 999; };
      if (kr>0.01){
        ctx.lineWidth=o.lw(0.9,0.6); ctx.lineCap='round';
        for (let i=0;i<14;i++){
          const x=-57+i*8.6+2.5*(hash(i*3.1)-0.5), sp=105+35*hash(i*1.7), off=hash(i*7.7)*90;
          const y=-64+((s.T*sp+off)%90);
          const top=roof(x);
          if (y>top-1 && top<900){
            const age=y-top; if (age<6){ ctx.fillStyle=rgba(RAIN,0.7*kr*(1-age/6)); ctx.fillRect(x-1.6,top-1.2-age*0.3,Math.max(0.8,o.mw(0.8)),Math.max(0.8,o.mw(0.8))); ctx.fillRect(x+1.0,top-1.6-age*0.4,Math.max(0.8,o.mw(0.8)),Math.max(0.8,o.mw(0.8))); }
            continue;
          }
          if (y>22){ const age=y-22; if (age<5){ ctx.strokeStyle=rgba(RAIN,0.6*kr*(1-age/5)); ctx.beginPath(); ctx.arc(x,23,1+age*0.5,Math.PI*1.1,Math.PI*1.9); ctx.stroke(); } continue; }
          ctx.strokeStyle=rgba(RAIN,0.6*kr); ctx.beginPath(); ctx.moveTo(x+0.9,y-5); ctx.lineTo(x,y); ctx.stroke();
        }
      }
      canopy(ctx,o,cx,cy,R,k,clamp(k*2));
      // drips off the two rim points
      if (k>0.8 && kr>0.01){
        for (const sd of [-1,1]){
          const ph=((s.T*0.9+(sd>0?0.5:0))%1), x=cx+sd*half, y=cy+3+ph*ph*30;
          ctx.fillStyle=rgba(RAIN,0.75*kr*(1-ph)); ctx.beginPath(); ctx.arc(x,y,Math.max(0.9,o.mw(0.8)),0,TAU); ctx.fill();
        }
      }
      s.cs.prop={k};
    },
    exitDur:0.26,
    exit(o,s,c,u){
      const ctx=o.ctx, pr=s.cs.prop||{};
      canopy(ctx,o,this.cx,this.cy,this.R,(pr.k||1)*(1-easeInCubic(u))*0.9,1-u);
      startle(o,s,u);
    }
  },

  /* Hops along the island and back, turning round with a paper flip. */
  hop:{
    start(s,a){ a.dust=0; },
    hops:[[0.35,1],[0.95,1],[1.55,1],[2.75,-1],[3.35,-1],[3.95,-1]],
    pose(s,a,P){
      const t=a.t, D=a.dur;
      let x=0, by=0, legs=1, w=0, tilt=0, sy=1;
      for (const [h,d] of this.hops){
        const u=seg(t,h,h+0.42), b=bell(u);
        x+=7*d*easeInOut(u); by-=7*b; legs-=0.45*b; w+=0.32*b; tilt+=0.1*b;
        sy-=0.08*bell(seg(t,h+0.42,h+0.56));
      }
      const face = t<2.15?1: t<2.45? lerp(1,-1,easeInOut(seg(t,2.15,2.45))) : t<4.6? -1 : lerp(-1,1,easeInOut(seg(t,4.6,4.9)));
      P.face=face; P.x=x; P.by=by; P.legs=legs; P.w=w; P.tilt=tilt*(face>=0?1:-1); P.sy=sy;
      P.tau.face=0.03; P.tau.x=0.02; P.tau.by=0.015; P.tau.legs=0.03; P.tau.w=0.03; P.tau.sy=0.02;
      if (t>5.05 && t<5.7) P.happy=1;
    },
    update(s,dt,a){
      const t=a.t;
      while (a.dust<this.hops.length && t>=this.hops[a.dust][0]+0.42){
        const W=toWorld(s,5,23);
        for (let i=0;i<2;i++) s.spawn({k:'smoke',x:W[0]+rnd(-3,3),y:23,vx:rnd(-8,8),vy:rnd(-4,-1),max:0.5,r:1.4,grow:2.5,col:PAPER.shade});
        a.dust++;
      }
    },
    end(s,a,caught){ if (caught) for (let i=0;i<3;i++) s.spawn({k:'smoke',x:s.p.x+rnd(-6,8),y:23,vx:rnd(-12,12),vy:rnd(-5,-1),max:0.45,r:1.6,grow:3,col:PAPER.shade}); },
    exitDur:0.25,
    exit(o,s,c,u){ startle(o,s,u); }
  },

  /* A little envelope slides in; Wren postmarks it with two pecks and sends it on. */
  postmark:{
    start(s,a){ s.cs.prop={}; a.ink=0; },
    pecks:[1.25,2.25],
    env(a){
      const t=a.t; let x=30;
      if (t<0.7) x=lerp(66,30,easeOutCubic(seg(t,0,0.7)));
      else if (t>3.85) x=lerp(30,74,easeInCubic(seg(t,3.85,4.5)));
      let sy=1; for (const tp of this.pecks) sy-=0.16*bell(seg(t,tp,tp+0.16));
      return {x,sy,ring:seg(t,this.pecks[0],this.pecks[0]+0.2),wave:seg(t,this.pecks[1],this.pecks[1]+0.25)};
    },
    pose(s,a,P){
      const t=a.t, D=a.dur, cs=s.cs;
      P.face=1; cs.lx=0.8; cs.ly=0.7; P.hr=0.18;
      for (const tp of this.pecks){
        const r=bell(seg(t,tp-0.55,tp-0.12)), d=bell(seg(t,tp-0.12,tp+0.14));
        P.hr+=-0.55*r+0.5*d; P.by+=-1.8*r+2.4*d; P.tilt+=0.15*d; if (r>0.5) cs.eye='wide';
      }
      P.tau.hr=0.035; P.tau.by=0.03; P.tau.tilt=0.03;
      if (t>2.6 && t<3.5){ P.hr=-0.05+0.1*Math.sin(t*5); P.happy=t>2.9?1:0; }
      const n=bell(seg(t,3.6,4.05)); P.hr+=0.3*n; P.x+=3*n; P.tilt+=0.08*n;
      if (t>4.4 && t<5.3){ P.w=0.4+0.3*Math.sin(TAU*4*t); P.tau.w=0.03; cs.lx=1; cs.ly=0; P.hr=-0.1; }
      if (t>5.3) P.happy=1;
    },
    update(s,dt,a){
      while (a.ink<this.pecks.length && a.t>=this.pecks[a.ink]){
        for (let i=0;i<4;i++) s.spawn({k:'dot',x:24+rnd(-2,4),y:9,vx:rnd(-18,18),vy:rnd(-26,-10),g:90,max:rnd(0.35,0.6),r:rnd(0.6,1.0),col:C.banked});
        a.ink++;
      }
    },
    draw(o,s,a){
      const e=this.env(a);
      s.cs.prop={x:e.x};
      envelope(o.ctx,o,e.x,21,22,13,e.sy,1,e);
      if (a.t>3.85 && a.t<4.6){
        const ctx=o.ctx, al=bell(seg(a.t,3.85,4.6))*0.6;
        ctx.strokeStyle=rgba(PAPER.light,al); ctx.lineWidth=o.lw(1,0.6); ctx.lineCap='round';
        for (const yy of [10,15]){ ctx.beginPath(); ctx.moveTo(e.x-20,yy); ctx.lineTo(e.x-13,yy); ctx.stroke(); }
      }
    },
    exitDur:0.28,
    exit(o,s,c,u){
      const pr=s.cs.prop||{};
      if (pr.x!==undefined) envelope(o.ctx,o,pr.x+40*u*u,21,22,13,1,1-u,null);
      startle(o,s,u);
    }
  },

  /* A gust ruffles every fold; then Wren smooths itself down and refolds, crisp. */
  gust:{
    start(s,a){ a.sc=0; },
    g(t){ return seg(t,0.2,0.6)*(1-seg(t,2.8,3.2)); },
    pose(s,a,P){
      const t=a.t, D=a.dur, cs=s.cs, g=this.g(t);
      P.face=1;
      if (g>0){
        P.tl=1.3*g+0.35*g*Math.sin(TAU*6*t); P.w=g*(0.16+0.24*Math.abs(Math.sin(TAU*6.5*t)));
        P.tilt=0.1*g; P.hr=0.28*g; P.by=1.2*g; P.legs=1-0.12*g; P.puff=0.5*g; cs.jit=0.9*g;
        P.tau.w=0.02; P.tau.tl=0.03; if (g>0.5) cs.eye='closed';
      }
      if (t>3.25 && t<4.1){ const u=seg(t,3.25,4.1); P.w=-0.06; P.hs=u<0.75?-1:1; P.hr=-0.15; P.tau.hs=0.06; cs.crease=bell(u)*0.8; }
      if (t>4.15 && t<4.5) cs.fold=1-0.3*bell(seg(t,4.15,4.45));
      if (t>4.5) P.happy=1;
    },
    update(s,dt,a){
      const marks=[0.6,1.3,2.0];
      while (a.sc<marks.length && a.t>=marks[a.sc]){
        s.spawn({k:'tri',x:-60,y:rnd(-30,8),vx:rnd(85,110),vy:rnd(-12,8),drag:0.2,max:1.3,r:rnd(1.6,2.4),rot:rnd(0,6),vr:rnd(-10,10),col:pick([PAPER.mid,C.accent,PAPER.light])});
        a.sc++;
      }
    },
    draw(o,s,a){
      const ctx=o.ctx, t=a.t, g=this.g(t);
      if (g<=0.01) return;
      ctx.lineWidth=o.lw(1.0,0.6); ctx.lineCap='round';
      for (let i=0;i<5;i++){
        const ph=(t*1.25+hash(i*2.3))%1, x=-72+144*ph, y=-36+i*12+3*Math.sin(ph*6+i);
        ctx.strokeStyle=rgba(PAPER.mid,0.5*g*bell(ph));
        ctx.beginPath(); ctx.moveTo(x-22,y); ctx.quadraticCurveTo(x-11,y+3,x,y-2); ctx.stroke();
      }
    },
    exitDur:0.25,
    exit(o,s,c,u){ startle(o,s,u); }
  },

  /* Night: sits back and watches the stars; a shooting star; a wish. */
  stars:{
    start(s,a){ s.cs.prop={}; },
    ST:[[-52,-48,2.6],[-38,-36,1.8],[-6,-56,2.2],[6,-42,1.6],[24,-54,2.8],[40,-44,2.0],[54,-30,1.6]],
    pose(s,a,P){
      const t=a.t, D=a.dur, cs=s.cs;
      P.face=1; P.legs=0.66; P.by=3; P.hr=-0.55; P.puff=0.3; cs.ly=-1; cs.lx=0.15+0.25*Math.sin(t*0.45); P.tau.hr=0.25;
      if (t>6 && t<7){ const u=seg(t,6,6.8); cs.lx=lerp(-0.8,1,u); P.hr=lerp(-0.62,-0.38,u); P.tau.hr=0.08; }
      if (t>7.1 && t<8.7) cs.eye='closed';
      if (t>8.7 && t<9.4) P.happy=1;
    },
    draw(o,s,a){
      const ctx=o.ctx, t=a.t, D=a.dur, out=1-seg(t,D-1.2,D-0.2);
      this.ST.forEach((q,i)=>{
        const fin=seg(t,0.3+i*0.32,0.9+i*0.32), tw=0.55+0.45*Math.sin(s.T*(1.3+hash(i)*1.6)+i*2.1);
        sparkle(ctx,q[0],q[1],Math.max(q[2],o.mw(1.3)),STAR,fin*tw*out);
      });
      paperStar(ctx,o,-22,-46,Math.max(4.2,o.mw(2.2)),0.12*Math.sin(s.T*0.8),seg(t,1.8,2.6)*out);
      if (t>6 && t<7){
        const u=seg(t,6,6.8), hx=lerp(-46,48,easeInCubic(u)), hy=lerp(-58,-28,easeInCubic(u)), fa=1-seg(t,6.75,7);
        for (let i=0;i<8;i++){ const q=i/8, x=hx-(94*0.12)*q*u*3, y=hy-(30*0.12)*q*u*3;
          ctx.fillStyle=rgba(STAR,fa*(1-q)*0.8); ctx.beginPath(); ctx.arc(x,y,Math.max(1.4*(1-q*0.7),o.mw(0.7)),0,TAU); ctx.fill(); }
      }
      if (t>7.4 && t<8.8){ const H=headPt(s,2,-15); sparkle(ctx,H[0]+3,H[1]-4,Math.max(2.2,o.mw(1.2)),C.hot,bell(seg(t,7.4,8.8))); }
    },
    exitDur:0.25,
    exit(o,s,c,u){
      this.ST.forEach(q=>sparkle(o.ctx,q[0],q[1],Math.max(q[2],o.mw(1.3))*(1-u),STAR,(1-u)*0.8));
      startle(o,s,u);
    }
  }
};

// ---- the character ------------------------------------------------------------------
const Wren={
  id:'wren', name:'Wren', kind:'folded-paper bird',
  line:'A little origami bird, cream on one side and terracotta on the other. Turns to face you, carries your notes.',
  enter(s,st){
    const cs=s.cs; cs.burst=0; cs.fl=0; cs.dz=0; cs.nodT=null; cs.sbT=null; cs.sb=2.6;
    if (st==='asleep') cs.zT=1.4;
    if (st==='sending'||st==='working'||st==='approval_needed') cs.face=1;
  },
  update(s,dt){
    const st=s.state,t=s.t,cs=s.cs,calm=s.calm,L=s.L,A=s.A;
    const an=s.antic && !calm ? ANTICS[s.antic.id] : null;
    if (!s.antic && st!=='sending' && st!=='working' && st!=='approval_needed' && Math.abs(s.look.x)>0.22) cs.face=Math.sign(s.look.x);
    if (st==='sending'||st==='working'||st==='approval_needed') cs.face=1;
    const P={face:cs.face??1, hr:-s.look.y*0.25, by:0, x:0, w:0, bo:0, tl:0, dark:0, legs:1, happy:0, sq:0,
             tilt:0, lid:0, tuck:0, hs:1, sy:1, puff:0, drape:0, tau:{}};
    cs.fold=1; cs.hide=0; cs.jit=0; cs.eye=null; cs.lx=0; cs.ly=null; cs.crease=0; cs.card=0;
    let flapAmp=0, flapHz=0, byTau=0.08;
    switch(st){
      case 'idle': {
        if (!calm && !s.antic){ cs.pk=(cs.pk??5)-dt; if (cs.pk<0){ cs.pkT=0; cs.pk=7+K.random()*6; } }
        if (cs.pkT!=null){ cs.pkT+=dt; const u=cs.pkT/0.75; if (u>1||s.antic) cs.pkT=null; else P.hr+=0.6*Math.sin(Math.PI*u); }
        P.by = calm?0: Math.sin(s.it*1.1)*0.5 - s.notice*s.notice*7;
        P.hr -= s.notice*0.3; byTau=0.03;
        break;
      }
      case 'off': P.dark=1; P.hr=0.85; P.by=3; P.legs=0.45; break;
      case 'listening': P.hr=-0.22-0.12*L+(calm?0:Math.sin(s.T*2)*0.03); P.tl=L; P.w=0.08+0.32*L; break;
      case 'thinking': P.hr=-0.42; P.by=calm?0:Math.sin(t*1.6)*0.8; break;
      case 'talking': P.bo=Math.pow(A,0.8); P.by=-A*2; P.hr=-0.08-A*0.14; break;
      case 'happy': {
        P.happy = t<2.4?1:0; byTau=0.03;
        if (!calm){ if (t<1.2){ flapAmp=0.75; flapHz=7; } if (t<1.0) P.by=-12*Math.abs(Math.sin(Math.PI*t/0.5)); }
        P.hr=-0.2; break;
      }
      case 'confused': P.hr=0.5; P.sq=1; if (!calm) P.x=2.5*1.6*Math.exp(-t/0.11)*Math.sin(TAU*5.5*t); break;
      case 'working': P.hr = calm?0.2: 0.38*Math.pow(Math.max(0,Math.sin(TAU*t/0.85)),6); break;
      case 'sending': {
        if (t<0.3){ P.by=3; P.hr=0.1; }
        else if (t<1.25){ flapAmp=0.8; flapHz=6; P.legs=0.3; }
        else if (t<2.25){ flapAmp=t<2.05?0.8:0; flapHz=6; P.legs=t<2.0?0.3:1; }
        if (t>2.2 && t<3.4) P.happy=1;
        break;
      }
      case 'woke': {
        // the wake word: a startled little perk-up, eye wide, a flick of the wing
        P.hr=-0.3; P.tl=0.6*bell(seg(t,0,0.5)); cs.eye=t<0.55?'wide':null;
        if (!calm){ P.by=-6*bell(seg(t,0,0.36)); P.sy=1+0.08*bell(seg(t,0,0.3)); if (t<0.4){ flapAmp=0.45; flapHz=8; } byTau=0.025; }
        break;
      }
      case 'approval_needed': {
        // holds a little card up to you and waits, head tilted, now and then a hopeful tap
        cs.card=1; cs.lx=0.4; cs.ly=-0.2; P.hr=-0.2;
        if (!calm){
          P.hr+=0.07*Math.sin(t*1.4); P.by=Math.sin(t*1.4)*0.6;
          const ph=t%2.8; if (ph<0.55){ P.w=0.3*bell(ph/0.55); P.tau.w=0.04; }
        }
        break;
      }
      case 'drowsy': {
        // the head sinks, the lids get heavy, slow blinks; now and then it catches itself
        P.legs=0.85; P.by=1.8; P.tl=-0.15;
        if (calm){ P.hr=0.28; P.lid=0.6; break; }
        cs.dz=(cs.dz||0)+dt;
        const sink=easeInOut(clamp(cs.dz/7.5));
        P.hr=-0.05+0.62*sink; P.by+=2.4*sink; P.lid=0.42+0.46*sink; P.puff=0.3*sink; P.tau.hr=0.4; P.tau.lid=0.3;
        if (cs.dz>8.2){ cs.dz=0; cs.nodT=0; }
        if (cs.nodT!=null){
          cs.nodT+=dt; const u=cs.nodT/0.6;
          if (u>1) cs.nodT=null;
          else { P.tau.hr=0.06; P.tau.lid=0.04; P.lid=0.12; P.by-=1.5*bell(u); P.w=0.18*bell(u); }
        }
        cs.sb-=dt;
        if (cs.sb<0 && cs.nodT==null && cs.dz<6.5){ cs.sbT=0; cs.sb=3.6+K.random()*2.8; }
        if (cs.sbT!=null){ cs.sbT+=dt; const u=cs.sbT/1.2; if (u>1) cs.sbT=null; else { P.lid=Math.max(P.lid,bell(clamp(u*1.15))); P.tau.lid=0.08; } }
        break;
      }
      case 'asleep': {
        // head tucked under the wing, puffed round, a slow paper breath
        P.tuck=1; P.hs=-1; P.hr=-0.38; P.by=4.2; P.legs=0.52; P.drape=1; P.dark=0.18; P.tl=-0.25; P.w=-0.05;
        P.puff = calm? 0.55 : 0.25+0.7*breath(s.T/4.6);
        P.tau.tuck=0.28; P.tau.hs=0.22; P.tau.puff=0.3; P.tau.drape=0.3;
        if (!calm){ cs.zT=(cs.zT??1.4)-dt; if (cs.zT<=0){ cs.zT=3.4; s.spawn({k:'z',x:-6,y:-20,vx:-2.2,vy:-4.5,max:3.4,r:5,col:PAPER.shade}); } }
        break;
      }
      case 'waking': {
        // a stretch, a shake-out, a crisp refold, a little hop
        if (calm){ P.tuck=t<0.6?1:0; P.hs=t<0.6?-1:1; P.drape=t<0.6?1:0; cs.eye=t<0.6?'closed':null; P.tau.tuck=0.12; P.tau.hs=0.1; P.tau.drape=0.12; break; }
        P.tau.tuck=0.07; P.tau.hs=0.06; P.tau.drape=0.06;
        if (t<0.45){ const u=seg(t,0.02,0.45); P.sy=1+0.14*bell(u); P.w=1.05*bell(seg(t,0.05,0.5)); P.hr=-0.45; P.legs=1.15; P.tau.w=0.05; P.tau.sy=0.04; cs.eye=t<0.25?'closed':null; }
        else if (t<0.88){
          const u=seg(t,0.45,0.88); cs.jit=2.4*bell(u); P.x=1.6*Math.sin(TAU*13*t)*bell(u); P.w=0.25+0.3*Math.sin(TAU*11*t);
          P.tl=0.5*Math.sin(TAU*9*t); P.puff=bell(u); P.tau.x=0.012; P.tau.w=0.015; P.tau.tl=0.02; cs.eye='closed';
          if (!cs.fl && t>0.55){ cs.fl=1; const W=toWorld(s,-4,-4); for (let i=0;i<4;i++) fleck(s,W[0],W[1],rnd(-30,30),rnd(-34,-14)); }
        }
        else if (t<1.08){ const u=seg(t,0.88,1.08); cs.fold=1-0.8*bell(u); cs.crease=bell(u); }
        else { const u=seg(t,1.1,1.48); P.by=-8*bell(u); P.legs=1-0.4*bell(u); P.w=0.2*bell(u); P.tau.by=0.02; if (t>1.46&&t<1.56) P.sy=0.93; P.happy=(t>1.15&&t<1.58)?1:0; }
        break;
      }
    }
    if (flapAmp>0) P.w = 0.45+flapAmp*Math.sin(TAU*flapHz*t);
    if (an && an.pose) an.pose(s,s.antic,P);
    const q=s.caught?0.35:1, tau=(k,d)=>(P.tau[k]??d)*q;
    s.fo('face',P.face,tau('face',0.1));
    s.fo('hr',P.hr,tau('hr',0.09)); s.fo('by',P.by,tau('by',byTau)); s.fo('x',P.x,tau('x',0.02));
    s.fo('w',P.w,tau('w',flapAmp>0?0.02:0.09)); s.fo('bo',P.bo,tau('bo',0.03)); s.fo('tl',P.tl,tau('tl',0.06)); s.fo('dark',P.dark,tau('dark',0.25));
    s.fo('legs',P.legs,tau('legs',0.08)); s.fo('happy',P.happy,tau('happy',0.06)); s.fo('sq',P.sq,tau('sq',0.3));
    s.fo('tilt',P.tilt,tau('tilt',0.08)); s.fo('lid',P.lid,tau('lid',0.12)); s.fo('tuck',P.tuck,tau('tuck',0.3)); s.fo('hs',P.hs,tau('hs',0.12));
    s.fo('sy',P.sy,tau('sy',0.05)); s.fo('puff',P.puff,tau('puff',0.2)); s.fo('drape',P.drape,tau('drape',0.25));
    if (st==='off'){
      cs.zT=(cs.zT??0.4)-dt;
      if (cs.zT<=0){ cs.zT=1.8; s.spawn({k:'z',x:16,y:-18,vx:5,vy:-7,max:2.4,r:7,col:C.dim}); }
    }
    if (st==='happy' && !cs.burst && t>0.05){
      cs.burst=1;
      for (let i=0;i<13;i++) s.spawn({k:'tri',x:rnd(-10,10),y:-18,vx:rnd(-45,45),vy:rnd(-95,-55),g:150,drag:0.6,max:rnd(1.2,1.8),r:rnd(2.5,4),rot:rnd(0,6),vr:rnd(-9,9),col:pick([PAPER.light,C.accent,C.ok,PAPER.mid])});
    }
  },
  draw(o,s){
    const ctx=o.ctx, p=s.p, st=s.state, t=s.t, calm=s.calm, cs=s.cs;
    const an=s.antic && !calm ? ANTICS[s.antic.id] : null;
    o.begin();
    const lineW=o.lw(0.9,0.5);
    /* flight path for sending */
    let fx=0, fy=0, gone=false, chk=0; cs.note=false;
    if (st==='sending'){
      cs.note = t>0.08 && t<1.25;
      if (!calm){
        if (t>=0.3 && t<1.25){ const u=(t-0.3)/0.95; fx=115*easeInCubic(u); fy=-55*easeInCubic(u)-6*Math.sin(Math.PI*clamp(u*3)); }
        else if (t>=1.25 && t<1.55) gone=true;
        else if (t>=1.55 && t<2.25){ const u=(t-1.55)/0.7; fx=-110*(1-easeOutCubic(u)); fy=-34*Math.pow(1-u,2); }
      } else cs.note = t<1.25;
      if (t>2.2) chk=clamp((t-2.2)/0.3)*(1-clamp((t-3.4)/0.3));
    }
    const lift=clamp(-(p.by+fy)/30);
    /* shadow */
    if (!cs.hide){
      ctx.fillStyle=rgba([0,0,0],0.32*(1-lift)*(gone?0:1)*Math.max(cs.fold,0.4));
      oval(ctx,p.x+fx*0.6,25,20*(1-lift*0.5)*(1+0.06*p.puff),3.2,0); ctx.fill();
    }
    /* working: a sheet being folded */
    if (st==='working'){
      const fp = calm? 0.5 : (0.5-0.5*Math.cos(TAU*t/1.7));
      const sx0=24, sy0=14, S=15;
      ctx.fillStyle=rgba(PAPER.mid,1); poly(ctx,[[sx0,sy0],[sx0+S,sy0],[sx0+S,sy0+S],[sx0,sy0+S]]); ctx.fill();
      const cx=sx0+S, cy=sy0, fd=S*0.6*fp+1;
      ctx.fillStyle=rgba(C.accent,1); poly(ctx,[[cx-fd,cy],[cx,cy+fd],[cx-fd,cy+fd]]); ctx.fill();
      ctx.fillStyle=rgba(C.bg,0.9); poly(ctx,[[cx-fd,cy],[cx,cy],[cx,cy+fd]]); ctx.fill();
      ctx.strokeStyle=rgba(PAPER.line,0.6); ctx.lineWidth=lineW; ctx.beginPath(); ctx.moveTo(cx-fd,cy); ctx.lineTo(cx,cy+fd); ctx.stroke();
    }
    if (!gone && !cs.hide) drawBird(o,s,fx,fy,an);
    /* thinking: three little folded triangles */
    if (st==='thinking'){
      for (let i=0;i<3;i++){
        const a=calm?0.8: 0.25+0.75*clamp(Math.sin(t*3-i*0.9)*0.5+0.5);
        const yy=-30-i*6+(calm?0:Math.sin(t*3-i)*1.5), xx=(p.face>=0?8:-14)+i*7*(p.face>=0?1:-1);
        ctx.fillStyle=rgba(i===2?C.accent:PAPER.light,a); poly(ctx,[[xx-3,yy+2],[xx+3,yy+2],[xx,yy-3]]); ctx.fill();
      }
    }
    if (st==='listening' && s.L>0.03){
      const dir = p.face>=0?1:-1;
      ctx.lineWidth=o.lw(1.4,0.8); ctx.lineCap='round';
      for (let i=0;i<3;i++){
        const fr=((s.T*1.4+i/3)%1), R=40-fr*18;
        ctx.strokeStyle=rgba(C.fg,s.L*(1-fr)*0.9*(fr<0.15?fr/0.15:1));
        ctx.beginPath(); ctx.arc(5,-8,R,dir>0?-0.5:Math.PI-0.5,dir>0?0.5:Math.PI+0.5); ctx.stroke();
      }
    }
    if (st==='woke' && t<0.7) startle(o,s,seg(t,0.12,0.7));
    if (st==='confused'){ drawQ(ctx,-2,-38,14,C.bad,clamp(t/0.2),o.lw(2.2,1.1)); }
    if (chk>0) drawCheck(ctx,26,-26,16,clamp((t-2.2)/0.3),C.ok,chk,o.lw(2.6,1.3));
    drawParts(o,s);
    o.end();
  },
  // frame rate: the kit's, plus a little more for Wren's slow drowsy blinks and idle head-bobs
  fps(s,B){
    const f=K.wantFps(s,B);
    if (s.calm || s.antic || s.caught) return f;
    const cs=s.cs, st=s.state;
    if (st==='drowsy' && (cs.sbT!=null || cs.nodT!=null)) return Math.max(f,15);
    if (st==='idle' && cs.pkT!=null) return Math.max(f,15);
    if (st==='approval_needed' && s.t>1.2) return Math.min(f,24);
    return f;
  },
  antics: ANTICS
};
return Wren;
}
