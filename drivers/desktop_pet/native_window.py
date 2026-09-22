"""Windows native OpenGL pet window with color-key transparency.

Color-key transparency is binary (not per-pixel alpha). Magenta background
is reserved; antialiased silhouettes may have fringes. No PNG simulation.
"""
import ctypes
import os
from drivers.desktop_pet.window import DesktopPetWindow


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
    import math
    import time
    validate_package(manifest)
    digest = package_sha256(manifest)
    if expected_sha256 and digest != expected_sha256:
        raise RuntimeError('Model changed after verification; launch rejected')
    window = NativePetWindow(400, 500)
    renderer = CubismRenderer(400, 500)
    initialized = gl_ready = False
    count = 0
    try:
        window.show()
        sdk.init(); initialized = True
        sdk.glInit(); gl_ready = True
        renderer.load_model(manifest)
        while window.is_running():
            window.handle_events()
            if not window.is_running(): break
            renderer._model.Update()
            if 'ParamAngleX' in renderer.parameter_ids:
                renderer.set_parameter('ParamAngleX', math.sin(time.monotonic()) * 20)
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
