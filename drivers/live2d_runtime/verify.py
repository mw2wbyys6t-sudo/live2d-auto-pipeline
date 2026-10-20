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


def verify(manifest, evidence_dir=None):
    path, _ = validate_package(manifest)
    initial_digest = package_sha256(str(path))
    import pygame
    import live2d.v3 as sdk
    from OpenGL.GL import glReadPixels, glFinish, GL_RGBA, GL_UNSIGNED_BYTE
    session = CubismModelSession(400, 500)
    initialized = gl_ready = False
    try:
        pygame.display.init()
        pygame.display.gl_set_attribute(pygame.GL_ALPHA_SIZE, 8)
        pygame.display.set_mode((400, 500), pygame.OPENGL | pygame.DOUBLEBUF)
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
            pixels = np.frombuffer(glReadPixels(0, 0, 400, 500, GL_RGBA, GL_UNSIGNED_BYTE), dtype=np.uint8).reshape(500, 400, 4).copy()
            pygame.display.flip()
            pygame.event.pump()
            return pixels
        results = []
        for name in ('ParamAngleX', 'ParamMouthOpenY', 'ParamEyeLOpen'):
            if name not in session.parameter_ids:
                continue
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
            results.append(dict(parameter=name, visible_pixels=visible, changed_pixels=changed, control_drift=drift, restore_error=restore_error, passed=passed))
            if evidence_dir:
                from PIL import Image
                dest = Path(evidence_dir); dest.mkdir(parents=True, exist_ok=True)
                Image.fromarray(np.flipud(a)).save(dest / (name + '-min.png'))
                Image.fromarray(np.flipud(b)).save(dest / (name + '-max.png'))
            session.set_parameter(name, original)
        verified = bool(results) and all(r['passed'] for r in results)
        if package_sha256(str(path)) != initial_digest:
            raise RuntimeError('Model package changed during verification')
        return dict(runtime_verified=verified, verification_scope='local_opengl_pixels_and_controlled_parameters',
                    package_sha256=initial_digest, checks=results,
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
