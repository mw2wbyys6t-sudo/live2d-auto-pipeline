package services

import (
	"fmt"
	"path/filepath"
	"regexp"
	"strings"
)

var characterIDPattern = regexp.MustCompile(`^[A-Za-z0-9_-]{1,128}$`)

func validateCharacterID(id string) error {
	if !characterIDPattern.MatchString(id) {
		return fmt.Errorf("角色编号无效")
	}
	return nil
}

// 生图尺寸上下界：防止单个请求申请超大画布导致 OOM（4096² × 4B ≈ 64MB/层，
// 已覆盖所有主流图像 API 的最大尺寸）。下限与 PSD 导入校验的 64px 对齐。
const (
	minImageDimension = 64
	maxImageDimension = 4096
)

// clampImageDimension 把宽高钳制到安全区间。
// 保持「<=0 → 调用方默认值」的既有语义，因此负值/0 原样返回。
func clampImageDimension(v int) int {
	if v <= 0 {
		return v
	}
	if v < minImageDimension {
		return minImageDimension
	}
	if v > maxImageDimension {
		return maxImageDimension
	}
	return v
}

// ClampImageDimensions 钳制请求的宽高（供 handler/service 在默认值填充后调用）。
func ClampImageDimensions(width, height int) (int, int) {
	return clampImageDimension(width), clampImageDimension(height)
}

// promptMaxRunes 是提示词上限：按**字符**计，不是字节。旧实现用 len()/切片
// 按字节截断，4000 字节约等于 1333 个汉字，而且切点可能落在汉字或 emoji
// 中间，下游拿到非法 UTF-8（显示成 �，模型也读不对）。
const promptMaxRunes = 4000

// truncatePromptRunes 按 Unicode 码点截断，绝不切开一个字符。
func truncatePromptRunes(s string, limit int) string {
	if limit <= 0 {
		return ""
	}
	count := 0
	for i := range s {
		if count == limit {
			return s[:i]
		}
		count++
	}
	return s
}

// ErrPathOutsideBase 是目录归属校验失败的哨兵错误，handler 层可用 errors.Is
// 把它映射为 400（请求非法）而非 500（服务内部错误）。
var ErrPathOutsideBase = fmt.Errorf("路径超出允许的输出目录范围")

// validateWithinBase 判定 target 解析为绝对路径后是否仍位于 baseDir 之内。
// 与 handlers.isPathSafe 同一语义（Abs + Rel 前缀检查），用于桥接层把请求体
// 中的 layers_dir/output_dir 等路径参数限定在输出根目录，阻止绝对路径穿越。
func validateWithinBase(target, baseDir, label string) error {
	absTarget, err := filepath.Abs(filepath.Clean(target))
	if err != nil {
		return fmt.Errorf("%s 路径无效", label)
	}
	absBase, err := filepath.Abs(filepath.Clean(baseDir))
	if err != nil {
		return fmt.Errorf("%s 根目录无效", label)
	}
	rel, err := filepath.Rel(absBase, absTarget)
	if err != nil || rel == ".." || strings.HasPrefix(rel, ".."+string(filepath.Separator)) {
		return fmt.Errorf("%w: %s必须位于输出目录内", ErrPathOutsideBase, label)
	}
	return nil
}
