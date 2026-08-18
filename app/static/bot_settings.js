(() => {
  const counterSample = document.querySelector('#sample-format');
  const serialSample = document.querySelector('#serial-sample');
  const status = document.querySelector('#file-status');
  const help = document.querySelector('#builder-help');
  const serialSourceSelect = document.querySelector('#serial-source-type');
  const serialFilename = document.querySelector('#serial-attachment-filename');
  const escapeRegex = (value) => value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&').replace(/\s+/g, '\\s+');
  const numberPattern = '[0-9]+(?:[,\\s][0-9]+)*';

  // Previous readings in the anchor must not make a rule specific to one month's values.
  const stableCounterAnchor = (value) => value.split(/([0-9][0-9,.]*)/).map((part) => (
    /^[0-9][0-9,.]*$/.test(part) ? '[0-9][0-9,.]*' : escapeRegex(part)
  )).join('');

  const syncSerialFilename = () => {
    const readsAttachment = serialSourceSelect.value !== 'email';
    serialFilename.disabled = !readsAttachment;
    serialFilename.closest('label').classList.toggle('disabled-field', !readsAttachment);
    if (!readsAttachment) serialFilename.value = '';
  };
  serialSourceSelect.addEventListener('change', syncSerialFilename);
  syncSerialFilename();

  document.querySelector('#read-file').addEventListener('click', async () => {
    const file = document.querySelector('#sample-file').files[0];
    if (!file) { status.textContent = '먼저 HTM, HTML 또는 RTF 파일을 선택하세요.'; return; }
    status.textContent = '파일을 읽는 중입니다…';
    const body = new FormData(); body.append('file', file);
    try {
      const response = await fetch('/bot-settings/preview', {method: 'POST', body});
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || '파일을 읽지 못했습니다.');
      counterSample.value = result.text;
      const attachmentType = file.name.toLowerCase().endsWith('.rtf') ? 'rtf' : 'html_attachment';
      document.querySelector('#source-type').value = attachmentType;
      document.querySelector('#attachment-filename').value = file.name;
      status.textContent = `${result.filename} 내용을 읽었습니다. 아래에서 실제 값을 선택하세요.`;
    } catch (error) { status.textContent = error.message; }
  });

  document.querySelectorAll('.target-button').forEach((button) => button.addEventListener('click', () => {
    const isSerial = button.dataset.target === 'serial_pattern';
    const serialSource = serialSourceSelect.value;
    // Samsung and similar reports contain both counters and serial in the same
    // attachment. In that case the loaded attachment textarea is the serial
    // selection source too; the separate box is only for email text.
    const serialUsesLoadedAttachment = isSerial && serialSource !== 'email';
    const sample = isSerial && !serialUsesLoadedAttachment ? serialSample : counterSample;
    const start = sample.selectionStart, end = sample.selectionEnd;
    const selected = sample.value.slice(start, end).trim();
    if (!selected) {
      help.textContent = isSerial ? '시리얼 원본 텍스트에서 시리얼 값을 선택하세요.' : '카운터 원본 텍스트에서 값을 선택하세요.';
      sample.focus(); return;
    }
    const prefix = sample.value.slice(Math.max(0, start - 160), start);
    // Subject, date and recipient headers vary on every delivery. Anchor mail
    // serials only to the label on the selected line, not the whole sample.
    const serialPrefix = prefix.split(/\r?\n/).pop().slice(-80);
    const anchor = isSerial ? escapeRegex(serialPrefix) : stableCounterAnchor(prefix);
    const valuePattern = isSerial ? '[-A-Za-z0-9._/]+' : numberPattern;
    document.querySelector(`[name="${button.dataset.target}"]`).value = `${anchor}(${valuePattern})`;
    help.textContent = `‘${selected}’의 위치를 지정했습니다. 값의 자릿수가 바뀌어도 이 위치에서 새 값을 읽습니다.`;
  }));
})();
