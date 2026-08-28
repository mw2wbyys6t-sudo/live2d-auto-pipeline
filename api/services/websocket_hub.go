package services

import (
	"crypto/sha1"
	"encoding/base64"
	"encoding/binary"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net"
	"net/http"
	"sync"
	"time"

	"github.com/gin-gonic/gin"

	"live2d-api/models"
)

// ======================================================================
// 轻量级 WebSocket 实现（无外部依赖）
// 基于 RFC 6455 的最小帧解析，支持文本帧和二进制帧
// ======================================================================

const (
	wsGUID       = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
	wsTextFrame  = 0x1
	wsBinaryFrame = 0x2
	wsCloseFrame = 0x8
	wsPingFrame  = 0x9
	wsPongFrame  = 0xA
)

// WSConn 封装一个 WebSocket 连接
type WSConn struct {
	conn   net.Conn
	mu     sync.Mutex
	closed bool
	hub    *WSHub
	id     string
	send   chan []byte
}

// WSHub WebSocket 中心：管理所有客户端连接
type WSHub struct {
	mu          sync.RWMutex
	clients     map[string]*WSConn
	register    chan *WSConn
	unregister  chan *WSConn
	broadcast   chan []byte
	maxConns    int
	onMessage   func(clientID string, msg []byte)
}

// NewWSHub 创建 WebSocket Hub（默认最大连接数 100）
func NewWSHub() *WSHub {
	hub := &WSHub{
		clients:    make(map[string]*WSConn),
		register:   make(chan *WSConn),
		unregister: make(chan *WSConn),
		broadcast:  make(chan []byte, 256),
		maxConns:   100,
	}
	return hub
}

// SetMaxConns 设置最大连接数
func (h *WSHub) SetMaxConns(n int) {
	h.mu.Lock()
	h.maxConns = n
	h.mu.Unlock()
}

// Run 启动 Hub 主循环（阻塞，应在 goroutine 中调用）
func (h *WSHub) Run() {
	h.run()
}

// SetMessageHandler 设置消息处理回调
func (h *WSHub) SetMessageHandler(fn func(clientID string, msg []byte)) {
	h.onMessage = fn
}

// run Hub 主循环
func (h *WSHub) run() {
	for {
		select {
		case c := <-h.register:
			h.mu.Lock()
			if len(h.clients) >= h.maxConns {
				h.mu.Unlock()
				c.close()
				continue
			}
			h.clients[c.id] = c
			h.mu.Unlock()
			log.Printf("[WS] 客户端连接: %s (在线: %d)", c.id[:8], h.ClientCount())

		case c := <-h.unregister:
			h.mu.Lock()
			if _, ok := h.clients[c.id]; ok {
				delete(h.clients, c.id)
				c.close()
			}
			h.mu.Unlock()
			log.Printf("[WS] 客户端断开: %s (在线: %d)", c.id[:8], h.ClientCount())

		case msg := <-h.broadcast:
			h.mu.RLock()
			for _, c := range h.clients {
				select {
				case c.send <- msg:
				default:
					// 发送缓冲区满，断开该客户端
					go func(c *WSConn) { h.unregister <- c }(c)
				}
			}
			h.mu.RUnlock()
		}
	}
}

// ClientCount 返回当前连接数
func (h *WSHub) ClientCount() int {
	h.mu.RLock()
	defer h.mu.RUnlock()
	return len(h.clients)
}

// Broadcast 向所有客户端广播消息
func (h *WSHub) Broadcast(msgType string, data interface{}) {
	wsMsg := models.WSMessage{
		Type: msgType,
		Time: time.Now().UnixMilli(),
		Data: data,
	}
	b, err := json.Marshal(wsMsg)
	if err != nil {
		return
	}
	h.broadcast <- b
}

// BroadcastProgress 广播生成进度
func (h *WSHub) BroadcastProgress(taskID, stage string, progress int, message string) {
	wsMsg := models.WSMessage{
		Type:     "progress",
		TaskID:   taskID,
		Stage:    stage,
		Progress: progress,
		Message:  message,
		Time:     time.Now().UnixMilli(),
	}
	b, _ := json.Marshal(wsMsg)
	h.broadcast <- b
}

// BroadcastTracking 广播人脸追踪参数
func (h *WSHub) BroadcastTracking(trackingData map[string]float64) {
	wsMsg := models.WSMessage{
		Type: "tracking",
		Data: trackingData,
		Time: time.Now().UnixMilli(),
	}
	b, _ := json.Marshal(wsMsg)
	h.broadcast <- b
}

// SendToClient 向指定客户端发送消息
func (h *WSHub) SendToClient(clientID string, msg models.WSMessage) {
	h.mu.RLock()
	c, ok := h.clients[clientID]
	h.mu.RUnlock()
	if !ok {
		return
	}
	b, err := json.Marshal(msg)
	if err != nil {
		return
	}
	select {
	case c.send <- b:
	default:
	}
}

// HandleConnection 处理 gin 上下文中的 WebSocket 连接
func (h *WSHub) HandleConnection(c *gin.Context) {
	h.HandleUpgrade(c.Writer, c.Request)
}

// HandleUpgrade 处理 WebSocket 升级请求
func (h *WSHub) HandleUpgrade(w http.ResponseWriter, r *http.Request) {
	// 验证 WebSocket 握手
	if r.Header.Get("Upgrade") != "websocket" {
		http.Error(w, "需要 WebSocket 升级", http.StatusBadRequest)
		return
	}

	// 计算 Accept key
	key := r.Header.Get("Sec-WebSocket-Key")
	hash := sha1.New()
	hash.Write([]byte(key + wsGUID))
	accept := base64.StdEncoding.EncodeToString(hash.Sum(nil))

	// Hijack 连接
	hj, ok := w.(http.Hijacker)
	if !ok {
		http.Error(w, "服务器不支持 Hijack", http.StatusInternalServerError)
		return
	}
	conn, bufrw, err := hj.Hijack()
	if err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}

	// 发送握手响应
	resp := "HTTP/1.1 101 Switching Protocols\r\n" +
		"Upgrade: websocket\r\n" +
		"Connection: Upgrade\r\n" +
		fmt.Sprintf("Sec-WebSocket-Accept: %s\r\n\r\n", accept)
	bufrw.WriteString(resp)
	bufrw.Flush()

	// 生成客户端 ID
	clientID := fmt.Sprintf("ws_%d_%s", time.Now().UnixNano(), conn.RemoteAddr().Network())
	hashID := sha1.Sum([]byte(clientID + time.Now().String()))
	cid := fmt.Sprintf("%x", hashID)

	wsConn := &WSConn{
		conn: conn,
		hub:  h,
		id:   cid,
		send: make(chan []byte, 64),
	}

	h.register <- wsConn

	// 启动读写 goroutine
	go wsConn.writePump()
	go wsConn.readPump()
}

// readPump 读取 WebSocket 帧
func (c *WSConn) readPump() {
	defer func() {
		c.hub.unregister <- c
	}()

	for {
		c.conn.SetReadDeadline(time.Now().Add(60 * time.Second))

		// 读取帧头（至少2字节）
		header := make([]byte, 2)
		if _, err := io.ReadFull(c.conn, header); err != nil {
			return
		}

		opcode := header[0] & 0x0F
		masked := (header[1] & 0x80) != 0
		payloadLen := uint64(header[1] & 0x7F)

		// 扩展长度
		if payloadLen == 126 {
			ext := make([]byte, 2)
			if _, err := io.ReadFull(c.conn, ext); err != nil {
				return
			}
			payloadLen = uint64(binary.BigEndian.Uint16(ext))
		} else if payloadLen == 127 {
			ext := make([]byte, 8)
			if _, err := io.ReadFull(c.conn, ext); err != nil {
				return
			}
			payloadLen = binary.BigEndian.Uint64(ext)
		}

		// 限制最大帧大小 64KB
		if payloadLen > 65536 {
			return
		}

		// 读取掩码键
		var maskKey [4]byte
		if masked {
			if _, err := io.ReadFull(c.conn, maskKey[:]); err != nil {
				return
			}
		}

		// 读取载荷
		payload := make([]byte, payloadLen)
		if _, err := io.ReadFull(c.conn, payload); err != nil {
			return
		}

		// 解码（客户端发送的帧必须被掩码）
		if masked {
			for i := range payload {
				payload[i] ^= maskKey[i%4]
			}
		}

		switch opcode {
		case wsCloseFrame:
			c.sendFrame(wsCloseFrame, nil)
			return
		case wsPingFrame:
			c.sendFrame(wsPongFrame, payload)
		case wsPongFrame:
			// 忽略
		case wsTextFrame, wsBinaryFrame:
			if c.hub.onMessage != nil {
				c.hub.onMessage(c.id, payload)
			}
		}
	}
}

// writePump 写入 WebSocket 帧
func (c *WSConn) writePump() {
	ticker := time.NewTicker(30 * time.Second)
	defer func() {
		ticker.Stop()
		c.hub.unregister <- c
	}()

	for {
		select {
		case msg, ok := <-c.send:
			c.conn.SetWriteDeadline(time.Now().Add(10 * time.Second))
			if !ok {
				c.sendFrame(wsCloseFrame, nil)
				return
			}
			if err := c.sendFrame(wsTextFrame, msg); err != nil {
				return
			}
		case <-ticker.C:
			c.conn.SetWriteDeadline(time.Now().Add(10 * time.Second))
			if err := c.sendFrame(wsPingFrame, nil); err != nil {
				return
			}
		}
	}
}

// sendFrame 发送一个 WebSocket 帧
func (c *WSConn) sendFrame(opcode byte, payload []byte) error {
	c.mu.Lock()
	defer c.mu.Unlock()

	if c.closed {
		return fmt.Errorf("连接已关闭")
	}

	// 构建帧头
	header := []byte{0x80 | (opcode & 0x0F)} // FIN + opcode

	payloadLen := len(payload)
	if payloadLen <= 125 {
		header = append(header, byte(payloadLen))
	} else if payloadLen <= 65535 {
		header = append(header, 126)
		ext := make([]byte, 2)
		binary.BigEndian.PutUint16(ext, uint16(payloadLen))
		header = append(header, ext...)
	} else {
		header = append(header, 127)
		ext := make([]byte, 8)
		binary.BigEndian.PutUint64(ext, uint64(payloadLen))
		header = append(header, ext...)
	}

	// 写入头和载荷（服务器不掩码）
	if _, err := c.conn.Write(header); err != nil {
		return err
	}
	if len(payload) > 0 {
		if _, err := c.conn.Write(payload); err != nil {
			return err
		}
	}
	return nil
}

// close 关闭连接
func (c *WSConn) close() {
	c.mu.Lock()
	defer c.mu.Unlock()
	if !c.closed {
		c.closed = true
		c.conn.Close()
		close(c.send)
	}
}
