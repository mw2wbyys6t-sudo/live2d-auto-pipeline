package services

import (
	"strings"
	"sync"
	"testing"
)

// 桌宠跟随依赖「readLoop 解析 tracking_frame → 分发给 frameSink」这一条链路。
// 这里用合成 JSON Lines 直接驱动 readLoop，验证：
//  1. 带 blendshapes 的帧（真实检测到人脸）会分发参数；
//  2. 不带 blendshapes 的帧（画面中无人脸）不分发，让桌宠回落到待机动画；
//  3. 取消订阅（SetFrameSink(nil)）后不再分发。
func TestReadLoopForwardsFramesOnlyWhenFaceDetected(t *testing.T) {
	const stream = `{"type":"ready","version":"1.0.0","has_camera":true,"has_model":true}
{"type":"frame","blendshapes":{"eyeBlinkLeft":0.1},"live2d_params":{"ParamAngleX":12.5,"ParamMouthOpenY":0.3},"fps":30}
{"type":"frame","live2d_params":{"ParamAngleX":99.0},"fps":30}
{"type":"stopped"}
`

	var mu sync.Mutex
	var got []map[string]float64

	nb := NewNativeBridge(nil, nil)
	nb.SetFrameSink(func(params map[string]float64) {
		mu.Lock()
		got = append(got, params)
		mu.Unlock()
	})

	nb.readLoop(strings.NewReader(stream))

	mu.Lock()
	defer mu.Unlock()
	if len(got) != 1 {
		t.Fatalf("应只分发 1 帧（仅检测到人脸的那帧），实为 %d 帧: %v", len(got), got)
	}
	if got[0]["ParamAngleX"] != 12.5 || got[0]["ParamMouthOpenY"] != 0.3 {
		t.Fatalf("分发参数不匹配: %v", got[0])
	}
	if nb.IsRunning() {
		t.Fatalf("stopped 消息后 running 应为 false")
	}
}

// 取消订阅后不应再收到任何帧。
func TestFrameSinkUnsubscribe(t *testing.T) {
	calls := 0
	nb := NewNativeBridge(nil, nil)
	nb.SetFrameSink(func(map[string]float64) { calls++ })
	nb.SetFrameSink(nil)

	nb.readLoop(strings.NewReader(
		`{"type":"frame","blendshapes":{"jawOpen":0.5},"live2d_params":{"ParamAngleX":1.0}}` + "\n",
	))

	if calls != 0 {
		t.Fatalf("取消订阅后仍收到 %d 次回调", calls)
	}
}

// 非法帧不应打断读取循环，也不应触发回调。
func TestReadLoopSkipsMalformedLines(t *testing.T) {
	calls := 0
	nb := NewNativeBridge(nil, nil)
	nb.SetFrameSink(func(map[string]float64) { calls++ })

	nb.readLoop(strings.NewReader("not-json\n" + `{"type":"frame"}` + "\n" + "\n"))

	if calls != 0 {
		t.Fatalf("非法帧触发了 %d 次回调", calls)
	}
}
