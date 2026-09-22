import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import dynamic from 'next/dynamic';
import {
  Box,
  ChevronRight,
  ChevronDown,
  Download,
  Play,
  Pause,
  Grid3x3,
  CheckCircle2,
  AlertCircle,
  Bug,
  Save,
  RotateCcw,
  Wind,
  ArrowDown,
} from 'lucide-react';
import type { NextPage } from 'next';
import type { Expression, ParameterDef, ParamMap } from '../types';
import type { ModelCanvasHandle } from '../components/ModelCanvas';
import ParameterSlider from '../components/ParameterSlider';
import LoadingSpinner from '../components/LoadingSpinner';

const ModelCanvas = dynamic(() => import('../components/ModelCanvas'), {
  ssr: false,
  loading: () => (
    <div className="w-full h-full flex items-center justify-center bg-[#0f0f13]">
      <LoadingSpinner label="Loading renderer…" />
    </div>
  ),
});

const ALL_PARAMS: ParameterDef[] = [
  { id: 'ParamAngleX', name: 'Angle X', min: -30, max: 30, default: 0, group: 'Head' },
  { id: 'ParamAngleY', name: 'Angle Y', min: -30, max: 30, default: 0, group: 'Head' },
  { id: 'ParamAngleZ', name: 'Angle Z', min: -30, max: 30, default: 0, group: 'Head' },
  { id: 'ParamEyeLOpen', name: 'Eye L Open', min: 0, max: 1, default: 1, group: 'Eyes' },
  { id: 'ParamEyeROpen', name: 'Eye R Open', min: 0, max: 1, default: 1, group: 'Eyes' },
  { id: 'ParamEyeBallX', name: 'Eye Ball X', min: -1, max: 1, default: 0, group: 'Eyes' },
  { id: 'ParamEyeBallY', name: 'Eye Ball Y', min: -1, max: 1, default: 0, group: 'Eyes' },
  { id: 'ParamBrowLY', name: 'Brow L Y', min: -1, max: 1, default: 0, group: 'Brows' },
  { id: 'ParamBrowRY', name: 'Brow R Y', min: -1, max: 1, default: 0, group: 'Brows' },
  { id: 'ParamBrowLAngle', name: 'Brow L Angle', min: -1, max: 1, default: 0, group: 'Brows' },
  { id: 'ParamBrowRAngle', name: 'Brow R Angle', min: -1, max: 1, default: 0, group: 'Brows' },
  { id: 'ParamMouthForm', name: 'Mouth Form', min: -1, max: 1, default: 0, group: 'Mouth' },
  { id: 'ParamMouthOpenY', name: 'Mouth Open Y', min: 0, max: 1, default: 0, group: 'Mouth' },
  { id: 'ParamCheek', name: 'Cheek', min: 0, max: 1, default: 0, group: 'Face' },
  { id: 'ParamBodyAngleX', name: 'Body X', min: -10, max: 10, default: 0, group: 'Body' },
  { id: 'ParamBodyAngleY', name: 'Body Y', min: -10, max: 10, default: 0, group: 'Body' },
  { id: 'ParamBodyAngleZ', name: 'Body Z', min: -10, max: 10, default: 0, group: 'Body' },
  { id: 'ParamBreath', name: 'Breath', min: 0, max: 1, default: 0.5, group: 'Body' },
  { id: 'ParamArmLA', name: 'Arm L', min: -90, max: 90, default: 0, group: 'Body' },
  { id: 'ParamArmRA', name: 'Arm R', min: -90, max: 90, default: 0, group: 'Body' },
];

const DEFAULT_EXPRESSIONS: Expression[] = [
  { name: 'default' },
  { name: 'happy', parameters: { ParamMouthForm: 0.6, ParamCheek: 0.8, ParamEyeLOpen: 0.9, ParamEyeROpen: 0.9 } },
  { name: 'sad', parameters: { ParamMouthForm: -0.5, ParamBrowLY: -0.4, ParamBrowRY: -0.4, ParamBrowLAngle: 0.4, ParamBrowRAngle: 0.4 } },
  { name: 'angry', parameters: { ParamBrowLY: -0.3, ParamBrowRY: -0.3, ParamBrowLAngle: -0.5, ParamBrowRAngle: -0.5, ParamMouthForm: -0.4 } },
  { name: 'surprised', parameters: { ParamEyeLOpen: 1, ParamEyeROpen: 1, ParamMouthOpenY: 0.6, ParamBrowLY: 0.3, ParamBrowRY: 0.3 } },
  { name: 'shy', parameters: { ParamCheek: 1, ParamEyeLOpen: 0.6, ParamEyeROpen: 0.6, ParamBrowLY: 0.2, ParamBrowRY: 0.2 } },
  { name: 'thinking', parameters: { ParamBrowLAngle: 0.3, ParamMouthForm: -0.2, ParamEyeBallX: 0.4 } },
];

interface TreeNode {
  id: string;
  label: string;
  type: 'bone' | 'deformer' | 'param' | 'mesh' | 'group';
  children?: TreeNode[];
}

const MODEL_TREE: TreeNode[] = [
  {
    id: 'root',
    label: 'Root',
    type: 'group',
    children: [
      {
        id: 'body',
        label: 'Body',
        type: 'bone',
        children: [
          { id: 'torso', label: 'Torso', type: 'deformer' },
          { id: 'armL', label: 'Arm L', type: 'bone' },
          { id: 'armR', label: 'Arm R', type: 'bone' },
        ],
      },
      {
        id: 'head',
        label: 'Head',
        type: 'bone',
        children: [
          {
            id: 'face',
            label: 'Face',
            type: 'deformer',
            children: [
              { id: 'eyeL', label: 'Eye L', type: 'mesh' },
              { id: 'eyeR', label: 'Eye R', type: 'mesh' },
              { id: 'mouth', label: 'Mouth', type: 'mesh' },
              { id: 'browL', label: 'Brow L', type: 'mesh' },
              { id: 'browR', label: 'Brow R', type: 'mesh' },
            ],
          },
          { id: 'hairFront', label: 'Hair Front', type: 'deformer' },
          { id: 'hairBack', label: 'Hair Back', type: 'deformer' },
          { id: 'hairSide', label: 'Hair Side', type: 'deformer' },
        ],
      },
    ],
  },
];

const Live2DPage: NextPage = () => {
  const canvasRef = useRef<ModelCanvasHandle>(null);
  const [values, setValues] = useState<Record<string, number>>(() => {
    const out: Record<string, number> = {};
    for (const p of ALL_PARAMS) out[p.id] = p.default;
    return out;
  });
  const [running, setRunning] = useState(true);
  const [wireframe, setWireframe] = useState(false);
  const [modelUrl, setModelUrl] = useState<string>('');
  const [gravity, setGravity] = useState({ x: 0, y: -1 });
  const [wind, setWind] = useState({ x: 0, y: 0 });
  const [currentExpression, setCurrentExpression] = useState('default');
  const [expandedNodes, setExpandedNodes] = useState<Set<string>>(new Set(['root', 'head', 'face']));
  const [activeTab, setActiveTab] = useState<'params' | 'physics' | 'expressions' | 'validate'>('params');
  const [fps, setFps] = useState(0);
  const [exporting, setExporting] = useState(false);
  const [currentModelDir, setCurrentModelDir] = useState('');
  const [validation, setValidation] = useState<Array<{ level: 'ok' | 'warn' | 'error'; msg: string }> | null>(null);

  // Keep the export model dir in sync with the pasted model URL: the parent
  // folder of the .model3.json file is used as the server-side model dir.
  useEffect(() => {
    if (!modelUrl) {
      setCurrentModelDir('');
      return;
    }
    try {
      const path = modelUrl.split('?')[0];
      const noExt = path.substring(0, path.lastIndexOf('/'));
      const dir = decodeURIComponent(noExt.substring(noExt.lastIndexOf('/') + 1));
      setCurrentModelDir(dir || '');
    } catch {
      setCurrentModelDir('');
    }
  }, [modelUrl]);

  const groups = useMemo(() => {
    const g = new Map<string, ParameterDef[]>();
    for (const p of ALL_PARAMS) {
      const arr = g.get(p.group || 'Other') || [];
      arr.push(p);
      g.set(p.group || 'Other', arr);
    }
    return g;
  }, []);

  const updateParam = useCallback((id: string, value: number) => {
    setValues((prev) => ({ ...prev, [id]: value }));
    const handle = canvasRef.current as (ModelCanvasHandle & { [k: string]: unknown }) | null;
    if (handle && typeof handle.setParameters === 'function') {
      handle.setParameters({ [id]: value } as ParamMap);
    }
  }, []);

  const resetAll = () => {
    const out: Record<string, number> = {};
    for (const p of ALL_PARAMS) out[p.id] = p.default;
    setValues(out);
    const handle = canvasRef.current as (ModelCanvasHandle & { [k: string]: unknown }) | null;
    if (handle && typeof handle.setParameters === 'function') {
      handle.setParameters(out as ParamMap);
    }
  };

  const applyExpression = (exp: Expression) => {
    setCurrentExpression(exp.name);
    const handle = canvasRef.current as (ModelCanvasHandle & { [k: string]: unknown }) | null;
    if (exp.parameters) {
      if (handle && typeof handle.setParameters === 'function') {
        handle.setParameters(exp.parameters as ParamMap);
      }
      setValues((prev) => ({ ...prev, ...exp.parameters }));
    } else {
      if (handle && typeof handle.setExpression === 'function') {
        handle.setExpression(exp.name);
      }
    }
  };

  // FPS tracking
  useEffect(() => {
    const id = setInterval(() => {
      const handle = canvasRef.current as (ModelCanvasHandle & { [k: string]: unknown }) | null;
      if (handle && typeof handle.getFps === 'function') {
        setFps(handle.getFps());
      } else {
        setFps(0);
      }
    }, 500);
    return () => clearInterval(id);
  }, []);

  const toggleNode = (id: string) => {
    setExpandedNodes((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const runValidation = () => {
    const issues: Array<{ level: 'ok' | 'warn' | 'error'; msg: string }> = [];
    const handle = canvasRef.current as
      | (ModelCanvasHandle & {
          layersLoaded?: number;
          hasMeshGeometry?: boolean;
          paramsCount?: number;
          modelMeta?: unknown;
          triangleCount?: number;
        })
      | null;

    // 1. Layers loaded
    const layersLoaded = handle?.layersLoaded ?? 0;
    if (layersLoaded > 0) {
      issues.push({ level: 'ok', msg: `Layers loaded: ${layersLoaded}` });
    } else {
      issues.push({ level: 'error', msg: 'No layers loaded — load a model to preview it' });
    }

    // 2. Mesh geometry (real vertex deformation vs legacy sprite)
    const hasMesh = handle?.hasMeshGeometry ?? false;
    if (hasMesh) {
      issues.push({ level: 'ok', msg: 'Mesh deformation: ENABLED' });
    } else {
      issues.push({ level: 'warn', msg: 'Mesh deformation: LEGACY SPRITE' });
    }

    // 3. Parameter count
    const paramsCount = handle?.paramsCount ?? 0;
    if (paramsCount >= 20) {
      issues.push({ level: 'ok', msg: `Parameters available: ${paramsCount}` });
    } else {
      issues.push({ level: 'warn', msg: `Only ${paramsCount} parameters (expected ≥20)` });
    }

    // 4. Model3 metadata
    if (handle?.modelMeta) {
      issues.push({ level: 'ok', msg: 'Model3 metadata: Loaded' });
    } else {
      issues.push({ level: 'warn', msg: 'Model3 metadata: not loaded' });
    }

    // 5. Triangle count
    const triCount = handle?.triangleCount ?? 0;
    if (hasMesh) {
      if (triCount > 0) {
        issues.push({ level: 'ok', msg: `Mesh triangles: ${triCount}` });
      } else {
        issues.push({ level: 'error', msg: 'Mesh mode active but triangle count is 0' });
      }
    } else {
      issues.push({ level: 'ok', msg: 'Mesh triangles: N/A (sprite mode)' });
    }

    setValidation(issues);
  };

  const handleExport = async () => {
    setExporting(true);
    try {
      const payload: Record<string, unknown> = {
        character_id: 'web_preview',
        download: true,
      };
      if (currentModelDir) {
        payload.model_dir = currentModelDir;
      }
      const res = await fetch('/api/export/live2d', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      if (!res.ok) {
        let detail = '';
        try {
          const errBody = await res.json();
          detail = errBody?.error || '';
        } catch {
          /* ignore parse error */
        }
        throw new Error(detail || `Export failed (${res.status})`);
      }
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `live2d_${Date.now()}.zip`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Export failed';
      // eslint-disable-next-line no-alert
      alert(`导出失败: ${msg}`);
    } finally {
      setExporting(false);
    }
  };

  const renderTree = (nodes: TreeNode[], depth = 0): React.ReactNode =>
    nodes.map((node) => {
      const hasChildren = node.children && node.children.length > 0;
      const expanded = expandedNodes.has(node.id);
      return (
        <div key={node.id}>
          <button
            onClick={() => hasChildren && toggleNode(node.id)}
            className={`w-full flex items-center gap-1.5 px-2 py-1 rounded text-xs hover:bg-gray-800 transition-colors ${
              depth === 0 ? 'text-gray-200 font-medium' : 'text-gray-400'
            }`}
            style={{ paddingLeft: depth * 12 + 8 }}
          >
            {hasChildren ? (
              expanded ? (
                <ChevronDown className="w-3 h-3 text-gray-500" />
              ) : (
                <ChevronRight className="w-3 h-3 text-gray-500" />
              )
            ) : (
              <span className="w-3" />
            )}
            <span
              className={`w-1.5 h-1.5 rounded-full ${
                node.type === 'bone'
                  ? 'bg-pink-400'
                  : node.type === 'deformer'
                  ? 'bg-purple-400'
                  : node.type === 'mesh'
                  ? 'bg-cyan-400'
                  : 'bg-gray-500'
              }`}
            />
            <span className="truncate">{node.label}</span>
            <span className="ml-auto text-[9px] text-gray-600 uppercase">{node.type}</span>
          </button>
          {hasChildren && expanded && renderTree(node.children!, depth + 1)}
        </div>
      );
    });

  return (
    <div className="animate-fade-in">
      <div className="flex items-center justify-between mb-4 flex-wrap gap-2">
        <div>
          <h1 className="text-2xl font-bold text-white flex items-center gap-2">
            <Box className="w-6 h-6 text-pink-400" /> Live2D Builder
          </h1>
          <p className="text-sm text-gray-500 mt-0.5">
            Compose, rig, and debug your Live2D model — parameters, physics, and expressions
          </p>
        </div>
        <div className="flex items-center gap-2">
          <div className="px-3 py-1.5 rounded-lg bg-gray-800 border border-gray-700 text-xs text-gray-400 flex items-center gap-2">
            <span className={`w-2 h-2 rounded-full ${running ? 'bg-emerald-400 animate-pulse' : 'bg-gray-500'}`} />
            {fps} FPS
          </div>
          <button
            onClick={() => setRunning((r) => !r)}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs bg-gray-800 border border-gray-700 text-gray-200 hover:bg-gray-700 transition-colors"
          >
            {running ? <Pause className="w-3.5 h-3.5" /> : <Play className="w-3.5 h-3.5" />}
            {running ? 'Pause' : 'Play'}
          </button>
          <button
            onClick={() => setWireframe((w) => !w)}
            className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs border transition-colors ${
              wireframe
                ? 'bg-purple-500/20 border-purple-500/40 text-purple-300'
                : 'bg-gray-800 border-gray-700 text-gray-300'
            }`}
          >
            <Grid3x3 className="w-3.5 h-3.5" /> Wireframe
          </button>
          <button
            onClick={runValidation}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs bg-emerald-500/10 border border-emerald-500/30 text-emerald-300 hover:bg-emerald-500/20 transition-colors"
          >
            <Bug className="w-3.5 h-3.5" /> Validate
          </button>
          <button
            onClick={handleExport}
            disabled={exporting}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs bg-gradient-to-r from-pink-500 to-purple-600 text-white hover:shadow-lg hover:shadow-pink-500/30 transition-all disabled:opacity-60 disabled:cursor-not-allowed"
          >
            <Download className="w-3.5 h-3.5" />
            {exporting ? 'Exporting…' : 'Export model3.zip'}
          </button>
        </div>
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-[280px_1fr_320px] gap-4">
        {/* Left: model tree */}
        <div className="bg-[#1a1a23] border border-gray-800 rounded-xl overflow-hidden flex flex-col max-h-[calc(100vh-180px)]">
          <div className="p-3 border-b border-gray-800 flex items-center justify-between">
            <p className="text-xs font-medium text-gray-400">Model structure</p>
            <label className="text-[10px] text-gray-500 flex items-center gap-1">
              <Save className="w-3 h-3" /> {modelUrl ? 'Loaded' : 'Demo'}
            </label>
          </div>
          <div className="p-2 border-b border-gray-800">
            <input
              type="text"
              value={modelUrl}
              onChange={(e) => setModelUrl(e.target.value)}
              placeholder="Paste model3.json URL"
              className="w-full px-2 py-1.5 bg-gray-900 border border-gray-700 rounded-md text-[11px] text-white placeholder:text-gray-600 focus:outline-none focus:border-pink-500"
            />
          </div>
          <div className="flex-1 overflow-y-auto py-2">{renderTree(MODEL_TREE)}</div>
        </div>

        {/* Center: canvas */}
        <div className="bg-[#1a1a23] border border-gray-800 rounded-xl overflow-hidden min-h-[500px] relative">
          <ModelCanvas
            ref={canvasRef}
            modelUrl={modelUrl || undefined}
            wireframe={wireframe}
            onReady={() => setRunning(true)}
          />
          {!modelUrl && (
            <div className="absolute inset-0 flex flex-col items-center justify-center pointer-events-none text-center p-6">
              <Box className="w-12 h-12 text-gray-700 mb-3" />
              <p className="text-sm text-gray-500">No model loaded</p>
              <p className="text-xs text-gray-600 mt-1 max-w-xs">
                Paste a model3.json URL or send a generation here. Parameter controls will drive the preview.
              </p>
            </div>
          )}
        </div>

        {/* Right: control panel */}
        <div className="bg-[#1a1a23] border border-gray-800 rounded-xl flex flex-col max-h-[calc(100vh-180px)]">
          <div className="flex border-b border-gray-800">
            {(
              [
                ['params', 'Params'],
                ['physics', 'Physics'],
                ['expressions', 'Express'],
                ['validate', 'Validate'],
              ] as const
            ).map(([id, label]) => (
              <button
                key={id}
                onClick={() => setActiveTab(id)}
                className={`flex-1 py-2.5 text-xs font-medium transition-colors ${
                  activeTab === id
                    ? 'text-pink-400 border-b-2 border-pink-500'
                    : 'text-gray-500 hover:text-gray-300'
                }`}
              >
                {label}
              </button>
            ))}
          </div>
          <div className="flex-1 overflow-y-auto p-4">
            {activeTab === 'params' && (
              <div className="space-y-4">
                <div className="flex items-center justify-between">
                  <p className="text-[11px] text-gray-500">Drag sliders to test</p>
                  <button
                    onClick={resetAll}
                    className="inline-flex items-center gap-1 text-[10px] text-gray-400 hover:text-pink-400"
                  >
                    <RotateCcw className="w-3 h-3" /> Reset
                  </button>
                </div>
                {Array.from(groups.entries()).map(([group, params]) => (
                  <div key={group}>
                    <p className="text-[10px] uppercase tracking-wide text-gray-500 mb-2">{group}</p>
                    <div className="space-y-3">
                      {params.map((p) => (
                        <ParameterSlider
                          key={p.id}
                          param={p}
                          value={values[p.id] ?? p.default}
                          onChange={(v) => updateParam(p.id, v)}
                        />
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            )}

            {activeTab === 'physics' && (
              <div className="space-y-5">
                <div>
                  <p className="text-xs text-gray-300 font-medium mb-3 flex items-center gap-1.5">
                    <ArrowDown className="w-3.5 h-3.5" /> Gravity
                  </p>
                  <div className="grid grid-cols-2 gap-3">
                    <PhysicsInput label="X" value={gravity.x} onChange={(v) => setGravity((g) => ({ ...g, x: v }))} min={-1} max={1} step={0.1} />
                    <PhysicsInput label="Y" value={gravity.y} onChange={(v) => setGravity((g) => ({ ...g, y: v }))} min={-1} max={1} step={0.1} />
                  </div>
                </div>
                <div>
                  <p className="text-xs text-gray-300 font-medium mb-3 flex items-center gap-1.5">
                    <Wind className="w-3.5 h-3.5" /> Wind
                  </p>
                  <div className="grid grid-cols-2 gap-3">
                    <PhysicsInput label="X" value={wind.x} onChange={(v) => setWind((g) => ({ ...g, x: v }))} min={-1} max={1} step={0.1} />
                    <PhysicsInput label="Y" value={wind.y} onChange={(v) => setWind((g) => ({ ...g, y: v }))} min={-1} max={1} step={0.1} />
                  </div>
                </div>
                <div className="p-3 rounded-lg bg-gray-900 border border-gray-800">
                  <p className="text-[10px] text-gray-500 uppercase tracking-wide mb-2">Physics groups</p>
                  {['HairFront', 'HairSide', 'HairBack', 'Ribbon', 'Skirt'].map((g) => (
                    <div key={g} className="flex items-center justify-between py-1 text-xs">
                      <span className="text-gray-400">{g}</span>
                      <span className="text-emerald-400 flex items-center gap-1">
                        <CheckCircle2 className="w-3 h-3" /> 3 inputs
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {activeTab === 'expressions' && (
              <div className="space-y-3">
                <p className="text-[11px] text-gray-500">Click to apply expression</p>
                <div className="grid grid-cols-2 gap-2">
                  {DEFAULT_EXPRESSIONS.map((exp) => (
                    <button
                      key={exp.name}
                      onClick={() => applyExpression(exp)}
                      className={`p-3 rounded-lg border text-left transition-all ${
                        currentExpression === exp.name
                          ? 'bg-pink-500/10 border-pink-500/40 shadow-sm'
                          : 'bg-gray-900 border-gray-800 hover:border-gray-700'
                      }`}
                    >
                      <p className="text-xs font-medium text-white capitalize">{exp.name}</p>
                      {exp.parameters && (
                        <p className="text-[10px] text-gray-500 mt-0.5">
                          {Object.keys(exp.parameters).length} params
                        </p>
                      )}
                    </button>
                  ))}
                </div>
              </div>
            )}

            {activeTab === 'validate' && (
              <div className="space-y-3">
                <button
                  onClick={runValidation}
                  className="w-full inline-flex items-center justify-center gap-2 px-3 py-2 rounded-lg text-xs bg-emerald-500/10 border border-emerald-500/30 text-emerald-300 hover:bg-emerald-500/20"
                >
                  <Bug className="w-3.5 h-3.5" /> Run validation
                </button>
                {validation ? (
                  <div className="space-y-2">
                    {validation.map((v, i) => (
                      <div
                        key={i}
                        className={`flex items-start gap-2 p-2.5 rounded-lg border text-xs ${
                          v.level === 'ok'
                            ? 'bg-emerald-500/5 border-emerald-500/20 text-emerald-300'
                            : v.level === 'warn'
                            ? 'bg-amber-500/5 border-amber-500/20 text-amber-300'
                            : 'bg-red-500/5 border-red-500/20 text-red-300'
                        }`}
                      >
                        {v.level === 'ok' ? (
                          <CheckCircle2 className="w-3.5 h-3.5 mt-0.5 shrink-0" />
                        ) : (
                          <AlertCircle className="w-3.5 h-3.5 mt-0.5 shrink-0" />
                        )}
                        <span>{v.msg}</span>
                      </div>
                    ))}
                  </div>
                ) : (
                  <p className="text-xs text-gray-500">Run validation to see any issues</p>
                )}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};

interface PhysicsInputProps {
  label: string;
  value: number;
  onChange: (v: number) => void;
  min: number;
  max: number;
  step: number;
}

function PhysicsInput({ label, value, onChange, min, max, step }: PhysicsInputProps) {
  return (
    <label className="block">
      <span className="text-[10px] text-gray-500 uppercase tracking-wide">{label}</span>
      <input
        type="number"
        value={value}
        min={min}
        max={max}
        step={step}
        onChange={(e) => onChange(parseFloat(e.target.value) || 0)}
        className="mt-1 w-full px-2 py-1.5 bg-gray-900 border border-gray-700 rounded-md text-xs text-white font-mono focus:outline-none focus:border-pink-500"
      />
    </label>
  );
}

export default Live2DPage;
