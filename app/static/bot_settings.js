(() => {
  const sample = document.querySelector('#sample-format');
  const status = document.querySelector('#file-status');
  const help = document.querySelector('#builder-help');
  const escapeRegex = (value) => value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&').replace(/\s+/g, '\\s+');

  document.querySelector('#read-file').addEventListener('click', async () => {
    const file = document.querySelector('#sample-file').files[0];
    if (!file) { status.textContent = '먼저 HTM, HTML 또는 RTF 파일을 선택하세요.'; return; }
    status.textContent = '파일을 읽는 중입니다…';
    const body = new FormData(); body.append('file', file);
    try {
      const response = await fetch('/bot-settings/preview', {method: 'POST', body});
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || '파일을 읽지 못했습니다.');
      sample.value = result.text;
      document.querySelector('#source-type').value = file.name.toLowerCase().endsWith('.rtf') ? 'rtf' : 'html_attachment';
      status.textContent = `${result.filename} 내용을 읽었습니다. 아래에서 실제 값을 선택하세요.`;
    } catch (error) { status.textContent = error.message; }
  });

  document.querySelectorAll('.target-button').forEach((button) => button.addEventListener('click', () => {
    const start = sample.selectionStart, end = sample.selectionEnd;
    const selected = sample.value.slice(start, end).trim();
    if (!selected) { help.textContent = '먼저 원본 텍스트에서 추출할 값 하나를 드래그해 선택하세요.'; sample.focus(); return; }
    const lineStart = sample.value.lastIndexOf('\n', start - 1) + 1;
    const lineEndIndex = sample.value.indexOf('\n', end);
    const lineEnd = lineEndIndex < 0 ? sample.value.length : lineEndIndex;
    const prefix = sample.value.slice(lineStart, start).slice(-80);
    const suffix = sample.value.slice(end, lineEnd).slice(0, 40);
    const valuePattern = /^[-A-Za-z0-9._/]+$/.test(selected) && /[A-Za-z]/.test(selected) ? '[-A-Za-z0-9._/]+' : '[0-9][0-9, ]*';
    document.querySelector(`[name="${button.dataset.target}"]`).value = `${escapeRegex(prefix)}(${valuePattern})${escapeRegex(suffix)}`;
    help.textContent = `‘${selected}’ 위치를 지정했습니다. 같은 방식으로 필요한 값들을 지정하세요.`;
  }));
})();
