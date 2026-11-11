import { computed, onBeforeUnmount, onMounted, ref } from 'vue';

// 桌面端的开机动画结束后直接进工作台，不再经过网页版启动页。
const TARGET = 'http://localhost:8080/studio';
/**
 * 开机动画至少展示这么久：图标旋转一圈 + 品牌字样淡入。
 * 后端已经在跑时也不会只闪一帧。
 */
const MIN_SHOW = 2100;
/** 退场动画时长，结束后再跳工作台。 */
const LEAVE_MS = 420;

export function useLaunch() {
  const params = new URLSearchParams(window.location.search);
  const preview = params.get('preview') === '1';
  const hold = params.get('hold') === '1';
  const previewError = params.get('error') === '1';

  const phase = ref('starting');
  const leaving = ref(false);
  const errorMessage = ref('');

  const srStatus = computed(() => {
    if (phase.value === 'ready') return '启动完成';
    if (phase.value === 'error') return '启动失败';
    return '正在启动';
  });

  let startedAt = Date.now();
  let pollTimer = null;
  let timeoutTimer = null;
  let startTimer = null;
  let leaveTimer = null;
  let retryTimer = null;
  const unlisteners = [];

  function clearTimer(id, kind) {
    if (id === null) return;
    if (kind === 'interval') window.clearInterval(id);
    else window.clearTimeout(id);
  }

  function clearAll() {
    clearTimer(pollTimer, 'interval');
    clearTimer(timeoutTimer, 'timeout');
    clearTimer(startTimer, 'timeout');
    clearTimer(leaveTimer, 'timeout');
    clearTimer(retryTimer, 'timeout');
    pollTimer = null;
    timeoutTimer = null;
    startTimer = null;
    leaveTimer = null;
    retryTimer = null;
  }

  function enter() {
    if (phase.value !== 'starting') return;
    clearTimer(pollTimer, 'interval');
    clearTimer(timeoutTimer, 'timeout');
    pollTimer = null;
    timeoutTimer = null;
    phase.value = 'ready';

    if (preview) return;

    startTimer = window.setTimeout(() => {
      leaving.value = true;
      leaveTimer = window.setTimeout(() => {
        window.location.href = TARGET;
      }, LEAVE_MS);
    }, Math.max(0, MIN_SHOW - (Date.now() - startedAt)));
  }

  function fail(message) {
    if (phase.value !== 'starting') return;
    clearTimer(pollTimer, 'interval');
    clearTimer(timeoutTimer, 'timeout');
    pollTimer = null;
    timeoutTimer = null;
    phase.value = 'error';
    errorMessage.value = message || '本地服务没有启动。';
  }

  async function check() {
    const tauri = window.__TAURI_INTERNALS__;
    if (!tauri) return;
    try {
      const running = await tauri.invoke('check_backend');
      if (running) {
        enter();
        return;
      }
      // 后端已经判定失败时直接拿原因，避免对着开机页干等 30 秒。
      const message = await tauri.invoke('backend_error');
      if (message && phase.value === 'starting') fail(message);
    } catch (_) {
      /* webview 还没注入 tauri internals 时忽略，下一轮再试 */
    }
  }

  function bindEvents() {
    const tauri = window.__TAURI_INTERNALS__;
    if (!tauri) return;
    try {
      const ready = tauri.listen('backend-ready', enter);
      const error = tauri.listen('backend-error', (event) => {
        const payload = (event && event.payload) || {};
        fail(payload.message || '后端进程启动失败。');
      });
      Promise.all([ready, error]).then((values) => {
        values.forEach((unlisten) => {
          if (typeof unlisten === 'function') unlisteners.push(unlisten);
        });
      }).catch(() => {});
    } catch (_) {}
  }

  function retry() {
    clearAll();
    phase.value = 'starting';
    leaving.value = false;
    errorMessage.value = '';
    startedAt = Date.now();

    if (window.__TAURI_INTERNALS__) {
      check();
      pollTimer = window.setInterval(check, 700);
      retryTimer = window.setTimeout(() => {
        if (phase.value === 'starting') fail('仍然没有检测到本地服务。');
      }, 12000);
    } else {
      retryTimer = window.setTimeout(() => {
        if (phase.value === 'starting') fail('仍没有检测到本地服务。');
      }, 1200);
    }
  }

  function start() {
    startedAt = Date.now();

    if (previewError) {
      startTimer = window.setTimeout(() => fail('预览：模拟后端启动失败。'), 1400);
      return;
    }

    if (window.__TAURI_INTERNALS__) {
      bindEvents();
      check();
      pollTimer = window.setInterval(check, 700);
      timeoutTimer = window.setTimeout(() => {
        if (phase.value === 'starting') fail('等待本地服务超时。');
      }, 35000);
      return;
    }

    // 纯浏览器预览：播放开机动画后停留在当前画面，不跳转。
    if (preview && !hold) {
      startTimer = window.setTimeout(enter, 2600);
    }
  }

  onMounted(start);
  onBeforeUnmount(() => {
    clearAll();
    unlisteners.forEach((unlisten) => {
      try {
        unlisten();
      } catch (_) {}
    });
  });

  return {
    phase,
    leaving,
    errorMessage,
    preview,
    srStatus,
    retry,
  };
}
