import { useEffect, useMemo, useState } from 'react';
import {
  CheckCircle2,
  Eye,
  EyeOff,
  Loader2,
  RefreshCw,
  Save,
  Trash2,
  XCircle,
} from 'lucide-react';
import {
  apiClient,
  type ApiKeyName,
  type ImageEngineSettings,
  type ImageGenerationSettings,
  type ImageProviderSettings,
  type ImageSettingsPayload,
  type ProviderTestResult,
} from '../lib/api-client';

/**
 * 设置页：把图像生成上游配置收敛到本机。
 *
 * - Cloud Key 只走 Python 加密保险库（响应里只有布尔），页面永不展示 Key；
 * - 本地引擎地址/模型/画幅写 settings.json，保存后下一轮生成即生效；
 * - 「测试连接」是真实出图（会消耗一次额度），按钮旁标注探测尺寸。
 */
const CANVAS_PRESETS: Array<{ label: string; width: number; height: number }> = [
  { label: '竖版立绘 1024×1536', width: 1024, height: 1536 },
  { label: '方图 1024×1024', width: 1024, height: 1024 },
  { label: '横版 1536×1024', width: 1536, height: 1024 },
  { label: '大图 2048×2048', width: 2048, height: 2048 },
];

const POLLINATIONS_MODELS = ['flux', 'turbo', 'kontext'];

interface KeyForm {
  ark: string;
  sensenova: string;
  openai: string;
}

/** Seedream 在保险库里就是 ARK Key，页面不再单独提供一个输入框。 */
type EditableKey = Exclude<ApiKeyName, 'seedream'>;

interface TestState {
  loading: boolean;
  result?: ProviderTestResult;
  error?: string;
}

export default function SettingsPage() {
  const [data, setData] = useState<ImageSettingsPayload | null>(null);
  const [engines, setEngines] = useState<ImageEngineSettings | null>(null);
  const [providers, setProviders] = useState<ImageProviderSettings | null>(null);
  const [generation, setGeneration] = useState<ImageGenerationSettings | null>(null);
  const [keys, setKeys] = useState<KeyForm>({ ark: '', sensenova: '', openai: '' });
  const [visible, setVisible] = useState<Record<string, boolean>>({});
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const [tests, setTests] = useState<Record<string, TestState>>({});

  const apply = (payload: ImageSettingsPayload) => {
    setData(payload);
    setEngines(payload.engines);
    setProviders(payload.providers);
    setGeneration(payload.generation);
  };

  const load = async () => {
    setLoading(true);
    setError('');
    try {
      apply(await apiClient.getSettings());
    } catch (e) {
      setError(e instanceof Error ? e.message : '读取设置失败');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const providerStatus = useMemo(() => {
    const map: Record<string, { available: boolean; note?: string }> = {};
    for (const item of data?.provider_status || []) {
      map[item.name] = { available: !!item.available, note: item.probe_note };
    }
    return map;
  }, [data]);

  const save = async (extraSecrets: Partial<Record<ApiKeyName, string>> = {}) => {
    if (!engines || !providers || !generation) return;
    setSaving(true);
    setError('');
    setMessage('');
    try {
      const secrets: Partial<Record<ApiKeyName, string>> = { ...extraSecrets };
      if (keys.ark.trim()) secrets.ark = keys.ark.trim();
      if (keys.sensenova.trim()) secrets.sensenova = keys.sensenova.trim();
      if (keys.openai.trim()) secrets.openai = keys.openai.trim();

      const saved = await apiClient.updateSettings({ secrets, engines, providers, generation });
      apply(saved.settings);
      setKeys({ ark: '', sensenova: '', openai: '' });
      setMessage(
        saved.warnings?.length
          ? `已保存，但：${saved.warnings.join('；')}`
          : '设置已保存，下一次生成立即生效',
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : '保存失败');
    } finally {
      setSaving(false);
    }
  };

  const runTest = async (provider: string) => {
    setTests((prev) => ({ ...prev, [provider]: { loading: true } }));
    try {
      const result = await apiClient.testProvider(provider);
      setTests((prev) => ({ ...prev, [provider]: { loading: false, result } }));
    } catch (e) {
      setTests((prev) => ({
        ...prev,
        [provider]: { loading: false, error: e instanceof Error ? e.message : '测试失败' },
      }));
    }
  };

  const clearKey = async (name: EditableKey) => {
    if (!window.confirm('确定删除已保存的 Key？删除后该上游将不可用。')) return;
    await save({ [name]: '' });
  };

  const keyField = (name: EditableKey, label: string, placeholder: string) => (
    <div>
      <label className="mb-1 block text-[11px] text-haze-500">{label}</label>
      <div className="flex gap-2">
        <div className="relative flex-1">
          <input
            type={visible[name] ? 'text' : 'password'}
            value={keys[name]}
            onChange={(e) => setKeys((prev) => ({ ...prev, [name]: e.target.value }))}
            placeholder={placeholder}
            className="field pr-9"
            autoComplete="off"
          />
          <button
            type="button"
            onClick={() => setVisible((prev) => ({ ...prev, [name]: !prev[name] }))}
            className="absolute right-2 top-1/2 -translate-y-1/2 text-haze-500 hover:text-haze-200"
            aria-label={visible[name] ? '隐藏 Key' : '显示 Key'}
          >
            {visible[name] ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
          </button>
        </div>
        {data?.keys[name] && (
          <button type="button" onClick={() => void clearKey(name)} className="btn-icon" title="删除已保存的 Key">
            <Trash2 className="h-4 w-4" />
          </button>
        )}
      </div>
    </div>
  );

  const testBlock = (provider: string) => {
    const state = tests[provider];
    return (
      <div className="mt-2 space-y-2">
        <div className="flex items-center gap-2">
          <button
            type="button"
            className="btn-ghost text-xs"
            disabled={state?.loading}
            onClick={() => void runTest(provider)}
          >
            {state?.loading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null}
            {state?.loading ? '真实出图中…' : '测试连接（真实出图）'}
          </button>
          {providerStatus[provider] && (
            <span className={`chip ${providerStatus[provider].available ? 'text-emerald-600' : 'text-haze-500'}`}>
              {providerStatus[provider].available ? '可用' : '未就绪'}
            </span>
          )}
          <span className="text-[10px] text-haze-600">测试使用已保存的 Key</span>
        </div>
        {state?.result && (
          <div className={`rounded-lg border p-2 text-[11px] ${state.result.ok ? 'border-emerald-400/40 bg-emerald-50/60' : 'border-rose-400/40 bg-rose-50/60'}`}>
            <div className="flex items-center gap-1.5">
              {state.result.ok ? (
                <CheckCircle2 className="h-3.5 w-3.5 text-emerald-600" />
              ) : (
                <XCircle className="h-3.5 w-3.5 text-rose-600" />
              )}
              <span className="text-haze-200">
                {state.result.ok
                  ? `成功 · ${state.result.elapsed_ms ?? 0}ms · ${(state.result.probe_size || []).join('×')}`
                  : `失败：${state.result.message || state.result.error_code || '未知原因'}`}
              </span>
            </div>
            {state.result.probe_note && <p className="mt-1 text-haze-500">{state.result.probe_note}</p>}
            {state.result.image_url && (
              <img
                src={state.result.image_url}
                alt="测试出图"
                className="mt-2 max-h-40 rounded-md border border-black/[0.08] object-contain"
              />
            )}
          </div>
        )}
        {state?.error && <p className="text-[11px] text-rose-600">{state.error}</p>}
      </div>
    );
  };

  if (loading) {
    return (
      <div className="flex items-center gap-2 text-sm text-haze-400">
        <Loader2 className="h-4 w-4 animate-spin" /> 正在读取设置…
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-5xl space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold text-haze-100">本机生图上游</h1>
          <p className="mt-1 text-xs text-haze-500">
            Key 只保存在本机加密保险库，不会回传或写入配置文件；保存后下一次生成立即生效。
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button type="button" className="btn-ghost" onClick={() => void load()}>
            <RefreshCw className="h-4 w-4" /> 刷新状态
          </button>
          <button type="button" className="btn-primary" disabled={saving} onClick={() => void save()}>
            {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
            保存设置
          </button>
        </div>
      </div>

      {data && !data.vault_available && (
        <div className="rounded-xl border border-amber-400/50 bg-amber-50/70 p-3 text-xs text-amber-800">
          加密保险库不可用（缺少 cryptography 或主密钥无法创建），API Key 无法保存。请修复内置 Python 环境后重试。
        </div>
      )}
      {error && <div className="rounded-xl border border-rose-400/50 bg-rose-50/70 p-3 text-xs text-rose-700">{error}</div>}
      {message && (
        <div className="rounded-xl border border-emerald-400/50 bg-emerald-50/70 p-3 text-xs text-emerald-700">{message}</div>
      )}

      {/* 云端上游 */}
      {providers && (
        <section className="grid gap-4 lg:grid-cols-3">
          <div className="card space-y-3">
            <div className="flex items-center justify-between">
              <h2 className="text-sm font-semibold text-haze-100">Seedream / ARK</h2>
              <span className={`chip ${data?.keys.ark ? 'text-emerald-600' : 'text-haze-500'}`}>
                {data?.keys.ark ? '已配 Key' : '缺 Key'}
              </span>
            </div>
            {keyField('ark', 'ARK_API_KEY', data?.keys.ark ? '已保存（留空保持不变）' : '粘贴 ARK API Key')}
            <div className="grid grid-cols-2 gap-2">
              <div>
                <label className="mb-1 block text-[11px] text-haze-500">模型版本</label>
                <input
                  className="field"
                  value={providers.seedream.version}
                  onChange={(e) =>
                    setProviders({ ...providers, seedream: { ...providers.seedream, version: e.target.value } })
                  }
                  placeholder="seedream-4.0"
                />
              </div>
              <div>
                <label className="mb-1 block text-[11px] text-haze-500">默认尺寸</label>
                <input
                  className="field"
                  value={providers.seedream.size}
                  onChange={(e) =>
                    setProviders({ ...providers, seedream: { ...providers.seedream, size: e.target.value } })
                  }
                  placeholder="1024x1024"
                />
              </div>
            </div>
            {testBlock('seedream')}
          </div>

          <div className="card space-y-3">
            <div className="flex items-center justify-between">
              <h2 className="text-sm font-semibold text-haze-100">SenseNova</h2>
              <span className={`chip ${data?.keys.sensenova ? 'text-emerald-600' : 'text-haze-500'}`}>
                {data?.keys.sensenova ? '已配 Key' : '缺 Key'}
              </span>
            </div>
            {keyField('sensenova', 'SENSENOVA_API_KEY', data?.keys.sensenova ? '已保存（留空保持不变）' : '粘贴 SenseNova API Key')}
            <div>
              <label className="mb-1 block text-[11px] text-haze-500">Base URL</label>
              <input
                className="field"
                value={providers.sensenova.base_url}
                onChange={(e) => setProviders({ ...providers, sensenova: { base_url: e.target.value } })}
              />
            </div>
            {testBlock('sensenova')}
          </div>

          <div className="card space-y-3">
            <div className="flex items-center justify-between">
              <h2 className="text-sm font-semibold text-haze-100">OpenAI 兼容</h2>
              <span className={`chip ${data?.keys.openai ? 'text-emerald-600' : 'text-haze-500'}`}>
                {data?.keys.openai ? '已配 Key' : '缺 Key'}
              </span>
            </div>
            {keyField('openai', 'OPENAI_API_KEY', data?.keys.openai ? '已保存（留空保持不变）' : '粘贴 API Key（含中转网关）')}
            <div>
              <label className="mb-1 block text-[11px] text-haze-500">Base URL</label>
              <input
                className="field"
                value={providers.openai.base_url}
                onChange={(e) => setProviders({ ...providers, openai: { ...providers.openai, base_url: e.target.value } })}
                placeholder="https://api.openai.com/v1"
              />
            </div>
            <div>
              <label className="mb-1 block text-[11px] text-haze-500">模型名</label>
              <input
                className="field"
                value={providers.openai.model}
                onChange={(e) => setProviders({ ...providers, openai: { ...providers.openai, model: e.target.value } })}
                placeholder="gpt-image-1"
              />
            </div>
            {testBlock('openai')}
          </div>
        </section>
      )}

      {/* 本地引擎 */}
      {engines && (
        <section className="card space-y-3">
          <h2 className="text-sm font-semibold text-haze-100">本地引擎</h2>
          <p className="text-[11px] text-haze-500">只允许本机或局域网地址；ComfyUI 默认 8188，SD WebUI 需加 --api（默认 7860）。</p>
          <div className="grid gap-3 sm:grid-cols-2">
            <div>
              <div className="mb-1 flex items-center justify-between text-[11px] text-haze-500">
                <span>ComfyUI 地址</span>
                <span className={providerStatus.comfyui?.available ? 'text-emerald-600' : 'text-haze-500'}>
                  {providerStatus.comfyui?.available ? '在线' : '离线'}
                </span>
              </div>
              <input
                className="field"
                value={engines.comfyui_base_url}
                onChange={(e) => setEngines({ ...engines, comfyui_base_url: e.target.value })}
                placeholder="http://127.0.0.1:8188"
              />
              {testBlock('comfyui')}
            </div>
            <div>
              <div className="mb-1 flex items-center justify-between text-[11px] text-haze-500">
                <span>SD WebUI 地址</span>
                <span className={providerStatus.sd_webui?.available ? 'text-emerald-600' : 'text-haze-500'}>
                  {providerStatus.sd_webui?.available ? '在线' : '离线'}
                </span>
              </div>
              <input
                className="field"
                value={engines.sd_webui_base_url}
                onChange={(e) => setEngines({ ...engines, sd_webui_base_url: e.target.value })}
                placeholder="http://127.0.0.1:7860"
              />
              {testBlock('sd_webui')}
            </div>
          </div>
        </section>
      )}

      {/* 生图默认值 */}
      {generation && (
        <section className="card space-y-3">
          <h2 className="text-sm font-semibold text-haze-100">生图默认值</h2>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <div>
              <label className="mb-1 block text-[11px] text-haze-500">默认画幅</label>
              <select
                className="field"
                value={`${generation.width}x${generation.height}`}
                onChange={(e) => {
                  const preset = CANVAS_PRESETS.find((p) => `${p.width}x${p.height}` === e.target.value);
                  if (preset) setGeneration({ ...generation, width: preset.width, height: preset.height });
                }}
              >
                {CANVAS_PRESETS.map((p) => (
                  <option key={p.label} value={`${p.width}x${p.height}`}>
                    {p.label}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label className="mb-1 block text-[11px] text-haze-500">候选数（1 = 关闭择优）</label>
              <input
                type="number"
                min={1}
                max={4}
                className="field"
                value={generation.candidates}
                onChange={(e) => setGeneration({ ...generation, candidates: Number(e.target.value) || 1 })}
              />
            </div>
            <div>
              <label className="mb-1 block text-[11px] text-haze-500">Pollinations 模型</label>
              <select
                className="field"
                value={POLLINATIONS_MODELS.includes(generation.pollinations_model) ? generation.pollinations_model : 'flux'}
                onChange={(e) => setGeneration({ ...generation, pollinations_model: e.target.value })}
              >
                {POLLINATIONS_MODELS.map((m) => (
                  <option key={m} value={m}>
                    {m}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label className="mb-1 block text-[11px] text-haze-500">随机种子（0 = 每次随机）</label>
              <input
                type="number"
                min={0}
                className="field"
                value={generation.pollinations_seed}
                onChange={(e) => setGeneration({ ...generation, pollinations_seed: Math.max(0, Number(e.target.value) || 0) })}
              />
            </div>
          </div>
          <label className="flex items-center gap-2 text-xs text-haze-400">
            <input
              type="checkbox"
              checked={generation.normalize}
              onChange={(e) => setGeneration({ ...generation, normalize: e.target.checked })}
            />
            生成后做立绘规范化（去背 → 居中留边 → 缩放目标画幅 → 轻锐化）
          </label>
          {testBlock('pollinations')}
        </section>
      )}

      {/* 预留：自训模型 */}
      <section className="card space-y-2">
        <h2 className="text-sm font-semibold text-haze-100">自训专精模型（预留）</h2>
        <p className="text-[11px] leading-relaxed text-haze-500">
          后续版本支持接入你为 Live2D 素材自训的生图模型：配置结构已预留（custom_providers），
          届时在 Python registry 注册一个 provider 即可出现在生成页，无需再改前端。
        </p>
      </section>
    </div>
  );
}
