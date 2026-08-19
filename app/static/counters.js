(() => {
  const series = [
    { key: 'black_limit', label: '월 계약한도 흑백', color: '#667085', dash: [7, 5] },
    { key: 'color_limit', label: '월 계약한도 컬러', color: '#8b5cf6', dash: [7, 5] },
    { key: 'black', label: '실사용량 흑백', color: '#2563eb', dash: [] },
    { key: 'color', label: '실사용량 컬러', color: '#f97316', dash: [] },
  ];

  const niceStep = (range, ticks = 5) => {
    const rough = Math.max(range / ticks, 1);
    const magnitude = 10 ** Math.floor(Math.log10(rough));
    const normalized = rough / magnitude;
    const factor = normalized <= 1 ? 1 : normalized <= 2 ? 2 : normalized <= 5 ? 5 : 10;
    return factor * magnitude;
  };

  function summarize(rows, period) {
    const buckets = new Map();
    rows.forEach(row => {
      const [year, month] = row.month.split('-').map(Number);
      let key = row.month;
      if (period === 'quarterly') key = `${year} ${Math.ceil(month / 3)}분기`;
      if (period === 'half') key = `${year} ${month <= 6 ? '상반기' : '하반기'}`;
      if (period === 'yearly') key = `${year}년`;
      const bucket = buckets.get(key) || { month: key, black: 0, color: 0, black_limit: 0, color_limit: 0 };
      ['black', 'color', 'black_limit', 'color_limit'].forEach(field => { bucket[field] += Number(row[field]) || 0; });
      buckets.set(key, bucket);
    });
    return [...buckets.values()];
  }

  function draw(canvas, rows) {
    const ctx = canvas.getContext('2d');
    const ratio = window.devicePixelRatio || 1;
    const width = canvas.clientWidth || 520;
    const height = Number(canvas.getAttribute('height')) || 240;
    canvas.width = width * ratio;
    canvas.height = height * ratio;
    ctx.scale(ratio, ratio);
    ctx.clearRect(0, 0, width, height);

    if (!rows.length) {
      ctx.fillStyle = '#667085';
      ctx.font = '12px sans-serif';
      ctx.fillText('선택 기간의 카운터 기록이 없습니다.', 20, 40);
      return;
    }

    const values = rows.flatMap(row => series.map(item => Number(row[item.key]) || 0));
    const dataMin = Math.min(...values);
    const dataMax = Math.max(...values);
    const padding = Math.max((dataMax - dataMin) * 0.1, dataMax * 0.05, 1);
    const step = niceStep((dataMax - dataMin) + padding * 2);
    const axisMin = Math.max(0, Math.floor((dataMin - padding) / step) * step);
    let axisMax = Math.ceil((dataMax + padding) / step) * step;
    if (axisMax <= axisMin) axisMax = axisMin + step;

    const left = 68;
    const right = 16;
    const top = 18;
    const bottom = 48;
    const plotWidth = Math.max(width - left - right, 1);
    const plotHeight = height - top - bottom;
    const y = value => top + ((axisMax - value) / (axisMax - axisMin)) * plotHeight;
    const x = index => left + (rows.length === 1 ? plotWidth / 2 : index * plotWidth / (rows.length - 1));

    ctx.font = '10px sans-serif';
    ctx.lineWidth = 1;
    ctx.textAlign = 'right';
    ctx.textBaseline = 'middle';
    for (let tick = axisMin; tick <= axisMax + step / 2; tick += step) {
      const tickY = y(tick);
      ctx.strokeStyle = '#e6eaf0';
      ctx.beginPath();
      ctx.moveTo(left, tickY);
      ctx.lineTo(width - right, tickY);
      ctx.stroke();
      ctx.fillStyle = '#667085';
      ctx.fillText(Math.round(tick).toLocaleString(), left - 8, tickY);
    }

    const labelEvery = Math.max(1, Math.ceil(rows.length / Math.max(2, Math.floor(plotWidth / 48))));
    ctx.textAlign = 'center';
    ctx.textBaseline = 'top';
    rows.forEach((row, index) => {
      if (index % labelEvery === 0 || index === rows.length - 1) {
        ctx.fillStyle = '#667085';
        ctx.fillText(row.month, x(index), height - bottom + 10);
      }
    });

    series.forEach(item => {
      ctx.strokeStyle = item.color;
      ctx.fillStyle = item.color;
      ctx.lineWidth = 2;
      ctx.lineJoin = 'round';
      ctx.setLineDash(item.dash);
      ctx.beginPath();
      rows.forEach((row, index) => {
        const pointX = x(index);
        const pointY = y(Number(row[item.key]) || 0);
        if (index === 0) ctx.moveTo(pointX, pointY);
        else ctx.lineTo(pointX, pointY);
      });
      ctx.stroke();
      ctx.setLineDash([]);
      rows.forEach((row, index) => {
        ctx.beginPath();
        ctx.arc(x(index), y(Number(row[item.key]) || 0), 2.5, 0, Math.PI * 2);
        ctx.fill();
      });
    });

    ctx.save();
    ctx.translate(13, top + plotHeight / 2);
    ctx.rotate(-Math.PI / 2);
    ctx.fillStyle = '#475467';
    ctx.font = '11px sans-serif';
    ctx.textAlign = 'center';
    ctx.fillText('사용량', 0, 0);
    ctx.restore();
  }

  document.querySelectorAll('[data-open]').forEach(button => button.addEventListener('click', () => {
    const dialog = document.getElementById(button.dataset.open);
    dialog.showModal();
    dialog.dispatchEvent(new Event('dialogopen'));
  }));
  document.querySelectorAll('.counter-dialog').forEach(dialog => {
    dialog.querySelectorAll('.dialog-close,.dialog-cancel').forEach(button => button.addEventListener('click', () => dialog.close()));
    dialog.addEventListener('click', event => { if (event.target === dialog) dialog.close(); });
  });
  document.querySelectorAll('.counter-dialog:not(.anomaly-dialog)').forEach(dialog => {
    const data = JSON.parse(dialog.querySelector('.counter-data').textContent);
    const years = [...new Set(data.map(row => row.month.slice(0, 4)))];
    let index = Math.max(0, years.length - 1);
    const render = () => {
      const year = years[index] || new Date().getFullYear().toString();
      const period = dialog.querySelector('.usage-period').value;
      const source = period === 'yearly' ? data : data.filter(row => row.month.startsWith(year));
      const rows = summarize(source, period);
      dialog.querySelector('.year-label').textContent = `${year}년`;
      dialog.querySelector('.usage-body').innerHTML = rows.map(row => `<tr><td>${period === 'monthly' ? `${row.month.slice(5)}월` : row.month}</td><td>${row.black_limit.toLocaleString()} / <b>${row.black.toLocaleString()}</b></td><td>${row.color_limit.toLocaleString()} / <b>${row.color.toLocaleString()}</b></td></tr>`).join('') || '<tr><td colspan="3">기록 없음</td></tr>';
      draw(dialog.querySelector('.usage-chart'), rows);
    };
    dialog.querySelector('.year-prev').onclick = () => { if (index > 0) { index--; render(); } };
    dialog.querySelector('.year-next').onclick = () => { if (index < years.length - 1) { index++; render(); } };
    dialog.querySelector('.usage-period').addEventListener('change', render);
    dialog.addEventListener('dialogopen', render);
  });
  document.querySelectorAll('.anomaly-dialog').forEach(dialog => {
    const data = JSON.parse(dialog.querySelector('.counter-data').textContent);
    const inputs = dialog.querySelectorAll('input[type=month]');
    const period = dialog.querySelector('select[name=period_type]');
    const render = () => {
      const filtered = data.filter(row =>
        (!inputs[0].value || row.month >= inputs[0].value) &&
        (!inputs[1].value || row.month <= inputs[1].value));
      draw(dialog.querySelector('.anomaly-chart'), summarize(filtered, period.value));
    };
    inputs.forEach(input => input.addEventListener('change', render));
    period.addEventListener('change', render);
    dialog.addEventListener('dialogopen', render);
  });
})();
