package main

import (
	"strings"
)

// Slash-command catalog. Commands are matched by prefix from user input
// after a leading `/`. Each command produces either a chat message to send
// or a local action (handled in model.go).

type slashCmd struct {
	name        string // includes leading "/"
	desc        string
	usage       string // shown alongside desc
	localAction string // "" = send to chat; otherwise a token handled by handleSlashAction
	// transform takes the rest of the input (everything after the command + space)
	// and returns the message to actually send to the API.
	transform func(args string) string
}

// catalog: keep alphabetised; first match wins on prefix.
var slashCommands = []slashCmd{
	{
		name: "/activity", desc: "show recent vault writes from this session",
		localAction: "activity",
	},
	{
		name: "/bookmark", desc: "save a URL as a bookmark", usage: "<url> [topic]",
		transform: func(a string) string {
			if a == "" {
				return "I want to save a bookmark — what's the URL?"
			}
			return "Save this bookmark: " + a
		},
	},
	{
		name: "/clear", desc: "clear the current chat (keeps thread history server-side)",
		localAction: "clear",
	},
	{
		name: "/deal", desc: "add a sales deal", usage: "new | <client> <value> <status>",
		transform: func(a string) string {
			if a == "" || a == "new" {
				return "I want to add a new sales deal — walk me through it."
			}
			return "Add a sales deal: " + a
		},
	},
	{
		name: "/help", desc: "show available commands",
		localAction: "help",
	},
	{
		name: "/idea", desc: "quick-capture an idea to inbox-ideas", usage: "<text>",
		transform: func(a string) string {
			if a == "" {
				return "Capture this as an idea: (what?)"
			}
			return "Capture this as an idea: " + a
		},
	},
	{
		name: "/journal", desc: "write today's journal entry", usage: "[mood:N energy:N text]",
		transform: func(a string) string {
			if a == "" {
				return "Open today's journal entry — ask me about my day."
			}
			return "Write a journal entry for today: " + a
		},
	},
	{
		name: "/new", desc: "start a new thread",
		localAction: "new",
	},
	{
		name: "/quit", desc: "exit overseer",
		localAction: "quit",
	},
	{
		name: "/sessions", desc: "open the session list",
		localAction: "sessions",
	},
}

// matchSlash returns commands whose name starts with the given partial.
// Partial includes the leading "/" (or is just "/" to show all).
func matchSlash(partial string) []slashCmd {
	if partial == "" || !strings.HasPrefix(partial, "/") {
		return nil
	}
	q := strings.ToLower(partial)
	out := make([]slashCmd, 0, len(slashCommands))
	for _, c := range slashCommands {
		if strings.HasPrefix(c.name, q) {
			out = append(out, c)
		}
	}
	return out
}

// splitSlash takes "/journal mood:8 today was good" and returns
// ("/journal", "mood:8 today was good").
func splitSlash(input string) (cmd, args string) {
	input = strings.TrimSpace(input)
	if !strings.HasPrefix(input, "/") {
		return "", input
	}
	parts := strings.SplitN(input, " ", 2)
	cmd = parts[0]
	if len(parts) > 1 {
		args = strings.TrimSpace(parts[1])
	}
	return
}

// findCmd returns the catalog entry matching `name` exactly (case-insensitive).
func findCmd(name string) (slashCmd, bool) {
	q := strings.ToLower(name)
	for _, c := range slashCommands {
		if strings.EqualFold(c.name, q) {
			return c, true
		}
	}
	return slashCmd{}, false
}
