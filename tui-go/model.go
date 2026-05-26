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

	// W2: slash-command palette state
	slashOpen    bool       // is the palette currently visible?
	slashMatches []slashCmd // commands matching current input
	slashCursor  int        // selected entry in slashMatches

	// W2: input history (arrow-up cycles past user messages)
	inputHistory  []string
	historyCursor int    // -1 = not navigating; else index into inputHistory
	historySaved  []rune // original buffer to restore on Esc-out-of-history
}

func newModel(apiURL, stateDir string) model {
	sp := spinner.New()
	sp.Spinner = spinner.Dot
	sp.Style = lipgloss.NewStyle().Foreground(colAccent)
	return model{
		apiURL:        apiURL,
		stateDir:      stateDir,
		threadID:      genID(),
		sp:            sp,
		view:          startView,
		inputHistory:  loadInputHistory(),
		historyCursor: -1,
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
	// Slash palette consumes most keys when open.
	if m.slashOpen {
		return m.handleSlashKey(msg)
	}

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
			// Preserve partial assistant content with a [cancelled] tag so the
			// user sees what came through. Empty partials are still removed.
			if n := len(m.messages); n > 0 && m.messages[n-1].role == "assistant" {
				if m.messages[n-1].content == "" {
					m.messages = m.messages[:n-1]
				} else {
					m.messages[n-1].content = strings.TrimRight(m.messages[n-1].content, " \n") + "  [cancelled]"
				}
			}
		}
		return m, nil

	case tea.KeyUp:
		// If input is empty and we have history, cycle backward through it
		// instead of scrolling messages. Matches Claude Code / bash UX.
		if len(m.inputBuf) == 0 && len(m.inputHistory) > 0 && !m.loading {
			if m.historyCursor == -1 {
				m.historySaved = nil
				m.historyCursor = len(m.inputHistory) - 1
			} else if m.historyCursor > 0 {
				m.historyCursor--
			}
			m.inputBuf = []rune(m.inputHistory[m.historyCursor])
			return m, nil
		}
		if m.historyCursor != -1 {
			// Already navigating; keep cycling backward
			if m.historyCursor > 0 {
				m.historyCursor--
				m.inputBuf = []rune(m.inputHistory[m.historyCursor])
			}
			return m, nil
		}
		m.scrollOffset++
		return m, nil

	case tea.KeyDown:
		if m.historyCursor != -1 {
			if m.historyCursor < len(m.inputHistory)-1 {
				m.historyCursor++
				m.inputBuf = []rune(m.inputHistory[m.historyCursor])
			} else {
				// Past the latest entry → restore (or empty)
				m.historyCursor = -1
				m.inputBuf = m.historySaved
				m.historySaved = nil
			}
			return m, nil
		}
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
		m.historyCursor = -1
		m.historySaved = nil
		m.inputHistory = appendInputHistory(m.inputHistory, text)
		// Slash commands typed directly (without using the palette) get
		// the same treatment as palette-completed ones.
		if strings.HasPrefix(text, "/") {
			return m.executeSlash(text)
		}
		return m.sendChat(text)

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
		// Backing out of a history-restored buffer cancels nav mode.
		if m.historyCursor != -1 {
			m.historyCursor = -1
			m.historySaved = nil
		}
		return m, nil

	case tea.KeyRunes:
		// First "/" with empty buffer opens the slash palette.
		s := msg.String()
		if len(m.inputBuf) == 0 && s == "/" {
			m.inputBuf = []rune{'/'}
			m.slashOpen = true
			m.slashMatches = matchSlash("/")
			m.slashCursor = 0
			return m, nil
		}
		m.inputBuf = append(m.inputBuf, []rune(s)...)
		// Any typing exits history nav.
		if m.historyCursor != -1 {
			m.historyCursor = -1
			m.historySaved = nil
		}
		return m, nil

	case tea.KeyCtrlN:
		return m.resetThread()
	}

	return m, nil
}

// ── Slash palette ──────────────────────────────────────────────────────────────

func (m model) handleSlashKey(msg tea.KeyMsg) (tea.Model, tea.Cmd) {
	switch msg.Type {
	case tea.KeyCtrlC:
		return m, tea.Quit
	case tea.KeyEsc:
		// Close the palette but keep the typed text — user might want to chat literally.
		m.slashOpen = false
		m.slashMatches = nil
		return m, nil
	case tea.KeyUp:
		if m.slashCursor > 0 {
			m.slashCursor--
		}
		return m, nil
	case tea.KeyDown:
		if m.slashCursor < len(m.slashMatches)-1 {
			m.slashCursor++
		}
		return m, nil
	case tea.KeyTab:
		if len(m.slashMatches) > 0 {
			sel := m.slashMatches[m.slashCursor]
			m.inputBuf = []rune(sel.name + " ")
			m.slashOpen = false
			m.slashMatches = nil
		}
		return m, nil
	case tea.KeyEnter:
		if len(m.slashMatches) == 0 {
			m.slashOpen = false
			return m, nil
		}
		// Pick selected (or only) command; if user typed extra args after
		// the partial, treat the full inputBuf as the command line.
		typed := strings.TrimSpace(string(m.inputBuf))
		sel := m.slashMatches[m.slashCursor]
		var cmdLine string
		if strings.HasPrefix(typed, sel.name+" ") || typed == sel.name {
			cmdLine = typed
		} else {
			cmdLine = sel.name
		}
		m.inputBuf = nil
		m.slashOpen = false
		m.slashMatches = nil
		m.historyCursor = -1
		m.inputHistory = appendInputHistory(m.inputHistory, cmdLine)
		return m.executeSlash(cmdLine)
	case tea.KeyBackspace, tea.KeyDelete:
		if len(m.inputBuf) > 0 {
			m.inputBuf = m.inputBuf[:len(m.inputBuf)-1]
		}
		if len(m.inputBuf) == 0 || m.inputBuf[0] != '/' {
			m.slashOpen = false
			m.slashMatches = nil
			return m, nil
		}
		m.slashMatches = matchSlash(string(m.inputBuf))
		if m.slashCursor >= len(m.slashMatches) {
			m.slashCursor = 0
		}
		return m, nil
	case tea.KeySpace:
		m.inputBuf = append(m.inputBuf, ' ')
		// Once the user types a space after the command name, hide the palette
		// — they're typing args now, not picking commands.
		if cmd, _ := splitSlash(string(m.inputBuf)); cmd != "" {
			if _, ok := findCmd(cmd); ok {
				m.slashOpen = false
				m.slashMatches = nil
			}
		}
		return m, nil
	case tea.KeyRunes:
		m.inputBuf = append(m.inputBuf, []rune(msg.String())...)
		m.slashMatches = matchSlash(string(m.inputBuf))
		if len(m.slashMatches) == 0 {
			// User typed past any match — leave palette open with empty list
			// so they can backspace; or close immediately. Close is simpler.
			m.slashOpen = false
		}
		if m.slashCursor >= len(m.slashMatches) {
			m.slashCursor = 0
		}
		return m, nil
	}
	return m, nil
}

// executeSlash takes a full slash-command line and dispatches.
func (m model) executeSlash(line string) (tea.Model, tea.Cmd) {
	name, args := splitSlash(line)
	cmd, ok := findCmd(name)
	if !ok {
		// Unknown slash — surface as error so the user notices the typo.
		m.lastErr = "unknown command: " + name + " (try /help)"
		return m, nil
	}
	switch cmd.localAction {
	case "quit":
		return m, tea.Quit
	case "new":
		return m.resetThread()
	case "sessions":
		m.view = sessionView
		m.sessionCursor = 0
		return m, loadSessionsCmd(m.stateDir)
	case "clear":
		m.messages = []chatMsg{{role: "assistant", content: "cleared. what's next?"}}
		m.activityLog = nil
		m.scrollOffset = 0
		m.lastErr = ""
		return m, nil
	case "help":
		var b strings.Builder
		b.WriteString("commands:\n")
		for _, c := range slashCommands {
			b.WriteString("  ")
			b.WriteString(c.name)
			if c.usage != "" {
				b.WriteString(" ")
				b.WriteString(c.usage)
			}
			b.WriteString("  —  ")
			b.WriteString(c.desc)
			b.WriteString("\n")
		}
		m.messages = append(m.messages, chatMsg{role: "assistant", content: b.String()})
		m.scrollOffset = 0
		return m, nil
	case "activity":
		var b strings.Builder
		if len(m.activityLog) == 0 {
			b.WriteString("no tool activity this session yet.")
		} else {
			b.WriteString("recent vault writes / tool calls:\n")
			for _, e := range m.activityLog {
				mark := "·"
				if e.done {
					mark = "✓"
				}
				b.WriteString("  " + mark + " " + e.name)
				if e.args != "" {
					b.WriteString(" — " + e.args)
				}
				b.WriteString("\n")
			}
		}
		m.messages = append(m.messages, chatMsg{role: "assistant", content: b.String()})
		m.scrollOffset = 0
		return m, nil
	}
	// No local action → transform args into a chat message and send.
	if cmd.transform != nil {
		return m.sendChat(cmd.transform(args))
	}
	// Fallback: send the raw line if no transform defined.
	return m.sendChat(line)
}

// sendChat enqueues a user→assistant exchange and starts the stream.
func (m model) sendChat(text string) (tea.Model, tea.Cmd) {
	m.messages = append(m.messages, chatMsg{role: "user", content: text})
	m.messages = append(m.messages, chatMsg{role: "assistant", content: ""})
	m.loading = true
	m.lastErr = ""
	m.scrollOffset = 0
	return m, tea.Batch(m.sp.Tick, startStreamCmd(m.apiURL, text, m.threadID))
}

func (m model) resetThread() (tea.Model, tea.Cmd) {
	m.threadID = genID()
	m.messages = []chatMsg{{role: "assistant", content: "ready. what do you need?"}}
	m.activityLog = nil
	m.tokensTotal = 0
	m.tps = 0
	m.scrollOffset = 0
	m.lastErr = ""
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
	hints := "enter:send  alt+enter:newline  ↑:history/scroll  ctrl+n:new  ctrl+l:sessions  /:commands  ctrl+c:quit"
	_ = w
	return statusStyle.Render(hints)
}

func (m model) renderBody(w, h int) string {
	// h total lines: 1 header + 1 (body) + 1 status = 3 fixed, so body = h - 2
	bodyH := h - 2
	if bodyH < 3 {
		bodyH = 3
	}

	// Palette height: one line per matching command, capped at 7.
	paletteH := 0
	if m.slashOpen && len(m.slashMatches) > 0 {
		paletteH = len(m.slashMatches)
		if paletteH > 7 {
			paletteH = 7
		}
	}

	// body = msgArea(bodyH-2-paletteH) + sep(1) + palette(paletteH) + input(1)
	msgAreaH := bodyH - 2 - paletteH
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
	input := m.renderInput(w)

	return strings.Join(rows, "\n") + "\n" + sep + "\n" + input
}

func (m model) renderInput(w int) string {
	if m.loading {
		n := len(m.messages)
		if n > 0 && m.messages[n-1].role == "assistant" && m.messages[n-1].content == "" {
			return m.sp.View() + " thinking..."
		}
		return hintStyle.Render("▌")
	}

	var sb strings.Builder

	// Slash palette — rendered above the input line.
	if m.slashOpen && len(m.slashMatches) > 0 {
		max := len(m.slashMatches)
		if max > 7 {
			max = 7
		}
		// Find widest command name for alignment.
		nameW := 0
		for i := 0; i < max; i++ {
			if n := len(m.slashMatches[i].name); n > nameW {
				nameW = n
			}
		}
		for i := 0; i < max; i++ {
			c := m.slashMatches[i]
			pad := strings.Repeat(" ", nameW-len(c.name))
			desc := c.desc
			if c.usage != "" {
				desc += "  " + c.usage
			}
			line := fmt.Sprintf("  %s%s  %s", c.name, pad, desc)
			if w > 4 && lipgloss.Width(line) > w-2 {
				line = line[:w-2]
			}
			if i == m.slashCursor {
				line = "▶ " + line[2:]
				sb.WriteString(selectedStyle.Render(line))
			} else {
				sb.WriteString(hintStyle.Render(line))
			}
			sb.WriteByte('\n')
		}
	}

	inputText := string(m.inputBuf)
	lines := strings.Split(inputText, "\n")
	display := lines[len(lines)-1]
	if len(lines) > 1 {
		display = fmt.Sprintf("[%d lines] ", len(lines)) + display
	}
	sb.WriteString(hintStyle.Render("> ") + display + "█")
	return sb.String()
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
