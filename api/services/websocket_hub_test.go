package services

import (
	"encoding/json"
	"testing"
	"time"

	"live2d-api/models"
)

// Hub 的实时进度出口：慢客户端不能把构建线程卡住。

func TestTryBroadcastIsNonBlocking(t *testing.T) {
	hub := NewWSHub() // 故意不 Run()：广播缓冲会填满，正是慢客户端的情形
	for i := 0; i < cap(hub.broadcast); i++ {
		if !hub.TryBroadcast([]byte("frame")) {
			t.Fatalf("缓冲区未满就该收下，第 %d 帧失败", i)
		}
	}

	done := make(chan bool, 1)
	go func() { done <- hub.TryBroadcast([]byte("one more")) }()
	select {
	case ok := <-done:
		if ok {
			t.Error("缓冲区满了还报告成功")
		}
	case <-time.After(2 * time.Second):
		t.Fatal("TryBroadcast 被慢/无客户端卡住了 —— 构建线程会跟着一起卡")
	}
}

func TestPublishDeliversFrameToRegisteredClient(t *testing.T) {
	hub := NewWSHub()
	go hub.Run()

	client := &WSConn{id: "stageclient0001", send: make(chan []byte, 8)}
	hub.register <- client
	waitForClients(t, hub, 1)

	msg := models.WSMessage{
		Type: "progress", TaskID: "export_1", Stage: "Compiling moc3",
		Progress: 80, Message: "[9/10] Compiling moc3 (started)",
		Data:     StageEvent{JobID: "export_1", Step: 9, Total: 10, Status: StageStarted},
		Time:     time.Now().UnixMilli(),
	}
	if !hub.Publish(msg) {
		t.Fatal("已连接客户端时 Publish 不应丢帧")
	}

	select {
	case frame := <-client.send:
		var got models.WSMessage
		if err := json.Unmarshal(frame, &got); err != nil {
			t.Fatalf("下发不是合法 WSMessage: %v / %s", err, frame)
		}
		if got.Type != "progress" || got.TaskID != "export_1" || got.Stage != "Compiling moc3" {
			t.Errorf("下发的消息形状不对: %s", frame)
		}
		if got.Progress != 80 || got.Time == 0 {
			t.Errorf("progress/time 字段不对: %s", frame)
		}
		// 结构化细节必须原样可达（前端靠 data.status 区分 advanced / not_reported）。
		data, _ := got.Data.(map[string]interface{})
		if data == nil || data["status"] != StageStarted {
			t.Errorf("data.status 丢失: %s", frame)
		}
	case <-time.After(3 * time.Second):
		t.Fatal("注册后的客户端没有收到帧")
	}
}

// TestPublishWithoutHubLoopDoesNotBlock 覆盖单测里常见的「hub 建了但没 Run」。
func TestPublishWithoutHubLoopDoesNotBlock(t *testing.T) {
	hub := NewWSHub()
	done := make(chan struct{})
	go func() {
		for i := 0; i < 400; i++ {
			hub.Publish(models.WSMessage{Type: "progress", TaskID: "x"})
		}
		close(done)
	}()
	select {
	case <-done:
	case <-time.After(3 * time.Second):
		t.Fatal("无人消费的 hub 上 Publish 阻塞了")
	}
}

func waitForClients(t *testing.T, hub *WSHub, want int) {
	t.Helper()
	deadline := time.After(3 * time.Second)
	for {
		if hub.ClientCount() == want {
			return
		}
		select {
		case <-deadline:
			t.Fatalf("客户端数量停在 %d，期望 %d", hub.ClientCount(), want)
		default:
			time.Sleep(5 * time.Millisecond)
		}
	}
}
