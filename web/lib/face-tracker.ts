/**
 * 浏览器端面捕：MediaPipe FaceLandmarker (WASM)。
 *
 * 设计取舍：面捕完全在浏览器内运行（摄像头 getUserMedia + WASM 推理），
 * 不经后端 —— Go 后端没有面捕管线（/api/tracking/* 显式 501），Python 后端
 * 的实现需要本机 mediapipe + 摄像头设备占用；浏览器方案在两种后端部署下
 * 都可用，且省掉一跳 WS 往返。
 *
 * 资源策略：
 * - WASM 运行时已本地化到 /mediapipe/wasm（离线可用）；
 * - face_landmarker.task 模型（~3.7MB）默认尝试本地 /models/，缺失时回退
 *   官方 CDN，可用 NEXT_PUBLIC_FACE_LANDMARKER_URL 覆盖。
 */
import { FilesetResolver, FaceLandmarker } from '@mediapipe/tasks-vision';

export interface TrackingParams {
  [param: string]: number;
}

const LOCAL_MODEL_URL = '/models/face_landmarker.task';
const CDN_MODEL_URL =
  'https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task';
// WASM 运行时优先用本地 vendored 资源（web/public/mediapipe/wasm，离线可用）；
// 未 vendored 的部署回退官方 CDN（版本与 package.json 锁定一致）。
const LOCAL_WASM_URL = '/mediapipe/wasm';
const CDN_WASM_URL = 'https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.35/wasm';

function clamp(v: number, lo: number, hi: number): number {
  return Math.min(hi, Math.max(lo, v));
}

let landmarkerPromise: Promise<FaceLandmarker> | null = null;

/** 本地资源存在时用本地路径，否则回退 CDN。 */
async function probeOk(url: string): Promise<boolean> {
  try {
    return (await fetch(url, { method: 'HEAD' })).ok;
  } catch {
    return false;
  }
}

/** 加载（并缓存）FaceLandmarker 实例；本地模型缺失时回退 CDN。 */
export function loadFaceLandmarker(): Promise<FaceLandmarker> {
  if (landmarkerPromise) return landmarkerPromise;
  landmarkerPromise = (async () => {
    const wasmOk = await probeOk(LOCAL_WASM_URL.concat('/', 'vision_wasm_internal.js'));
    const fileset = await FilesetResolver.forVisionTasks(wasmOk ? LOCAL_WASM_URL : CDN_WASM_URL);
    const delegate = 'GPU';
    const make = (modelAssetPath: string) =>
      FaceLandmarker.createFromOptions(fileset, {
        baseOptions: { modelAssetPath, delegate },
        runningMode: 'VIDEO',
        numFaces: 1,
        outputFaceBlendshapes: true,
        outputFacialTransformationMatrixes: true,
      });
    const modelUrl = process.env.NEXT_PUBLIC_FACE_LANDMARKER_URL;
    const modelOk = modelUrl ? false : await probeOk(LOCAL_MODEL_URL);
    const modelAssetPath = modelUrl || (modelOk ? LOCAL_MODEL_URL : CDN_MODEL_URL);
    try {
      return await make(modelAssetPath);
    } catch (err) {
      landmarkerPromise = null;
      throw new Error(
        `面捕模型加载失败（可下载 face_landmarker.task 放到 web/public/models/，` +
          `或设置 NEXT_PUBLIC_FACE_LANDMARKER_URL）: ${err instanceof Error ? err.message : err}`,
      );
    }
  })();
  return landmarkerPromise;
}

interface BlendshapeCategory {
  categoryName?: string;
  displayName?: string;
  score?: number;
}

function bs(map: Map<string, number>, name: string): number {
  return map.get(name) ?? 0;
}

/**
 * 把 FaceLandmarker 一帧结果映射为 Live2D 参数。
 * 头部姿态来自 facialTransformationMatrix（4x4 列主序），表情来自 ARKit 52
 * blendshape 系数 —— 与 Python 端 drivers/face_tracker 的映射语义保持一致
 *（yaw→ParamAngleX、-pitch→ParamAngleY、roll→ParamAngleZ）。
 */
export function resultToLive2DParams(
  result: {
    facialTransformationMatrixes?: Array<{ data: Float32Array | number[] }>;
    faceBlendshapes?: Array<{ categories?: BlendshapeCategory[] }>;
  },
): TrackingParams {
  const params: TrackingParams = {};

  const matrix = result.facialTransformationMatrixes?.[0]?.data;
  if (matrix && matrix.length >= 11) {
    // 列主序取 3x3 旋转块：R[c][r] = data[c*4 + r]
    const r20 = matrix[2];
    const r21 = matrix[6];
    const r22 = matrix[10];
    const r10 = matrix[1];
    const r11 = matrix[5];
    const deg = (rad: number) => (rad * 180) / Math.PI;
    // ZYX 欧拉角
    const pitch = deg(Math.asin(clamp(-r21, -1, 1)));
    const yaw = deg(Math.atan2(r20, r22));
    const roll = deg(Math.atan2(r10, r11));
    params.ParamAngleX = clamp(yaw, -30, 30);
    params.ParamAngleY = clamp(-pitch, -30, 30);
    params.ParamAngleZ = clamp(roll, -30, 30);
  }

  const categories = result.faceBlendshapes?.[0]?.categories;
  if (categories && categories.length > 0) {
    const map = new Map<string, number>();
    for (const c of categories) {
      if (c.categoryName) map.set(c.categoryName, c.score ?? 0);
    }
    params.ParamEyeLOpen = clamp(1 - bs(map, 'eyeBlinkLeft'), 0, 1);
    params.ParamEyeROpen = clamp(1 - bs(map, 'eyeBlinkRight'), 0, 1);
    params.ParamMouthOpenY = clamp(bs(map, 'jawOpen'), 0, 1);
    params.ParamMouthForm = clamp(
      (bs(map, 'mouthSmileLeft') + bs(map, 'mouthSmileRight')) / 2,
      -1,
      1,
    );
    params.ParamBrowLY = clamp(bs(map, 'browInnerUp'), -1, 1);
    params.ParamBrowRY = clamp(bs(map, 'browInnerUp'), -1, 1);
    // 眼球：blendshape 系数合成为 -1..1 归一化视线
    const eyeX =
      (bs(map, 'eyeLookOutLeft') + bs(map, 'eyeLookInRight')) / 2 -
      (bs(map, 'eyeLookInLeft') + bs(map, 'eyeLookOutRight')) / 2;
    const eyeY =
      (bs(map, 'eyeLookUpLeft') + bs(map, 'eyeLookUpRight')) / 2 -
      (bs(map, 'eyeLookDownLeft') + bs(map, 'eyeLookDownRight')) / 2;
    params.ParamEyeBallX = clamp(eyeX, -1, 1);
    params.ParamEyeBallY = clamp(eyeY, -1, 1);
  }

  return params;
}
