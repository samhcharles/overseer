package main

import (
	"strings"
	"sync"

	"github.com/charmbracelet/glamour"
)

// markdownRenderer caches one glamour renderer per (theme, width) tuple.
// Glamour's TermRenderer is fairly expensive to construct, so reusing it
// across messages matters when the viewport repaints.

type rendererKey struct {
	theme string
	width int
}

var (
	rendererCache   = map[rendererKey]*glamour.TermRenderer{}
	rendererCacheMu sync.Mutex
)

func getRenderer(width int) *glamour.TermRenderer {
	if width < 20 {
		width = 20
	}
	key := rendererKey{theme: activeTheme.Name, width: width}
	rendererCacheMu.Lock()
	defer rendererCacheMu.Unlock()
	if r, ok := rendererCache[key]; ok {
		return r
	}
	style := "dark"
	if activeTheme.Name == "light" {
		style = "light"
	} else if activeTheme.Name == "term" {
		style = "notty"
	}
	r, err := glamour.NewTermRenderer(
		glamour.WithStandardStyle(style),
		glamour.WithWordWrap(width),
		glamour.WithEmoji(),
	)
	if err != nil {
		return nil
	}
	rendererCache[key] = r
	return r
}

// renderMarkdown returns the rendered string, falling back to plain text on error.
// Trims trailing whitespace glamour adds (extra newlines around blocks).
func renderMarkdown(text string, width int) string {
	if text == "" {
		return ""
	}
	r := getRenderer(width)
	if r == nil {
		return text
	}
	out, err := r.Render(text)
	if err != nil {
		return text
	}
	// Glamour pads blocks with leading/trailing blank lines. Strip outer.
	out = strings.Trim(out, "\n")
	return out
}

// invalidateRendererCache is called when theme changes so old renderers
// (with stale colours) are dropped.
func invalidateRendererCache() {
	rendererCacheMu.Lock()
	rendererCache = map[rendererKey]*glamour.TermRenderer{}
	rendererCacheMu.Unlock()
}
