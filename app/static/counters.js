(() => {
  const colors = { limit: '#aeb8ca', actual: '#3563e9', over: '#e5484d' };
  function draw(canvas, rows) {
    const ctx = canvas.getContext('2d'), ratio = devicePixelRatio || 1;
    const width = canvas.clientWidth || 520, height = Number(canvas.getAttribute('height')) || 240;
    canvas.width = width * ratio; canvas.height = height * ratio; ctx.scale(ratio, ratio);
    ctx.clearRect(0, 0, width, height);
    if (!rows.length) { ctx.fillStyle='#667085'; ctx.fillText('선택 기간의 카운터 기록이 없습니다.', 20, 40); return; }
    const max = Math.max(...rows.map(r => Math.max(r.black_limit + r.color_limit, r.black + r.color)), 1);
    const left=42, bottom=34, top=18, plotH=height-bottom-top, groupW=(width-left-12)/rows.length, barW=Math.min(18, groupW/3);
    ctx.font='10px sans-serif'; ctx.textAlign='center';
    rows.forEach((r,i) => { const x=left+i*groupW+groupW/2, values=[r.black_limit+r.color_limit,r.black+r.color,r.over];
      values.forEach((v,j)=>{const h=v/max*plotH;ctx.fillStyle=[colors.limit,colors.actual,colors.over][j];ctx.fillRect(x+(j-1)*barW-barW/2,top+plotH-h,barW,h)});
      ctx.fillStyle='#667085'; ctx.fillText(r.month.slice(5),x,height-12);
    });
  }
  document.querySelectorAll('[data-open]').forEach(b=>b.addEventListener('click',()=>{const d=document.getElementById(b.dataset.open);d.showModal();d.dispatchEvent(new Event('dialogopen'));}));
  document.querySelectorAll('.counter-dialog').forEach(d=>{d.querySelectorAll('.dialog-close,.dialog-cancel').forEach(b=>b.addEventListener('click',()=>d.close()));d.addEventListener('click',e=>{if(e.target===d)d.close()});});
  document.querySelectorAll('.counter-dialog:not(.anomaly-dialog)').forEach(d=>{const data=JSON.parse(d.querySelector('.counter-data').textContent),years=[...new Set(data.map(r=>r.month.slice(0,4)))];let index=Math.max(0,years.length-1);const render=()=>{const year=years[index]||new Date().getFullYear().toString(),rows=data.filter(r=>r.month.startsWith(year));d.querySelector('.year-label').textContent=year+'년';d.querySelector('.usage-body').innerHTML=rows.map(r=>`<tr><td>${r.month.slice(5)}월</td><td>${r.black_limit.toLocaleString()} / <b>${r.black.toLocaleString()}</b></td><td>${r.color_limit.toLocaleString()} / <b>${r.color.toLocaleString()}</b></td></tr>`).join('')||'<tr><td colspan="3">기록 없음</td></tr>';draw(d.querySelector('.usage-chart'),rows)};d.querySelector('.year-prev').onclick=()=>{if(index>0){index--;render()}};d.querySelector('.year-next').onclick=()=>{if(index<years.length-1){index++;render()}};d.addEventListener('dialogopen',render);});
  document.querySelectorAll('.anomaly-dialog').forEach(d=>{const data=JSON.parse(d.querySelector('.counter-data').textContent),inputs=d.querySelectorAll('input[type=month]');const render=()=>draw(d.querySelector('.anomaly-chart'),data.filter(r=>(!inputs[0].value||r.month>=inputs[0].value)&&(!inputs[1].value||r.month<=inputs[1].value)));inputs.forEach(i=>i.addEventListener('change',render));d.addEventListener('dialogopen',render);});
})();
