export const MAX_FILE_SIZE = 50 * 1024 * 1024;
export const MAX_IMAGE_DIMENSION = 8192;
export const PARSE_TIMEOUT = 30000;

export function sanitizeFileName(name: string): string {
  return name
    .replace(/[<>"'\/\\|?*\x00-\x1f\u202E\u200E\u200F]/g, '')
    .replace(/\.+$/g, '')
    .replace(/\s+/g, '_')
    .trim()
    .slice(0, 200) || 'download';
}

export async function validatePSDFile(file: File): Promise<boolean> {
  if (file.size < 4) return false;
  const buffer = await file.slice(0, 4).arrayBuffer();
  const bytes = new Uint8Array(buffer);
  return bytes[0] === 0x38 && bytes[1] === 0x42 && bytes[2] === 0x50 && bytes[3] === 0x53;
}

export async function validateImageFile(file: File): Promise<boolean> {
  const signatures: Record<string, number[]> = {
    'image/png': [0x89, 0x50, 0x4E, 0x47],
    'image/jpeg': [0xFF, 0xD8, 0xFF],
    'image/gif': [0x47, 0x49, 0x46],
    'image/webp': [0x52, 0x49, 0x46, 0x46],
    'image/bmp': [0x42, 0x4D],
  };

  const buffer = await file.slice(0, 8).arrayBuffer();
  const bytes = new Uint8Array(buffer);

  for (const sig of Object.values(signatures)) {
    if (sig.every((b, i) => bytes[i] === b)) return true;
  }
  return false;
}

export function withTimeout<T>(promise: Promise<T>, ms: number, message: string): Promise<T> {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error(message)), ms);
    promise
      .then((result) => { clearTimeout(timer); resolve(result); })
      .catch((err) => { clearTimeout(timer); reject(err); });
  });
}
