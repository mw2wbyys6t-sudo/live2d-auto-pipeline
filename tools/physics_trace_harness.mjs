/** 物理轨迹跑手：跑 TS 的 PhysicsEngine，输出与官方内核同格式的逐帧轨迹。
 *
 * 用法（由 tools/probe_physics_parity.py 调用）：
 *   PHYSICS_CONFIG=<json> PHYSICS_TS=<路径到 live2d-physics.ts> \
 *   node --input-type=module tools/physics_trace_harness.mjs
 */
import { pathToFileURL } from 'node:url';

const mod = await import(pathToFileURL(process.env.PHYSICS_TS).href);
const cfg = JSON.parse(process.env.PHYSICS_CONFIG);

const doc = mod.parsePhysics3(cfg.physics3);
const engine = new mod.PhysicsEngine(doc);

const values = { ...cfg.defaults };
const io = {
  read: (name) => (name in values ? values[name] : 0),
  range: (name) => cfg.ranges[name],
  write: (name, value) => { values[name] = value; },
};

// 每帧在物理之前外部写入的参数（模拟 motion/表情/用户拖动）。
// 用来判定内核 weight<100 的混合基准是「本帧物理前的值」还是「上一帧物理输出」。
const drive = cfg.drive || {};

// 一帧的契约与播放器一致：帧首还原物理输出 → 外部写入 → 快照基准 → 物理。
// 物理输出是当帧瞬态，不回流成下一帧的混合基准（内核 LoadParameters 语义）。
const frame = () => {
  engine.restoreOutputs(io);
  for (const [name, value] of Object.entries(drive)) values[name] = value;
  engine.captureOutputs(io);
  engine.step(cfg.dt, io);
};

for (let i = 0; i < cfg.frames; i++) frame();
const baseline = values[cfg.output];

values[cfg.input] = cfg.stepValue;
const trace = [];
for (let i = 0; i < cfg.frames; i++) {
  frame();
  trace.push(values[cfg.output]);
}

values[cfg.input] = cfg.base;
for (let i = 0; i < cfg.frames; i++) frame();

console.log('PHYSICS_TRACE=' + JSON.stringify({
  baseline, trace, settle: values[cfg.output],
}));
