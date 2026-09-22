package handlers

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/gin-gonic/gin"
	"live2d-api/config"
	"live2d-api/services"
)

// Opt-in, real HTTP -> verification subprocess -> native window -> status test.
func TestNativeDesktopHTTP(t *testing.T) {
	python, manifest := os.Getenv("LIVE2D_TEST_PYTHON"), os.Getenv("LIVE2D_TEST_MANIFEST")
	root := os.Getenv("LIVE2D_TEST_ROOT")
	if python == "" || manifest == "" || root == "" {
		t.Skip("real desktop environment not configured")
	}
	cfg := &config.Config{
		Python: config.PythonConfig{PythonPath: python, ScriptsDir: root, TimeoutSec: 90},
		Output: config.OutputConfig{BaseDir: filepath.Dir(filepath.Dir(manifest))},
	}
	h := &Handler{pythonBridge: services.NewPythonBridge(cfg)}
	gin.SetMode(gin.TestMode)
	router := gin.New()
	router.POST("/api/deploy/desktop", h.DeployDesktop)
	router.GET("/api/deploy/desktop/status", h.DesktopStatus)
	server := httptest.NewServer(router)
	defer server.Close()
	body, _ := json.Marshal(map[string]string{"model_dir": filepath.Dir(manifest)})
	response, err := http.Post(server.URL+"/api/deploy/desktop", "application/json", bytes.NewReader(body))
	if err != nil {
		t.Fatal(err)
	}
	defer response.Body.Close()
	var result struct {
		Success bool                   `json:"success"`
		Message string                 `json:"message"`
		Data    map[string]interface{} `json:"data"`
	}
	if err := json.NewDecoder(response.Body).Decode(&result); err != nil {
		t.Fatal(err)
	}
	if response.StatusCode != 200 || !result.Success || result.Data["deployed"] != true {
		t.Fatalf("deployment failed: %+v", result)
	}
	pid := int(result.Data["pid"].(float64))
	process, err := os.FindProcess(pid)
	if err != nil {
		t.Fatal(err)
	}
	defer process.Kill()
	if result.Data["runtime_verified"] != true {
		t.Fatal("unverified deployment")
	}
	// Duplicate requests must not launch a second pet.
	duplicate, err := http.Post(server.URL+"/api/deploy/desktop", "application/json", bytes.NewReader(body))
	if err != nil {
		t.Fatal(err)
	}
	duplicate.Body.Close()
	if duplicate.StatusCode != 422 {
		t.Fatalf("duplicate status %d", duplicate.StatusCode)
	}
	if err := process.Kill(); err != nil {
		t.Fatal(err)
	}
	deadline := time.Now().Add(10 * time.Second)
	for time.Now().Before(deadline) {
		r, err := http.Get(server.URL + "/api/deploy/desktop/status")
		if err != nil {
			t.Fatal(err)
		}
		var status struct {
			Data map[string]interface{} `json:"data"`
		}
		decodeErr := json.NewDecoder(r.Body).Decode(&status)
		r.Body.Close()
		if decodeErr != nil {
			t.Fatal(decodeErr)
		}
		if status.Data["deployed"] == false && status.Data["deployment_status"] == "stopped" {
			return
		}
		time.Sleep(100 * time.Millisecond)
	}
	t.Fatal("terminated window retained deployed state")
}
