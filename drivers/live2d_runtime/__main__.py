"""Interactive native preview: python -m drivers.live2d_runtime MODEL.model3.json."""
import argparse
import json

from .native import CubismRenderer, validate_package


def main():
    parser = argparse.ArgumentParser(description='Native Cubism preview (not deployment acceptance)')
    parser.add_argument('manifest')
    parser.add_argument('--frames', type=int, default=0, help='0: run until window closes')
    parser.add_argument('--parameter', action='append', default=[], metavar='ID=VALUE',
                        help='Native parameter override; may be repeated')
    args = parser.parse_args()
    if args.frames < 0:
        parser.error('--frames must be non-negative')
    renderer = CubismRenderer()
    pygame = sdk = None
    initialized = gl_ready = False
    try:
        validate_package(args.manifest)  # fail before opening a window
        import pygame
        import live2d.v3 as sdk
        pygame.display.init()
        pygame.display.gl_set_attribute(pygame.GL_ALPHA_SIZE, 8)
        pygame.display.set_mode((renderer.width, renderer.height), pygame.OPENGL | pygame.DOUBLEBUF)
        pygame.display.set_caption('Cubism native preview - visual acceptance pending')
        sdk.init()
        initialized = True
        sdk.glInit()
        gl_ready = True
        renderer.load_model(args.manifest)
        for assignment in args.parameter:
            name, value = assignment.split('=', 1)
            renderer.set_parameter(name, float(value))
        clock = pygame.time.Clock()
        running = True
        while running:
            for event in pygame.event.get():
                if event.type == pygame.QUIT or (event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE):
                    running = False
            if not running:
                break
            sdk.clearBuffer()
            renderer.draw()
            pygame.display.flip()
            if args.frames and renderer.frames_drawn >= args.frames:
                break
            clock.tick(60)
        print(json.dumps(renderer.report(), ensure_ascii=False))
        return 0
    except Exception as exc:
        print(json.dumps({'runtime_verified': False, 'deployment_status': 'blocked', 'error': str(exc)}))
        return 1
    finally:
        renderer.close()
        if gl_ready:
            sdk.glRelease()
        if initialized:
            sdk.dispose()
        if pygame is not None:
            pygame.display.quit()


if __name__ == '__main__':
    raise SystemExit(main())
