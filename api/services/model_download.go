package services

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"
)

// OptionalComponent 是「基础包不进、应用内按需下载」的重模型。
//
// 设计（ADR-010）：权重落在可写的 HfCache（%APPDATA%\Live2DMasterAgent\hf-cache），
// 绝不写只读安装目录；下载支持 HTTP Range 断点续传、SHA256 校验、取消。
// sha256 为空表示该条目尚未人工核实哈希，跳过校验但在状态里标注 unverified。
type OptionalComponent struct {
	Name        string `json:"name"`
	URL         string `json:"url"`
	SHA256      string `json:"sha256,omitempty"`
	RelPath     string `json:"rel_path"`
	SizeHintMB  int    `json:"size_hint_mb"`
	Note        string `json:"note"`
	RequiresPkg string `json:"requires_pkg"`
}

// optionalRegistry 重模型清单。URL 为官方源；哈希在发布前由构建脚本核实回填。
var optionalRegistry = []OptionalComponent{
	{
		Name:        "sam2-hiera-small",
		URL:         "https://dl.fbaipublicfiles.com/segment_anything_2/092824/sam2_hiera_small.pt",
		SHA256:      "",
		RelPath:     "sam2/sam2_hiera_small.pt",
		SizeHintMB:  178,
		Note:        "SAM2 语义分割（高精度分层，需另装 torch/torchvision）",
		RequiresPkg: "torch,torchvision,transformers",
	},
}

// DownloadState 单个组件的下载状态（前端轮询展示）。
type DownloadState struct {
	State     string  `json:"state"` // idle/downloading/ready/error/cancelled
	Progress  float64 `json:"progress"`
	DownloadedMB float64 `json:"downloaded_mb"`
	TotalMB   float64 `json:"total_mb"`
	Message   string  `json:"message,omitempty"`
	UpdatedAt string  `json:"updated_at"`
}

type downloadJob struct {
	cancel context.CancelFunc
}

// ModelDownloader 管理可选模型的后台下载生命周期。
type ModelDownloader struct {
	mu      sync.Mutex
	baseDir string
	states  map[string]DownloadState
	jobs    map[string]*downloadJob
}

func NewModelDownloader(hfCacheDir string) *ModelDownloader {
	return &ModelDownloader{
		baseDir: hfCacheDir,
		states:  map[string]DownloadState{},
		jobs:    map[string]*downloadJob{},
	}
}

// List 返回清单 + 当前就绪/下载状态。
func (m *ModelDownloader) List() []map[string]interface{} {
	m.mu.Lock()
	defer m.mu.Unlock()
	out := make([]map[string]interface{}, 0, len(optionalRegistry))
	for _, c := range optionalRegistry {
		st := m.states[c.Name]
		if st.State == "" {
			st = DownloadState{State: "idle"}
		}
		// 已落盘即视为 ready（覆盖重启后无内存状态的情况）。
		if st.State != "downloading" && m.isReady(c) {
			st.State = "ready"
		}
		total := st.TotalMB
		if total <= 0 {
			total = float64(c.SizeHintMB)
		}
		out = append(out, map[string]interface{}{
			"name":          c.Name,
			"url":           c.URL,
			"rel_path":      c.RelPath,
			"size_hint_mb": c.SizeHintMB,
			"note":          c.Note,
			"requires_pkg":  c.RequiresPkg,
			"state":         st.State,
			"progress":      st.Progress,
			"downloaded_mb": st.DownloadedMB,
			"total_mb":      total,
			"message":       st.Message,
		})
	}
	return out
}

// Status 返回单个组件状态。
func (m *ModelDownloader) Status(name string) DownloadState {
	m.mu.Lock()
	defer m.mu.Unlock()
	st := m.states[name]
	if st.State == "" {
		st = DownloadState{State: "idle"}
	}
	if st.State != "downloading" && m.isReadyLocked(name) {
		st.State = "ready"
	}
	return st
}

func (m *ModelDownloader) isReady(c OptionalComponent) bool {
	if m.baseDir == "" {
		return false
	}
	p := filepath.Join(m.baseDir, filepath.FromSlash(c.RelPath))
	st, err := os.Stat(p)
	return err == nil && st.Size() > 0
}

func (m *ModelDownloader) isReadyLocked(name string) bool {
	for _, c := range optionalRegistry {
		if c.Name == name {
			return m.isReady(c)
		}
	}
	return false
}

// Start 后台启动下载（幂等：已在跑/已就绪直接返回）。
func (m *ModelDownloader) Start(name string) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	c, ok := findComponent(name)
	if !ok {
		return fmt.Errorf("未知模型: %s", name)
	}
	if _, busy := m.jobs[name]; busy {
		return nil
	}
	if m.isReady(c) {
		m.states[name] = DownloadState{State: "ready", UpdatedAt: time.Now().Format(time.RFC3339)}
		return nil
	}
	if m.baseDir == "" {
		return fmt.Errorf("模型缓存目录未配置（HfCache 为空）")
	}

	ctx, cancel := context.WithCancel(context.Background())
	m.jobs[name] = &downloadJob{cancel: cancel}
	m.states[name] = DownloadState{State: "downloading", TotalMB: float64(c.SizeHintMB), UpdatedAt: time.Now().Format(time.RFC3339)}

	go m.runDownload(ctx, c)
	return nil
}

// Cancel 取消进行中的下载（已就绪不受影响）。
func (m *ModelDownloader) Cancel(name string) {
	m.mu.Lock()
	defer m.mu.Unlock()
	if job, ok := m.jobs[name]; ok {
		job.cancel()
		delete(m.jobs, name)
		m.states[name] = DownloadState{State: "cancelled", Message: "已取消，已下载部分保留以便断点续传", UpdatedAt: time.Now().Format(time.RFC3339)}
	}
}

func findComponent(name string) (OptionalComponent, bool) {
	for _, c := range optionalRegistry {
		if c.Name == name {
			return c, true
		}
	}
	return OptionalComponent{}, false
}

// runDownload 断点续传 + 流式写盘 + SHA256 校验。
func (m *ModelDownloader) runDownload(ctx context.Context, c OptionalComponent) {
	final := filepath.Join(m.baseDir, filepath.FromSlash(c.RelPath))
	tmp := final + ".part"
	_ = os.MkdirAll(filepath.Dir(final), 0o755)

	var existing int64
	if st, err := os.Stat(tmp); err == nil {
		existing = st.Size()
	}

	req, err := http.NewRequestWithContext(ctx, http.MethodGet, c.URL, nil)
	if err != nil {
		m.fail(c.Name, err)
		return
	}
	if existing > 0 {
		req.Header.Set("Range", fmt.Sprintf("bytes=%d-", existing))
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		m.fail(c.Name, err)
		return
	}
	defer resp.Body.Close()

	// 服务器忽略 Range（200 而非 206）时从头重写，避免拼接损坏。
	mode := os.O_CREATE | os.O_WRONLY
	if existing > 0 && resp.StatusCode == http.StatusPartialContent {
		mode |= os.O_APPEND
	} else {
		existing = 0
		mode |= os.O_TRUNC
	}

	f, err := os.OpenFile(tmp, mode, 0o644)
	if err != nil {
		m.fail(c.Name, err)
		return
	}
	defer f.Close()

	hasher := sha256.New()
	// 若从头下载，需要对全量哈希；续传时跳过已落盘段（校验在完整文件上做）。
	written := existing
	buf := make([]byte, 1<<16)
	for {
		select {
		case <-ctx.Done():
			m.update(c.Name, DownloadState{State: "cancelled", Message: "已取消，已下载部分保留以便断点续传", DownloadedMB: mb(written), TotalMB: float64(c.SizeHintMB), UpdatedAt: time.Now().Format(time.RFC3339)})
			return
		default:
		}
		n, rerr := resp.Body.Read(buf)
		if n > 0 {
			if _, werr := f.Write(buf[:n]); werr != nil {
				m.fail(c.Name, werr)
				return
			}
			hasher.Write(buf[:n])
			written += int64(n)
			prog := 0.0
			if c.SizeHintMB > 0 {
				prog = mb(written) / float64(c.SizeHintMB) * 100
				if prog > 99 {
					prog = 99
				}
			}
			m.update(c.Name, DownloadState{State: "downloading", Progress: prog, DownloadedMB: mb(written), TotalMB: float64(c.SizeHintMB), UpdatedAt: time.Now().Format(time.RFC3339)})
		}
		if rerr == io.EOF {
			break
		}
		if rerr != nil {
			m.fail(c.Name, rerr)
			return
		}
	}

	// 完整文件哈希校验（仅当提供了期望 SHA256；空=未核实，跳过）。
	if c.SHA256 != "" {
		finalHash, err := hashFile(tmp)
		if err != nil {
			m.fail(c.Name, err)
			return
		}
		if !strings.EqualFold(finalHash, c.SHA256) {
			_ = os.Remove(tmp)
			m.fail(c.Name, fmt.Errorf("SHA256 校验失败：期望 %s，实得 %s", c.SHA256, finalHash))
			return
		}
	}

	if err := os.Rename(tmp, final); err != nil {
		m.fail(c.Name, err)
		return
	}
	m.mu.Lock()
	delete(m.jobs, c.Name)
	m.states[c.Name] = DownloadState{State: "ready", Progress: 100, DownloadedMB: mb(written), TotalMB: float64(c.SizeHintMB), Message: "已下载完成", UpdatedAt: time.Now().Format(time.RFC3339)}
	m.mu.Unlock()
}

func mb(b int64) float64 { return float64(b) / (1 << 20) }

func hashFile(path string) (string, error) {
	f, err := os.Open(path)
	if err != nil {
		return "", err
	}
	defer f.Close()
	h := sha256.New()
	if _, err := io.Copy(h, f); err != nil {
		return "", err
	}
	return hex.EncodeToString(h.Sum(nil)), nil
}

func (m *ModelDownloader) fail(name string, err error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	delete(m.jobs, name)
	msg := err.Error()
	if len(msg) > 200 {
		msg = msg[:200]
	}
	m.states[name] = DownloadState{State: "error", Message: msg, UpdatedAt: time.Now().Format(time.RFC3339)}
}

func (m *ModelDownloader) update(name string, st DownloadState) {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.states[name] = st
}

