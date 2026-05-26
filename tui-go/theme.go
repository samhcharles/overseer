package main

import (
	"os"
	"path/filepath"
	"strings"

	"github.com/charmbracelet/lipgloss"
)

// Theme is a named colour palette. Switching themes rebuilds all
// package-level lipgloss styles in styles.go via applyTheme().
type Theme struct {
	Name   string
	Accent lipgloss.Color // primary brand colour (orange in default)
	Dim    lipgloss.Color // dimmed text — hints, separators
	Bright lipgloss.Color // body text
	Green  lipgloss.Color // success
	Red    lipgloss.Color // error
	Blue   lipgloss.Color // info / user accent alt
	Yellow lipgloss.Color // warning / active
	Muted  lipgloss.Color // secondary body
	BgAlt  lipgloss.Color // subtle panel background (e.g. input border)
}

var themes = map[string]Theme{
	"dark": {
		Name:   "dark",
		Accent: lipgloss.Color("#e06c00"),
		Dim:    lipgloss.Color("#555555"),
		Bright: lipgloss.Color("#d0d0d0"),
		Green:  lipgloss.Color("#73F59F"),
		Red:    lipgloss.Color("#FF5F57"),
		Blue:   lipgloss.Color("#74B9E7"),
		Yellow: lipgloss.Color("#f0c040"),
		Muted:  lipgloss.Color("#888888"),
		BgAlt:  lipgloss.Color("#1a1a1a"),
	},
	"light": {
		Name:   "light",
		Accent: lipgloss.Color("#c25400"),
		Dim:    lipgloss.Color("#999999"),
		Bright: lipgloss.Color("#1a1a1a"),
		Green:  lipgloss.Color("#1f7a3a"),
		Red:    lipgloss.Color("#c0392b"),
		Blue:   lipgloss.Color("#2980b9"),
		Yellow: lipgloss.Color("#b88500"),
		Muted:  lipgloss.Color("#555555"),
		BgAlt:  lipgloss.Color("#f0f0f0"),
	},
	"term": { // adapts to terminal's ANSI palette — best for tmux/screen
		Name:   "term",
		Accent: lipgloss.Color("3"),
		Dim:    lipgloss.Color("8"),
		Bright: lipgloss.Color("15"),
		Green:  lipgloss.Color("2"),
		Red:    lipgloss.Color("1"),
		Blue:   lipgloss.Color("4"),
		Yellow: lipgloss.Color("11"),
		Muted:  lipgloss.Color("7"),
		BgAlt:  lipgloss.Color("0"),
	},
}

var activeTheme = themes["dark"]

func themeNames() []string {
	out := []string{"dark", "light", "term"}
	return out
}

func setTheme(name string) bool {
	t, ok := themes[strings.ToLower(name)]
	if !ok {
		return false
	}
	activeTheme = t
	applyTheme()
	invalidateRendererCache()
	persistTheme(name)
	return true
}

func themeConfigPath() string {
	home, err := os.UserHomeDir()
	if err != nil {
		return ""
	}
	return filepath.Join(home, ".config", "overseer", "theme")
}

func loadPersistedTheme() {
	p := themeConfigPath()
	if p == "" {
		return
	}
	data, err := os.ReadFile(p)
	if err != nil {
		return
	}
	name := strings.TrimSpace(string(data))
	if t, ok := themes[name]; ok {
		activeTheme = t
		applyTheme()
	}
}

func persistTheme(name string) {
	p := themeConfigPath()
	if p == "" {
		return
	}
	_ = os.MkdirAll(filepath.Dir(p), 0o755)
	_ = os.WriteFile(p, []byte(name), 0o644)
}
