"""Fresh-process OpenGL pixel and controlled parameter verification."""
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
from drivers.live2d_runtime.native import CubismRenderer, validate_package, package_sha256


def verify(manifest, evidence_dir=None):
    path, _ = validate_package(manifest)
    initial_digest = package_sha256(str(path))
    import pygame
    import live2d.v3 as sdk
    from OpenGL.GL import glReadPixels, glFinish, GL_RGBA, GL_UNSIGNED_BYTE
    renderer = CubismRenderer(400, 500)
    initialized = gl_ready = False
    try:
        pygame.display.init()
        pygame.display.gl_set_attribute(pygame.GL_ALPHA_SIZE, 8)
        pygame.display.set_mode((400, 500), pygame.OPENGL | pygame.DOUBLEBUF)
        pygame.display.set_caption('Live2D controlled pixel verification')
        sdk.init(); initialized = True
        sdk.glInit(); gl_ready = True
        renderer.load_model(str(path))
        model = renderer._model
        model.StopAllMotions()
        model.SetAutoBlinkEnable(False)
        model.SetAutoBreathEnable(False)
        # Freeze time so physics/breathing cannot masquerade as parameter response.
        def frame():
            sdk.clearBuffer()
            model._model.Update(0.0)
            model.Draw()
            glFinish()
            pixels = np.frombuffer(glReadPixels(0, 0, 400, 500, GL_RGBA, GL_UNSIGNED_BYTE), dtype=np.uint8).reshape(500, 400, 4).copy()
            pygame.display.flip()
            pygame.event.pump()
            return pixels
        results = []
        for name in ('ParamAngleX', 'ParamMouthOpenY', 'ParamEyeLOpen'):
            if name not in renderer.parameter_ids:
                continue
            idx = renderer.parameter_ids.index(name)
            definition = model.GetParameter(idx)
            original = model.GetParameterValue(idx)
            renderer.set_parameter(name, definition.min)
            frame(); a = frame(); control = frame()
            renderer.set_parameter(name, definition.max)
            frame(); b = frame()
            renderer.set_parameter(name, definition.min)
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
            renderer.set_parameter(name, original)
        verified = bool(results) and all(r['passed'] for r in results)
        if package_sha256(str(path)) != initial_digest:
            raise RuntimeError('Model package changed during verification')
        return dict(runtime_verified=verified, verification_scope='local_opengl_pixels_and_controlled_parameters',
                    package_sha256=initial_digest, checks=results,
                    deployment_status='ready_for_local_launch' if verified else 'blocked')
    finally:
        renderer.close()
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
