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
