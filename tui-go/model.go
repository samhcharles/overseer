package main

import (
	"bufio"
	"crypto/rand"
	"fmt"
	"io"
	"strings"
	"time"

	"github.com/charmbracelet/bubbles/spinner"
	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"
)

// ── view states ───────────────────────────────────────────────────────────────

type activeView int

const (
	startView activeView = iota
	chatView
	sessionView
)

// ── data types ────────────────────────────────────────────────────────────────

type chatMsg struct {
	role    string
	content string
}

type activityEntry struct {
	name    string
	args    string
	preview string
	done    bool
}

type sessionInfo struct {
	id       string
	preview  string
	modified time.Time
}

// ── model ─────────────────────────────────────────────────────────────────────

type model struct {
	width, height int
	view          activeView

	apiURL   string
	apiOK    bool
	apiModel string

	messages      []chatMsg
	inputBuf      []rune
	loading       bool
	threadID      string
	sp            spinner.Model
	lastErr       string
	streamScanner *bufio.Scanner
	streamBody    io.ReadCloser

	scrollOffset int // lines scrolled up from bottom; 0 = live bottom

	activityLog []activityEntry
	tokensTotal int
	tps         float64

	sessions      []sessionInfo
	sessionCursor int
	stateDir      string
}

func newModel(apiURL, stateDir string) model {
	sp := spinner.New()
	sp.Spinner = spinner.Dot
	sp.Style = lipgloss.NewStyle().Foreground(colAccent)
	return model{
		apiURL:   apiURL,
		stateDir: stateDir,
		threadID: genID(),
		sp:       sp,
		view:     startView,
	}
}

func genID() string {
	b := make([]byte, 6)
	rand.Read(b)
	return fmt.Sprintf("%x", b)
}

// ── Init ──────────────────────────────────────────────────────────────────────

func (m model) Init() tea.Cmd {
	return tea.Batch(healthCheckCmd(m.apiURL), loadSessionsCmd(m.stateDir))
}

// ── Update ────────────────────────────────────────────────────────────────────

func (m model) Update(msg tea.Msg) (tea.Model, tea.Cmd) {
	switch msg := msg.(type) {

	case tea.WindowSizeMsg:
		m.width = msg.Width
		m.height = msg.Height
		return m, nil

	case tea.KeyMsg:
		return m.handleKey(msg)

	case healthMsg:
		m.apiOK = msg.ok
		if msg.model != "" {
			m.apiModel = msg.model
		}
		return m, nil

	case sessionsLoadedMsg:
		m.sessions = msg.sessions
		return m, nil

	case threadLoadedMsg:
		if msg.err != nil {
			m.lastErr = msg.err.Error()
		} else {
			m.threadID = msg.threadID
			m.messages = msg.messages
			m.activityLog = nil
			m.scrollOffset = 0
			m.tokensTotal = 0
			m.tps = 0
			m.lastErr = ""
		}
		m.view = chatView
		return m, nil

	case streamStartMsg:
		m.streamScanner = msg.scanner
		m.streamBody = msg.body
		return m, readChunkCmd(m.streamScanner, m.streamBody)

	case chunkMsg:
		return m.handleChunk(msg)

	case spinner.TickMsg:
		if !m.loading {
			return m, nil
		}
		var cmd tea.Cmd
		m.sp, cmd = m.sp.Update(msg)
		return m, cmd
	}

	return m, nil
}

func (m model) handleChunk(msg chunkMsg) (tea.Model, tea.Cmd) {
	if msg.err != nil {
		m.loading = false
		m.lastErr = msg.err.Error()
		m.streamScanner = nil
		m.streamBody = nil
		if n := len(m.messages); n > 0 && m.messages[n-1].role == "assistant" && m.messages[n-1].content == "" {
			m.messages = m.messages[:n-1]
		}
		return m, nil
	}

	if msg.done {
		m.loading = false
		m.streamScanner = nil
		m.streamBody = nil
		if msg.model != "" {
			m.apiModel = msg.model
		}
		if msg.tokens > 0 {
			m.tokensTotal += msg.tokens
		}
		if msg.tps > 0 {
			m.tps = msg.tps
		}
		return m, nil
	}

	if msg.content != "" {
		if n := len(m.messages); n > 0 && m.messages[n-1].role == "assistant" {
			m.messages[n-1].content += msg.content
		}
		return m, readChunkCmd(m.streamScanner, m.streamBody)
	}

	if msg.toolDone {
		for i := len(m.activityLog) - 1; i >= 0; i-- {
			if m.activityLog[i].name == msg.toolName && !m.activityLog[i].done {
				m.activityLog[i].done = true
				m.activityLog[i].preview = msg.toolPreview
				break
			}
		}
		return m, readChunkCmd(m.streamScanner, m.streamBody)
	}

	if msg.toolName != "" {
		m.activityLog = append(m.activityLog, activityEntry{
			name: msg.toolName,
			args: msg.toolArgs,
		})
		if len(m.activityLog) > 30 {
			m.activityLog = m.activityLog[len(m.activityLog)-30:]
		}
		return m, readChunkCmd(m.streamScanner, m.streamBody)
	}

	return m, readChunkCmd(m.streamScanner, m.streamBody)
}

// ── Key handling ──────────────────────────────────────────────────────────────

func (m model) handleKey(msg tea.KeyMsg) (tea.Model, tea.Cmd) {
	switch m.view {
	case startView:
		return m.handleStartKey(msg)
	case sessionView:
		return m.handleSessionKey(msg)
	default:
		return m.handleChatKey(msg)
	}
}

func (m model) handleStartKey(msg tea.KeyMsg) (tea.Model, tea.Cmd) {
	if msg.Type == tea.KeyCtrlC {
		return m, tea.Quit
	}
	if msg.Type == tea.KeyEnter {
		if len(m.sessions) > 0 {
			return m, loadThreadCmd(m.apiURL, m.sessions[0].id)
		}
	}
	if msg.Type == tea.KeyRunes {
		switch msg.String() {
		case "l", "L":
			if len(m.sessions) > 0 {
				m.view = sessionView
				m.sessionCursor = 0
				return m, nil
			}
		}
	}
	// Any key → new session
	m.view = chatView
	m.messages = []chatMsg{{role: "assistant", content: "ready. what do you need?"}}
	return m, nil
}

func (m model) handleSessionKey(msg tea.KeyMsg) (tea.Model, tea.Cmd) {
	switch msg.Type {
	case tea.KeyCtrlC:
		return m, tea.Quit
	case tea.KeyEsc:
		m.view = chatView
		if len(m.messages) == 0 {
			m.messages = []chatMsg{{role: "assistant", content: "ready. what do you need?"}}
		}
		return m, nil
	case tea.KeyUp:
		if m.sessionCursor > 0 {
			m.sessionCursor--
		}
		return m, nil
	case tea.KeyDown:
		if m.sessionCursor < len(m.sessions)-1 {
			m.sessionCursor++
		}
		return m, nil
	case tea.KeyEnter:
		if len(m.sessions) > 0 {
			return m, loadThreadCmd(m.apiURL, m.sessions[m.sessionCursor].id)
		}
		m.view = chatView
		return m, nil
	case tea.KeyRunes:
		if msg.String() == "n" || msg.String() == "N" {
			m.threadID = genID()
			m.messages = []chatMsg{{role: "assistant", content: "ready. what do you need?"}}
			m.activityLog = nil
			m.tokensTotal = 0
			m.tps = 0
			m.scrollOffset = 0
			m.view = chatView
			return m, nil
		}
	}
	return m, nil
}

func (m model) handleChatKey(msg tea.KeyMsg) (tea.Model, tea.Cmd) {
	// Always-on keys
	switch msg.Type {
	case tea.KeyCtrlC:
		return m, tea.Quit

	case tea.KeyEsc:
		if m.loading {
			m.loading = false
			if m.streamBody != nil {
				m.streamBody.Close()
				m.streamBody = nil
				m.streamScanner = nil
			}
			if n := len(m.messages); n > 0 && m.messages[n-1].role == "assistant" && m.messages[n-1].content == "" {
				m.messages = m.messages[:n-1]
			}
		}
		return m, nil

	case tea.KeyUp:
		m.scrollOffset++
		return m, nil

	case tea.KeyDown:
		if m.scrollOffset > 0 {
			m.scrollOffset--
		}
		return m, nil

	case tea.KeyPgUp:
		m.scrollOffset += 10
		return m, nil

	case tea.KeyPgDown:
		if m.scrollOffset > 10 {
			m.scrollOffset -= 10
		} else {
			m.scrollOffset = 0
		}
		return m, nil

	case tea.KeyCtrlL:
		m.view = sessionView
		m.sessionCursor = 0
		return m, loadSessionsCmd(m.stateDir)
	}

	if m.loading {
		return m, nil
	}

	switch msg.Type {
	case tea.KeyEnter:
		if msg.Alt {
			m.inputBuf = append(m.inputBuf, '\n')
			return m, nil
		}
		text := strings.TrimSpace(string(m.inputBuf))
		if text == "" {
			return m, nil
		}
		m.inputBuf = nil
		m.messages = append(m.messages, chatMsg{role: "user", content: text})
		m.messages = append(m.messages, chatMsg{role: "assistant", content: ""})
		m.loading = true
		m.lastErr = ""
		m.scrollOffset = 0
		return m, tea.Batch(m.sp.Tick, startStreamCmd(m.apiURL, text, m.threadID))

	case tea.KeyCtrlJ:
		m.inputBuf = append(m.inputBuf, '\n')
		return m, nil

	case tea.KeySpace:
		m.inputBuf = append(m.inputBuf, ' ')
		return m, nil

	case tea.KeyBackspace, tea.KeyDelete:
		if len(m.inputBuf) > 0 {
			m.inputBuf = m.inputBuf[:len(m.inputBuf)-1]
		}
		return m, nil

	case tea.KeyRunes:
		m.inputBuf = append(m.inputBuf, []rune(msg.String())...)
		return m, nil

	case tea.KeyCtrlN:
		m.threadID = genID()
		m.messages = []chatMsg{{role: "assistant", content: "ready. what do you need?"}}
		m.activityLog = nil
		m.tokensTotal = 0
		m.tps = 0
		m.scrollOffset = 0
		m.lastErr = ""
		return m, nil
	}

	return m, nil
}

// ── word wrap ─────────────────────────────────────────────────────────────────

func wordWrap(text string, width int) []string {
	if width <= 0 {
		width = 80
	}
	var result []string
	for _, para := range strings.Split(text, "\n") {
		if para == "" {
			result = append(result, "")
			continue
		}
		words := strings.Fields(para)
		var line strings.Builder
		for _, w := range words {
			if line.Len()+len(w)+1 > width && line.Len() > 0 {
				result = append(result, line.String())
				line.Reset()
			}
			if line.Len() > 0 {
				line.WriteByte(' ')
			}
			line.WriteString(w)
		}
		if line.Len() > 0 {
			result = append(result, line.String())
		}
	}
	return result
}

func trunc(s string, max int) string {
	if max < 1 {
		return ""
	}
	if len(s) <= max {
		return s
	}
	if max <= 1 {
		return "~"
	}
	return s[:max-1] + "~"
}

// ── layout constants ──────────────────────────────────────────────────────────

const panelWidth = 26

func (m model) usePanel() bool { return m.width > 82 }

func (m model) leftWidth() int {
	if m.usePanel() {
		return m.width - panelWidth - 1
	}
	return m.width
}

// ── View dispatch ─────────────────────────────────────────────────────────────

func (m model) View() string {
	w := m.width
	if w == 0 {
		w = 80
	}
	h := m.height
	if h == 0 {
		h = 24
	}
	switch m.view {
	case startView:
		return m.renderStart(w, h)
	case sessionView:
		return m.renderSession(w, h)
	default:
		return m.renderMain(w, h)
	}
}

// ── Start screen ──────────────────────────────────────────────────────────────

func (m model) renderStart(w, h int) string {
	center := func(s string) string {
		n := lipgloss.Width(s)
		pad := (w - n) / 2
		if pad < 0 {
			pad = 0
		}
		return strings.Repeat(" ", pad) + s
	}

	var lines []string
	topPad := (h - 12) / 2
	if topPad < 0 {
		topPad = 0
	}
	for i := 0; i < topPad; i++ {
		lines = append(lines, "")
	}

	lines = append(lines, center(titleStyle.Render("overseer")))
	lines = append(lines, center(hintStyle.Render("your second brain, running local")))
	lines = append(lines, "")
	lines = append(lines, center(sepStyle.Render(strings.Repeat("─", 28))))
	lines = append(lines, "")

	dot := statusDot(m.apiOK)
	modelStr := "connecting..."
	if m.apiModel != "" {
		modelStr = m.apiModel
	}
	lines = append(lines, center(dot+"  "+hintStyle.Render(modelStr)))
	lines = append(lines, "")

	if len(m.sessions) > 0 {
		last := m.sessions[0]
		preview := last.preview
		if preview == "" {
			preview = last.id
		}
		lines = append(lines, center(hintStyle.Render(fmt.Sprintf("%d session(s) saved", len(m.sessions)))))
		lines = append(lines, center(hintStyle.Render("last: "+trunc(preview, 40))))
		lines = append(lines, "")
		lines = append(lines, center(hintStyle.Render("enter → resume last   any key → new session")))
		lines = append(lines, center(hintStyle.Render("l → session list")))
	} else {
		lines = append(lines, center(hintStyle.Render("press any key to start")))
	}

	for len(lines) < h {
		lines = append(lines, "")
	}
	return strings.Join(lines[:h], "\n")
}

// ── Session list ──────────────────────────────────────────────────────────────

func (m model) renderSession(w, h int) string {
	var lines []string

	hdr := titleStyle.Render("sessions") + "   " +
		hintStyle.Render("↑↓:navigate  enter:load  n:new  esc:back  ctrl+c:quit")
	lines = append(lines, hdr)
	lines = append(lines, sepStyle.Render(strings.Repeat("─", w)))

	maxShow := h - 5
	for i, s := range m.sessions {
		if i >= maxShow {
			break
		}
		timeStr := s.modified.Format("Jan 02 15:04")
		preview := s.preview
		previewMax := w - 22
		if previewMax < 10 {
			previewMax = 10
		}
		if len(preview) > previewMax {
			preview = preview[:previewMax-3] + "..."
		}
		row := fmt.Sprintf("  %s  %s", timeStr, preview)
		if i == m.sessionCursor {
			row = selectedStyle.Render(fmt.Sprintf("▶ %s  %s", timeStr, preview))
		}
		lines = append(lines, row)
	}

	if len(m.sessions) == 0 {
		lines = append(lines, "")
		lines = append(lines, "  "+hintStyle.Render("no sessions yet"))
	}

	lines = append(lines, "")
	lines = append(lines, "  "+hintStyle.Render("n → start new session"))

	for len(lines) < h {
		lines = append(lines, "")
	}
	return strings.Join(lines[:h], "\n")
}

// ── Main chat view ────────────────────────────────────────────────────────────

func (m model) renderMain(w, h int) string {
	return m.renderHeader(w) + "\n" + m.renderBody(w, h) + "\n" + m.renderStatus(w)
}

func (m model) renderHeader(w int) string {
	left := titleStyle.Render("overseer")
	if m.apiModel != "" {
		left += "  " + hintStyle.Render(m.apiModel)
	}

	var rightParts []string
	if m.tokensTotal > 0 {
		stat := fmt.Sprintf("%d tok", m.tokensTotal)
		if m.tps > 0 {
			stat += fmt.Sprintf("  %.0f t/s", m.tps)
		}
		rightParts = append(rightParts, statsStyle.Render(stat))
	}
	if m.scrollOffset > 0 {
		rightParts = append(rightParts, scrollStyle.Render(fmt.Sprintf("↑%d", m.scrollOffset)))
	}
	rightParts = append(rightParts, statusDot(m.apiOK))
	right := strings.Join(rightParts, "  ")

	gap := w - lipgloss.Width(left) - lipgloss.Width(right)
	if gap < 1 {
		gap = 1
	}
	return left + strings.Repeat(" ", gap) + right
}

func (m model) renderStatus(w int) string {
	hints := "enter:send  ctrl+j/alt+enter:newline  ↑↓:scroll  ctrl+n:new  ctrl+l:sessions  ctrl+c:quit"
	_ = w
	return statusStyle.Render(hints)
}

func (m model) renderBody(w, h int) string {
	// h total lines: 1 header + 1 (body) + 1 status = 3 fixed, so body = h - 2
	bodyH := h - 2
	if bodyH < 3 {
		bodyH = 3
	}

	// body = msgArea(bodyH-2) + sep(1) + input(1)
	msgAreaH := bodyH - 2
	if msgAreaH < 1 {
		msgAreaH = 1
	}

	lw := m.leftWidth()
	useP := m.usePanel()

	chatLines := m.buildChatLines(lw, msgAreaH)

	var panelLines []string
	if useP {
		panelLines = m.buildPanelLines(panelWidth, bodyH)
	}

	var rows []string
	for i := 0; i < msgAreaH; i++ {
		left := ""
		if i < len(chatLines) {
			left = chatLines[i]
		}
		if !useP {
			rows = append(rows, left)
			continue
		}
		padN := lw - lipgloss.Width(left)
		if padN < 0 {
			padN = 0
		}
		right := ""
		if i < len(panelLines) {
			right = panelLines[i]
		}
		rows = append(rows, left+strings.Repeat(" ", padN)+sepStyle.Render("│")+right)
	}

	sep := sepStyle.Render(strings.Repeat("─", w))
	input := m.renderInput()

	return strings.Join(rows, "\n") + "\n" + sep + "\n" + input
}

func (m model) renderInput() string {
	if m.loading {
		n := len(m.messages)
		if n > 0 && m.messages[n-1].role == "assistant" && m.messages[n-1].content == "" {
			return m.sp.View() + " thinking..."
		}
		return hintStyle.Render("▌")
	}
	inputText := string(m.inputBuf)
	lines := strings.Split(inputText, "\n")
	display := lines[len(lines)-1]
	if len(lines) > 1 {
		display = fmt.Sprintf("[%d lines] ", len(lines)) + display
	}
	return hintStyle.Render("> ") + display + "█"
}

// ── Chat line builder ─────────────────────────────────────────────────────────

func (m model) buildChatLines(w, maxLines int) []string {
	var all []string
	for _, msg := range m.messages {
		var label string
		if msg.role == "user" {
			label = userLabelStyle.Render("you")
		} else {
			label = asstLabelStyle.Render("overseer")
		}
		all = append(all, label)
		wrapped := wordWrap(msg.content, w-2)
		if len(wrapped) == 0 {
			wrapped = []string{""}
		}
		for _, line := range wrapped {
			all = append(all, "  "+line)
		}
		all = append(all, "")
	}
	if m.lastErr != "" {
		all = append(all, errStyle.Render("error: "+m.lastErr))
		all = append(all, "")
	}

	total := len(all)
	start := total - maxLines - m.scrollOffset
	if start < 0 {
		start = 0
	}
	end := start + maxLines
	if end > total {
		end = total
	}
	visible := append([]string{}, all[start:end]...)

	// Pad top with empty lines
	for len(visible) < maxLines {
		visible = append([]string{""}, visible...)
	}
	return visible
}

// ── Activity panel builder ────────────────────────────────────────────────────

func (m model) buildPanelLines(w, h int) []string {
	lines := make([]string, h)

	idx := 0

	set := func(s string) {
		if idx < h {
			lines[idx] = s
			idx++
		}
	}

	set(panelTitleStyle.Render("activity"))
	set(sepStyle.Render(strings.Repeat("─", w)))

	// Entries — show last N that fit, reserve 3 for stats
	statsH := 3
	entryAreaH := h - 2 - statsH
	if entryAreaH < 0 {
		entryAreaH = 0
	}

	entries := m.activityLog
	// Estimate lines per entry: name line + optional args/preview = 2
	maxEntries := entryAreaH / 2
	if maxEntries < 1 {
		maxEntries = 1
	}
	if len(entries) > maxEntries {
		entries = entries[len(entries)-maxEntries:]
	}

	for _, e := range entries {
		if idx >= h-statsH {
			break
		}
		var nameLine string
		if e.done {
			nameLine = activityDoneStyle.Render("✓") + " " + hintStyle.Render(trunc(e.name, w-3))
		} else {
			nameLine = activityActiveStyle.Render("·") + " " + activityActiveStyle.Render(trunc(e.name, w-3))
		}
		set(nameLine)

		detail := ""
		if e.preview != "" {
			detail = e.preview
		} else if e.args != "" {
			detail = e.args
		}
		if detail != "" && idx < h-statsH {
			set("  " + hintStyle.Render(trunc(detail, w-3)))
		}
	}

	// Stats block at bottom
	if m.tokensTotal > 0 {
		statsLine := fmt.Sprintf("%d tok", m.tokensTotal)
		if m.tps > 0 {
			statsLine += fmt.Sprintf("  %.0f t/s", m.tps)
		}
		if h >= 3 {
			lines[h-3] = sepStyle.Render(strings.Repeat("─", w))
			lines[h-2] = statsStyle.Render(statsLine)
		}
	}

	return lines
}
