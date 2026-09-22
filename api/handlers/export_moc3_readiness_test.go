package handlers

import (
	"encoding/json"
	"strings"
	"testing"
)

// 导出结果的 moc3 就绪状态必须原样透出：缺少信息一律按未就绪处理。

func TestUnwrapPythonResult(t *testing.T) {
	nested := map[string]interface{}{
		"result": map[string]interface{}{"output_dir": "/tmp/x"},
	}
	if got := unwrapPythonResult(nested)["output_dir"]; got != "/tmp/x" {
		t.Errorf("嵌套结果未摊平，output_dir = %v", got)
	}
	flat := map[string]interface{}{"output_dir": "/tmp/y"}
	if got := unwrapPythonResult(flat)["output_dir"]; got != "/tmp/y" {
		t.Errorf("本已摊平的结果被破坏，output_dir = %v", got)
	}
	// result 不是对象时不能吞掉原始字段
	weird := map[string]interface{}{"result": "text", "output_dir": "/tmp/z"}
	if got := unwrapPythonResult(weird)["output_dir"]; got != "/tmp/z" {
		t.Errorf("非对象 result 应保留原字典，output_dir = %v", got)
	}
}

func TestExportResponseDataReadyModel(t *testing.T) {
	data := exportResponseData(map[string]interface{}{
		"result": map[string]interface{}{
			"success":     true,
			"model3_json": "/tmp/char/char.model3.json",
			"output_dir":  "/tmp/char",
			"moc3": map[string]interface{}{
				"moc3_written":             true,
				"moc3_bytes":               5888,
				"runtime_ready":            true,
				"official_core_consistent": true,
				"moc3_dropped":             []interface{}{},
			},
		},
	})
	if data["runtime_ready"] != true {
		t.Fatalf("官方内核已验收却未报告就绪: %v", data["runtime_ready"])
	}
	if data["blocker"] != "" {
		t.Errorf("就绪时不应带 blocker，实得 %v", data["blocker"])
	}
	// 路径字段必须原样保留，前端与下载模式都靠它们
	for _, key := range []string{"model3_json", "output_dir"} {
		if data[key] == nil {
			t.Errorf("摊平后丢失字段 %s", key)
		}
	}
	if _, ok := data["moc3"].(map[string]interface{}); !ok {
		t.Errorf("moc3 明细应保留在 data 里，实得 %T", data["moc3"])
	}
}

func TestExportResponseDataNotReadyCases(t *testing.T) {
	tests := []struct {
		name          string
		raw           map[string]interface{}
		wantBlockerIn string
	}{
		{
			name: "完全没有 moc3 字段",
			raw: map[string]interface{}{"result": map[string]interface{}{
				"success": true,
			}},
			wantBlockerIn: "未经官方 Cubism Core 验收",
		},
		{
			name: "moc3 写了但内核拒绝",
			raw: map[string]interface{}{"result": map[string]interface{}{
				"success": true,
				"moc3": map[string]interface{}{
					"moc3_written":  true,
					"runtime_ready": false,
					"moc3_blocker":  "官方内核拒绝: inconsistent",
				},
			}},
			wantBlockerIn: "官方内核拒绝",
		},
		{
			name: "变形器被丢弃（静默降级必须显式报出）",
			raw: map[string]interface{}{"result": map[string]interface{}{
				"success": true,
				"moc3": map[string]interface{}{
					"moc3_written":  true,
					"runtime_ready": false,
					"moc3_blocker":  "变形器未编译，产物仅含静态几何；不得用于部署验收",
					"moc3_dropped":  []interface{}{"deformers"},
				},
			}},
			wantBlockerIn: "变形器未编译",
		},
		{
			name: "runtime_ready 不是布尔真值",
			raw: map[string]interface{}{"result": map[string]interface{}{
				"success": true,
				"moc3":    map[string]interface{}{"runtime_ready": "yes"},
			}},
			wantBlockerIn: "缺少 moc3 就绪信息",
		},
		{
			name: "Python 报告导出失败",
			raw: map[string]interface{}{"result": map[string]interface{}{
				"success": false,
				"message": "图层目录内没有可用 PNG: /tmp/none",
			}},
			wantBlockerIn: "没有可用 PNG",
		},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			data := exportResponseData(tc.raw)
			if ready, _ := data["runtime_ready"].(bool); ready {
				t.Fatalf("该情形绝不能报告就绪: %#v", data["moc3"])
			}
			blocker, _ := data["blocker"].(string)
			if blocker == "" {
				t.Fatal("未就绪却没有 blocker，调用方无从判断原因")
			}
			if !strings.Contains(blocker, tc.wantBlockerIn) {
				t.Errorf("blocker %q 未包含预期片段 %q", blocker, tc.wantBlockerIn)
			}
		})
	}
}

func TestExportResponseDataWireFieldNames(t *testing.T) {
	// 前端 api-client.ts 直接读平铺的 runtime_ready / blocker，键名是契约
	raw := map[string]interface{}{"result": map[string]interface{}{
		"success": true,
		"moc3":    map[string]interface{}{"runtime_ready": true},
	}}
	encoded, err := json.Marshal(exportResponseData(raw))
	if err != nil {
		t.Fatalf("data 无法序列化: %v", err)
	}
	var wire map[string]interface{}
	if err := json.Unmarshal(encoded, &wire); err != nil {
		t.Fatalf("data 反序列化失败: %v", err)
	}
	for _, key := range []string{"runtime_ready", "blocker", "moc3"} {
		if _, ok := wire[key]; !ok {
			t.Errorf("响应缺少前端契约字段 %s：%s", key, encoded)
		}
	}
	if wire["runtime_ready"] != true {
		t.Errorf("wire runtime_ready = %v", wire["runtime_ready"])
	}
}

func TestExportResponseDataAcceptsFlatInput(t *testing.T) {
	data := exportResponseData(map[string]interface{}{
		"success": true,
		"moc3":    map[string]interface{}{"runtime_ready": true},
	})
	if data["runtime_ready"] != true {
		t.Errorf("未经包装的输入也要能识别，实得 %v", data["runtime_ready"])
	}
}
