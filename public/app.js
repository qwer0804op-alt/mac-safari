'use strict';

const $ = (id) => document.getElementById(id);
const loginView = $('loginView');
const controlView = $('controlView');
const codeInput = $('code');
const loginButton = $('loginButton');
const loginMessage = $('loginMessage');
const screen = $('screen');
const sessionEnded = '연결이 종료됐어. Mac에서 새 코드를 받아.';
const screenTimeoutMs = 18000;
let session = null;
let internetMode = false;
let refreshTimer = null;
let screenRequest = null;
let currentImageUrl = null;
let lastImageSize = null;
let loginPending = false;
let logoutPending = false;
let actionQueue = Promise.resolve();
let gesture = null;

function setStatus(connected, text) {
  $('statusDot').classList.toggle('connected', connected);
  $('statusText').textContent = text;
}

function connectionError() {
  return internetMode
    ? 'Mac에 연결할 수 없어. Mac 리모컨에서 인터넷 공유 상태를 확인해.'
    : 'Mac에 연결할 수 없어. Wi-Fi와 Mac 리모컨 실행 상태를 확인해.';
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
  cancelScreenRefresh();
  clearScreen(message, loading);
}

function showLogin(message = '') {
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
}

function showControl() {
  loginView.hidden = true;
  controlView.hidden = false;
  loginMessage.textContent = '';
  if (!session) {
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
    let message = '요청을 처리하지 못했어.';
    try { const body = await response.json(); message = body.error || message; } catch {}
    const error = new Error(message);
    error.status = response.status;
    throw error;
  }
  return response;
}

const jsonOptions = (body) => ({ method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });

async function checkStatus() {
  try {
    const response = await api('/api/status');
    const status = await response.json();
    internetMode = status.internet === true;
    if (internetMode) $('codeHint').textContent = 'Mac 리모컨의 4자리 코드를 입력한 다음, Mac에서 연결을 승인해.';
    if (status.authenticated === true) showControl();
    else showLogin();
  } catch {
    showLogin(connectionError());
  }
}

codeInput.addEventListener('input', () => {
  codeInput.removeAttribute('aria-invalid');
  if (!loginPending) loginMessage.textContent = '';
});

$('loginForm').addEventListener('submit', async (event) => {
  event.preventDefault();
  if (loginPending) return;
  const code = codeInput.value;
  if (code.length !== 4 || /[^0-9]/.test(code)) {
    codeInput.setAttribute('aria-invalid', 'true');
    loginMessage.textContent = '숫자 4자리를 입력해.';
    codeInput.focus();
    return;
  }
  loginPending = true;
  loginButton.disabled = true;
  loginMessage.textContent = '';
  if (internetMode) {
    codeInput.readOnly = true;
    $('loginButtonLabel').textContent = '승인 대기 중';
    loginMessage.classList.add('approval-pending');
    loginMessage.textContent = 'Mac 리모컨에서 연결을 승인해.';
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

async function refreshScreen() {
  if (!session || document.hidden || screenRequest) return;
  clearTimeout(refreshTimer);
  const request = new AbortController();
  screenRequest = request;
  const { signal } = request;
  let imageUrl = null;
  let probe = null;
  const cancelled = new Promise((_, reject) => {
    signal.addEventListener('abort', () => {
      reject(new Error('화면을 불러오는 데 시간이 너무 오래 걸려. 새로고침을 눌러 다시 시도해.'));
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
    lastImageSize = { width: probe.naturalWidth, height: probe.naturalHeight };
    screen.src = currentImageUrl;
    if (oldUrl) URL.revokeObjectURL(oldUrl);
    $('screenPlaceholder').hidden = true;
    $('lastUpdate').textContent = '방금 업데이트';
    setStatus(true, '연결됨');
  } catch (error) {
    if (screenRequest !== request) return;
    if (error.status === 401) return showLogin(sessionEnded);
    clearScreen(error instanceof DOMException ? '화면 이미지를 읽지 못했어.' : error.message, false);
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
  if (!lastImageSize) {
    clearScreen();
    $('lastUpdate').textContent = '불러오는 중';
  }
  refreshScreen();
});
window.addEventListener('offline', () => {
  if (!session) return;
  pauseScreen(internetMode ? '인터넷 연결을 확인해.' : 'Wi-Fi 연결을 확인해.', false);
  $('lastUpdate').textContent = '연결 끊김';
  setStatus(false, '연결 끊김');
});
window.addEventListener('online', refreshScreen);
document.addEventListener('visibilitychange', () => {
  if (document.hidden) pauseScreen();
  else refreshScreen();
});

function sendAction(action) {
  if (!session) return Promise.resolve(false);
  const currentSession = session;
  actionQueue = actionQueue.then(async () => {
    if (currentSession !== session || logoutPending || document.hidden) return false;
    try {
      await api('/api/action', { ...jsonOptions(action), signal: currentSession.signal });
      if (currentSession !== session) return false;
      $('controlMessage').textContent = '';
      return true;
    } catch (error) {
      if (currentSession === session) {
        if (error.status === 401) showLogin(sessionEnded);
        else $('controlMessage').textContent = error.message;
      }
      return false;
    }
  });
  return actionQueue;
}

screen.addEventListener('pointerdown', (event) => {
  if (!lastImageSize || !session || !event.isPrimary || event.button !== 0) return;
  event.preventDefault();
  gesture = { x: event.clientX, y: event.clientY, pointerId: event.pointerId };
  screen.setPointerCapture(event.pointerId);
});
screen.addEventListener('pointerup', (event) => {
  if (!gesture || gesture.pointerId !== event.pointerId || !lastImageSize) return;
  event.preventDefault();
  const dx = event.clientX - gesture.x;
  const dy = event.clientY - gesture.y;
  if (Math.hypot(dx, dy) > 12) {
    if (Math.abs(dy) > 12) sendAction({ type: 'scroll', dy: Math.max(-1000, Math.min(1000, Math.round(-dy * 2))) });
  } else {
    const rect = screen.getBoundingClientRect();
    if (rect.width > 0 && rect.height > 0) {
      const x = Math.max(0, Math.min(lastImageSize.width - 1, Math.floor((gesture.x - rect.left) / rect.width * lastImageSize.width)));
      const y = Math.max(0, Math.min(lastImageSize.height - 1, Math.floor((gesture.y - rect.top) / rect.height * lastImageSize.height)));
      sendAction({ type: 'click', x, y });
    }
  }
  gesture = null;
});
screen.addEventListener('pointercancel', () => { gesture = null; });
screen.addEventListener('lostpointercapture', () => { gesture = null; });

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
checkStatus();
