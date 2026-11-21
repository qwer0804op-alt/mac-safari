'use strict';

const $ = (id) => document.getElementById(id);
const loginView = $('loginView');
const controlView = $('controlView');
const codeInput = $('code');
const loginButton = $('loginButton');
const loginMessage = $('loginMessage');
const screen = $('screen');
const sessionEnded = '연결이 종료됐습니다. Mac에서 새 코드를 받아 주세요.';
const screenTimeoutMs = 18000;
const actionTimeoutMs = 18000;
let session = null;
let internetMode = false;
let refreshTimer = null;
let screenRequest = null;
let currentImageUrl = null;
let lastImageSize = null;
let loginPending = false;
let logoutPending = false;
let actionQueue = Promise.resolve();
let inputGeneration = 0;
let gesture = null;
let statusRequest = null;
let statusRetry = null;
let deviceName = 'Mac';
let streamFailed = false;
let streamFailures = 0;
let pendingFrame = null;
let presenting = false;
const fpsSelect = $('fpsSelect');
const stallMs = 8000;
let wheelTotal = 0;
let wheelTimer = null;
let wheelBusy = false;
const keyCatcher = $('keyCatcher');
const keyNames = {
  Enter: 'enter', Tab: 'tab', Escape: 'escape', Backspace: 'backspace', Delete: 'delete',
  ArrowUp: 'up', ArrowDown: 'down', ArrowLeft: 'left', ArrowRight: 'right',
  Home: 'home', End: 'end', PageUp: 'pageup', PageDown: 'pagedown',
};

function setStatus(connected, text) {
  $('statusDot').classList.toggle('connected', connected);
  $('statusText').textContent = text;
}

function connectionError() {
  return internetMode
    ? `${deviceName}에 연결할 수 없습니다. Mac 리모컨에서 인터넷 공유 상태를 확인해 주세요.`
    : `${deviceName}에 연결할 수 없습니다. Wi-Fi와 Mac 리모컨 실행 상태를 확인해 주세요.`;
}

function applyPlatform(platform) {
  deviceName = platform === 'windows' ? 'PC' : 'Mac';
  document.querySelectorAll('[data-device]').forEach((element) => {
    element.textContent = element.dataset.device.replace('{d}', deviceName);
  });
  screen.alt = `${deviceName} 화면. 누르면 해당 위치를 클릭합니다.`;
}

function clearScreen(message = '화면을 불러오는 중', loading = true) {
  if (currentImageUrl) URL.revokeObjectURL(currentImageUrl);
  currentImageUrl = null;
  lastImageSize = null;
  gesture = null;
  screen.removeAttribute('src');
  $('screenSpinner').hidden = !loading;
  $('screenMessage').textContent = message;
  $('screenPlaceholder').hidden = false;
}

function cancelScreenRefresh() {
  clearTimeout(refreshTimer);
  const request = screenRequest;
  screenRequest = null;
  if (request) request.abort();
}

function pauseScreen(message, loading) {
  inputGeneration++;
  clearTimeout(wheelTimer);
  wheelTimer = null;
  wheelTotal = 0;
  keyCatcher.value = '';
  cancelScreenRefresh();
  clearScreen(message, loading);
}

function showLogin(message = '') {
  setImmersive(false);
  if (session) session.abort();
  session = null;
  actionQueue = Promise.resolve();
  pauseScreen();
  loginView.hidden = false;
  controlView.hidden = true;
  $('controlMessage').textContent = '';
  $('textInput').value = '';
  loginMessage.textContent = message;
  codeInput.value = '';
  codeInput.removeAttribute('aria-invalid');
  setStatus(false, '연결 안 됨');
  codeInput.focus({ preventScroll: true });
}

function savedFps() {
  try {
    const value = localStorage.getItem('macRemoteFps');
    if (['60', '30', '15', '0'].includes(value)) return value;
  } catch {}
  return null;
}

function showControl() {
  loginView.hidden = true;
  controlView.hidden = false;
  loginMessage.textContent = '';
  if (!session) {
    fpsSelect.value = savedFps() ?? (internetMode ? '30' : '60');
    streamFailed = false;
    streamFailures = 0;
    session = new AbortController();
    $('lastUpdate').textContent = '불러오는 중';
    setStatus(true, '연결됨');
    refreshScreen();
  }
}

async function api(path, options = {}) {
  let response;
  try {
    response = await fetch(path, { credentials: 'same-origin', cache: 'no-store', ...options });
  } catch {
    throw new Error(connectionError());
  }
  if (!response.ok) {
    let message = '요청을 처리하지 못했습니다.';
    try { const body = await response.json(); message = body.error || message; } catch {}
    const error = new Error(message);
    error.status = response.status;
    throw error;
  }
  return response;
}

const jsonOptions = (body) => ({ method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });

function stopStatusCheck() {
  clearTimeout(statusRetry);
  if (statusRequest) statusRequest.abort();
  statusRequest = null;
}

async function checkStatus() {
  if (session || loginPending || document.hidden || !navigator.onLine) return;
  stopStatusCheck();
  const request = new AbortController();
  statusRequest = request;
  const deadline = setTimeout(() => request.abort(), screenTimeoutMs);
  try {
    const response = await api('/api/status', { signal: request.signal });
    const status = await response.json();
    if (statusRequest !== request) return;
    internetMode = status.internet === true;
    applyPlatform(status.platform);
    if (internetMode) $('codeHint').textContent = 'Mac 리모컨의 4자리 코드를 입력한 다음, Mac에서 연결을 승인해 주세요.';
    if (status.authenticated === true) showControl();
    else {
      loginView.hidden = false;
      loginMessage.textContent = '';
      setStatus(false, '연결 안 됨');
      codeInput.focus({ preventScroll: true });
    }
  } catch {
    if (statusRequest !== request) return;
    loginView.hidden = false;
    loginMessage.textContent = connectionError();
    setStatus(false, '재연결 중');
    statusRetry = setTimeout(checkStatus, 3000);
  } finally {
    clearTimeout(deadline);
    if (statusRequest === request) statusRequest = null;
  }
}

codeInput.addEventListener('input', () => {
  codeInput.removeAttribute('aria-invalid');
  if (!loginPending) loginMessage.textContent = '';
  const digits = codeInput.value.replace(/[^0-9]/g, '');
  if (digits !== codeInput.value) codeInput.value = digits;
  // The fourth digit connects right away, so no extra tap is needed.
  if (digits.length === 4 && !loginPending) $('loginForm').requestSubmit();
});

$('loginForm').addEventListener('submit', async (event) => {
  event.preventDefault();
  if (loginPending) return;
  const code = codeInput.value;
  if (code.length !== 4 || /[^0-9]/.test(code)) {
    codeInput.setAttribute('aria-invalid', 'true');
    loginMessage.textContent = '숫자 4자리를 입력해 주세요.';
    codeInput.focus();
    return;
  }
  stopStatusCheck();
  loginPending = true;
  loginButton.disabled = true;
  loginMessage.textContent = '';
  if (internetMode) {
    codeInput.readOnly = true;
    $('loginButtonLabel').textContent = '승인 대기 중';
    loginMessage.classList.add('approval-pending');
    loginMessage.textContent = 'Mac 리모컨에서 연결을 승인해 주세요.';
  }
  try {
    await api('/api/login', jsonOptions({ code }));
    codeInput.value = '';
    showControl();
  } catch (error) {
    if (error.status === 401 && code === codeInput.value) {
      codeInput.setAttribute('aria-invalid', 'true');
      codeInput.select();
    }
    loginMessage.textContent = error.message;
  } finally {
    loginPending = false;
    loginButton.disabled = false;
    codeInput.readOnly = false;
    $('loginButtonLabel').textContent = '연결하기';
    loginMessage.classList.remove('approval-pending');
  }
});

$('logoutButton').addEventListener('click', async () => {
  if (!session || logoutPending) return;
  const currentSession = session;
  logoutPending = true;
  $('logoutButton').disabled = true;
  try {
    await api('/api/logout', { ...jsonOptions({}), signal: currentSession.signal });
  } catch (error) {
    if (session !== currentSession) return;
    if (error.status !== 401) {
      $('controlMessage').textContent = error.message;
      return;
    }
  } finally {
    logoutPending = false;
    $('logoutButton').disabled = false;
  }
  if (session === currentSession) showLogin(sessionEnded);
});

function findPart(buffer) {
  // One part: headers, a blank line, Content-Length bytes of JPEG, then CRLF.
  let end = -1;
  for (let index = 0; index + 3 < buffer.length && index < 512; index++) {
    if (buffer[index] === 13 && buffer[index + 1] === 10 && buffer[index + 2] === 13 && buffer[index + 3] === 10) { end = index; break; }
  }
  if (end < 0) return null;
  const length = Number(/content-length:\s*(\d+)/i.exec(new TextDecoder().decode(buffer.subarray(0, end)))?.[1]);
  if (!Number.isInteger(length) || length <= 0 || length > 50 * 1024 * 1024) throw new Error('화면 스트림 형식이 올바르지 않습니다.');
  const start = end + 4;
  if (buffer.length < start + length + 2) return null;
  return { body: buffer.subarray(start, start + length), rest: buffer.subarray(start + length + 2) };
}

async function showStreamFrame(blob, counters) {
  const url = URL.createObjectURL(blob);
  const oldUrl = currentImageUrl;
  try {
    screen.src = url;
    await screen.decode();
  } catch {
    URL.revokeObjectURL(url);
    if (currentImageUrl === oldUrl && oldUrl) screen.src = oldUrl;
    return;
  }
  currentImageUrl = url;
  if (oldUrl) URL.revokeObjectURL(oldUrl);
  const size = { width: screen.naturalWidth, height: screen.naturalHeight };
  if (lastImageSize && (lastImageSize.width !== size.width || lastImageSize.height !== size.height)) gesture = null;
  lastImageSize = size;
  $('screenPlaceholder').hidden = true;
  setStatus(true, '연결됨');
  counters.shown++;
}

async function startStream(fps) {
  clearTimeout(refreshTimer);
  const request = new AbortController();
  screenRequest = request;
  const { signal } = request;
  const counters = { shown: 0 };
  let received = 0;
  let stall = null;
  let report = null;
  const arm = () => { clearTimeout(stall); stall = setTimeout(() => request.abort(), stallMs); };
  const present = async () => {
    if (presenting) return;
    presenting = true;
    try {
      while (pendingFrame && screenRequest === request) {
        const blob = pendingFrame;
        pendingFrame = null;
        await showStreamFrame(blob, counters);
      }
    } finally { presenting = false; }
  };
  let failure = null;
  try {
    arm();
    const response = await api(`/api/stream?fps=${fps}`, { signal });
    const reader = response.body.getReader();
    let buffer = new Uint8Array(0);
    let last = performance.now();
    report = setInterval(() => {
      const now = performance.now();
      if (screenRequest === request && counters.shown) $('lastUpdate').textContent = `실시간 ${Math.round(counters.shown * 1000 / (now - last))}fps`;
      counters.shown = 0;
      last = now;
    }, 1000);
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      arm();
      const joined = new Uint8Array(buffer.length + value.length);
      joined.set(buffer);
      joined.set(value, buffer.length);
      buffer = joined;
      for (let part = findPart(buffer); part; part = findPart(buffer)) {
        buffer = part.rest;
        received++;
        pendingFrame = new Blob([part.body], { type: 'image/jpeg' });
        present();
      }
    }
  } catch (error) {
    failure = error;
  } finally {
    clearTimeout(stall);
    clearInterval(report);
  }
  if (screenRequest !== request) return;
  screenRequest = null;
  pendingFrame = null;
  if (failure?.status === 401) return showLogin(sessionEnded);
  if (received > 0) streamFailures = 0;
  else if (failure?.status || ++streamFailures >= 2) streamFailed = true;
  refreshTimer = setTimeout(refreshScreen, received > 0 ? 400 : 200);
}

fpsSelect.addEventListener('change', () => {
  try { localStorage.setItem('macRemoteFps', fpsSelect.value); } catch {}
  streamFailed = false;
  streamFailures = 0;
  if (!session || document.hidden) return;
  cancelScreenRefresh();
  refreshScreen();
});

async function refreshScreen() {
  if (!session || document.hidden || !navigator.onLine || screenRequest) return;
  if (fpsSelect.value !== '0' && !streamFailed && window.ReadableStream) return startStream(Number(fpsSelect.value));
  clearTimeout(refreshTimer);
  const request = new AbortController();
  screenRequest = request;
  const { signal } = request;
  let imageUrl = null;
  let probe = null;
  const cancelled = new Promise((_, reject) => {
    signal.addEventListener('abort', () => {
      reject(new Error('화면을 불러오는 데 시간이 너무 오래 걸립니다. 새로고침을 눌러 다시 시도해 주세요.'));
    }, { once: true });
  });
  const deadline = setTimeout(() => request.abort(), screenTimeoutMs);

  async function loadFrame() {
    const response = await api('/api/screen', { signal });
    if (signal.aborted) return;
    const blob = await response.blob();
    if (signal.aborted) return;
    imageUrl = URL.createObjectURL(blob);
    probe = new Image();
    probe.src = imageUrl;
    await probe.decode();
  }

  try {
    await Promise.race([loadFrame(), cancelled]);
    if (screenRequest !== request) return;
    const oldUrl = currentImageUrl;
    currentImageUrl = imageUrl;
    imageUrl = null;
    if (lastImageSize && (lastImageSize.width !== probe.naturalWidth || lastImageSize.height !== probe.naturalHeight)) gesture = null;
    lastImageSize = { width: probe.naturalWidth, height: probe.naturalHeight };
    screen.src = currentImageUrl;
    if (oldUrl) URL.revokeObjectURL(oldUrl);
    $('screenPlaceholder').hidden = true;
    $('lastUpdate').textContent = '방금 업데이트';
    setStatus(true, '연결됨');
  } catch (error) {
    if (screenRequest !== request) return;
    if (error.status === 401) return showLogin(sessionEnded);
    clearScreen(error instanceof DOMException ? '화면 이미지를 읽지 못했습니다.' : error.message, false);
    $('lastUpdate').textContent = '연결 확인 필요';
    setStatus(false, '연결 확인 필요');
  } finally {
    clearTimeout(deadline);
    if (imageUrl) {
      if (probe) probe.removeAttribute('src');
      URL.revokeObjectURL(imageUrl);
    }
    if (screenRequest === request) {
      screenRequest = null;
      refreshTimer = setTimeout(refreshScreen, 1200);
    }
  }
}

$('refreshButton').addEventListener('click', () => {
  if (!session || document.hidden) return;
  cancelScreenRefresh();
  streamFailed = false;
  streamFailures = 0;
  if (!lastImageSize) {
    clearScreen();
    $('lastUpdate').textContent = '불러오는 중';
  }
  refreshScreen();
});
function reconnect() {
  if (session) refreshScreen();
  else if (!navigator.onLine && !loginPending) {
    loginView.hidden = false;
    setStatus(false, '연결 끊김');
    loginMessage.textContent = connectionError();
  } else checkStatus();
}
window.addEventListener('offline', () => {
  stopStatusCheck();
  if (!session) return reconnect();
  pauseScreen(internetMode ? '인터넷 연결을 확인해 주세요.' : 'Wi-Fi 연결을 확인해 주세요.', false);
  $('lastUpdate').textContent = '연결 끊김';
  setStatus(false, '연결 끊김');
});
window.addEventListener('online', reconnect);
window.addEventListener('pageshow', (event) => { if (event.persisted) reconnect(); });
document.addEventListener('visibilitychange', () => {
  if (document.hidden) {
    stopStatusCheck();
    pauseScreen();
  } else reconnect();
});

function sendAction(action) {
  if (!session) return Promise.resolve(false);
  const currentSession = session;
  const generation = inputGeneration;
  actionQueue = actionQueue.then(async () => {
    if (currentSession !== session || generation !== inputGeneration || logoutPending || document.hidden || !navigator.onLine) return false;
    const request = new AbortController();
    const abort = () => request.abort();
    const cancelled = new Promise((_, reject) => {
      request.signal.addEventListener('abort', () => {
        reject(new Error('Mac의 조작 응답을 받지 못했습니다. 화면을 확인하고 다시 눌러 주세요.'));
      }, { once: true });
    });
    currentSession.signal.addEventListener('abort', abort, { once: true });
    const deadline = setTimeout(abort, actionTimeoutMs);
    try {
      await Promise.race([
        api('/api/action', { ...jsonOptions(action), signal: request.signal }),
        cancelled,
      ]);
      if (currentSession !== session) return false;
      $('controlMessage').textContent = '';
      return true;
    } catch (error) {
      if (currentSession === session) {
        if (error.status === 401) showLogin(sessionEnded);
        else $('controlMessage').textContent = error.message;
      }
      return false;
    } finally {
      clearTimeout(deadline);
      currentSession.signal.removeEventListener('abort', abort);
    }
  });
  return actionQueue;
}

screen.addEventListener('pointerdown', (event) => {
  if (!event.isPrimary) { gesture = null; return; }
  if (!lastImageSize || !session || event.button !== 0) return;
  event.preventDefault();
  gesture = { x: event.clientX, y: event.clientY, pointerId: event.pointerId, dragged: false };
  screen.setPointerCapture(event.pointerId);
});
screen.addEventListener('pointermove', (event) => {
  if (gesture?.pointerId === event.pointerId && Math.hypot(event.clientX - gesture.x, event.clientY - gesture.y) > 12) gesture.dragged = true;
});
screen.addEventListener('pointerup', (event) => {
  if (!gesture || gesture.pointerId !== event.pointerId || !lastImageSize) return;
  event.preventDefault();
  const dx = event.clientX - gesture.x;
  const dy = event.clientY - gesture.y;
  if (gesture.dragged || Math.hypot(dx, dy) > 12) {
    if (Math.abs(dy) > 12) sendAction({ type: 'scroll', dy: Math.max(-1000, Math.min(1000, Math.round(-dy * 2))) });
  } else {
    const rect = screen.getBoundingClientRect();
    if (event.clientX >= rect.left && event.clientX < rect.right &&
        event.clientY >= rect.top && event.clientY < rect.bottom) {
      const x = Math.max(0, Math.min(lastImageSize.width - 1, Math.floor((event.clientX - rect.left) / rect.width * lastImageSize.width)));
      const y = Math.max(0, Math.min(lastImageSize.height - 1, Math.floor((event.clientY - rect.top) / rect.height * lastImageSize.height)));
      sendAction({ type: 'click', x, y });
      showRipple(event);
      // Only a mouse focuses the hidden keyboard field, so phones keep their on-screen keyboard closed.
      if (event.pointerType === 'mouse') keyCatcher.focus({ preventScroll: true });
    }
  }
  gesture = null;
});
function cancelGesture(event) {
  if (gesture && gesture.pointerId === event.pointerId) gesture = null;
}

screen.addEventListener('pointercancel', cancelGesture);
screen.addEventListener('lostpointercapture', cancelGesture);

screen.addEventListener('wheel', (event) => {
  if (!session || !lastImageSize) return;
  event.preventDefault();
  const unit = event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? screen.clientHeight : 1;
  wheelTotal += event.deltaY * unit;
  if (!wheelTimer && !wheelBusy) wheelTimer = setTimeout(flushWheel, 50);
}, { passive: false });

async function flushWheel() {
  wheelTimer = null;
  const dy = Math.max(-1000, Math.min(1000, Math.round(wheelTotal * 2)));
  wheelTotal = 0;
  if (!dy) return;
  wheelBusy = true;
  try {
    await sendAction({ type: 'scroll', dy });
  } finally {
    wheelBusy = false;
    if (wheelTotal && !wheelTimer) wheelTimer = setTimeout(flushWheel, 50);
  }
}

function showRipple(event) {
  const ripple = $('ripple');
  const area = screen.parentElement.getBoundingClientRect();
  ripple.style.left = `${event.clientX - area.left}px`;
  ripple.style.top = `${event.clientY - area.top}px`;
  ripple.classList.remove('go');
  void ripple.offsetWidth;
  ripple.classList.add('go');
}

function setImmersive(on) {
  document.body.classList.toggle('immersive', on);
  if (!on) {
    document.body.classList.remove('tools-open');
    $('toolsButton').setAttribute('aria-expanded', 'false');
  }
  const button = $('immersiveButton');
  button.setAttribute('aria-pressed', String(on));
  button.textContent = on ? '⤡' : '⛶';
  button.setAttribute('aria-label', on ? '전체 화면 닫기' : '전체 화면');
  button.title = button.getAttribute('aria-label');
  if (on && !document.fullscreenElement && document.documentElement.requestFullscreen) document.documentElement.requestFullscreen().catch(() => {});
  if (!on && document.fullscreenElement && document.exitFullscreen) document.exitFullscreen().catch(() => {});
}

$('immersiveButton').addEventListener('click', () => setImmersive(!document.body.classList.contains('immersive')));
$('toolsButton').addEventListener('click', () => {
  const open = document.body.classList.toggle('tools-open');
  $('toolsButton').setAttribute('aria-expanded', String(open));
});
document.addEventListener('fullscreenchange', () => {
  if (!document.fullscreenElement && document.body.classList.contains('immersive')) setImmersive(false);
});

document.querySelectorAll('[data-tab]').forEach((tab) => tab.addEventListener('click', () => {
  document.querySelectorAll('[data-tab]').forEach((other) => other.setAttribute('aria-selected', String(other === tab)));
  document.querySelectorAll('.tab-panel').forEach((panel) => panel.classList.toggle('active', panel.id === tab.dataset.tab));
}));

$('inputHint').textContent = matchMedia('(pointer: coarse)').matches
  ? '탭하면 클릭, 밀면 스크롤'
  : '누른 뒤 키보드로 입력, 휠로 스크롤';

function flushTyped() {
  const text = keyCatcher.value.replaceAll('\0', '');
  keyCatcher.value = '';
  if (!text || !session) return;
  const characters = Array.from(text);
  for (let index = 0; index < characters.length; index += 1000) {
    sendAction({ type: 'type', text: characters.slice(index, index + 1000).join('') });
  }
}

keyCatcher.addEventListener('keydown', (event) => {
  if (event.isComposing || event.keyCode === 229 || event.ctrlKey || event.metaKey || event.altKey) return;
  const key = keyNames[event.key];
  if (!key) return;
  event.preventDefault();
  sendAction({ type: 'key', key });
});
keyCatcher.addEventListener('input', (event) => { if (!event.isComposing) flushTyped(); });
keyCatcher.addEventListener('compositionend', flushTyped);

$('textForm').addEventListener('submit', async (event) => {
  event.preventDefault();
  const input = $('textInput');
  const text = input.value;
  if (!text) return;
  if (await sendAction({ type: 'type', text }) && input.value === text) input.value = '';
});
document.querySelectorAll('[data-key]').forEach((button) => button.addEventListener('click', () => sendAction({ type: 'key', key: button.dataset.key })));
document.querySelectorAll('[data-scroll]').forEach((button) => button.addEventListener('click', () => sendAction({ type: 'scroll', dy: Number(button.dataset.scroll) })));
if ('serviceWorker' in navigator && window.isSecureContext) {
  navigator.serviceWorker.register('/service-worker.js').catch(() => {});
}
reconnect();
