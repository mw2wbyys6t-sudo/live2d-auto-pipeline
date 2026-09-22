package handlers

import (
	"bytes"
	"encoding/json"
	"image"
	"image/color"
	"image/png"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"

	"github.com/gin-gonic/gin"
	"live2d-api/config"
	"live2d-api/services"
)

// 真实链路：HTTP -> Go 桥接 -> Python 构建 -> 编译 moc3 -> 官方 Cubism Core 验收。
//
// 需要本机装了运行时，故按仓库惯例用环境变量显式开启：
//
//	LIVE2D_TEST_PYTHON=<python 可执行文件> LIVE2D_TEST_ROOT=<仓库根目录>
func TestNativeExportLive2D(t *testing.T) {
	python, root := os.Getenv("LIVE2D_TEST_PYTHON"), os.Getenv("LIVE2D_TEST_ROOT")
	if python == "" || root == "" {
		t.Skip("real export environment not configured")
	}

	layersDir := filepath.Join(t.TempDir(), "layers")
	if err := writeTestLayers(layersDir); err != nil {
		t.Fatalf("构造测试图层失败: %v", err)
	}
	outputDir := filepath.Join(t.TempDir(), "export")

	cfg := &config.Config{
		Python: config.PythonConfig{PythonPath: python, ScriptsDir: root, TimeoutSec: 600},
		Output: config.OutputConfig{BaseDir: filepath.Dir(outputDir)},
	}
	h := &Handler{pythonBridge: services.NewPythonBridge(cfg), cfg: cfg}
	gin.SetMode(gin.TestMode)
	router := gin.New()
	router.POST("/api/export/live2d", h.ExportLive2D)
	server := httptest.NewServer(router)
	defer server.Close()

	payload, _ := json.Marshal(map[string]string{
		"character_id": "gptest",
		"layers_dir":   layersDir,
		"output_dir":   outputDir,
	})
	response, err := http.Post(server.URL+"/api/export/live2d",
		"application/json", bytes.NewReader(payload))
	if err != nil {
		t.Fatal(err)
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		t.Fatalf("导出返回 %d，期望 200", response.StatusCode)
	}
	var envelope struct {
		Success bool                   `json:"success"`
		Data    map[string]interface{} `json:"data"`
	}
	if err := json.NewDecoder(response.Body).Decode(&envelope); err != nil {
		t.Fatal(err)
	}
	data := envelope.Data

	// API 必须把就绪状态平铺出来，前端就读这两个字段
	if data["runtime_ready"] != true {
		t.Fatalf("官方内核已参与验收却报告未就绪: blocker=%v moc3=%v",
			data["blocker"], data["moc3"])
	}
	if blocker, _ := data["blocker"].(string); blocker != "" {
		t.Errorf("就绪时 blocker 应为空，实得 %q", blocker)
	}
	moc3, _ := data["moc3"].(map[string]interface{})
	if moc3["moc3_written"] != true || moc3["official_core_consistent"] != true {
		t.Fatalf("moc3 明细与就绪状态矛盾: %v", moc3)
	}
	if size, _ := moc3["moc3_bytes"].(float64); size <= 1984 {
		t.Errorf("moc3 大小 %v 不像是含实体的产物", moc3["moc3_bytes"])
	}

	// 磁盘上的 moc3 必须是真文件、真头部
	model3, _ := data["model3_json"].(string)
	if model3 == "" {
		t.Fatal("响应里没有 model3_json 路径")
	}
	mocPath := filepath.Join(filepath.Dir(model3), "gptest.moc3")
	raw, err := os.ReadFile(mocPath)
	if err != nil {
		t.Fatalf("导出目录缺少 moc3: %v", err)
	}
	if len(raw) < 4 || string(raw[:4]) != "MOC3" {
		t.Errorf("moc3 头部不是 MOC3: %q", raw[:4])
	}
	if _, err := os.Stat(filepath.Join(filepath.Dir(model3), "build_meta.json")); err != nil {
		t.Errorf("缺少 build_meta.json: %v", err)
	}
}

// TestNativeExportLive2DWithoutLayersIsHonest：空图层目录不能报成功。
func TestNativeExportLive2DWithoutLayersIsHonest(t *testing.T) {
	python, root := os.Getenv("LIVE2D_TEST_PYTHON"), os.Getenv("LIVE2D_TEST_ROOT")
	if python == "" || root == "" {
		t.Skip("real export environment not configured")
	}
	empty := t.TempDir()
	cfg := &config.Config{
		Python: config.PythonConfig{PythonPath: python, ScriptsDir: root, TimeoutSec: 120},
		Output: config.OutputConfig{BaseDir: empty},
	}
	h := &Handler{pythonBridge: services.NewPythonBridge(cfg), cfg: cfg}
	gin.SetMode(gin.TestMode)
	router := gin.New()
	router.POST("/api/export/live2d", h.ExportLive2D)
	server := httptest.NewServer(router)
	defer server.Close()

	payload, _ := json.Marshal(map[string]string{
		"character_id": "empty",
		"layers_dir":   empty,
		"output_dir":   filepath.Join(empty, "out"),
	})
	response, err := http.Post(server.URL+"/api/export/live2d",
		"application/json", bytes.NewReader(payload))
	if err != nil {
		t.Fatal(err)
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusUnprocessableEntity {
		t.Fatalf("空图层必须显式失败，实得 %d", response.StatusCode)
	}
	var envelope struct {
		Success bool                   `json:"success"`
		Data    map[string]interface{} `json:"data"`
	}
	if err := json.NewDecoder(response.Body).Decode(&envelope); err != nil {
		t.Fatal(err)
	}
	if envelope.Success {
		t.Error("空图层导出不得报告 success")
	}
	if ready, _ := envelope.Data["runtime_ready"].(bool); ready {
		t.Error("空图层导出不得报告 runtime_ready")
	}
	if blocker, _ := envelope.Data["blocker"].(string); blocker == "" {
		t.Error("未就绪必须给出 blocker")
	}
}

func writeTestLayers(dir string) error {
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return err
	}
	colors := map[string]color.NRGBA{
		"00_body":  {R: 220, G: 180, B: 160, A: 255},
		"01_hair":  {R: 60, G: 40, B: 40, A: 255},
		"02_face":  {R: 250, G: 220, B: 200, A: 255},
		"03_empty": {},
	}
	for name, c := range colors {
		img := image.NewNRGBA(image.Rect(0, 0, 64, 64))
		if name != "03_empty" {
			for y := 8; y < 56; y++ {
				for x := 8; x < 56; x++ {
					img.SetNRGBA(x, y, c)
				}
			}
		}
		file, err := os.Create(filepath.Join(dir, name+".png"))
		if err != nil {
			return err
		}
		if err := png.Encode(file, img); err != nil {
			file.Close()
			return err
		}
		if err := file.Close(); err != nil {
			return err
		}
	}
	return nil
}
