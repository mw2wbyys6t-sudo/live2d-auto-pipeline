"""Fresh-process OpenGL pixel and controlled parameter verification."""
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
from drivers.live2d_runtime.native import (
    CubismModelSession,
    package_sha256,
    validate_package,
)

#: 旧产物没有 ``Capabilities`` 时的兜底抽查项（与历史行为一致）。
FALLBACK_PARAMETERS = ("ParamAngleX", "ParamMouthOpenY", "ParamEyeLOpen")

#: 验收视口：取模型画布尺寸，让「1 模型像素 = 1 渲染像素」。
#: 曾经固定 400×500，对 667×1000 的画布只有 0.6 倍，眉毛/嘴这类小部件的形变被缩到
#: 十几个像素，卡在 ``changed > 20`` 阈值上（实测 ParamMouthForm 只有 19px）。
DEFAULT_VIEWPORT = (400, 500)
MAX_VIEWPORT = 1024


def _viewport(manifest_data):
    """模型画布尺寸（最长边夹到 ``MAX_VIEWPORT``）；拿不到就用兜底值。"""
    layout = (manifest_data or {}).get("Layout") or {}
    try:
        width = int(round(float(layout.get("Width") or 0)))
        height = int(round(float(layout.get("Height") or 0)))
    except (TypeError, ValueError):
        return DEFAULT_VIEWPORT
    if width <= 0 or height <= 0:
        return DEFAULT_VIEWPORT
    scale = min(1.0, MAX_VIEWPORT / float(max(width, height)))
    return max(1, int(round(width * scale))), max(1, int(round(height * scale)))


def drivable_parameters(manifest_data):
    """模型自报的「能驱动哪些参数」。

    优先读 ``Capabilities.DrivableParameters``（导出器按编译后的绑定写出来：
    网格键形 + warp 变形器的带）。读不到时退回三个固定抽查项 —— 旧产物仍然可验，
    只是覆盖面小。
    """
    return _claimed_parameters(manifest_data)[0]


def _claimed_parameters(manifest_data):
    """``(待验参数, 是否为模型显式承诺)``。

    区分这两种来源很重要：``Capabilities`` 是产物**明确承诺**能驱动的参数，缺一个
    就是产物自身不一致；而兜底三项只是抽查样本，模型没有那几个参数时跳过即可
    （手工构造的最小测试模型就属于这一类，不该被判失败）。
    """
    caps = (manifest_data or {}).get("Capabilities") or {}
    declared = caps.get("DrivableParameters")
    if isinstance(declared, list) and declared:
        return [str(p) for p in declared], True
    return list(FALLBACK_PARAMETERS), False


def verify(manifest, evidence_dir=None):
    path, manifest_data = validate_package(manifest)
    # 证据图可能落在产物目录里；那是本函数自己的输出，不能算「包被改动」。
    unbounded = [evidence_dir] if evidence_dir else []
    initial_digest = package_sha256(str(path), exclude=unbounded)
    import pygame
    import live2d.v3 as sdk
    from OpenGL.GL import glReadPixels, glFinish, GL_RGBA, GL_UNSIGNED_BYTE
    view_w, view_h = _viewport(manifest_data)
    session = CubismModelSession(view_w, view_h)
    initialized = gl_ready = False
    try:
        pygame.display.init()
        pygame.display.gl_set_attribute(pygame.GL_ALPHA_SIZE, 8)
        pygame.display.set_mode((view_w, view_h), pygame.OPENGL | pygame.DOUBLEBUF)
        pygame.display.set_caption('Live2D controlled pixel verification')
        sdk.init(); initialized = True
        sdk.glInit(); gl_ready = True
        session.load_model(str(path))
        session.stop_all_motions()
        session.set_auto_blink(False)
        session.set_auto_breath(False)
        # Freeze time (dt=0) so physics/breathing cannot masquerade as parameter response.
        def frame():
            sdk.clearBuffer()
            session.update(0.0)
            session.draw()
            glFinish()
            pixels = np.frombuffer(
                glReadPixels(0, 0, view_w, view_h, GL_RGBA, GL_UNSIGNED_BYTE),
                dtype=np.uint8).reshape(view_h, view_w, 4).copy()
            pygame.display.flip()
            pygame.event.pump()
            return pixels
        # 逐项验：模型自报能驱动的参数**必须**真的改变画面；声明了但没绑定的参数
        # 单独列出来（如实记录，不冒充已落地，也不因此压住整体结论）。
        promised, explicit = _claimed_parameters(manifest_data)
        claimed = [p for p in promised if p in session.parameter_ids]
        undeclared = [p for p in promised if p not in session.parameter_ids]
        not_driven = [p for p in session.parameter_ids if p not in claimed]
        results = []
        for name in claimed:
            idx = session.parameter_ids.index(name)
            low, high = session.parameter_bounds(name)
            original = session.get_parameter_value(idx)
            session.set_parameter(name, low)
            frame(); a = frame(); control = frame()
            session.set_parameter(name, high)
            frame(); b = frame()
            session.set_parameter(name, low)
            frame(); restored = frame()
            changed = int(np.count_nonzero(np.max(np.abs(a.astype(int)-b.astype(int)), axis=2) > 8))
            drift = int(np.count_nonzero(np.max(np.abs(a.astype(int)-control.astype(int)), axis=2) > 8))
            restore_error = int(np.count_nonzero(np.max(np.abs(a.astype(int)-restored.astype(int)), axis=2) > 8))
            visible = min(int(np.count_nonzero(a[:,:,3] > 8)), int(np.count_nonzero(b[:,:,3] > 8)))
            passed = visible > 100 and changed > max(20, drift * 5) and restore_error <= max(10, changed // 20)
            results.append(dict(parameter=name, driven=True, visible_pixels=visible,
                                changed_pixels=changed, control_drift=drift,
                                restore_error=restore_error, passed=passed))
            if evidence_dir:
                from PIL import Image
                dest = Path(evidence_dir); dest.mkdir(parents=True, exist_ok=True)
                Image.fromarray(np.flipud(a)).save(dest / (name + '-min.png'))
                Image.fromarray(np.flipud(b)).save(dest / (name + '-max.png'))
            session.set_parameter(name, original)
        for name in not_driven:
            # 没有任何键形/变形器绑定它：改了画面不会变。如实列出，不当作通过。
            results.append(dict(parameter=name, driven=False, visible_pixels=None,
                                changed_pixels=0, control_drift=None,
                                restore_error=None, passed=False))
        # 只有"产物显式承诺"的参数缺失才算失败；兜底抽查项缺失按老行为跳过
        verified = bool(claimed) and all(
            r['passed'] for r in results if r['driven'])
        if explicit and undeclared:
            verified = False
        if package_sha256(str(path), exclude=unbounded) != initial_digest:
            raise RuntimeError('Model package changed during verification')
        return dict(runtime_verified=verified,
                    verification_scope='local_opengl_pixels_and_controlled_parameters',
                    package_sha256=initial_digest, checks=results,
                    drivable_parameters=list(claimed),
                    missing_parameters=list(undeclared),
                    declared_but_not_driven=list(not_driven),
                    deployment_status='ready_for_local_launch' if verified else 'blocked')
    finally:
        session.close()
        if gl_ready: sdk.glRelease()
        if initialized: sdk.dispose()
        pygame.display.quit()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('manifest')
    parser.add_argument('--evidence-dir')
    parser.add_argument('--report')
    args = parser.parse_args()
    try:
        result = verify(args.manifest, args.evidence_dir)
    except Exception as exc:
        result = dict(runtime_verified=False, deployment_status='blocked', error=str(exc))
    if args.report:
        Path(args.report).write_text(json.dumps(result, indent=2), encoding='utf-8')
    print('LIVE2D_VERIFICATION=' + json.dumps(result))
    return 0 if result['runtime_verified'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
