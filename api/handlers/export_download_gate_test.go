package handlers

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/gin-gonic/gin"

	"live2d-api/models"
)

// 打包下载闸门：只有 build_meta.json 里 moc3.runtime_ready 为 true 才允许打 zip。
//
// 回归背景（实测，2026-10）：Download 分支只查「目录是否为空」，于是被官方
// Cubism Core 拒绝的产物（文件仍在磁盘上）照样被打包发出去 —— 用户装进
// Cubism / VTube Studio 才发现加载不了。

func writeBuildMeta(t *testing.T, dir string, moc3 map[string]interface{}) {
	t.Helper()
	writeBuildMetaFull(t, dir, moc3, nil)
}

// writeBuildMetaFull 可同时写入 FF-2 的 layer_order 结论。
func writeBuildMetaFull(t *testing.T, dir string, moc3, layerOrder map[string]interface{}) {
	t.Helper()
	payload := map[string]interface{}{
		"character_name": "gate",
		"moc3":           moc3,
	}
	if layerOrder != nil {
		payload["layer_order"] = layerOrder
	}
	raw, err := json.Marshal(payload)
	if err != nil {
		t.Fatalf("序列化 build_meta 失败: %v", err)
	}
	if err := os.WriteFile(filepath.Join(dir, "build_meta.json"), raw, 0o644); err != nil {
		t.Fatalf("写 build_meta.json 失败: %v", err)
	}
}

func TestModelDirReadinessAllowsVerifiedModel(t *testing.T) {
	dir := t.TempDir()
	writeBuildMeta(t, dir, map[string]interface{}{
		"moc3_written":  true,
		"runtime_ready": true,
	})

	ready, blocker := modelDirReadiness(dir)
	if !ready {
		t.Fatalf("内核验收通过却未放行，blocker = %q", blocker)
	}
	if blocker != "" {
		t.Errorf("就绪时不该有 blocker，实际 %q", blocker)
	}
}

func TestModelDirReadinessBlocksRejectedModelWithVerbatimBlocker(t *testing.T) {
	dir := t.TempDir()
	reason := "官方内核拒绝: motion 未被官方内核播放: error: Could not load EGL library"
	writeBuildMeta(t, dir, map[string]interface{}{
		"moc3_written":  true, // 磁盘上确实写出了 moc3 —— 这正是要拦的情形
		"runtime_ready": false,
		"moc3_blocker":  reason,
	})

	ready, blocker := modelDirReadiness(dir)
	if ready {
		t.Fatal("被内核拒绝的产物被放行了")
	}
	if blocker != reason {
		t.Errorf("blocker 必须逐字等于实测原因\n got: %q\nwant: %q", blocker, reason)
	}
}

func TestModelDirReadinessBlocksMissingBuildMeta(t *testing.T) {
	// 无法证明已验收 → 一律拒绝，不得默认成功。
	ready, blocker := modelDirReadiness(t.TempDir())
	if ready {
		t.Fatal("缺少 build_meta.json 时被放行了")
	}
	if !strings.Contains(blocker, "build_meta.json") {
		t.Errorf("blocker 应说明缺的是 build_meta.json，实际 %q", blocker)
	}
}

func TestModelDirReadinessBlocksWhenRuntimeReadyIsNotBoolTrue(t *testing.T) {
	// 防御性：字段是字符串 "yes" / 缺失 / null 时都不得放行。
	cases := []map[string]interface{}{
		{"moc3_written": true},
		{"runtime_ready": "yes"},
		{"runtime_ready": nil},
	}
	for i, moc3 := range cases {
		dir := t.TempDir()
		writeBuildMeta(t, dir, moc3)
		if ready, _ := modelDirReadiness(dir); ready {
			t.Errorf("case %d: 非布尔真值被当成就绪: %v", i, moc3)
		}
	}
}

// ---------- FF-2：层序错位也要挡住下载 ----------

func TestModelDirReadinessBlocksLayerOrderMismatch(t *testing.T) {
	dir := t.TempDir()
	msg := "Live2D 渲染层序 中 'mouth_cavity' 位于第 12 位，按 STANDARD_LAYER_ORDER 应为第 11 位"
	writeBuildMetaFull(t, dir,
		map[string]interface{}{"moc3_written": true, "runtime_ready": true},
		map[string]interface{}{
			"issue_count": 1,
			"issues": []interface{}{
				map[string]interface{}{
					"code": "layer_order_mismatch", "layer": "mouth_cavity",
					"message": msg,
				},
			},
		})

	ready, blocker := modelDirReadiness(dir)
	if ready {
		t.Fatal("层序错位却被放行了 —— 内核接受不代表层级关系对")
	}
	if blocker != msg {
		t.Errorf("blocker 必须逐字取自第一条错位说明\n got: %q\nwant: %q", blocker, msg)
	}
}

func TestModelDirReadinessAllowsZeroLayerOrderIssues(t *testing.T) {
	dir := t.TempDir()
	writeBuildMetaFull(t, dir,
		map[string]interface{}{"moc3_written": true, "runtime_ready": true},
		map[string]interface{}{"issue_count": 0, "issues": []interface{}{}})

	if ready, blocker := modelDirReadiness(dir); !ready {
		t.Fatalf("层序无问题却未放行，blocker = %q", blocker)
	}
}

func TestModelDirReadinessLayerOrderBlockerHasFallbackText(t *testing.T) {
	dir := t.TempDir()
	// issues 结构异常（不是数组）时也要给出可读原因，不能返回空串。
	writeBuildMetaFull(t, dir,
		map[string]interface{}{"moc3_written": true, "runtime_ready": true},
		map[string]interface{}{"issue_count": 2, "issues": "oops"})

	ready, blocker := modelDirReadiness(dir)
	if ready {
		t.Fatal("层序错位却被放行了")
	}
	if blocker == "" {
		t.Error("未就绪却没有 blocker，调用方无从判断")
	}
}

// ---------- HTTP 层：闸门确实接在 Download 分支上 ----------

func postExportDownload(t *testing.T, h *Handler, modelDir string) *httptest.ResponseRecorder {
	t.Helper()
	r := gin.New()
	r.POST("/api/export/live2d", h.ExportLive2D)

	payload, err := json.Marshal(models.ExportModelRequest{
		CharacterID: "gate",
		ModelDir:    modelDir,
		Download:    true,
	})
	if err != nil {
		t.Fatalf("序列化请求失败: %v", err)
	}
	req := httptest.NewRequest(http.MethodPost, "/api/export/live2d", bytes.NewReader(payload))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	r.ServeHTTP(w, req)
	return w
}

func TestExportLive2DDownloadRefusesRejectedModel(t *testing.T) {
	h := newTestHandler(t)
	modelDir := filepath.Join(h.cfg.Output.BaseDir, "rejected")
	if err := os.MkdirAll(modelDir, 0o755); err != nil {
		t.Fatalf("建目录失败: %v", err)
	}
	// 磁盘上确实有 moc3 —— 旧实现只查「目录是否为空」，于是会照常打包。
	if err := os.WriteFile(filepath.Join(modelDir, "gate.moc3"), []byte("MOC3"), 0o644); err != nil {
		t.Fatalf("写 moc3 失败: %v", err)
	}
	reason := "官方内核拒绝: inconsistent"
	writeBuildMeta(t, modelDir, map[string]interface{}{
		"moc3_written":  true,
		"runtime_ready": false,
		"moc3_blocker":  reason,
	})

	w := postExportDownload(t, h, modelDir)

	if w.Code != http.StatusConflict {
		t.Fatalf("未就绪的模型必须被拒绝，实际 %d (body=%s)", w.Code, w.Body.String())
	}
	if ct := w.Header().Get("Content-Type"); strings.Contains(ct, "zip") {
		t.Errorf("拒绝时不得返回 zip，Content-Type = %q", ct)
	}
	var resp models.Response
	if err := json.Unmarshal(w.Body.Bytes(), &resp); err != nil {
		t.Fatalf("响应不是 JSON: %v (body=%s)", err, w.Body.String())
	}
	if resp.Success {
		t.Error("拒绝时 success 必须为 false")
	}
	if !strings.Contains(resp.Error, reason) {
		t.Errorf("错误信息必须逐字带上实测 blocker\n got: %q\nwant contains: %q", resp.Error, reason)
	}
}

func TestExportLive2DDownloadServesVerifiedModel(t *testing.T) {
	h := newTestHandler(t)
	modelDir := filepath.Join(h.cfg.Output.BaseDir, "verified")
	if err := os.MkdirAll(modelDir, 0o755); err != nil {
		t.Fatalf("建目录失败: %v", err)
	}
	if err := os.WriteFile(filepath.Join(modelDir, "gate.moc3"), []byte("MOC3"), 0o644); err != nil {
		t.Fatalf("写 moc3 失败: %v", err)
	}
	writeBuildMeta(t, modelDir, map[string]interface{}{
		"moc3_written":  true,
		"runtime_ready": true,
	})

	w := postExportDownload(t, h, modelDir)

	if w.Code != http.StatusOK {
		t.Fatalf("已验收的模型必须放行，实际 %d (body=%s)", w.Code, w.Body.String())
	}
	if ct := w.Header().Get("Content-Type"); !strings.Contains(ct, "zip") {
		t.Errorf("应返回 zip，Content-Type = %q", ct)
	}
}
