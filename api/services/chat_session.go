package services

import (
	"crypto/rand"
	"encoding/hex"
	"sync"
	"time"

	"live2d-api/models"
)

// ChatSession tracks a lightweight conversational session so that repeated
// calls to /api/chat and /api/chat/stream share history behind a single
// session_id. Sessions are kept in memory (best-effort; the chat service
// still accepts an explicit `history` array, so nothing breaks if the
// session is evicted).
type ChatSession struct {
	ID        string
	History   []models.ChatMessage
	CreatedAt time.Time
	UpdatedAt time.Time
}

// ChatSessionStore is a concurrency-safe in-memory map of ChatSession.
type ChatSessionStore struct {
	mu       sync.RWMutex
	sessions map[string]*ChatSession
}

// NewChatSessionStore creates an empty session store.
func NewChatSessionStore() *ChatSessionStore {
	return &ChatSessionStore{sessions: make(map[string]*ChatSession)}
}

func newSessionID() string {
	b := make([]byte, 12)
	if _, err := rand.Read(b); err != nil {
		// rand failing is extraordinarily unlikely; fall back to time.
		return "sess_" + time.Now().Format("20060102150405.000000")
	}
	return "sess_" + hex.EncodeToString(b)
}

// LoadOrCreate returns the session identified by id, creating a new one when
// id is empty or unknown. The returned pointer is shared; callers must not
// mutate History without going through AppendHistory (or holding the store's
// write semantics via Record*).
func (s *ChatSessionStore) LoadOrCreate(id string) *ChatSession {
	s.mu.Lock()
	defer s.mu.Unlock()
	if id != "" {
		if sess, ok := s.sessions[id]; ok {
			return sess
		}
	}
	sess := &ChatSession{
		ID:        newSessionID(),
		History:   []models.ChatMessage{},
		CreatedAt: time.Now(),
		UpdatedAt: time.Now(),
	}
	s.sessions[sess.ID] = sess
	return sess
}

// AppendHistory appends messages to a session and bumps UpdatedAt.
func (s *ChatSessionStore) AppendHistory(id string, msgs ...models.ChatMessage) {
	if len(msgs) == 0 {
		return
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	sess, ok := s.sessions[id]
	if !ok {
		return
	}
	sess.History = append(sess.History, msgs...)
	sess.UpdatedAt = time.Now()
}
