package services

import (
	"fmt"
	"image"
	"image/draw"
	"image/jpeg"
	"image/png"
	"io"
	"os"
)

// 本包所有图像处理均基于 Go 标准库（image/png、image/jpeg、image/draw），
// 不引入外部图像依赖，也不执行任何外部进程。
//
// 注意：标准库不解码 webp/bmp；图层导入因此仅接受 png/jpg/jpeg。

// decodeImageFile 按真实格式解码图片文件（png/jpeg）。
func decodeImageFile(path string) (image.Image, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer f.Close()
	img, _, err := image.Decode(f)
	if err != nil {
		return nil, err
	}
	return img, nil
}

// decodeImageReader 按真实格式解码图片流（png/jpeg）。
func decodeImageReader(r io.Reader) (image.Image, error) {
	img, _, err := image.Decode(r)
	if err != nil {
		return nil, err
	}
	return img, nil
}

// NormalizeToCanvas 把 src 绘制到 maxW×maxH 的透明 RGBA 画布上
// （左上对齐），并写出为 PNG。Live2D 的所有网格共享同一张画布，
// 图层集必须在进入构建器之前完成这一规范化。
func NormalizeToCanvas(src image.Image, maxW, maxH int, destPath string) error {
	if src.Bounds().Dx() > maxW || src.Bounds().Dy() > maxH {
		return fmt.Errorf("图层尺寸 %s 超出画布 %dx%d", src.Bounds().Size(), maxW, maxH)
	}
	canvas := image.NewRGBA(image.Rect(0, 0, maxW, maxH))
	draw.Draw(canvas, canvas.Bounds(), src, src.Bounds().Min, draw.Over)

	out, err := os.Create(destPath)
	if err != nil {
		return err
	}
	defer out.Close()
	if err := png.Encode(out, canvas); err != nil {
		return err
	}
	return nil
}

// 保留 jpeg 解码注册（图层导入允许 jpg/jpeg 源），避免编译器报未使用。
var _ = jpeg.Decode
