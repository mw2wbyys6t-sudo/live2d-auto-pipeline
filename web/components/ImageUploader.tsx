import { useCallback, useEffect, useRef, useState, DragEvent } from 'react';
import { Upload, ImageIcon, X } from 'lucide-react';

interface ImageUploaderProps {
  value?: string | null;
  onChange: (file: File | null) => void;
  label?: string;
  accept?: string;
  maxSizeMB?: number;
  className?: string;
  /** 预览完全由 value（如工作台的 object URL）驱动，组件不再自建 URL。 */
  useExternalPreview?: boolean;
}

export default function ImageUploader({
  value,
  onChange,
  label = '拖入或点击上传图片',
  accept = 'image/*',
  maxSizeMB = 10,
  className = '',
  useExternalPreview = false,
}: ImageUploaderProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [preview, setPreview] = useState<string | null>(value || null);
  const [dragOver, setDragOver] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const internalUrlRef = useRef<string | null>(null);

  useEffect(() => {
    setPreview(useExternalPreview || !internalUrlRef.current ? value || null : internalUrlRef.current);
  }, [value, useExternalPreview]);

  useEffect(() => {
    return () => {
      if (internalUrlRef.current) URL.revokeObjectURL(internalUrlRef.current);
      internalUrlRef.current = null;
    };
  }, []);

  const releaseInternal = () => {
    if (internalUrlRef.current) URL.revokeObjectURL(internalUrlRef.current);
    internalUrlRef.current = null;
  };

  const handleFile = useCallback(
    (file: File | null) => {
      setError(null);
      if (!file) {
        releaseInternal();
        setPreview(null);
        onChange(null);
        return;
      }
      if (!file.type.startsWith('image/')) {
        setError('请上传图片文件');
        return;
      }
      if (file.size > maxSizeMB * 1024 * 1024) {
        setError(`文件超过 ${maxSizeMB}MB 上限`);
        return;
      }
      if (!useExternalPreview) {
        releaseInternal();
        const url = URL.createObjectURL(file);
        internalUrlRef.current = url;
        setPreview(url);
      }
      onChange(file);
    },
    [maxSizeMB, onChange, useExternalPreview],
  );

  const onDrop = useCallback(
    (e: DragEvent<HTMLDivElement>) => {
      e.preventDefault();
      setDragOver(false);
      const file = e.dataTransfer.files?.[0];
      if (file) handleFile(file);
    },
    [handleFile],
  );

  const onClear = () => {
    handleFile(null);
    if (inputRef.current) inputRef.current.value = '';
  };

  return (
    <div className={className}>
      {preview ? (
        <div className="group relative overflow-hidden rounded-xl border border-black/[0.08] bg-ink-950">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src={preview} alt="preview" className="max-h-64 w-full object-contain" />
          <button
            onClick={onClear}
            className="absolute right-2 top-2 rounded-lg bg-ink-950/70 p-1.5 text-white opacity-0 backdrop-blur transition-colors hover:bg-rose-500 group-hover:opacity-100"
            aria-label="Remove image"
          >
            <X className="w-4 h-4" />
          </button>
        </div>
      ) : (
        <div
          onClick={() => inputRef.current?.click()}
          onDragOver={(e) => {
            e.preventDefault();
            setDragOver(true);
          }}
          onDragLeave={() => setDragOver(false)}
          onDrop={onDrop}
          className={`cursor-pointer rounded-xl border border-dashed p-5 text-center transition-all ${
            dragOver
              ? 'border-sakura-500/70 bg-sakura-500/[0.08]'
              : 'border-black/[0.14] bg-black/[0.02] hover:border-sakura-500/40 hover:bg-black/[0.04]'
          }`}
        >
          <div className="flex flex-col items-center gap-2">
            <div className="flex h-11 w-11 items-center justify-center rounded-xl border border-black/[0.08] bg-black/[0.03]">
              {dragOver ? (
                <Upload className="h-5 w-5 text-sakura-600" />
              ) : (
                <ImageIcon className="h-5 w-5 text-haze-400" />
              )}
            </div>
            <p className="text-xs text-haze-300">{label}</p>
            <p className="text-[10px] text-haze-600">PNG, JPG, WebP · up to {maxSizeMB}MB</p>
          </div>
        </div>
      )}
      <input
        ref={inputRef}
        type="file"
        accept={accept}
        className="hidden"
        onChange={(e) => handleFile(e.target.files?.[0] || null)}
      />
      {error && <p className="mt-2 text-xs text-red-400">{error}</p>}
    </div>
  );
}
