// Central TypeScript types for the Live2D Master Agent workbench

// ---------- Characters ----------

export interface Character {
  id: string;
  name: string;
  description?: string;
  personality?: string;
  appearance?: string;
  colorPalette?: ColorPalette;
  referenceImages?: ReferenceImage[];
  thumbnailUrl?: string;
  modelUrl?: string;
  embeddingStatus?: 'pending' | 'processing' | 'ready' | 'failed';
  embeddingId?: string;
  generationCount: number;
  createdAt: string;
  updatedAt: string;
  outfits?: Outfit[];
}

export interface CharacterCreate {
  name: string;
  description?: string;
  personality?: string;
  appearance?: string;
  colorPalette?: ColorPalette;
  referenceImages?: File[];
}

export interface CharacterCard {
  id: string;
  name: string;
  thumbnailUrl?: string;
  createdAt: string;
  generationCount: number;
}

export interface ColorPalette {
  primary: string;
  secondary: string;
  hair: string;
  eyes: string;
  skin: string;
  accent: string;
}

export interface ReferenceImage {
  id: string;
  view: 'front' | 'side' | 'back' | 'extra';
  url: string;
  filename: string;
}

export interface Outfit {
  id: string;
  name: string;
  description?: string;
  thumbnailUrl?: string;
}

// ---------- Generation ----------

export type ProviderId = 'pollinations' | 'seedream' | 'sensenova' | 'local';
export type Resolution = 512 | 768 | 1024 | 2048;
export type StylePreset =
  | 'moe'
  | 'realistic'
  | 'chibi'
  | 'anime'
  | 'watercolor'
  | 'lineart'
  | 'pixel'
  | 'cyberpunk';

export type SegmentationMethod = 'kmeans' | 'semantic';

export interface GenerationRequest {
  prompt: string;
  negativePrompt?: string;
  provider: ProviderId;
  width: Resolution;
  height: Resolution;
  style: StylePreset;
  seed?: number;
  characterId?: string;
  characterConsistency: boolean;
  steps?: number;
  cfg?: number;
  segmentationMethod?: SegmentationMethod;
}

export type PipelineStatus =
  | 'queued'
  | 'generating'
  | 'qa'
  | 'optimizing'
  | 'segmenting'
  | 'layering'
  | 'rigging'
  | 'done'
  | 'error';

export interface GenerationStep {
  id: PipelineStatus;
  label: string;
  status: 'pending' | 'active' | 'done' | 'error';
  progress: number; // 0-100
  message?: string;
  startedAt?: string;
  finishedAt?: string;
}

export interface GenerationResult {
  id: string;
  requestId: string;
  imageUrl: string;
  segmentedLayers: LayerInfo[];
  maskUrl?: string;
  qaResult?: QAResult;
  model3Url?: string;
  metadata: Record<string, string | number | boolean>;
  createdAt: string;
}

// ---------- Layers ----------

export interface LayerInfo {
  id: string;
  name: string;
  index: number;
  visible: boolean;
  opacity: number;
  blendMode: BlendMode;
  offsetX: number;
  offsetY: number;
  width: number;
  height: number;
  thumbnailUrl?: string;
  imageUrl?: string;
  bounds: LayerBounds;
  groupId?: string;
  isGroup: boolean;
  children?: LayerInfo[];
}

export interface LayerBounds {
  x: number;
  y: number;
  width: number;
  height: number;
}

export type BlendMode =
  | 'normal'
  | 'multiply'
  | 'screen'
  | 'overlay'
  | 'darken'
  | 'lighten'
  | 'color-dodge'
  | 'color-burn'
  | 'soft-light'
  | 'hard-light';

export interface LayerMask {
  layerId: string;
  maskUrl: string;
  width: number;
  height: number;
}

// ---------- Live2D ----------

export interface Live2DModel {
  id: string;
  name: string;
  version: string;
  model3: Model3Json;
  textures: string[];
  physics?: PhysicsConfig;
  expressions: Expression[];
  motions: MotionGroup[];
  hitAreas: HitArea[];
  parameters: ParameterDef[];
  createdAt: string;
}

export interface Model3Json {
  Version: number;
  FileReferences: {
    Moc: string;
    Textures: string[];
    Physics?: string;
    Expressions?: Array<{ Name: string; File: string }>;
    Motions?: Record<string, Array<{ File: string; FadeInTime?: number; FadeOutTime?: number }>>;
  };
  Groups?: Array<{
    Target: string;
    Name: string;
    Ids: string[];
  }>;
  HitAreas?: Array<{ Id: string; Name: string }>;
  Layout?: Record<string, number>;
  Parameters?: Array<{ Id: string; Min?: number; Max?: number; Default?: number }>;
  Deformers?: Array<Record<string, unknown>>;
  PreviewLayers?: Array<{
    Name: string;
    Texture: string;
    Group: string;
    Width: number;
    Height: number;
    Z: number;
  }>;
}

export interface PhysicsConfig {
  Version: number;
  Meta: {
    PhysicsSettingCount: number;
    TotalInputCount: number;
    TotalOutputCount: number;
    VertexCount: number;
    Gravity: { X: number; Y: number };
    Wind: { X: number; Y: number };
  };
  Settings: PhysicsSetting[];
}

export interface PhysicsSetting {
  Id: string;
  Input: Array<{ Source: { Target: string; Id: string }; Weight: number; Type: 'X' | 'Y' | 'Angle' }>;
  Output: Array<{ Destination: { Target: string; Id: string }; Index: number; Weight: number; Type: 'X' | 'Y' | 'Angle' }>;
  Vertices: Array<{ Position: { X: number; Y: number }; Mobility: number; Delay: number; Acceleration: number; Radius: number }>;
  Normalization?: { Position?: { Minimum: number; Maximum: number; Default: number }; Angle?: { Minimum: number; Maximum: number; Default: number } };
}

export interface Expression {
  name: string;
  file?: string;
  thumbnailUrl?: string;
  parameters?: Record<string, number>;
}

export interface MotionGroup {
  name: string;
  motions: Motion[];
}

export interface Motion {
  file: string;
  fadeInTime?: number;
  fadeOutTime?: number;
}

export interface HitArea {
  id: string;
  name: string;
}

export interface ParameterDef {
  id: string;
  name: string;
  min: number;
  max: number;
  default: number;
  value?: number;
  group?: string;
}

// ---------- Tracking ----------

export interface TrackingParams {
  ParamAngleX?: number;
  ParamAngleY?: number;
  ParamAngleZ?: number;
  ParamEyeLOpen?: number;
  ParamEyeROpen?: number;
  ParamEyeBallX?: number;
  ParamEyeBallY?: number;
  ParamMouthForm?: number;
  ParamMouthOpenY?: number;
  ParamBrowLY?: number;
  ParamBrowRY?: number;
  ParamBrowLX?: number;
  ParamBrowRX?: number;
  ParamBrowLAngle?: number;
  ParamBrowRAngle?: number;
  ParamCheek?: number;
  ParamBodyAngleX?: number;
  ParamBodyAngleY?: number;
  ParamBodyAngleZ?: number;
  ParamBreath?: number;
  ParamArmLA?: number;
  ParamArmRA?: number;
  [key: string]: number | undefined;
}

export type ParamMap = Record<string, number>;

// ---------- Chat ----------

export type ChatRole = 'user' | 'assistant' | 'system';

export type Emotion =
  | 'neutral'
  | 'happy'
  | 'sad'
  | 'angry'
  | 'surprised'
  | 'shy'
  | 'thinking'
  | 'excited';

export interface ChatMessage {
  id: string;
  role: ChatRole;
  content: string;
  emotion?: Emotion;
  timestamp: string;
  characterId?: string;
}

export interface ChatResponse {
  messageId: string;
  content: string;
  emotion?: Emotion;
  tokensUsed?: number;
  latencyMs?: number;
}

// ---------- WebSocket ----------

export type WSMessageType =
  | 'progress'
  | 'tracking'
  | 'chat'
  | 'error'
  | 'connected'
  | 'pong'
  | 'generation_start'
  | 'generation_step'
  | 'generation_complete';

export interface WSMessage {
  type: WSMessageType;
  requestId?: string;
  data?: unknown;
  error?: string;
  timestamp?: string;
}

export interface WSProgressPayload {
  step: GenerationStep;
  requestId: string;
}

export interface WSTrackingPayload {
  params: TrackingParams;
  source: 'webcam' | 'mic' | 'auto' | 'manual';
  fps?: number;
}

export interface WSChatPayload {
  messageId: string;
  chunk: string;
  done: boolean;
  emotion?: Emotion;
}

// ---------- QA ----------

export type QASeverity = 'error' | 'warning' | 'info';

export interface QAIssue {
  id: string;
  layer?: string;
  title: string;
  description: string;
  severity: QASeverity;
  category: string;
  suggestion?: string;
}

export interface QAResult {
  score: number;
  issues: QAIssue[];
  warnings: QAIssue[];
  suggestions: string[];
  layerStats: LayerStats;
  summary: QASummary;
}

export interface LayerStats {
  total: number;
  visible: number;
  hidden: number;
  groups: number;
  empty: number;
  semiTransparent: number;
  nonNormalBlend: number;
  offscreen: number;
  duplicateNames: number;
}

export interface QASummary {
  totalLayers: number;
  visibleLayers: number;
  hiddenLayers: number;
  groups: number;
  hasMissingCritical: boolean;
  hasNamingIssues: boolean;
  hasStructuralIssues: boolean;
}

// ---------- Export ----------

export type ExportFormat =
  | 'psd'
  | 'png-sequence'
  | 'live2d-package'
  | 'desktop-pet'
  | 'character-card'
  | 'texture-atlas';

export interface ExportOptions {
  characterId: string;
  formats: ExportFormat[];
  includePhysics: boolean;
  includeExpressions: boolean;
  includeMotions: boolean;
  compressionLevel: number;
}

export interface ExportJob {
  id: string;
  characterId: string;
  characterName: string;
  formats: ExportFormat[];
  status: 'pending' | 'processing' | 'done' | 'error';
  progress: number;
  downloads?: ExportDownload[];
  error?: string;
  createdAt: string;
  completedAt?: string;
}

export interface ExportDownload {
  format: ExportFormat;
  filename: string;
  size: number;
  url: string;
}

// ---------- System ----------

export interface SystemStatus {
  apiConnected: boolean;
  latencyMs: number;
  gpuAvailable: boolean;
  gpuName?: string;
  vramUsed?: number;
  vramTotal?: number;
  modelsLoaded: string[];
  providers: ProviderStatus[];
  version: string;
}

export interface ProviderStatus {
  id: ProviderId;
  name: string;
  available: boolean;
  latencyMs?: number;
}
