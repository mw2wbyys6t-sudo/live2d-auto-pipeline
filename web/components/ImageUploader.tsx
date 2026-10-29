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
        <div className="relative group rounded-xl overflow-hidden border border-gray-800 bg-gray-900">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src={preview} alt="preview" className="w-full h-auto max-h-64 object-contain" />
          <button
            onClick={onClear}
            className="absolute top-2 right-2 p-1.5 rounded-lg bg-black/60 text-white hover:bg-red-500 transition-colors opacity-0 group-hover:opacity-100"
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
          className={`cursor-pointer rounded-xl border-2 border-dashed p-6 text-center transition-all ${
            dragOver
              ? 'border-pink-500 bg-pink-500/10'
              : 'border-gray-700 hover:border-gray-600 bg-gray-900/50 hover:bg-gray-900'
          }`}
        >
          <div className="flex flex-col items-center gap-2">
            <div className="w-12 h-12 rounded-xl bg-gray-800 flex items-center justify-center">
              {dragOver ? (
                <Upload className="w-5 h-5 text-pink-400" />
              ) : (
                <ImageIcon className="w-5 h-5 text-gray-400" />
              )}
            </div>
            <p className="text-sm text-gray-300">{label}</p>
            <p className="text-xs text-gray-500">PNG, JPG, WebP · up to {maxSizeMB}MB</p>
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
