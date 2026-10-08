import type {
  Character,
  CharacterCreate,
  ChatMessage,
  ExportFormat,
  Expression,
  GenerationRequest,
  GenerationResult,
  GenerationStep,
  PipelineStatus,
  SystemStatus,
} from '../types';

// Use empty string (relative paths) so requests go through Next.js rewrites
// which proxies /api/* to the Go backend. This avoids CORS issues and works
// in any deployment environment (localhost, Docker, preview URLs, etc.).
const DEFAULT_BASE_URL =
  typeof window !== 'undefined'
    ? (window as unknown as { __LIVE2D_API_URL__?: string }).__LIVE2D_API_URL__ || ''
    : (process.env.NEXT_PUBLIC_API_URL || '');

export class APIError extends Error {
  constructor(
    message: string,
    public status: number,
    public data?: unknown,
  ) {
    super(message);
    this.name = 'APIError';
  }
}

export interface APIClientOptions {
  baseURL?: string;
  timeoutMs?: number;
  onUnauthorized?: () => void;
}

// Helper to extract data from the standard Go API wrapper: { success, data, message, error }
function extractData<T>(res: unknown): T {
  const wrapper = res as { success?: boolean; data?: T; error?: string };
  if (wrapper && typeof wrapper === 'object' && 'data' in wrapper) {
    if (wrapper.error) {
      throw new APIError(wrapper.error, 200, res);
    }
    return wrapper.data as T;
  }
  return res as T;
}

export class APIClient {
  readonly baseURL: string;
  private readonly timeoutMs: number;
  private readonly onUnauthorized?: () => void;

  constructor(options: APIClientOptions = {}) {
    this.baseURL = options.baseURL || DEFAULT_BASE_URL;
    this.timeoutMs = options.timeoutMs ?? 300_000; // 5min default for full pipeline
    this.onUnauthorized = options.onUnauthorized;
  }

  // ---------- internal ----------

  private async request<T>(
    path: string,
    init: RequestInit = {},
    timeoutMs?: number,
  ): Promise<T> {
    const controller = new AbortController();
    const timeout = setTimeout(
      () => controller.abort(),
      timeoutMs ?? this.timeoutMs,
    );
    try {
      const res = await fetch(`${this.baseURL}${path}`, {
        ...init,
        signal: controller.signal,
        headers: {
          'Content-Type': 'application/json',
          ...(init.headers || {}),
        },
      });
      if (res.status === 401) {
        this.onUnauthorized?.();
      }
      if (!res.ok) {
        let data: unknown = undefined;
        try {
          data = await res.json();
        } catch {
          // ignore
        }
        // Extract error message from Go wrapper if available
        let errMsg = `Request failed: ${res.status} ${res.statusText}`;
        const wrapper = data as { error?: string; message?: string };
        if (wrapper?.error) errMsg = wrapper.error;
        else if (wrapper?.message) errMsg = wrapper.message;
        throw new APIError(errMsg, res.status, data);
      }
      if (res.status === 204) {
        return undefined as T;
      }
      const ct = res.headers.get('content-type') || '';
      if (ct.includes('application/json')) {
        return (await res.json()) as T;
      }
      return (await res.text()) as unknown as T;
    } catch (err) {
      if (err instanceof APIError) throw err;
      if (err instanceof DOMException && err.name === 'AbortError') {
        throw new APIError('Request timed out', 408);
      }
      throw new APIError(
        err instanceof Error ? err.message : 'Network error',
        0,
      );
    } finally {
      clearTimeout(timeout);
    }
  }

  private async requestBlob(path: string, init: RequestInit = {}): Promise<Blob> {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), this.timeoutMs);
    try {
      const res = await fetch(`${this.baseURL}${path}`, {
        ...init,
        signal: controller.signal,
      });
      if (!res.ok) {
        throw new APIError(
          `Download failed: ${res.status} ${res.statusText}`,
          res.status,
        );
      }
      return await res.blob();
    } catch (err) {
      if (err instanceof APIError) throw err;
      throw new APIError(
        err instanceof Error ? err.message : 'Network error',
        0,
      );
    } finally {
      clearTimeout(timeout);
    }
  }

  // ---------- characters ----------

  async getCharacters(): Promise<Character[]> {
    const res = await this.request<unknown>('/api/characters');
    const data = extractData<Character[] | { characters?: Character[] }>(res);
    const arr = Array.isArray(data) ? data : (data?.characters && Array.isArray(data.characters) ? data.characters : []);
    // Map Go snake_case (character_id/created_at) to frontend camelCase (id/createdAt)
    return arr.map((c: any) => this.normalizeCharacter(c));
  }

  async createCharacter(data: CharacterCreate): Promise<Character> {
    // v0.10.1: Send as JSON matching Go CharacterRequest structure (snake_case)
    // referenceImages file upload is handled separately via addReferenceImage
    const body: Record<string, unknown> = {
      name: data.name,
    };
    // Map frontend fields to Go CharacterRequest fields (Go uses nested Face/Hair/Body/Palette/Persona/Style)
    // For simplicity, map basic fields into persona/style
    if (data.personality || data.description || data.appearance) {
      body.persona = {
        personality: data.personality || data.description || '',
        backstory: data.appearance || '',
      };
    }
    if (data.colorPalette) {
      body.palette = {
        primary_colors: [
          data.colorPalette.primary,
          data.colorPalette.secondary,
          data.colorPalette.hair,
          data.colorPalette.eyes,
          data.colorPalette.skin,
          data.colorPalette.accent,
        ].filter(Boolean),
        skin_tone: data.colorPalette.skin,
        accent_color: data.colorPalette.accent,
      };
    }
    const res = await this.request<unknown>('/api/characters', {
      method: 'POST',
      body: JSON.stringify(body),
    });
    return this.normalizeCharacter(extractData<any>(res));
  }

  async getCharacter(id: string): Promise<Character> {
    const res = await this.request<unknown>(`/api/characters/${encodeURIComponent(id)}`);
    return this.normalizeCharacter(extractData<any>(res));
  }

  /**
   * Normalize a raw character object returned by the Go API into the
   * frontend Character shape (camelCase + sensible defaults).
   */
  private normalizeCharacter(raw: any): Character {
    if (!raw || typeof raw !== 'object') {
      return {
        id: '',
        name: '',
        generationCount: 0,
        createdAt: new Date().toISOString(),
        updatedAt: new Date().toISOString(),
      };
    }
    const id = raw.id || raw.character_id || raw.characterId || '';
    const name = raw.name || '';
    const createdAt = raw.createdAt || raw.created_at || new Date().toISOString();
    const updatedAt = raw.updatedAt || raw.updated_at || createdAt;
    // 缩略图只能使用后端实际返回的地址（/output/xxx.png）。
    // 后端未返回时留空，由卡片组件显示占位图标，避免请求必然 404 的路径。
    const apiThumb = raw.thumbnailUrl || raw.thumbnail_url || raw.image_url || raw.imageUrl || '';
    const thumbnailUrl = apiThumb;
    const description = raw.description || raw.persona?.personality || raw.persona?.backstory || '';
    return {
      ...raw,
      id,
      name,
      description,
      createdAt,
      updatedAt,
      thumbnailUrl,
      generationCount: raw.generationCount ?? raw.generation_count ?? 0,
    } as Character;
  }

  async updateCharacter(
    id: string,
    data: Partial<Character>,
  ): Promise<Character> {
    // v0.10.1: Backend uses PUT (not PATCH)
    const body: Record<string, unknown> = {};
    if (data.name) body.name = data.name;
    if (data.personality || data.description) {
      body.persona = {
        personality: data.personality || data.description || '',
      };
    }
    const res = await this.request<unknown>(
      `/api/characters/${encodeURIComponent(id)}`,
      {
        method: 'PUT',
        body: JSON.stringify(body),
      },
    );
    return extractData<Character>(res);
  }

  async deleteCharacter(id: string): Promise<void> {
    await this.request<void>(`/api/characters/${encodeURIComponent(id)}`, {
      method: 'DELETE',
    });
  }

  // ---------- generation ----------

  private buildGenerationPayload(req: GenerationRequest) {
    // Map frontend camelCase to Go snake_case
    return {
      prompt: req.prompt,
      negative_prompt: req.negativePrompt,
      width: req.width,
      height: req.height,
      seed: req.seed ?? 0,
      character_id: req.characterId,
      use_semantic: req.segmentationMethod === 'semantic' || req.characterConsistency,
      export_live2d: true,
      deploy_desktop: false,
    };
  }

  async generateImage(req: GenerationRequest): Promise<GenerationResult> {
    // Simple image generation (no character workflow)
    const payload: Record<string, unknown> = {
      prompt: req.prompt,
      width: req.width,
      height: req.height,
      seed: req.seed ?? 0,
    };
    const res = await this.request<unknown>('/api/generate', {
      method: 'POST',
      body: JSON.stringify(payload),
    });
    return this.mapLegacyResult(extractData<any>(res));
  }

  async generateCharacter(req: GenerationRequest): Promise<GenerationResult> {
    // v0.10.1: Full pipeline generation via /api/generate/character (image→QA→segment→rig→Live2D)
    const payload = this.buildGenerationPayload(req);
    const res = await this.request<unknown>('/api/generate/character', {
      method: 'POST',
      body: JSON.stringify(payload),
    });
    return this.mapGenerationResult(extractData<any>(res));
  }

  async generateStream(
    req: GenerationRequest,
    onProgress: (step: GenerationStep) => void,
  ): Promise<GenerationResult> {
    // v0.10.1: POST /api/generate/character 是同步接口；进度来自后端在生成
    // 期间向 /api/ws 广播的 {type:"progress"} 消息（此前该通道无人订阅，
    // onProgress 从未被调用，进度条全程静止）。WS 不可用时静默退化为
    // 一次性请求，只是没有过程反馈。
    const payload = this.buildGenerationPayload(req);
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 10 * 60_000);

    let ws: WebSocket | null = null;
    try {
      ws = new WebSocket(getBackendWsUrl('/api/ws'));
      ws.onmessage = (ev) => {
        try {
          const msg = JSON.parse(ev.data) as {
            type?: string;
            stage?: string;
            progress?: number;
            message?: string;
          };
          if (msg.type !== 'progress') return;
          onProgress(progressMessageToStep(msg.stage || '', msg.progress ?? 0, msg.message));
        } catch { /* 忽略坏帧 */ }
      };
      ws.onerror = () => { /* 由 HTTP 请求本身兜底 */ };
    } catch { /* WS 建连失败不阻断生成 */ }

    try {
      const res = await fetch(`${this.baseURL}/api/generate/character`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
        signal: controller.signal,
      });
      if (!res.ok) {
        let errMsg = `Generation failed: ${res.status} ${res.statusText}`;
        try {
          const errData = await res.json();
          if (errData.error) errMsg = errData.error;
        } catch { /* ignore */ }
        throw new APIError(errMsg, res.status);
      }
      const data = await res.json();
      const result = extractData<any>(data);
      return this.mapGenerationResult(result);
    } finally {
      clearTimeout(timeout);
      if (ws) {
        ws.onmessage = null;
        try { ws.close(); } catch { /* ignore */ }
      }
    }
  }

  private mapLegacyResult(data: any): GenerationResult {
    // Map legacy /api/generate response to GenerationResult
    const imageUrl = data.image_url || '';
    return {
      id: `gen_${Date.now()}`,
      requestId: `req_${Date.now()}`,
      imageUrl,
      segmentedLayers: [],
      metadata: {
        seed: data.seed ?? 0,
        width: data.width ?? 1024,
        height: data.height ?? 1024,
        source: data.source ?? 'legacy',
      },
      createdAt: data.created_at || new Date().toISOString(),
    };
  }

  private mapGenerationResult(data: any): GenerationResult {
    // v0.10.1: Map full workflow result (with layers, model3, psd, etc.)
    const imageUrl = data.image_url || (data.image_path ? `/output/${data.image_path.split('/').pop()}` : '');
    const model3Url = data.model3_json || '';
    const layersDir = data.layers_dir || '';

    return {
      id: `gen_${Date.now()}`,
      requestId: `req_${Date.now()}`,
      imageUrl,
      segmentedLayers: [], // Layer info loaded from layers_dir if needed
      model3Url,
      metadata: {
        seed: data.seed ?? 0,
        width: data.width ?? 1024,
        height: data.height ?? 1024,
        source: data.source ?? 'workflow_v0.10.2',
        layers_dir: layersDir,
        psd_path: data.psd_path || '',
        output_dir: data.output_dir || '',
        character_id: data.character_id || '',
      },
      createdAt: data.created_at || new Date().toISOString(),
    };
  }

  // ---------- chat ----------

  async chat(
    messages: ChatMessage[],
    onChunk: (text: string) => void,
    characterId?: string,
  ): Promise<void> {
    // v0.10.1: Use SSE chat/stream endpoint with snake_case payload matching Go ChatRequest
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 2 * 60_000);
    try {
      // Convert messages to Go format: { role, content } history array + single message
      const history = messages.slice(0, -1).map(m => ({
        role: m.role,
        content: m.content,
      }));
      const lastMessage = messages[messages.length - 1];
      const payload: Record<string, unknown> = {
        character_id: characterId,
        message: lastMessage?.content || '',
        history,
        stream: true,
      };
      const res = await fetch(`${this.baseURL}/api/chat/stream`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
        signal: controller.signal,
      });
      if (!res.ok || !res.body) {
        throw new APIError(
          `Chat failed: ${res.status} ${res.statusText}`,
          res.status,
        );
      }
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';
        for (const line of lines) {
          const trimmed = line.trim();
          if (!trimmed.startsWith('data:')) continue;
          const payload = trimmed.slice(5).trim();
          if (!payload) continue;
          if (payload === '[DONE]') return;
          try {
            const parsed = JSON.parse(payload) as {
              type?: string;
              chunk?: string;
              content?: string;
              reply?: string;
              error?: string;
              finished?: boolean;
            };
            if (parsed.error) {
              throw new APIError(parsed.error, 500);
            }
            // Support multiple chunk shapes from Go stream
            const text = parsed.chunk || parsed.content || parsed.reply;
            if (text) onChunk(text);
            if (parsed.finished) return;
          } catch (err) {
            if (err instanceof APIError) throw err;
          }
        }
      }
    } finally {
      clearTimeout(timeout);
    }
  }

  // ---------- export ----------

  async exportModel(
    characterId: string,
    format: ExportFormat,
    layersDir?: string,
  ): Promise<{
    model3_json?: string;
    texture?: string;
    model_path?: string;
    success: boolean;
    /** 该模型包能否被 Live2D 运行时直接加载（构建期官方 Cubism Core 验收通过才为 true）。 */
    runtime_ready?: boolean;
    blocker?: string;
  }> {
    // v0.10.1: POST /api/export/live2d with JSON body (not GET with query params)
    const payload: Record<string, unknown> = {
      character_id: characterId,
      format,
    };
    if (layersDir) payload.layers_dir = layersDir;
    const res = await this.request<unknown>('/api/export/live2d', {
      method: 'POST',
      body: JSON.stringify(payload),
    });
    return extractData<any>(res);
  }

  /** PSD 分层方案：调用后端 /api/export/psd 对角色立绘执行分层规划。 */
  async exportPSDPlan(
    imagePath: string,
    useAI = false,
  ): Promise<{
    layers?: number;
    plan?: string;
    plan_dir?: string;
    psd_path?: string;
    applied?: boolean;
    success: boolean;
  }> {
    const payload = {
      image_path: imagePath,
      use_ai: useAI,
    };
    const res = await this.request<unknown>('/api/export/psd', {
      method: 'POST',
      body: JSON.stringify(payload),
    });
    return extractData<any>(res);
  }

  // ---------- expressions ----------

  async getExpressions(characterId?: string): Promise<Expression[]> {
    const q = characterId ? `?character_id=${encodeURIComponent(characterId)}` : '';
    const res = await this.request<unknown>(`/api/expressions${q}`);
    const data = extractData<any[]>(res);
    return (Array.isArray(data) ? data : []).map(e => ({
      name: e.name || e.Name || '',
      file: e.file || e.File,
      thumbnailUrl: e.thumbnail || e.ThumbnailUrl,
      parameters: e.params || e.Parameters || [],
    }));
  }

  // ---------- health ----------

  async healthCheck(): Promise<boolean> {
    try {
      const res = await fetch(`${this.baseURL}/api/health`, {
        signal: AbortSignal.timeout?.(5000),
      });
      return res.ok;
    } catch {
      return false;
    }
  }

  async getStatus(): Promise<SystemStatus | null> {
    try {
      const res = await this.request<unknown>('/api/status', undefined, 5000);
      const wrapper = res as { data?: unknown };
      const data: Record<string, unknown> =
        (wrapper?.data as Record<string, unknown> | undefined) ??
        (res as Record<string, unknown> | undefined) ??
        {};
      const services = Array.isArray(data.services)
        ? (data.services as Array<{ name: string; available: boolean; version?: string }>)
        : [];
      return {
        apiConnected: true,
        latencyMs: 0,
        gpuAvailable: false,
        version: (data.version as string) ?? 'v0.10.2',
        modelsLoaded: services.map((s) => s.name),
        providers: services.map((s) => ({
          id: s.name as never,
          name: s.name,
          available: s.available,
        })),
      } as SystemStatus;
    } catch {
      return null;
    }
  }

  // ---------- cross-page pipeline ----------

  async getLatestGeneration(): Promise<LatestGeneration | null> {
    try {
      const res = await this.request<unknown>('/api/generations/latest', undefined, 15_000);
      return extractData<LatestGeneration>(res);
    } catch {
      return null;
    }
  }

  async segmentImage(
    imagePath = '',
    method: 'semantic' | 'kmeans' = 'semantic',
  ): Promise<SegmentResult> {
    // Segmentation runs a real model (SAM on CPU ≈ 25s+, K-means is fast),
    // hence the generous timeout.
    const res = await this.request<unknown>(
      '/api/segment',
      { method: 'POST', body: JSON.stringify({ image_path: imagePath, method }) },
      30 * 60_000,
    );
    return extractData<SegmentResult>(res);
  }

  // ---------- generation providers ----------

  /**
   * 查询图像生成的上游路由状态：哪些 provider 已配置可用（含外部
   * OpenAI 兼容大模型端点）、哪些已注册但缺 Key。
   */
  async listProviders(): Promise<{
    available: Array<{ name: string; display_name: string; requires_key: boolean }>;
    registered: string[];
    query_ok: boolean;
  }> {
    try {
      const res = await this.request<unknown>('/api/providers', undefined, 15_000);
      return extractData<any>(res);
    } catch {
      return { available: [], registered: [], query_ok: false };
    }
  }

  // ---------- external asset import ----------

  /** 导入外部 PSD：提取全部像素图层为整幅画布 PNG + 图层目录。 */
  async importPSD(file: File): Promise<ImportResult> {
    return this.uploadMultipart<ImportResult>('/api/import/psd', file, 'file', 10 * 60_000);
  }

  /** 导入多张 PNG（按提交顺序作为图层集）。 */
  async importPNGs(files: File[]): Promise<ImportResult> {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 10 * 60_000);
    try {
      const form = new FormData();
      for (const f of files) form.append('files', f);
      const res = await fetch(`${this.baseURL}/api/import/pngs`, {
        method: 'POST',
        body: form,
        signal: controller.signal,
      });
      if (!res.ok) {
        let errMsg = `Import failed: ${res.status} ${res.statusText}`;
        try {
          const errData = await res.json();
          if (errData.error) errMsg = errData.error;
        } catch { /* ignore */ }
        throw new APIError(errMsg, res.status);
      }
      return extractData<ImportResult>(await res.json());
    } finally {
      clearTimeout(timeout);
    }
  }

  private async uploadMultipart<T>(
    path: string,
    file: File,
    field: string,
    timeoutMs: number,
  ): Promise<T> {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), timeoutMs);
    try {
      const form = new FormData();
      form.append(field, file);
      const res = await fetch(`${this.baseURL}${path}`, {
        method: 'POST',
        body: form,
        signal: controller.signal,
      });
      if (!res.ok) {
        let errMsg = `Import failed: ${res.status} ${res.statusText}`;
        try {
          const errData = await res.json();
          if (errData.error) errMsg = errData.error;
        } catch { /* ignore */ }
        throw new APIError(errMsg, res.status);
      }
      return extractData<T>(await res.json());
    } finally {
      clearTimeout(timeout);
    }
  }

  // ---------- exported model list ----------

  /** 列出 output 下全部已导出的 Live2D 模型（新→旧）。 */
  async listExportedModels(): Promise<ExportedModelInfo[]> {
    try {
      const res = await this.request<unknown>('/api/generations/models', undefined, 15_000);
      const data = extractData<{ models?: ExportedModelInfo[] }>(res);
      return Array.isArray(data?.models) ? data.models : [];
    } catch {
      return [];
    }
  }

  // ---------- upload ----------

  /** 上传一张图片到 output/uploads/，返回服务器路径与 Web URL。 */
  async uploadImage(file: File): Promise<{ path: string; url: string }> {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 120_000);
    try {
      const form = new FormData();
      form.append('file', file);
      const res = await fetch(`${this.baseURL}/api/upload`, {
        method: 'POST',
        body: form,
        signal: controller.signal,
      });
      if (!res.ok) {
        let errMsg = `Upload failed: ${res.status} ${res.statusText}`;
        try {
          const errData = await res.json();
          if (errData.error) errMsg = errData.error;
        } catch { /* ignore */ }
        throw new APIError(errMsg, res.status);
      }
      return extractData<{ path: string; url: string }>(await res.json());
    } finally {
      clearTimeout(timeout);
    }
  }

  // ---------- desktop pet ----------

  /**
   * 部署桌面桌宠：后端会先用官方 Cubism Core 做像素级验收，
   * 通过后拉起原生透明窗口并等待首帧。需要 Windows + live2d-py。
   */
  async deployDesktop(
    modelDir: string,
  ): Promise<{ deployed?: boolean; runtime_verified?: boolean; [key: string]: unknown }> {
    const res = await this.request<unknown>(
      '/api/deploy/desktop',
      { method: 'POST', body: JSON.stringify({ model_dir: modelDir }) },
      120_000,
    );
    return extractData<any>(res);
  }

  // ---------- character references & embedding ----------

  /** 为角色登记某个视角的参考图（imagePath 来自 uploadImage().path）。 */
  async addCharacterReference(
    id: string,
    imagePath: string,
    view: 'front' | 'side' | 'back',
  ): Promise<{ card: Character; embedding_extracted: boolean; embedding_note?: string }> {
    const res = await this.request<unknown>(
      `/api/characters/${encodeURIComponent(id)}/references`,
      { method: 'POST', body: JSON.stringify({ image_path: imagePath, view }) },
    );
    return extractData<any>(res);
  }

  /** 重新提取角色视觉 embedding（CLIP 可用时用 CLIP，否则直方图）。 */
  async recomputeCharacterEmbedding(
    id: string,
  ): Promise<{ has_embedding: boolean; dim: number; reason?: string }> {
    const res = await this.request<unknown>(
      `/api/characters/${encodeURIComponent(id)}/embedding`,
      { method: 'POST' },
      10 * 60_000,
    );
    return extractData<any>(res);
  }
}

export interface LatestGeneration {
  character_id: string;
  image_path: string;
  image_url: string;
  layers_dir: string;
  layer_count: number;
  segmentation_method: string;
  psd_path: string;
  psd_url: string;
  model3_json: string;
  created_at: string;
  /** Go 后端补充的派生字段：Web 可加载的 model3.json 地址 */
  model3_url?: string;
  /** Go 后端补充的派生字段：模型目录（桌宠部署 model_dir） */
  output_dir?: string;
}

/**
 * 后端 progress 广播的 stage 名 → 前端流水线步骤 id。
 * 覆盖 GenerateCharacter 的粗粒度阶段与导出 StageTracker 的 Python 阶段名；
 * 未识别的阶段退回 generating（仅更新 message，不推进步骤）。
 */
function progressMessageToStep(
  stage: string,
  progress: number,
  message?: string,
): GenerationStep {
  const id: PipelineStatus = (() => {
    switch (stage) {
      case 'starting':
        return 'queued';
      case 'generating':
      case 'generate':
        return 'generating';
      case 'qa':
      case 'qa_check':
        return 'qa';
      case 'optimizing':
        return 'optimizing';
      case 'segmenting':
      case 'layering':
        return 'segmenting';
      case 'rigging':
        return 'rigging';
      case 'done':
        return 'done';
      case 'error':
        return 'error';
      default:
        return 'generating';
    }
  })();
  return {
    id,
    label: '',
    status: stage === 'error' ? 'error' : stage === 'done' ? 'done' : 'active',
    progress,
    message,
  };
}

const OUTPUT_URL_PREFIX = '/output/';
const OUTPUT_PATH_SEP = '/output/';

/** 从 latest_generation.json 记录里的 model3_json 服务器路径推导 Web URL。 */
export function modelUrlFromRecord(
  record: Pick<LatestGeneration, 'model3_url' | 'model3_json'>,
): string {
  if (record.model3_url) return record.model3_url;
  const normalized = String(record.model3_json || '').split('\\').join('/');
  const idx = normalized.indexOf(OUTPUT_PATH_SEP);
  if (idx < 0) return '';
  const rel = normalized.substring(idx + OUTPUT_PATH_SEP.length);
  return OUTPUT_URL_PREFIX.concat(rel);
}

/** 从 model3_json 服务器路径推导模型目录（桌宠部署所需 model_dir）。 */
export function modelDirFromRecord(
  record: Pick<LatestGeneration, 'output_dir' | 'model3_json'>,
): string {
  if (record.output_dir) return record.output_dir;
  const normalized = String(record.model3_json || '').split('\\').join('/');
  const idx = normalized.lastIndexOf('/');
  if (idx < 0) return '';
  return normalized.substring(0, idx);
}

export interface SegmentedLayer {
  name: string;
  part_name: string;
  url: string;
  pixel_count: number;
}

export interface SegmentResult {
  method: string;
  layers_dir: string;
  layer_count: number;
  layers: SegmentedLayer[];
  composite_preview: string;
  source_image_url: string;
  psd_path: string;
  psd_url: string;
  psd_success: boolean;
}

export interface ImportResult {
  ok?: boolean;
  layers_dir: string;
  layer_count: number;
  layers: Array<{ name: string; path: string; url?: string; pixel_count?: number; group?: string }>;
  psd_url?: string;
  canvas?: [number, number];
  total_found?: number;
}

export interface ExportedModelInfo {
  model3_json: string;
  output_dir: string;
  model3_url: string;
  name: string;
  mod_time?: string;
}

/**
 * 推导后端 WebSocket 地址。
 * WS 不受 CORS 约束，可直连后端；优先用注入/内联的后端地址，
 * 否则退回当前源（依赖 dev server 代理转发 upgrade）。
 */
export function getBackendWsUrl(path: string): string {
  const httpBase =
    (typeof window !== 'undefined' &&
      (window as unknown as { __LIVE2D_API_URL__?: string }).__LIVE2D_API_URL__) ||
    process.env.NEXT_PUBLIC_API_URL ||
    (typeof window !== 'undefined' ? window.location.origin : '');
  return httpBase.replace(/^http/, 'ws').replace(/\/$/, '') + path;
}

export const apiClient = new APIClient();
