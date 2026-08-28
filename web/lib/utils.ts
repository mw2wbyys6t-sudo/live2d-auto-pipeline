export function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

export function getErrorMessage(error: string | undefined): { title: string; message: string; suggestion: string } {
  if (!error) {
    return { title: '未知错误', message: '发生了未知错误', suggestion: '请尝试重新上传文件' };
  }

  if (error.includes('magic') || error.includes('signature') || error.includes('8BPS')) {
    return {
      title: '无效的 PSD 文件',
      message: '文件头部签名无效，可能不是有效的 Photoshop PSD 文件',
      suggestion: '请确保上传的是标准的 PSD 文件格式'
    };
  }

  if (error.includes('length') || error.includes('size')) {
    return {
      title: '文件损坏',
      message: '文件大小与声明的大小不一致',
      suggestion: '请检查文件是否完整，或尝试重新保存 PSD 文件'
    };
  }

  if (error.includes('EOF') || error.includes('end of file')) {
    return {
      title: '文件不完整',
      message: '文件在传输过程中可能被截断',
      suggestion: '请重新上传完整的 PSD 文件'
    };
  }

  if (error.includes('version')) {
    return {
      title: '版本不兼容',
      message: 'PSD 文件版本不受支持',
      suggestion: '请使用 Photoshop CC 2015 或更高版本保存文件'
    };
  }

  if (error.includes('parse') || error.includes('decode')) {
    return {
      title: '解析失败',
      message: '无法解析 PSD 文件结构',
      suggestion: '请确保文件是有效的 PSD 格式，没有损坏'
    };
  }

  return {
    title: '分析失败',
    message: error,
    suggestion: '请尝试使用其他 PSD 文件，或检查文件是否损坏'
  };
}
