package services

import (
	"fmt"
	"image"
	"io"
	"mime/multipart"
	"os"
	"path/filepath"
	"strings"
	"time"

	"live2d-api/config"
)

// maxUploadBytes 单文件上传上限（25MB，足够 4K 立绘 PNG）。
const maxUploadBytes = 25 << 20

// allowedImageExt 上传图片扩展名白名单（小写）。
var allowedImageExt = map[string]bool{
	".png":  true,
	".jpg":  true,
	".jpeg": true,
	".webp": true,
	".bmp":  true,
}

// sanitizeUploadName 只保留文件名中的安全字符，其余折叠为下划线。
func sanitizeUploadName(name string) string {
	name = strings.TrimSpace(name)
	if name == "" {
		return "image"
	}
	var b strings.Builder
	for _, r := range name {
		switch {
		case r >= 'a' && r <= 'z', r >= 'A' && r <= 'Z', r >= '0' && r <= '9', r == '-', r == '_':
			b.WriteRune(r)
		default:
			b.WriteByte('_')
		}
	}
	out := b.String()
	if len(out) > 64 {
		out = out[len(out)-64:]
	}
	return out
}

// SaveUpload 把 multipart 上传的图片保存到 output/uploads/ 下，
// 返回服务器端绝对路径（供 /api/segment、参考图等端点使用）与 Web URL。
func SaveUpload(cfg *config.Config, fh *multipart.FileHeader) (string, string, error) {
	if fh == nil {
		return "", "", fmt.Errorf("缺少上传文件")
	}
	ext := strings.ToLower(filepath.Ext(fh.Filename))
	if !allowedImageExt[ext] {
		return "", "", fmt.Errorf("不支持的图片格式 %q（允许: png/jpg/jpeg/webp/bmp）", ext)
	}
	if fh.Size <= 0 || fh.Size > maxUploadBytes {
		return "", "", fmt.Errorf("文件大小超出限制（最多 %d MB）", maxUploadBytes>>20)
	}
	dir := filepath.Join(cfg.Output.BaseDir, "uploads")
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return "", "", fmt.Errorf("创建上传目录失败: %v", err)
	}
	base := sanitizeUploadName(strings.TrimSuffix(filepath.Base(fh.Filename), filepath.Ext(fh.Filename)))
	dest := filepath.Join(dir, fmt.Sprintf("upload_%d_%s%s", time.Now().UnixMilli(), base, ext))

	src, err := fh.Open()
	if err != nil {
		return "", "", fmt.Errorf("读取上传文件失败: %v", err)
	}
	defer src.Close()
	out, err := os.Create(dest)
	if err != nil {
		return "", "", fmt.Errorf("写入上传文件失败: %v", err)
	}
	defer out.Close()
	if _, err := io.Copy(out, src); err != nil {
		os.Remove(dest)
		return "", "", fmt.Errorf("保存上传文件失败: %v", err)
	}
	return dest, OutputURLFor(cfg, dest), nil
}

// ImportPNGSet 把用户上传的多张 PNG 按提交顺序组装成一个"图层集"目录
// （output/png_import_<ts>/），供分层工作台预览与 Live2D 导出直接使用。
// 所有图层统一规范到最大画布尺寸（左上对齐）—— Live2D 网格要求各层
// 共享同一画布，异尺寸直接导出会被 moc3 构建器以"画布尺寸不一致"拒绝。
// 返回目录路径与有序图层信息。
func ImportPNGSet(cfg *config.Config, files []*multipart.FileHeader) (string, []map[string]interface{}, error) {
	if len(files) == 0 {
		return "", nil, fmt.Errorf("缺少上传文件")
	}
	if len(files) > 200 {
		return "", nil, fmt.Errorf("单次最多导入 200 张 PNG")
	}
	// 图层导入仅接受标准库可解码的 png/jpg/jpeg（webp/bmp 请先转格式）
	importableExt := map[string]bool{".png": true, ".jpg": true, ".jpeg": true}
	for _, fh := range files {
		ext := strings.ToLower(filepath.Ext(fh.Filename))
		if !importableExt[ext] {
			return "", nil, fmt.Errorf("不支持的图片格式 %q（图层导入允许: png/jpg/jpeg）", ext)
		}
		if fh.Size <= 0 || fh.Size > maxUploadBytes {
			return "", nil, fmt.Errorf("文件 %s 大小超出限制（最多 %d MB）", fh.Filename, maxUploadBytes>>20)
		}
	}
	dir := filepath.Join(cfg.Output.BaseDir, fmt.Sprintf("png_import_%d", time.Now().Unix()))
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return "", nil, fmt.Errorf("创建导入目录失败: %v", err)
	}

	// 先整体解码，拿到最大画布
	type decoded struct {
		img  image.Image
		name string
		base string
		ext  string
	}
	decodedFiles := make([]decoded, 0, len(files))
	maxW, maxH := 0, 0
	for _, fh := range files {
		src, err := fh.Open()
		if err != nil {
			return "", nil, fmt.Errorf("读取上传文件失败: %v", err)
		}
		img, err := decodeImageReader(src)
		src.Close()
		if err != nil {
			return "", nil, fmt.Errorf("文件 %s 无法解码为图片: %v", fh.Filename, err)
		}
		b := img.Bounds()
		if b.Dx() > maxW {
			maxW = b.Dx()
		}
		if b.Dy() > maxH {
			maxH = b.Dy()
		}
		decodedFiles = append(decodedFiles, decoded{
			img:  img,
			name: strings.TrimSuffix(fh.Filename, filepath.Ext(fh.Filename)),
			base: sanitizeUploadName(strings.TrimSuffix(filepath.Base(fh.Filename), filepath.Ext(fh.Filename))),
			ext:  strings.ToLower(filepath.Ext(fh.Filename)),
		})
	}

	layers := make([]map[string]interface{}, 0, len(decodedFiles))
	for i, df := range decodedFiles {
		dest := filepath.Join(dir, fmt.Sprintf("%03d_%s.png", i, df.base))
		// 规范化到统一画布（左上对齐，透明补边）
		if err := NormalizeToCanvas(df.img, maxW, maxH, dest); err != nil {
			return "", nil, fmt.Errorf("图层 %s 规范化失败: %v", df.name, err)
		}
		layers = append(layers, map[string]interface{}{
			"name":  df.name,
			"path":  dest,
			"order": i,
		})
	}
	return dir, layers, nil
}

// SavePSD 把上传的 PSD 源文件保存到 output/uploads/（供下载与 psd-tools 解析）。
func SavePSD(cfg *config.Config, fh *multipart.FileHeader) (string, error) {
	if fh == nil {
		return "", fmt.Errorf("缺少上传文件")
	}
	if !strings.EqualFold(filepath.Ext(fh.Filename), ".psd") {
		return "", fmt.Errorf("仅支持 .psd 文件")
	}
	if fh.Size <= 0 || fh.Size > 500<<20 {
		return "", fmt.Errorf("PSD 大小超出限制（最多 500MB）")
	}
	dir := filepath.Join(cfg.Output.BaseDir, "uploads")
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return "", fmt.Errorf("创建上传目录失败: %v", err)
	}
	base := sanitizeUploadName(strings.TrimSuffix(filepath.Base(fh.Filename), filepath.Ext(fh.Filename)))
	dest := filepath.Join(dir, fmt.Sprintf("upload_%d_%s.psd", time.Now().UnixMilli(), base))
	src, err := fh.Open()
	if err != nil {
		return "", fmt.Errorf("读取上传文件失败: %v", err)
	}
	defer src.Close()
	out, err := os.Create(dest)
	if err != nil {
		return "", fmt.Errorf("写入上传文件失败: %v", err)
	}
	defer out.Close()
	if _, err := io.Copy(out, src); err != nil {
		os.Remove(dest)
		return "", fmt.Errorf("保存上传文件失败: %v", err)
	}
	return dest, nil
}

// ValidateUploadedImagePath 校验一个"已上传图片"的服务器端路径：必须是
// output/uploads/ 下的既有文件。参考图端点用它防止把任意服务器路径塞进
// Python 处理管线。
func ValidateUploadedImagePath(cfg *config.Config, path string) error {
	if path == "" {
		return fmt.Errorf("缺少图片路径")
	}
	uploadsDir := filepath.Join(cfg.Output.BaseDir, "uploads")
	abs, err := filepath.Abs(path)
	if err != nil {
		return fmt.Errorf("非法的图片路径")
	}
	absUploads, err := filepath.Abs(uploadsDir)
	if err != nil {
		return fmt.Errorf("非法的图片路径")
	}
	rel, err := filepath.Rel(absUploads, abs)
	if err != nil || rel == ".." || strings.HasPrefix(rel, "..") {
		return fmt.Errorf("图片路径必须位于 uploads 目录内")
	}
	info, err := os.Stat(abs)
	if err != nil {
		return fmt.Errorf("图片不存在，请先通过 /api/upload 上传")
	}
	if info.IsDir() {
		return fmt.Errorf("图片路径指向的是目录而不是文件")
	}
	return nil
}
