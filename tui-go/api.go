package main

import (
	"bufio"
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"

	tea "github.com/charmbracelet/bubbletea"
)

// ── message types ─────────────────────────────────────────────────────────────

type streamStartMsg struct {
	scanner *bufio.Scanner
	body    io.ReadCloser
}

type chunkMsg struct {
	content     string
	toolName    string
	toolArgs    string
	toolPreview string
	toolDone    bool
	model       string
	tokens      int
	tps         float64
	done        bool
	err         error
}

type healthMsg struct {
	ok    bool
	model string
}

type sessionsLoadedMsg struct {
	sessions []sessionInfo
}

type threadLoadedMsg struct {
	threadID string
	messages []chatMsg
	err      error
}

// ── streaming ─────────────────────────────────────────────────────────────────

func startStreamCmd(apiURL, message, threadID string) tea.Cmd {
	return func() tea.Msg {
		payload, _ := json.Marshal(map[string]string{
			"message":   message,
			"thread_id": threadID,
		})
		client := &http.Client{Timeout: 180 * time.Second}
		resp, err := client.Post(apiURL+"/chat/stream", "application/json", bytes.NewReader(payload))
		if err != nil {
			return chunkMsg{err: fmt.Errorf("api unreachable: %w", err)}
		}
		if resp.StatusCode != 200 {
			resp.Body.Close()
			return chunkMsg{err: fmt.Errorf("api error: %d", resp.StatusCode)}
		}
		sc := bufio.NewScanner(resp.Body)
		sc.Buffer(make([]byte, 1<<20), 1<<20) // 1MB buffer for long tool results
		return streamStartMsg{scanner: sc, body: resp.Body}
	}
}

func readChunkCmd(sc *bufio.Scanner, body io.ReadCloser) tea.Cmd {
	return func() tea.Msg {
		for sc.Scan() {
			line := sc.Text()
			if !strings.HasPrefix(line, "data: ") {
				continue
			}
			line = strings.TrimPrefix(line, "data: ")
			if line == "" {
				continue
			}
			var ev struct {
				Type    string  `json:"type"`
				Content string  `json:"content"`
				Name    string  `json:"name"`
				Args    string  `json:"args"`
				Preview string  `json:"preview"`
				Model   string  `json:"model"`
				Tokens  int     `json:"tokens"`
				Tps     float64 `json:"tps"`
			}
			if err := json.Unmarshal([]byte(line), &ev); err != nil {
				continue
			}
			switch ev.Type {
			case "chunk":
				return chunkMsg{content: ev.Content}
			case "tool":
				return chunkMsg{toolName: ev.Name, toolArgs: ev.Args}
			case "tool_done":
				return chunkMsg{toolName: ev.Name, toolPreview: ev.Preview, toolDone: true}
			case "done":
				body.Close()
				return chunkMsg{done: true, model: ev.Model, tokens: ev.Tokens, tps: ev.Tps}
			case "error":
				body.Close()
				return chunkMsg{err: fmt.Errorf("%s", ev.Content), done: true}
			}
		}
		if err := sc.Err(); err != nil {
			body.Close()
			return chunkMsg{err: fmt.Errorf("stream: %w", err), done: true}
		}
		body.Close()
		return chunkMsg{done: true}
	}
}

// ── model switch ──────────────────────────────────────────────────────────────

// switchModelCmd PATCHes the API node with a new active model name.
// The node is expected to expose POST /model { "name": "..." }.
// Returns a healthMsg (re-runs health check) so the UI updates.
func switchModelCmd(apiURL, name string) tea.Cmd {
	return func() tea.Msg {
		payload, _ := json.Marshal(map[string]string{"name": name})
		client := &http.Client{Timeout: 5 * time.Second}
		resp, err := client.Post(apiURL+"/model", "application/json", bytes.NewReader(payload))
		if err != nil {
			return chunkMsg{err: fmt.Errorf("model switch failed: %w", err), done: true}
		}
		defer resp.Body.Close()
		var result struct {
			OK    bool   `json:"ok"`
			Model string `json:"model"`
		}
		json.NewDecoder(resp.Body).Decode(&result)
		return healthMsg{ok: true, model: result.Model}
	}
}

// ── health ────────────────────────────────────────────────────────────────────

func healthCheckCmd(apiURL string) tea.Cmd {
	return func() tea.Msg {
		client := &http.Client{Timeout: 3 * time.Second}
		resp, err := client.Get(apiURL + "/health")
		if err != nil {
			return healthMsg{ok: false}
		}
		defer resp.Body.Close()
		var result struct {
			Status string `json:"status"`
			Model  string `json:"model"`
		}
		json.NewDecoder(resp.Body).Decode(&result)
		return healthMsg{ok: result.Status == "ok", model: result.Model}
	}
}

// ── sessions ──────────────────────────────────────────────────────────────────

func loadSessionsCmd(stateDir string) tea.Cmd {
	return func() tea.Msg {
		threadsDir := filepath.Join(stateDir, "threads")
		entries, err := os.ReadDir(threadsDir)
		if err != nil {
			return sessionsLoadedMsg{}
		}
		var sessions []sessionInfo
		for _, e := range entries {
			name := e.Name()
			if !strings.HasSuffix(name, ".json") {
				continue
			}
			id := strings.TrimSuffix(name, ".json")
			fi, _ := e.Info()
			var mod time.Time
			if fi != nil {
				mod = fi.ModTime()
			}
			data, err := os.ReadFile(filepath.Join(threadsDir, name))
			preview := ""
			if err == nil {
				var history []struct {
					Role    string `json:"role"`
					Content string `json:"content"`
				}
				if json.Unmarshal(data, &history) == nil {
					for _, h := range history {
						if h.Role == "user" {
							preview = h.Content
							if len(preview) > 48 {
								preview = preview[:45] + "..."
							}
							break
						}
					}
				}
			}
			sessions = append(sessions, sessionInfo{id: id, preview: preview, modified: mod})
		}
		sort.Slice(sessions, func(i, j int) bool {
			return sessions[i].modified.After(sessions[j].modified)
		})
		return sessionsLoadedMsg{sessions: sessions}
	}
}

func loadThreadCmd(apiURL, threadID string) tea.Cmd {
	return func() tea.Msg {
		client := &http.Client{Timeout: 5 * time.Second}
		resp, err := client.Get(apiURL + "/threads/" + threadID)
		if err != nil {
			return threadLoadedMsg{err: fmt.Errorf("load failed: %w", err)}
		}
		defer resp.Body.Close()
		var result struct {
			History []struct {
				Role    string `json:"role"`
				Content string `json:"content"`
			} `json:"history"`
		}
		if err := json.NewDecoder(resp.Body).Decode(&result); err != nil {
			return threadLoadedMsg{err: fmt.Errorf("decode: %w", err)}
		}
		var msgs []chatMsg
		for _, h := range result.History {
			if h.Role == "system" || h.Role == "tool" {
				continue
			}
			msgs = append(msgs, chatMsg{role: h.Role, content: h.Content})
		}
		return threadLoadedMsg{threadID: threadID, messages: msgs}
	}
}
