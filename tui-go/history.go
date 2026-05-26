package main

import (
	"bufio"
	"os"
	"path/filepath"
	"strings"
)

// Input history persistence — keeps the last N user messages so arrow-up
// in the chat input cycles through them across sessions. Stored as
// newline-separated lines at ~/.local/share/overseer/input_history.

const inputHistoryCap = 200

func inputHistoryPath() string {
	home, err := os.UserHomeDir()
	if err != nil {
		return ""
	}
	return filepath.Join(home, ".local", "share", "overseer", "input_history")
}

func loadInputHistory() []string {
	path := inputHistoryPath()
	if path == "" {
		return nil
	}
	f, err := os.Open(path)
	if err != nil {
		return nil
	}
	defer f.Close()
	var out []string
	s := bufio.NewScanner(f)
	s.Buffer(make([]byte, 1<<16), 1<<16)
	for s.Scan() {
		line := s.Text()
		if line == "" {
			continue
		}
		out = append(out, line)
	}
	if len(out) > inputHistoryCap {
		out = out[len(out)-inputHistoryCap:]
	}
	return out
}

func appendInputHistory(entries []string, msg string) []string {
	msg = strings.ReplaceAll(strings.TrimSpace(msg), "\n", " ")
	if msg == "" {
		return entries
	}
	// dedup against last entry — typing the same thing twice in a row is noise
	if n := len(entries); n > 0 && entries[n-1] == msg {
		return entries
	}
	entries = append(entries, msg)
	if len(entries) > inputHistoryCap {
		entries = entries[len(entries)-inputHistoryCap:]
	}
	go persistInputHistory(entries)
	return entries
}

func persistInputHistory(entries []string) {
	path := inputHistoryPath()
	if path == "" {
		return
	}
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return
	}
	tmp := path + ".tmp"
	f, err := os.Create(tmp)
	if err != nil {
		return
	}
	w := bufio.NewWriter(f)
	for _, e := range entries {
		w.WriteString(e)
		w.WriteByte('\n')
	}
	w.Flush()
	f.Close()
	os.Rename(tmp, path)
}
