package services

import (
	"bufio"
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"

	"live2d-api/config"
	"live2d-api/models"
)

// ChatStreamChunk 流式输出回调块
type ChatStreamChunk = models.ChatStreamChunk

// ChatService LLM 聊天服务 - 管理 SSE 流式响应
type ChatService struct {
	cfg    *config.Config
	client *http.Client
}

// NewChatService 创建聊天服务
func NewChatService(cfg *config.Config) *ChatService {
	return &ChatService{
		cfg: cfg,
		client: &http.Client{
			Timeout: 60 * time.Second,
		},
	}
}

type ollamaChatRequest struct {
	Model    string             `json:"model"`
	Messages []ollamaMessage    `json:"messages"`
	Stream   bool               `json:"stream"`
	Options  map[string]float64 `json:"options,omitempty"`
}

type ollamaMessage struct {
	Role    string `json:"role"`
	Content string `json:"content"`
}

type ollamaChatResponse struct {
	Message ollamaMessage `json:"message"`
	Done    bool          `json:"done"`
	Error   string        `json:"error,omitempty"`
}

type openAIChatRequest struct {
	Model       string          `json:"model"`
	Messages    []openAIMessage `json:"messages"`
	Stream      bool            `json:"stream"`
	MaxTokens   int             `json:"max_tokens,omitempty"`
	Temperature float64         `json:"temperature,omitempty"`
}

type openAIMessage struct {
	Role    string `json:"role"`
	Content string `json:"content"`
}

type openAIChatChunk struct {
	Choices []struct {
		Delta struct {
			Content string `json:"content"`
		} `json:"delta"`
		FinishReason string `json:"finish_reason"`
	} `json:"choices"`
	Error *struct {
		Message string `json:"message"`
	} `json:"error,omitempty"`
}

// ChatStream 流式聊天，回调接收 ChatStreamChunk
func (s *ChatService) ChatStream(req models.ChatRequest, onChunk func(ChatStreamChunk)) error {
	messages := s.buildMessages(req)
	switch s.cfg.LLM.Provider {
	case "ollama":
		return s.chatOllama(messages, true, onChunk)
	case "openai", "anthropic", "custom":
		return s.chatOpenAI(messages, true, onChunk)
	default:
		return s.chatOllama(messages, true, onChunk)
	}
}

// Chat 非流式聊天
func (s *ChatService) Chat(req models.ChatRequest) (*models.ChatResponse, error) {
	var fullReply strings.Builder
	var lastEmotion, lastAction string

	err := s.ChatStream(req, func(chunk ChatStreamChunk) {
		switch chunk.Type {
		case "token":
			fullReply.WriteString(chunk.Content)
		case "emotion":
			lastEmotion = chunk.Emotion
		case "action":
			lastAction = chunk.Action
		}
	})
	if err != nil {
		return nil, err
	}
	return &models.ChatResponse{
		Reply:    fullReply.String(),
		Emotion:  lastEmotion,
		Action:   lastAction,
		Finished: true,
	}, nil
}

func (s *ChatService) buildMessages(req models.ChatRequest) []ChatMessage {
	var messages []ChatMessage
	sysPrompt := req.SystemPrompt
	if sysPrompt == "" {
		sysPrompt = s.defaultSystemPrompt(req.CharacterID)
	}
	messages = append(messages, ChatMessage{Role: "system", Content: sysPrompt})
	for _, m := range req.History {
		messages = append(messages, ChatMessage{Role: m.Role, Content: m.Content})
	}
	messages = append(messages, ChatMessage{Role: "user", Content: req.Message})
	return messages
}

// ChatMessage 内部消息格式
type ChatMessage struct {
	Role    string
	Content string
}

func (s *ChatService) defaultSystemPrompt(characterID string) string {
	prompt := "你是一个可爱的动漫角色桌面宠物。用简短、活泼的语气与用户对话。"
	prompt += "回复控制在2-3句话以内。可以使用表情符号。"
	if characterID != "" {
		prompt += fmt.Sprintf(" (角色ID: %s)", characterID)
	}
	return prompt
}

func (s *ChatService) chatOllama(messages []ChatMessage, stream bool, onChunk func(ChatStreamChunk)) error {
	ollamaMsgs := make([]ollamaMessage, len(messages))
	for i, m := range messages {
		ollamaMsgs[i] = ollamaMessage{Role: m.Role, Content: m.Content}
	}
	apiReq := ollamaChatRequest{
		Model:    s.cfg.LLM.Model,
		Messages: ollamaMsgs,
		Stream:   stream,
		Options:  map[string]float64{"temperature": s.cfg.LLM.Temperature},
	}
	body, err := json.Marshal(apiReq)
	if err != nil {
		return fmt.Errorf("请求序列化失败: %v", err)
	}
	url := strings.TrimSuffix(s.cfg.LLM.BaseURL, "/") + "/api/chat"
	resp, err := s.client.Post(url, "application/json", bytes.NewReader(body))
	if err != nil {
		return s.fallbackChat(messages, onChunk)
	}
	defer resp.Body.Close()
	return s.parseOllamaStream(resp.Body, onChunk)
}

func (s *ChatService) parseOllamaStream(body io.Reader, onChunk func(ChatStreamChunk)) error {
	scanner := bufio.NewScanner(body)
	scanner.Buffer(make([]byte, 0, 65536), 65536)
	for scanner.Scan() {
		line := scanner.Text()
		if line == "" {
			continue
		}
		var chunk ollamaChatResponse
		if err := json.Unmarshal([]byte(line), &chunk); err != nil {
			continue
		}
		if chunk.Error != "" {
			onChunk(ChatStreamChunk{Type: "error", Error: chunk.Error})
			return errors.New(chunk.Error)
		}
		if chunk.Message.Content != "" {
			onChunk(ChatStreamChunk{Type: "token", Content: chunk.Message.Content})
		}
		if chunk.Done {
			onChunk(ChatStreamChunk{Type: "done", Finished: true})
			break
		}
	}
	return scanner.Err()
}

func (s *ChatService) chatOpenAI(messages []ChatMessage, stream bool, onChunk func(ChatStreamChunk)) error {
	openaiMsgs := make([]openAIMessage, len(messages))
	for i, m := range messages {
		openaiMsgs[i] = openAIMessage{Role: m.Role, Content: m.Content}
	}
	apiReq := openAIChatRequest{
		Model:       s.cfg.LLM.Model,
		Messages:    openaiMsgs,
		Stream:      stream,
		MaxTokens:   s.cfg.LLM.MaxTokens,
		Temperature: s.cfg.LLM.Temperature,
	}
	body, err := json.Marshal(apiReq)
	if err != nil {
		return fmt.Errorf("请求序列化失败: %v", err)
	}
	url := strings.TrimSuffix(s.cfg.LLM.BaseURL, "/") + "/v1/chat/completions"
	httpReq, err := http.NewRequest("POST", url, bytes.NewReader(body))
	if err != nil {
		return err
	}
	httpReq.Header.Set("Content-Type", "application/json")
	if s.cfg.LLM.APIKey != "" {
		httpReq.Header.Set("Authorization", "Bearer "+s.cfg.LLM.APIKey)
	}
	resp, err := s.client.Do(httpReq)
	if err != nil {
		return s.fallbackChat(messages, onChunk)
	}
	defer resp.Body.Close()
	return s.parseOpenAIStream(resp.Body, onChunk)
}

func (s *ChatService) parseOpenAIStream(body io.Reader, onChunk func(ChatStreamChunk)) error {
	scanner := bufio.NewScanner(body)
	scanner.Buffer(make([]byte, 0, 65536), 65536)
	for scanner.Scan() {
		line := scanner.Text()
		if !strings.HasPrefix(line, "data: ") {
			continue
		}
		data := strings.TrimPrefix(line, "data: ")
		if data == "[DONE]" {
			onChunk(ChatStreamChunk{Type: "done", Finished: true})
			break
		}
		var chunk openAIChatChunk
		if err := json.Unmarshal([]byte(data), &chunk); err != nil {
			continue
		}
		if chunk.Error != nil {
			onChunk(ChatStreamChunk{Type: "error", Error: chunk.Error.Message})
			return errors.New(chunk.Error.Message)
		}
		if len(chunk.Choices) > 0 {
			content := chunk.Choices[0].Delta.Content
			if content != "" {
				onChunk(ChatStreamChunk{Type: "token", Content: content})
			}
			if chunk.Choices[0].FinishReason == "stop" {
				onChunk(ChatStreamChunk{Type: "done", Finished: true})
				break
			}
		}
	}
	return scanner.Err()
}

func (s *ChatService) fallbackChat(messages []ChatMessage, onChunk func(ChatStreamChunk)) error {
	lastMsg := ""
	for i := len(messages) - 1; i >= 0; i-- {
		if messages[i].Role == "user" {
			lastMsg = messages[i].Content
			break
		}
	}
	fallbackReplies := []string{
		"嗯嗯，我知道啦！(｡･ω･｡)",
		"这个问题好有趣呀~让我想想...",
		"主人说什么我都听着哦~ (=^･ω･^=)",
		"哼哼，我已经记住了！",
		"喵喵~今天也要开心呀！",
	}
	reply := fallbackReplies[len(lastMsg)%len(fallbackReplies)]
	time.Sleep(100 * time.Millisecond)
	onChunk(ChatStreamChunk{Type: "token", Content: reply})
	time.Sleep(50 * time.Millisecond)
	onChunk(ChatStreamChunk{Type: "emotion", Emotion: "happy"})
	onChunk(ChatStreamChunk{Type: "done", Finished: true})
	return nil
}
