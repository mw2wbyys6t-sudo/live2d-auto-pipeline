package services

import (
	"strings"
	"testing"
)

func TestFindTopLevelJSON_Simple(t *testing.T) {
	s := `prefix log
{"a":1,"b":2}
trailing`
	start, end := findTopLevelJSON(s)
	if start == -1 {
		t.Fatal("未找到 JSON")
	}
	got := s[start:end]
	if got != `{"a":1,"b":2}` {
		t.Errorf("提取错误: %s", got)
	}
}

func TestFindTopLevelJSON_NestedObjects(t *testing.T) {
	// 日志中混入了内嵌 JSON 对象（QA issues 数组）
	s := `log line 1
{"issues":[{"id":"QA-1","code":"E001"}],"success":true}
trailing`
	start, end := findTopLevelJSON(s)
	if start == -1 {
		t.Fatal("未找到 JSON")
	}
	got := s[start:end]
	if !strings.HasPrefix(got, `{"issues":`) {
		t.Errorf("未从最外层开始: %s", got)
	}
	if !strings.HasSuffix(got, `"success":true}`) {
		t.Errorf("未匹配到最外层结束: %s", got)
	}
}

func TestFindTopLevelJSON_NotFound(t *testing.T) {
	s := "no json here, just text"
	start, _ := findTopLevelJSON(s)
	if start != -1 {
		t.Errorf("期望 -1，实际 %d", start)
	}
}

func TestFindTopLevelJSON_WithEscapes(t *testing.T) {
	// 字符串内有转义引号
	s := `{"name":"test\"quote","value":"x{"}`
	start, end := findTopLevelJSON(s)
	if start == -1 {
		t.Fatal("未找到 JSON")
	}
	got := s[start:end]
	if got != s {
		t.Errorf("提取错误:\n got: %s\nwant: %s", got, s)
	}
}

func TestFindTopLevelJSON_Empty(t *testing.T) {
	start, _ := findTopLevelJSON("")
	if start != -1 {
		t.Errorf("期望 -1，实际 %d", start)
	}
}

func TestFindWorkflowJSON_SkipsBraceNoise(t *testing.T) {
	// 复现真实故障：stderr 里的回溯源码行含 {self.model_type}，
	// 真正的结果 JSON 打印在最后。必须跳过噪声取到结果对象。
	s := `Traceback (most recent call last):
  File "semantic.py", line 100, in __init__
    log.info(f"SemanticSegmenter initialized (backend={self.model_type}, device={self.device})")
{
  "success": true,
  "layers_dir": "output/layers_1",
  "output_dir": "output"
}`
	start, end := findWorkflowJSON(s)
	if start == -1 {
		t.Fatal("未找到结果 JSON")
	}
	got := s[start:end]
	if !strings.Contains(got, `"success": true`) || !strings.HasPrefix(got, "{") {
		t.Errorf("提取错误:\n%s", got)
	}
	if strings.Contains(got, "self.model_type") {
		t.Errorf("误取到噪声片段: %s", got)
	}
}

