package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"net/http"
	"time"

	tea "github.com/charmbracelet/bubbletea"
)

type chatResponseMsg struct {
	response  string
	toolCalls []string
	model     string
	err       error
}

type healthMsg struct {
	ok    bool
	model string
}

func sendMessageCmd(apiURL, message, threadID string) tea.Cmd {
	return func() tea.Msg {
		body, _ := json.Marshal(map[string]string{
			"message":   message,
			"thread_id": threadID,
		})
		client := &http.Client{Timeout: 120 * time.Second}
		resp, err := client.Post(apiURL+"/chat", "application/json", bytes.NewReader(body))
		if err != nil {
			return chatResponseMsg{err: fmt.Errorf("api unreachable: %w", err)}
		}
		defer resp.Body.Close()

		var result struct {
			Response  string   `json:"response"`
			ToolCalls []string `json:"tool_calls"`
			Model     string   `json:"model"`
			Error     string   `json:"error"`
		}
		if err := json.NewDecoder(resp.Body).Decode(&result); err != nil {
			return chatResponseMsg{err: fmt.Errorf("bad response: %w", err)}
		}
		if result.Error != "" {
			return chatResponseMsg{err: fmt.Errorf("%s", result.Error)}
		}
		return chatResponseMsg{
			response:  result.Response,
			toolCalls: result.ToolCalls,
			model:     result.Model,
		}
	}
}

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
