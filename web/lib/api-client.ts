import type {
  Character,
  CharacterCreate,
  ChatMessage,
  ExportFormat,
  Expression,
  GenerationRequest,
  GenerationResult,
  GenerationStep,
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
    // v10.1: Send as JSON matching Go CharacterRequest structure (snake_case)
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
    // Try backend-provided thumbnails, then fall back to a local convention:
    //   /generated/{characterId}.png — populated when a real workflow image
    //   was generated for this character.
    const apiThumb = raw.thumbnailUrl || raw.thumbnail_url || raw.image_url || raw.imageUrl || '';
    const localThumb = id ? `/generated/${id}.png` : '';
    const thumbnailUrl = apiThumb || localThumb;
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
    // v10.1: Backend uses PUT (not PATCH)
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
    // v10.1: Full pipeline generation via /api/generate/character (image→QA→segment→rig→Live2D)
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
    // v10.1: Stream endpoint returns progress via SSE from WebSocket hub,
    // but for simplicity we fall back to calling generateCharacter with progress
    // simulated from the returned steps. If true SSE is needed, use /ws endpoint.
    const payload = this.buildGenerationPayload(req);
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 10 * 60_000);
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
    // v10.1: Map full workflow result (with layers, model3, psd, etc.)
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
        source: data.source ?? 'workflow_v10.1',
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
    // v10.1: Use SSE chat/stream endpoint with snake_case payload matching Go ChatRequest
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
  ): Promise<{ model3_json?: string; texture?: string; model_path?: string; success: boolean }> {
    // v10.1: POST /api/export/live2d with JSON body (not GET with query params)
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
        version: (data.version as string) ?? 'v10.1',
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
}

export const apiClient = new APIClient();
