"""Windows native OpenGL pet window with color-key transparency.

Color-key transparency is binary (not per-pixel alpha). Magenta background
is reserved; antialiased silhouettes may have fringes. No PNG simulation.
"""
import ctypes
import json
import math
import os
import sys
import threading
import time
from drivers.desktop_pet.window import DesktopPetWindow

# 无人脸数据时回落到中性姿势的参数（ParamAngleX 由待机正弦驱动，不在此列）。
# 数值采用跟踪器的中性约定（ParamMouthForm/ParamBrow* 的 0.5），避免面捕
# 接管与释放之间出现跳变。
_NEUTRAL_PARAMS = {
    "ParamAngleY": 0.0, "ParamAngleZ": 0.0,
    "ParamBodyAngleX": 0.0, "ParamBodyAngleY": 0.0,
    "ParamEyeLOpen": 1.0, "ParamEyeROpen": 1.0,
    "ParamEyeBallX": 0.0, "ParamEyeBallY": 0.0,
    "ParamMouthOpenY": 0.0, "ParamMouthForm": 0.5,
    "ParamBrowLY": 0.5, "ParamBrowRY": 0.5,
    "ParamCheek": 0.0, "ParamBreath": 0.5,
}

# 待机摆动幅度（度）——无面捕时的原有行为
_IDLE_ANGLE_X_SWING = 20.0


def _apply_params(renderer, params):
    """把一帧面捕参数写入模型；无可用人脸数据时回落到待机摆动。

    renderer.set_parameter 会对越界/非有限值抛错，逐参数兜住以免中断渲染循环。
    """
    if params:
        items = params.items()
    else:
        items = _idle_items(renderer)
    for name, value in items:
        if name not in renderer.parameter_ids:
            continue
        try:
            renderer.set_parameter(name, value)
        except (KeyError, ValueError, RuntimeError):
            continue


def _idle_items(renderer):
    """无面捕时的待机参数：AngleX 正弦摆动，其余回中。"""
    items = []
    if "ParamAngleX" in renderer.parameter_ids:
        items.append(("ParamAngleX", math.sin(time.monotonic()) * _IDLE_ANGLE_X_SWING))
    items.extend(_NEUTRAL_PARAMS.items())
    return items


class _ParamFeed:
    """从 stdin 读取后端转发的面捕参数（JSON Lines）。

    后端只在检测到人脸时才推送，因此超过 STALE_SECONDS 未更新即视为
    「画面中无人脸」，渲染循环随之回落到待机动画。
    """

    STALE_SECONDS = 0.5

    def __init__(self, stream=None):
        self._stream = stream if stream is not None else sys.stdin
        self._lock = threading.Lock()
        self._params = None
        self._updated = 0.0

    def start(self):
        if self._stream is None:
            return
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        try:
            while True:
                # 显式 readline：管道语义下逐行即到即取，不走迭代器预读缓冲
                line = self._stream.readline()
                if not line:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(payload, dict):
                    continue
                raw = payload.get("live2d_params")
                if not isinstance(raw, dict):
                    continue
                clean = {
                    name: float(value)
                    for name, value in raw.items()
                    if isinstance(name, str) and isinstance(value, (int, float))
                    and not isinstance(value, bool)
                }
                if clean:
                    with self._lock:
                        self._params = clean
                        self._updated = time.monotonic()
        except Exception:
            # stdin 关闭（后端退出/桌宠被回收）即正常结束
            return

    def latest(self):
        """返回最近一帧参数；无数据或已过期则返回 None。"""
        with self._lock:
            params = self._params
            age = time.monotonic() - self._updated
        if params is None or age > self.STALE_SECONDS:
            return None
        return params


class NativePetWindow(DesktopPetWindow):
    def show(self):
        if os.name != 'nt':
            raise RuntimeError('Native transparent pet currently supports Windows only')
        import pygame
        pygame.display.init()
        os.environ['SDL_VIDEO_WINDOW_POS'] = f'{self._x},{self._y}'
        pygame.display.gl_set_attribute(pygame.GL_ALPHA_SIZE, 8)
        self._screen = pygame.display.set_mode((self.width, self.height),
                        pygame.OPENGL | pygame.DOUBLEBUF | pygame.NOFRAME)
        self._clock = pygame.time.Clock()
        self.hwnd = pygame.display.get_wm_info()['window']
        user32 = ctypes.WinDLL('user32', use_last_error=True)
        from ctypes import wintypes as w
        get_style = user32.GetWindowLongPtrW
        get_style.argtypes = [w.HWND, ctypes.c_int]; get_style.restype = ctypes.c_ssize_t
        set_style = user32.SetWindowLongPtrW
        set_style.argtypes = [w.HWND, ctypes.c_int, ctypes.c_ssize_t]; set_style.restype = ctypes.c_ssize_t
        layer = user32.SetLayeredWindowAttributes
        layer.argtypes = [w.HWND, w.DWORD, w.BYTE, w.DWORD]; layer.restype = w.BOOL
        set_style(self.hwnd, -20, get_style(self.hwnd, -20) | 0x80000)
        if not layer(self.hwnd, 0x00FF00FF, 255, 1):
            raise ctypes.WinError(ctypes.get_last_error())
        self._running = self._visible = True
        self._set_always_on_top()

    def set_position(self, x, y):
        self._x = self._target_x = x
        self._y = self._target_y = y
        from ctypes import wintypes as w
        move = ctypes.windll.user32.SetWindowPos
        move.argtypes = [w.HWND, w.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, w.UINT]
        if not move(self.hwnd, None, x, y, 0, 0, 0x0001 | 0x0004):
            raise ctypes.WinError()


def run(manifest, frames=0, expected_sha256=None, on_ready=None):
    from drivers.live2d_runtime.native import CubismRenderer, validate_package, package_sha256
    import live2d.v3 as sdk
    validate_package(manifest)
    digest = package_sha256(manifest)
    if expected_sha256 and digest != expected_sha256:
        raise RuntimeError('Model changed after verification; launch rejected')
    window = NativePetWindow(400, 500)
    renderer = CubismRenderer(400, 500)
    # 面捕参数来自后端 stdin 管道（JSON Lines）；后端未接入时该 feed 始终为空。
    feed = _ParamFeed()
    initialized = gl_ready = False
    count = 0
    try:
        window.show()
        sdk.init(); initialized = True
        sdk.glInit(); gl_ready = True
        renderer.load_model(manifest)
        feed.start()
        while window.is_running():
            window.handle_events()
            if not window.is_running(): break
            # 先写入参数再 Update/Draw，模型当帧即反映面捕姿态
            _apply_params(renderer, feed.latest())
            renderer._model.Update()
            sdk.clearBuffer(1, 0, 1, 0)
            renderer._model.Draw()
            renderer.frames_drawn += 1
            window.flip()
            count += 1
            if count == 1 and on_ready:
                if package_sha256(manifest) != digest:
                    raise RuntimeError('Model changed while loading')
                on_ready({'event': 'ready', 'pid': os.getpid(), 'frames_drawn': count,
                          'package_sha256': digest, 'transparency': 'windows_color_key',
                          'visual_desktop_verified': False})
            if frames and count >= frames: break
        return {'frames_drawn': count, 'transparency': 'windows_color_key', 'visual_desktop_verified': False}
    finally:
        renderer.close()
        if gl_ready: sdk.glRelease()
        if initialized: sdk.dispose()
        window.close()


if __name__ == '__main__':
    import argparse
    import json
    parser = argparse.ArgumentParser()
    parser.add_argument('manifest')
    parser.add_argument('--frames', type=int, default=0)
    parser.add_argument('--expected-sha256')
    args = parser.parse_args()
    if args.frames < 0: parser.error('--frames must be non-negative')
    def ready(report):
        print('LIVE2D_PET=' + json.dumps(report), flush=True)
    print(json.dumps(run(args.manifest, args.frames, args.expected_sha256, ready)))
