package handlers

import (
	"net/http"
	"strings"

	"github.com/gin-gonic/gin"

	"live2d-api/models"
	"live2d-api/services"
)

// ChatContext is the normalized request + session state shared by the
// Chat and ChatStream handlers. Centralizing it guarantees both endpoints
// treat the session_id field name, casing, and default identically.
type ChatContext struct {
	Request   models.ChatRequest
	SessionID string
	Session   *services.ChatSession
}

// getChatContext binds and normalizes a chat request for both /api/chat
// and /api/chat/stream. It performs:
//   - BindJSON (writing a 400 and returning an error on failure)
//   - session_id extraction. Go's encoding/json matches the `session_id`
//     tag and the SessionID field name case-insensitively, so
//     session_id / sessionId / SessionID / SESSION_ID are all accepted.
//   - LoadOrCreateSession against the in-memory session store.
//
// On success the request carries the canonical SessionID (an id is
// generated when the client omitted it) and the returned context holds the
// shared session. Both endpoints MUST go through this helper so the
// binding/session behavior can never drift apart again.
func (h *Handler) getChatContext(c *gin.Context) (models.ChatRequest, *ChatContext, error) {
	var req models.ChatRequest
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, models.Response{
			Success: false,
			Error:   "参数错误: " + err.Error(),
		})
		return req, nil, err
	}

	// Normalize the supplied session id (trim whitespace); generate a
	// stable session when the client did not provide one.
	req.SessionID = strings.TrimSpace(req.SessionID)
	session := h.chatSessions.LoadOrCreate(req.SessionID)
	// Persist the canonical id back onto the request so subsequent turns
	// in the same conversation reuse it.
	req.SessionID = session.ID

	ctx := &ChatContext{
		Request:   req,
		SessionID: session.ID,
		Session:   session,
	}
	return req, ctx, nil
}
